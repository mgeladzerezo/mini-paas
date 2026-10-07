from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-jwt-secret-change-me-0123456789"
# Fernet key used only when adapters are fake; real deployments must set PAAS_ENCRYPTION_KEY.
DEV_ENCRYPTION_KEY = "ZGV2LW9ubHktZmVybmV0LWtleS0wMTIzNDU2Nzg5YWI="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAAS_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./paas.db"
    jwt_secret: str = DEV_JWT_SECRET
    jwt_ttl_minutes: int = 12 * 60
    encryption_key: str = DEV_ENCRYPTION_KEY
    cors_origins: list[str] = []

    # "fake" runs the whole release flow in memory so the API can be used without a cluster.
    adapters: Literal["fake", "real"] = "fake"
    run_worker: bool = False
    worker_interval_seconds: float = 3.0

    base_domain: str = "apps.localtest.me"
    # Hostname customers point their CNAME at (the ingress controller's load balancer).
    ingress_target: str = "ingress.localtest.me"
    ingress_class: str = "nginx"
    ingress_namespace: str = "ingress-nginx"
    cluster_issuer: str = "letsencrypt"

    builds_namespace: str = "paas-builds"
    builder_service_account: str = "builder"
    registry_repository: str = "registry.local/mini-paas/apps"
    build_timeout_seconds: int = 900
    deploy_timeout_seconds: int = 300

    github_app_id: str = ""
    github_app_slug: str = "mini-paas"
    github_private_key: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_webhook_secret: str = "dev-webhook-secret"

    @model_validator(mode="after")
    def reject_dev_secrets_with_real_adapters(self) -> "Settings":
        if self.adapters == "real":
            if self.jwt_secret == DEV_JWT_SECRET or self.encryption_key == DEV_ENCRYPTION_KEY:
                raise ValueError("PAAS_JWT_SECRET and PAAS_ENCRYPTION_KEY must be set")
            if self.github_webhook_secret == "dev-webhook-secret":
                raise ValueError("PAAS_GITHUB_WEBHOOK_SECRET must be set")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
