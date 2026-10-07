"""Pure functions that turn tenants, apps and releases into Kubernetes manifests.

Nothing here talks to a cluster or the database, which is what makes the isolation
and security properties of the output cheap to assert in tests.
"""

import base64
import hashlib
from dataclasses import dataclass

MANAGED_BY = "mini-paas"
WORKLOAD_SERVICE_ACCOUNT = "workload"
ENV_COMPONENT_LABEL = "paas.dev/component"
# Images are run as this UID regardless of the Dockerfile's USER, see README.
WORKLOAD_UID = 10001

CONTAINER_REQUESTS = {"cpu": "100m", "memory": "128Mi"}
CONTAINER_LIMITS = {"cpu": "500m", "memory": "256Mi"}

TENANT_QUOTA = {
    "requests.cpu": "2",
    "requests.memory": "4Gi",
    "limits.cpu": "4",
    "limits.memory": "8Gi",
    "pods": "20",
    "services": "10",
    "services.loadbalancers": "0",
    "services.nodeports": "0",
    "persistentvolumeclaims": "0",
    "count/ingresses.networking.k8s.io": "10",
}


@dataclass(frozen=True)
class PlatformConfig:
    base_domain: str
    ingress_class: str
    ingress_namespace: str
    cluster_issuer: str


@dataclass(frozen=True)
class AppSpec:
    tenant_slug: str
    name: str
    port: int
    health_path: str
    min_replicas: int
    max_replicas: int


@dataclass(frozen=True)
class ReleaseSpec:
    version: int
    image: str
    env: dict[str, str]


def tenant_namespace(tenant_slug: str) -> str:
    return f"tenant-{tenant_slug}"


def default_hostname(app_name: str, tenant_slug: str, base_domain: str) -> str:
    # Separate DNS labels, so app "a-b" of tenant "c" can never collide with app "a" of "b-c".
    return f"{app_name}.{tenant_slug}.{base_domain}"


def env_secret_name(app_name: str, version: int) -> str:
    # One immutable Secret per release. Pods of the previous release keep reading the
    # config they were started with, even if they restart in the middle of a rollout.
    return f"{app_name}-env-v{version}"


def tls_secret_name(app_name: str, hostname: str) -> str:
    # One certificate per hostname: a custom domain whose DNS is not pointed at the
    # cluster yet must not block issuance for the app's other hostnames.
    return f"{app_name}-tls-{hashlib.sha256(hostname.encode()).hexdigest()[:10]}"


def _tenant_labels(tenant_slug: str) -> dict[str, str]:
    return {"app.kubernetes.io/managed-by": MANAGED_BY, "paas.dev/tenant": tenant_slug}


def _app_labels(app: AppSpec) -> dict[str, str]:
    return {**_tenant_labels(app.tenant_slug), "app.kubernetes.io/name": app.name}


def _selector(app: AppSpec) -> dict[str, str]:
    return {"app.kubernetes.io/name": app.name}


def render_tenant(tenant_slug: str, config: PlatformConfig) -> list[dict]:
    namespace = tenant_namespace(tenant_slug)
    labels = _tenant_labels(tenant_slug)

    def metadata(name: str) -> dict:
        return {"name": name, "namespace": namespace, "labels": labels}

    return [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": namespace,
                "labels": {
                    **labels,
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/enforce-version": "latest",
                    "pod-security.kubernetes.io/audit": "restricted",
                    "pod-security.kubernetes.io/warn": "restricted",
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ResourceQuota",
            "metadata": metadata("tenant-quota"),
            "spec": {"hard": dict(TENANT_QUOTA)},
        },
        {
            "apiVersion": "v1",
            "kind": "LimitRange",
            "metadata": metadata("tenant-limits"),
            "spec": {
                "limits": [
                    {
                        "type": "Container",
                        "default": dict(CONTAINER_LIMITS),
                        "defaultRequest": dict(CONTAINER_REQUESTS),
                        "max": {"cpu": "2", "memory": "2Gi"},
                    }
                ]
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": metadata(WORKLOAD_SERVICE_ACCOUNT),
            # No Role is ever bound to this account, and without a mounted token a
            # compromised pod has nothing to present to the API server anyway.
            "automountServiceAccountToken": False,
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": metadata("default-deny"),
            "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": metadata("allow-ingress-controller"),
            "spec": {
                "podSelector": {},
                "policyTypes": ["Ingress"],
                "ingress": [
                    {
                        "from": [
                            {
                                # Both selectors in one entry: the peer must match both.
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": config.ingress_namespace
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": {"app.kubernetes.io/name": "ingress-nginx"}
                                },
                            }
                        ]
                    }
                ],
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": metadata("allow-dns"),
            "spec": {
                "podSelector": {},
                "policyTypes": ["Egress"],
                "egress": [
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                                },
                                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                            }
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                        ],
                    }
                ],
            },
        },
    ]


