from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator):
    """Timezone-aware timestamps on every backend.

    SQLite drops the offset on the way out; everything is written in UTC, so it is
    safe to put it back.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class ReleaseStatus(StrEnum):
    PENDING = "pending"
    BUILDING = "building"
    DEPLOYING = "deploying"
    HEALTHY = "healthy"
    SUPERSEDED = "superseded"
    FAILED = "failed"


ACTIVE_STATUSES = (ReleaseStatus.PENDING, ReleaseStatus.BUILDING, ReleaseStatus.DEPLOYING)


class ReleaseKind(StrEnum):
    BUILD = "build"
    CONFIG = "config"
    ROLLBACK = "rollback"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    @property
    def namespace(self) -> str:
        return f"tenant-{self.slug}"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    tenant: Mapped[Tenant] = relationship()


class GithubInstallation(Base):
    __tablename__ = "github_installations"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    installation_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    account_login: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class App(Base):
    __tablename__ = "apps"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_apps_tenant_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(30))
    repo_full_name: Mapped[str] = mapped_column(String(200), index=True)
    branch: Mapped[str] = mapped_column(String(200))
    installation_id: Mapped[int] = mapped_column(BigInteger)
    port: Mapped[int] = mapped_column(Integer, default=8080)
    health_path: Mapped[str] = mapped_column(String(200), default="/")
    min_replicas: Mapped[int] = mapped_column(Integer, default=1)
    max_replicas: Mapped[int] = mapped_column(Integer, default=3)
    # Plain integer rather than a foreign key: releases already point at apps, and a
    # two-way constraint would make inserts and deletes order-dependent.
    current_release_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    tenant: Mapped[Tenant] = relationship()
    env_vars: Mapped[list["EnvVar"]] = relationship(
        back_populates="app", cascade="all, delete-orphan", order_by="EnvVar.key"
    )
    releases: Mapped[list["Release"]] = relationship(
        back_populates="app", cascade="all, delete-orphan", order_by="Release.version.desc()"
    )
    domains: Mapped[list["Domain"]] = relationship(
        back_populates="app", cascade="all, delete-orphan", order_by="Domain.hostname"
    )


class EnvVar(Base):
    __tablename__ = "env_vars"
    __table_args__ = (UniqueConstraint("app_id", "key", name="uq_env_vars_app_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    value_encrypted: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)

    app: Mapped[App] = relationship(back_populates="env_vars")


class Release(Base):
    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("app_id", "version", name="uq_releases_app_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), default=ReleaseKind.BUILD)
    status: Mapped[str] = mapped_column(String(20), default=ReleaseStatus.PENDING, index=True)
    commit_sha: Mapped[str] = mapped_column(String(40))
    commit_message: Mapped[str] = mapped_column(String(500), default="")
    triggered_by: Mapped[str] = mapped_column(String(254), default="")
    image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Encrypted JSON snapshot of the app's env vars at the time the release was created.
    env_encrypted: Mapped[str] = mapped_column(Text)
    build_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    logs: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_release_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set once the rollout was observed healthy; only these releases can be rolled back to.
    healthy_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    build_started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    deploy_started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    # Set once the release's manifests were applied; until then the deploy step retries.
    applied_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    app: Mapped[App] = relationship(back_populates="releases")

    @property
    def can_rollback(self) -> bool:
        return self.healthy_at is not None and self.id != self.app.current_release_id


class Domain(Base):
    __tablename__ = "domains"
    __table_args__ = (UniqueConstraint("app_id", "hostname", name="uq_domains_app_hostname"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    hostname: Mapped[str] = mapped_column(String(253), index=True)
    verification_token: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    dns_status: Mapped[str] = mapped_column(String(20), default="unknown")
    tls_status: Mapped[str] = mapped_column(String(20), default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    app: Mapped[App] = relationship(back_populates="domains")


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event: Mapped[str] = mapped_column(String(50))
    received_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
