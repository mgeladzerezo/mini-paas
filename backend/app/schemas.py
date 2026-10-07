from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

# DNS labels: they end up in namespace names, object names and hostnames.
Slug = Annotated[str, Field(pattern=r"^[a-z]([a-z0-9-]{0,28}[a-z0-9])?$")]
EnvKey = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")]

RESERVED_ENV_KEYS = {"PORT"}


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    tenant_slug: Slug
    tenant_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    namespace: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    tenant: TenantOut


class PlatformInfo(BaseModel):
    base_domain: str
    ingress_target: str
    github_install_url: str


class InstallationClaim(BaseModel):
    installation_id: int
    code: str = Field(min_length=1, max_length=200)


class InstallationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    installation_id: int
    account_login: str


class RepoOut(BaseModel):
    installation_id: int
    full_name: str
    default_branch: str
    private: bool


class AppCreate(BaseModel):
    name: Slug
    installation_id: int
    repo_full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=200)
    branch: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_./-]+$")
    port: int = Field(default=8080, ge=1024, le=65535)
    health_path: str = Field(default="/", pattern=r"^/[A-Za-z0-9_./-]*$", max_length=200)
    min_replicas: int = Field(default=1, ge=1, le=5)
    max_replicas: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def replicas_ordered(self) -> "AppCreate":
        if self.max_replicas < self.min_replicas:
            raise ValueError("max_replicas must be at least min_replicas")
        return self


class AppOut(BaseModel):
    id: int
    name: str
    repo_full_name: str
    branch: str
    port: int
    health_path: str
    min_replicas: int
    max_replicas: int
    hostname: str
    current_release_id: int | None
    current_version: int | None
    latest_status: str | None
    created_at: datetime


class ReleaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    kind: str
    status: str
    commit_sha: str
    commit_message: str
    triggered_by: str
    image: str | None
    error: str | None
    source_release_id: int | None
    can_rollback: bool
    created_at: datetime
    finished_at: datetime | None


class ReleaseDetail(ReleaseOut):
    logs: str


class DeployRequest(BaseModel):
    # False builds the branch head; True redeploys the live image with the current env vars.
    reuse_image: bool = False


class EnvVarIn(BaseModel):
    key: EnvKey
    # None keeps the stored value, so the UI never has to read a secret back to save.
    value: str | None = Field(default=None, max_length=10_000)
    is_secret: bool = False


class EnvUpdate(BaseModel):
    variables: list[EnvVarIn] = Field(max_length=100)

    @model_validator(mode="after")
    def keys_valid(self) -> "EnvUpdate":
        keys = [variable.key for variable in self.variables]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate keys")
        reserved = RESERVED_ENV_KEYS.intersection(keys)
        if reserved:
            raise ValueError(f"{', '.join(sorted(reserved))} is set by the platform")
        return self


class EnvVarOut(BaseModel):
    key: str
    # Secret values are write-only.
    value: str | None
    is_secret: bool


class DomainCreate(BaseModel):
    hostname: str = Field(min_length=4, max_length=253)


class DomainOut(BaseModel):
    id: int
    hostname: str
    verified: bool
    txt_record_name: str
    txt_record_value: str
    cname_target: str
    dns_status: str
    tls_status: str
    last_checked_at: datetime | None