def env_secret_selector(app_name: str) -> str:
    return f"app.kubernetes.io/name={app_name},{ENV_COMPONENT_LABEL}=env"


def render_env_secret(app: AppSpec, release: ReleaseSpec) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": env_secret_name(app.name, release.version),
            "namespace": tenant_namespace(app.tenant_slug),
            "labels": {**_app_labels(app), ENV_COMPONENT_LABEL: "env"},
        },
        "type": "Opaque",
        "immutable": True,
        "data": {
            key: base64.b64encode(value.encode()).decode()
            for key, value in sorted(release.env.items())
        },
    }


def render_deployment(app: AppSpec, release: ReleaseSpec) -> dict:
    probe = {"httpGet": {"path": app.health_path, "port": "http"}}
    # `replicas` is deliberately absent: the HPA owns it, and with server-side apply a
    # field we never set is a field we never reset on the next deploy.
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": app.name,
            "namespace": tenant_namespace(app.tenant_slug),
            "labels": _app_labels(app),
        },
        "spec": {
            "selector": {"matchLabels": _selector(app)},
            "revisionHistoryLimit": 2,
            "progressDeadlineSeconds": 180,
            # Old pods stay until new ones are ready, so a broken release never takes
            # the app down; it just stalls until the platform reverts it.
            "strategy": {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
            },
            "template": {
                "metadata": {
                    "labels": _app_labels(app),
                    "annotations": {"paas.dev/release": str(release.version)},
                },
                "spec": {
                    "serviceAccountName": WORKLOAD_SERVICE_ACCOUNT,
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": WORKLOAD_UID,
                        "runAsGroup": WORKLOAD_UID,
                        "fsGroup": WORKLOAD_UID,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "app",
                            "image": release.image,
                            "ports": [{"name": "http", "containerPort": app.port}],
                            "env": [{"name": "PORT", "value": str(app.port)}],
                            "envFrom": [
                                {"secretRef": {"name": env_secret_name(app.name, release.version)}}
                            ],
                            "resources": {
                                "requests": dict(CONTAINER_REQUESTS),
                                "limits": dict(CONTAINER_LIMITS),
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "privileged": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "readinessProbe": {**probe, "periodSeconds": 5, "failureThreshold": 3},
                            "livenessProbe": {
                                **probe,
                                "initialDelaySeconds": 15,
                                "periodSeconds": 10,
                            },
                        }
                    ],
                },
            },
        },
    }


def render_service(app: AppSpec) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": app.name,
            "namespace": tenant_namespace(app.tenant_slug),
            "labels": _app_labels(app),
        },
        "spec": {
            "type": "ClusterIP",
            "selector": _selector(app),
            "ports": [{"name": "http", "port": 80, "targetPort": "http"}],
        },
    }


def render_ingress(app: AppSpec, verified_hosts: list[str], config: PlatformConfig) -> dict:
    """Ingress for the platform hostname plus custom domains.

    Callers must pass only domains whose ownership has been verified; see
    `services.domains.verified_hostnames`, the single place that list is built.
    """
    hosts = [default_hostname(app.name, app.tenant_slug, config.base_domain), *verified_hosts]
    backend = {"service": {"name": app.name, "port": {"name": "http"}}}
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": app.name,
            "namespace": tenant_namespace(app.tenant_slug),
            "labels": _app_labels(app),
            "annotations": {"cert-manager.io/cluster-issuer": config.cluster_issuer},
        },
        "spec": {
            "ingressClassName": config.ingress_class,
            "tls": [
                {"hosts": [host], "secretName": tls_secret_name(app.name, host)} for host in hosts
            ],
            "rules": [
                {
                    "host": host,
                    "http": {"paths": [{"path": "/", "pathType": "Prefix", "backend": backend}]},
                }
                for host in hosts
            ],
        },
    }


def render_hpa(app: AppSpec) -> dict:
    return {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": {
            "name": app.name,
            "namespace": tenant_namespace(app.tenant_slug),
            "labels": _app_labels(app),
        },
        "spec": {
            "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": app.name},
            "minReplicas": app.min_replicas,
            "maxReplicas": app.max_replicas,
            "metrics": [
                {
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {"type": "Utilization", "averageUtilization": 70},
                    },
                }
            ],
        },
    }


def render_app(
    app: AppSpec, release: ReleaseSpec, verified_hosts: list[str], config: PlatformConfig
) -> list[dict]:
    # The Secret comes first so the pods of a new Deployment never start without it.
    return [
        render_env_secret(app, release),
        render_deployment(app, release),
        render_service(app),
        render_hpa(app),
        render_ingress(app, verified_hosts, config),
    ]


# Kinds that exist once per app under the app's name, in the order they are torn down.
NAMED_APP_KINDS = [
    ("networking.k8s.io/v1", "Ingress"),
    ("autoscaling/v2", "HorizontalPodAutoscaler"),
    ("v1", "Service"),
    ("apps/v1", "Deployment"),
]
