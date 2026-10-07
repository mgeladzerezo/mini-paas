import pytest

from app.services.manifests import tls_secret_name
from tests.helpers import deploy_healthy, finish_healthy

HOSTNAME = "shop.example.org"


def ingress_hosts(workspace, name: str = "web") -> list[str]:
    ingress = workspace.adapters.kube.get(
        "networking.k8s.io/v1", "Ingress", workspace.namespace, name
    )
    return [rule["host"] for rule in ingress["spec"]["rules"]]


def applied_ingress_hosts(workspace) -> set[str]:
    """Every hostname that was ever sent to the cluster in an Ingress."""
    return {
        rule["host"]
        for manifest in workspace.adapters.kube.applied
        if manifest["kind"] == "Ingress"
        for rule in manifest["spec"]["rules"]
    }


def add_domain(workspace, app_id: int, hostname: str = HOSTNAME) -> dict:
    response = workspace.request("POST", f"/apps/{app_id}/domains", json={"hostname": hostname})
    assert response.status_code == 201, response.text
    return response.json()


def check(workspace, app_id: int, domain_id: int):
    return workspace.request("POST", f"/apps/{app_id}/domains/{domain_id}/check")


def publish_txt(workspace, domain: dict) -> None:
    workspace.adapters.dns.txt_records[domain["txt_record_name"]] = [domain["txt_record_value"]]


@pytest.fixture
def app(acme, worker) -> dict:
    app = acme.create_app()
    deploy_healthy(worker, acme, app["id"], "aaa")
    return app


def test_new_domain_is_unverified_and_explains_the_records_to_create(acme, app):
    domain = add_domain(acme, app["id"])
    assert domain["verified"] is False
    assert domain["txt_record_name"] == "_paas-verify.shop.example.org"
    assert domain["txt_record_value"].startswith("paas-verify=")
    assert domain["cname_target"] == "ingress.example.test"
    assert (domain["dns_status"], domain["tls_status"]) == ("unknown", "unknown")


def test_unverified_domain_never_reaches_an_ingress(acme, app, worker):
    domain = add_domain(acme, app["id"])
    assert ingress_hosts(acme) == ["web.acme.apps.example.test"]

    # Checking without the TXT record, redeploying and rolling back all re-render the
    # Ingress; none of them may include the unverified hostname.
    assert check(acme, app["id"], domain["id"]).json()["verified"] is False
    second = deploy_healthy(worker, acme, app["id"], "bbb")
    first_id = acme.request("GET", f"/apps/{app['id']}/releases").json()[-1]["id"]
    rollback = acme.request("POST", f"/apps/{app['id']}/releases/{first_id}/rollback").json()
    finish_healthy(worker, acme, app["id"], rollback["id"])

    assert second["status"] == "healthy"
    assert applied_ingress_hosts(acme) == {"web.acme.apps.example.test"}


def test_wrong_txt_value_does_not_verify(acme, app):
    domain = add_domain(acme, app["id"])
    acme.adapters.dns.txt_records[domain["txt_record_name"]] = ["paas-verify=someone-elses-token"]
    assert check(acme, app["id"], domain["id"]).json()["verified"] is False
    assert HOSTNAME not in applied_ingress_hosts(acme)


def test_txt_record_on_the_wrong_name_does_not_verify(acme, app):
    domain = add_domain(acme, app["id"])
    acme.adapters.dns.txt_records[HOSTNAME] = [domain["txt_record_value"]]
    assert check(acme, app["id"], domain["id"]).json()["verified"] is False


def test_pointing_dns_at_the_platform_is_not_proof_of_ownership(acme, app):
    domain = add_domain(acme, app["id"])
    acme.adapters.dns.cnames[HOSTNAME] = "ingress.example.test"
    checked = check(acme, app["id"], domain["id"]).json()
    assert checked["verified"] is False
    assert HOSTNAME not in applied_ingress_hosts(acme)


def test_verified_domain_is_added_to_the_ingress_with_tls(acme, app):
    domain = add_domain(acme, app["id"])
    publish_txt(acme, domain)
    checked = check(acme, app["id"], domain["id"]).json()

    assert checked["verified"] is True
    assert ingress_hosts(acme) == ["web.acme.apps.example.test", HOSTNAME]
    ingress = acme.adapters.kube.get("networking.k8s.io/v1", "Ingress", acme.namespace, "web")
    assert {"hosts": [HOSTNAME], "secretName": tls_secret_name("web", HOSTNAME)} in ingress["spec"][
        "tls"
    ]


def test_verification_survives_later_deploys(acme, app, worker):
    domain = add_domain(acme, app["id"])
    publish_txt(acme, domain)
    check(acme, app["id"], domain["id"])
    deploy_healthy(worker, acme, app["id"], "bbb")
    assert ingress_hosts(acme) == ["web.acme.apps.example.test", HOSTNAME]


