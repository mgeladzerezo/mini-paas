import base64
import json

import pytest

from app.services import manifests
from app.services.manifests import AppSpec, PlatformConfig, ReleaseSpec

CONFIG = PlatformConfig(
    base_domain="apps.example.test",
    ingress_class="nginx",
    ingress_namespace="ingress-nginx",
    cluster_issuer="letsencrypt",
)


def app_spec(tenant: str, name: str = "web") -> AppSpec:
    return AppSpec(
        tenant_slug=tenant,
        name=name,
        port=8080,
        health_path="/healthz",
        min_replicas=2,
        max_replicas=4,
    )


def release_spec(env: dict[str, str] | None = None, version: int = 3) -> ReleaseSpec:
    return ReleaseSpec(
        version=version,
        image="registry.test/apps@sha256:" + "a" * 64,
        env=env if env is not None else {"GREETING": "hello"},
    )


def by_kind(objects: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for obj in objects:
        grouped.setdefault(obj["kind"], []).append(obj)
    return grouped


def one(objects: list[dict], kind: str, name: str | None = None) -> dict:
    matches = [o for o in objects if o["kind"] == kind and name in (None, o["metadata"]["name"])]
    assert len(matches) == 1, f"expected one {kind} {name or ''}, found {len(matches)}"
    return matches[0]


@pytest.fixture
def tenant_objects() -> list[dict]:
    return manifests.render_tenant("acme", CONFIG)


@pytest.fixture
def app_objects() -> list[dict]:
    return manifests.render_app(app_spec("acme"), release_spec(), [], CONFIG)


class TestTenantIsolation:
    def test_namespace_enforces_restricted_pod_security(self, tenant_objects):
        namespace = one(tenant_objects, "Namespace")
        assert namespace["metadata"]["name"] == "tenant-acme"
        labels = namespace["metadata"]["labels"]
        assert labels["pod-security.kubernetes.io/enforce"] == "restricted"
        assert labels["pod-security.kubernetes.io/audit"] == "restricted"
        assert labels["pod-security.kubernetes.io/warn"] == "restricted"

    def test_every_isolation_object_is_rendered(self, tenant_objects):
        kinds = {kind: len(objs) for kind, objs in by_kind(tenant_objects).items()}
        assert kinds == {
            "Namespace": 1,
            "ResourceQuota": 1,
            "LimitRange": 1,
            "ServiceAccount": 1,
            "NetworkPolicy": 3,
        }

    def test_namespace_is_applied_before_its_contents(self, tenant_objects):
        assert tenant_objects[0]["kind"] == "Namespace"

    def test_all_namespaced_objects_live_in_the_tenant_namespace(self, tenant_objects):
        for obj in tenant_objects[1:]:
            assert obj["metadata"]["namespace"] == "tenant-acme"

    def test_quota_caps_compute_and_forbids_external_exposure(self, tenant_objects):
        hard = one(tenant_objects, "ResourceQuota")["spec"]["hard"]
        for resource in ("requests.cpu", "requests.memory", "limits.cpu", "limits.memory", "pods"):
            assert resource in hard
        # Tenants reach the outside world only through the shared ingress controller.
        assert hard["services.loadbalancers"] == "0"
        assert hard["services.nodeports"] == "0"

    def test_limit_range_gives_containers_defaults_and_a_ceiling(self, tenant_objects):
        limit = one(tenant_objects, "LimitRange")["spec"]["limits"][0]
        assert limit["type"] == "Container"
        assert set(limit["default"]) == {"cpu", "memory"}
        assert set(limit["defaultRequest"]) == {"cpu", "memory"}
        assert set(limit["max"]) == {"cpu", "memory"}

    def test_default_deny_selects_all_pods_and_allows_nothing(self, tenant_objects):
        policy = one(tenant_objects, "NetworkPolicy", "default-deny")["spec"]
        assert policy["podSelector"] == {}
        assert sorted(policy["policyTypes"]) == ["Egress", "Ingress"]
        assert "ingress" not in policy
        assert "egress" not in policy

    def test_only_the_ingress_controller_may_connect_in(self, tenant_objects):
        policies = by_kind(tenant_objects)["NetworkPolicy"]
        ingress_rules = [rule for p in policies for rule in p["spec"].get("ingress", [])]
        assert len(ingress_rules) == 1
        peers = ingress_rules[0]["from"]
        assert peers == [
            {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}
                },
                "podSelector": {"matchLabels": {"app.kubernetes.io/name": "ingress-nginx"}},
            }
        ]

    def test_only_dns_egress_is_allowed(self, tenant_objects):
        policies = by_kind(tenant_objects)["NetworkPolicy"]
        egress_rules = [rule for p in policies for rule in p["spec"].get("egress", [])]
        assert len(egress_rules) == 1
        rule = egress_rules[0]
        assert rule["to"] == [
            {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                },
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
            }
        ]
        assert {(p["protocol"], p["port"]) for p in rule["ports"]} == {("UDP", 53), ("TCP", 53)}

    def test_no_policy_peer_is_unrestricted(self, tenant_objects):
        # An empty peer ({}), or a rule with no from/to, would match everything.
        for policy in by_kind(tenant_objects)["NetworkPolicy"]:
            for direction, peer_key in (("ingress", "from"), ("egress", "to")):
                for rule in policy["spec"].get(direction, []):
                    assert rule.get(peer_key), f"{policy['metadata']['name']} has an open rule"
                    for peer in rule[peer_key]:
                        assert peer.get("namespaceSelector", {}).get("matchLabels")
                        assert "ipBlock" not in peer

    def test_service_account_has_no_token_and_no_rbac(self, tenant_objects):
        account = one(tenant_objects, "ServiceAccount")
        assert account["automountServiceAccountToken"] is False
        rbac_kinds = {"Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding"}
        assert not rbac_kinds & {obj["kind"] for obj in tenant_objects}


