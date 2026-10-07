from app.adapters.kube import KubeClient
from app.config import Settings
from app.models import App, Release
from app.security import SecretBox
from app.services import manifests
from app.services.domains import verified_hostnames
from app.services.manifests import AppSpec, PlatformConfig, ReleaseSpec


def platform_config(settings: Settings) -> PlatformConfig:
    return PlatformConfig(
        base_domain=settings.base_domain,
        ingress_class=settings.ingress_class,
        ingress_namespace=settings.ingress_namespace,
        cluster_issuer=settings.cluster_issuer,
    )


def app_spec(app: App) -> AppSpec:
    return AppSpec(
        tenant_slug=app.tenant.slug,
        name=app.name,
        port=app.port,
        health_path=app.health_path,
        min_replicas=app.min_replicas,
        max_replicas=app.max_replicas,
    )


def rollout_state(deployment: dict | None) -> str:
    """Classify a Deployment as "progressing", "healthy" or "failed".

    Mirrors what `kubectl rollout status` checks.
    """
    if deployment is None:
        return "progressing"
    status = deployment.get("status") or {}
    for condition in status.get("conditions") or []:
        if condition.get("type") == "Progressing" and condition.get("status") == "False":
            return "failed"
    # Until the controller has seen the new spec, the counters describe the old rollout.
    if status.get("observedGeneration", 0) < deployment["metadata"].get("generation", 0):
        return "progressing"
    desired = deployment.get("spec", {}).get("replicas", 1)
    counters = [status.get(k, 0) for k in ("replicas", "updatedReplicas", "availableReplicas")]
    return "healthy" if all(count == desired for count in counters) else "progressing"


class Deployer:
    def __init__(self, kube: KubeClient, settings: Settings, box: SecretBox) -> None:
        self._kube = kube
        self._config = platform_config(settings)
        self._box = box

    def apply_release(self, app: App, release: Release) -> None:
        # Tenant objects are re-applied on every deploy. It is idempotent, and it means a
        # namespace that was tampered with or predates a policy change converges again.
        for manifest in manifests.render_tenant(app.tenant.slug, self._config):
            self._kube.apply(manifest)
        spec = ReleaseSpec(
            version=release.version,
            image=release.image or "",
            env=self._box.decrypt_env(release.env_encrypted),
        )
        rendered = manifests.render_app(app_spec(app), spec, verified_hostnames(app), self._config)
        for manifest in rendered:
            self._kube.apply(manifest)

    def sync_ingress(self, app: App) -> None:
        if app.current_release_id is None:
            return
        self._kube.apply(
            manifests.render_ingress(app_spec(app), verified_hostnames(app), self._config)
        )

    def rollout_state(self, app: App) -> str:
        return rollout_state(
            self._kube.get("apps/v1", "Deployment", app.tenant.namespace, app.name)
        )

    def prune_env_secrets(self, app: App, keep_version: int | None) -> None:
        keep = manifests.env_secret_name(app.name, keep_version) if keep_version else None
        selector = manifests.env_secret_selector(app.name)
        for secret in self._kube.list_by_label("v1", "Secret", app.tenant.namespace, selector):
            name = secret["metadata"]["name"]
            if name != keep:
                self._kube.delete("v1", "Secret", app.tenant.namespace, name)

    def delete_app(self, app: App) -> None:
        for api_version, kind in manifests.NAMED_APP_KINDS:
            self._kube.delete(api_version, kind, app.tenant.namespace, app.name)
        self.prune_env_secrets(app, keep_version=None)

    def certificate_ready(self, app: App, hostname: str) -> bool:
        # cert-manager's ingress-shim names the Certificate after the TLS secret.
        certificate = self._kube.get(
            "cert-manager.io/v1",
            "Certificate",
            app.tenant.namespace,
            manifests.tls_secret_name(app.name, hostname),
        )
        conditions = ((certificate or {}).get("status") or {}).get("conditions") or []
        return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