def test_domain_verified_before_the_first_deploy_is_included_once_deployed(acme, worker):
    app = acme.create_app()
    domain = add_domain(acme, app["id"])
    publish_txt(acme, domain)
    assert check(acme, app["id"], domain["id"]).json()["verified"] is True
    assert acme.adapters.kube.applied == [], "nothing to route to before the first release"

    deploy_healthy(worker, acme, app["id"], "aaa")
    assert ingress_hosts(acme) == ["web.acme.apps.example.test", HOSTNAME]


def test_dns_and_tls_status_are_reported_separately(acme, app):
    domain = add_domain(acme, app["id"])
    publish_txt(acme, domain)
    checked = check(acme, app["id"], domain["id"]).json()
    assert (checked["dns_status"], checked["tls_status"]) == ("pending", "pending")

    acme.adapters.dns.cnames[HOSTNAME] = "ingress.example.test"
    checked = check(acme, app["id"], domain["id"]).json()
    assert (checked["dns_status"], checked["tls_status"]) == ("ok", "pending")

    acme.adapters.kube.apply(
        {
            "apiVersion": "cert-manager.io/v1",
            "kind": "Certificate",
            "metadata": {"name": tls_secret_name("web", HOSTNAME), "namespace": acme.namespace},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
    )
    checked = check(acme, app["id"], domain["id"]).json()
    assert (checked["dns_status"], checked["tls_status"]) == ("ok", "issued")
    assert checked["last_checked_at"] is not None


def test_apex_domain_is_routed_when_its_addresses_match_the_ingress(acme, app):
    domain = add_domain(acme, app["id"], "example.org")
    publish_txt(acme, domain)
    acme.adapters.dns.address_records["ingress.example.test"] = ["203.0.113.10", "203.0.113.11"]
    acme.adapters.dns.address_records["example.org"] = ["203.0.113.11"]
    assert check(acme, app["id"], domain["id"]).json()["dns_status"] == "ok"

    acme.adapters.dns.address_records["example.org"] = ["198.51.100.1"]
    assert check(acme, app["id"], domain["id"]).json()["dns_status"] == "pending"


def test_removing_a_verified_domain_removes_it_from_the_ingress(acme, app):
    domain = add_domain(acme, app["id"])
    publish_txt(acme, domain)
    check(acme, app["id"], domain["id"])

    response = acme.request("DELETE", f"/apps/{app['id']}/domains/{domain['id']}")
    assert response.status_code == 204
    assert ingress_hosts(acme) == ["web.acme.apps.example.test"]
    assert acme.request("GET", f"/apps/{app['id']}/domains").json() == []


def test_hostname_can_only_be_verified_by_one_app(acme, globex, app, worker):
    """Both tenants may claim a hostname; only whoever controls its DNS gets it."""
    globex_app = globex.create_app()
    deploy_healthy(worker, globex, globex_app["id"], "bbb")
    squatter = add_domain(globex, globex_app["id"])
    owner = add_domain(acme, app["id"])
    assert squatter["txt_record_value"] != owner["txt_record_value"]

    publish_txt(acme, owner)
    assert check(globex, globex_app["id"], squatter["id"]).json()["verified"] is False
    assert check(acme, app["id"], owner["id"]).json()["verified"] is True

    # Even if the squatter's token is published later, the hostname is taken.
    acme.adapters.dns.txt_records[owner["txt_record_name"]].append(squatter["txt_record_value"])
    assert check(globex, globex_app["id"], squatter["id"]).status_code == 409
    assert ingress_hosts(globex) == ["web.globex.apps.example.test"]
    assert ingress_hosts(acme) == ["web.acme.apps.example.test", HOSTNAME]


@pytest.mark.parametrize(
    "hostname",
    [
        "other.acme.apps.example.test",
        "web.globex.apps.example.test",
        "apps.example.test",
        "ingress.example.test",
    ],
)
def test_platform_hostnames_cannot_be_attached(acme, app, hostname):
    response = acme.request("POST", f"/apps/{app['id']}/domains", json={"hostname": hostname})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "hostname",
    ["localhost", "*.example.org", "exa mple.org", "-bad.example.org", "example.org/path", "a.b"],
)
def test_invalid_hostnames_are_rejected(acme, app, hostname):
    response = acme.request("POST", f"/apps/{app['id']}/domains", json={"hostname": hostname})
    assert response.status_code == 422


def test_hostname_is_normalized_and_deduplicated(acme, app):
    assert add_domain(acme, app["id"], "Shop.Example.ORG.")["hostname"] == HOSTNAME
    response = acme.request("POST", f"/apps/{app['id']}/domains", json={"hostname": HOSTNAME})
    assert response.status_code == 409