class TestWorkloadSecurity:
    def test_app_renders_the_expected_objects(self, app_objects):
        assert [obj["kind"] for obj in app_objects] == [
            "Secret",
            "Deployment",
            "Service",
            "HorizontalPodAutoscaler",
            "Ingress",
        ]

    def test_pod_runs_as_non_root(self, app_objects):
        pod = one(app_objects, "Deployment")["spec"]["template"]["spec"]
        context = pod["securityContext"]
        assert context["runAsNonRoot"] is True
        assert context["runAsUser"] > 0
        assert context["seccompProfile"] == {"type": "RuntimeDefault"}

    def test_containers_meet_the_restricted_profile(self, app_objects):
        pod = one(app_objects, "Deployment")["spec"]["template"]["spec"]
        assert "initContainers" not in pod
        for container in pod["containers"]:
            context = container["securityContext"]
            assert context["allowPrivilegeEscalation"] is False
            assert context["privileged"] is False
            assert context["capabilities"] == {"drop": ["ALL"]}
        for forbidden in ("hostNetwork", "hostPID", "hostIPC"):
            assert forbidden not in pod
        assert "volumes" not in pod

    def test_every_container_has_requests_and_limits(self, app_objects):
        pod = one(app_objects, "Deployment")["spec"]["template"]["spec"]
        for container in pod["containers"]:
            for bound in ("requests", "limits"):
                assert set(container["resources"][bound]) == {"cpu", "memory"}

    def test_pod_uses_the_tokenless_service_account(self, app_objects):
        pod = one(app_objects, "Deployment")["spec"]["template"]["spec"]
        assert pod["serviceAccountName"] == manifests.WORKLOAD_SERVICE_ACCOUNT
        assert pod["automountServiceAccountToken"] is False

    def test_rolling_update_never_removes_capacity(self, app_objects):
        strategy = one(app_objects, "Deployment")["spec"]["strategy"]
        assert strategy["rollingUpdate"]["maxUnavailable"] == 0

    def test_replicas_are_left_to_the_hpa(self, app_objects):
        assert "replicas" not in one(app_objects, "Deployment")["spec"]
        hpa = one(app_objects, "HorizontalPodAutoscaler")["spec"]
        assert hpa["scaleTargetRef"] == {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "name": "web",
        }
        assert (hpa["minReplicas"], hpa["maxReplicas"]) == (2, 4)

    def test_service_is_cluster_internal_and_selects_the_app(self, app_objects):
        service = one(app_objects, "Service")["spec"]
        deployment = one(app_objects, "Deployment")["spec"]
        assert service["type"] == "ClusterIP"
        assert service["selector"] == deployment["selector"]["matchLabels"]
        assert service["selector"].items() <= deployment["template"]["metadata"]["labels"].items()

    def test_env_is_delivered_through_the_release_secret(self, app_objects):
        secret = one(app_objects, "Secret")
        container = one(app_objects, "Deployment")["spec"]["template"]["spec"]["containers"][0]
        assert secret["metadata"]["name"] == "web-env-v3"
        assert container["envFrom"] == [{"secretRef": {"name": "web-env-v3"}}]
        assert base64.b64decode(secret["data"]["GREETING"]).decode() == "hello"
        # Values must not leak into the Deployment, which is readable more widely.
        assert "hello" not in json.dumps(one(app_objects, "Deployment"))

    def test_ingress_requests_a_certificate_per_host(self, app_objects):
        ingress = one(app_objects, "Ingress")
        assert ingress["metadata"]["annotations"] == {
            "cert-manager.io/cluster-issuer": "letsencrypt"
        }
        assert ingress["spec"]["ingressClassName"] == "nginx"
        hosts = [rule["host"] for rule in ingress["spec"]["rules"]]
        assert hosts == ["web.acme.apps.example.test"]
        assert [tls["hosts"] for tls in ingress["spec"]["tls"]] == [hosts]

    def test_verified_hosts_are_added_with_their_own_certificate(self):
        ingress = manifests.render_ingress(app_spec("acme"), ["shop.example.org"], CONFIG)
        hosts = [rule["host"] for rule in ingress["spec"]["rules"]]
        assert hosts == ["web.acme.apps.example.test", "shop.example.org"]
        secret_names = [tls["secretName"] for tls in ingress["spec"]["tls"]]
        assert len(set(secret_names)) == 2

    def test_ingress_routes_only_to_the_apps_own_service(self, app_objects):
        ingress = one(app_objects, "Ingress")
        backends = {
            path["backend"]["service"]["name"]
            for rule in ingress["spec"]["rules"]
            for path in rule["http"]["paths"]
        }
        assert backends == {"web"}


def strings_in(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*strings_in(k), *strings_in(v))]
    if isinstance(value, list):
        return [s for item in value for s in strings_in(item)]
    return []


class TestCrossTenantReferences:
    """Two tenants with an identically named app must render fully disjoint objects."""

    @pytest.fixture
    def rendered(self) -> dict[str, list[dict]]:
        result = {}
        for tenant, secret_value in (("acme", "acme-db-password"), ("globex", "globex-api-key")):
            release = release_spec(env={"CREDENTIAL": secret_value})
            result[tenant] = [
                *manifests.render_tenant(tenant, CONFIG),
                *manifests.render_app(app_spec(tenant), release, [f"www.{tenant}.org"], CONFIG),
            ]
        return result

    def test_every_namespaced_object_is_in_its_own_tenant_namespace(self, rendered):
        for tenant, objects in rendered.items():
            for obj in objects:
                if obj["kind"] == "Namespace":
                    assert obj["metadata"]["name"] == f"tenant-{tenant}"
                else:
                    assert obj["metadata"]["namespace"] == f"tenant-{tenant}"

    def test_no_object_mentions_the_other_tenant(self, rendered):
        for tenant, other in (("acme", "globex"), ("globex", "acme")):
            for text in strings_in(rendered[tenant]):
                assert other not in text, f"{tenant} manifest mentions {other}: {text}"

    def test_no_object_carries_the_other_tenants_secret_values(self, rendered):
        acme_secret = base64.b64encode(b"acme-db-password").decode()
        globex_secret = base64.b64encode(b"globex-api-key").decode()
        assert acme_secret in strings_in(rendered["acme"])
        assert globex_secret not in strings_in(rendered["acme"])
        assert acme_secret not in strings_in(rendered["globex"])

    def test_secret_references_resolve_inside_the_same_namespace(self, rendered):
        for objects in rendered.values():
            own_secrets = {
                (o["metadata"]["namespace"], o["metadata"]["name"])
                for o in objects
                if o["kind"] == "Secret"
            }
            deployment = one(objects, "Deployment")
            namespace = deployment["metadata"]["namespace"]
            for container in deployment["spec"]["template"]["spec"]["containers"]:
                for source in container.get("envFrom", []):
                    assert (namespace, source["secretRef"]["name"]) in own_secrets

    def test_network_policies_only_admit_platform_namespaces(self, rendered):
        allowed = {"ingress-nginx", "kube-system"}
        for objects in rendered.values():
            for policy in by_kind(objects)["NetworkPolicy"]:
                rules = policy["spec"].get("ingress", []) + policy["spec"].get("egress", [])
                for rule in rules:
                    for peer in rule.get("from", []) + rule.get("to", []):
                        labels = peer["namespaceSelector"]["matchLabels"]
                        assert labels["kubernetes.io/metadata.name"] in allowed

    def test_same_app_name_gets_distinct_hostnames(self, rendered):
        hosts = {
            tenant: {rule["host"] for rule in one(objects, "Ingress")["spec"]["rules"]}
            for tenant, objects in rendered.items()
        }
        assert not hosts["acme"] & hosts["globex"]


def test_default_hostname_cannot_collide_across_tenants():
    # With a single joined label, app "a-b" of tenant "c" and app "a" of tenant "b-c"
    # would both be "a-b-c".
    first = manifests.default_hostname("a-b", "c", "apps.example.test")
    second = manifests.default_hostname("a", "b-c", "apps.example.test")
    assert first != second
