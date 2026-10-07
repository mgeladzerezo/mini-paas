from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import Adapters
from app.config import Settings
from app.models import App, Domain, Release, User
from app.security import SecretBox, decode_access_token
from app.services.deployer import Deployer

bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_adapters(request: Request) -> Adapters:
    return request.app.state.adapters


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
AdaptersDep = Annotated[Adapters, Depends(get_adapters)]
DbDep = Annotated[Session, Depends(get_db)]


def get_box(settings: SettingsDep) -> SecretBox:
    return SecretBox(settings.encryption_key)


BoxDep = Annotated[SecretBox, Depends(get_box)]


def get_deployer(adapters: AdaptersDep, settings: SettingsDep, box: BoxDep) -> Deployer:
    return Deployer(adapters.kube, settings, box)


DeployerDep = Annotated[Deployer, Depends(get_deployer)]


def get_current_user(
    db: DbDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    user_id = decode_access_token(credentials.credentials, settings.jwt_secret)
    user = db.get(User, user_id) if user_id is not None else None
    if user is None:
        raise unauthorized
    return user


UserDep = Annotated[User, Depends(get_current_user)]


def get_owned_app(app_id: int, db: DbDep, user: UserDep) -> App:
    """Load an app only if it belongs to the caller's tenant.

    Every /apps/{app_id}/... route depends on this, so the tenant filter lives in one
    place. Another tenant's app is reported as 404, the same as one that does not
    exist, so ids cannot be probed.
    """
    app = db.scalar(select(App).where(App.id == app_id, App.tenant_id == user.tenant_id))
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "App not found")
    return app


OwnedAppDep = Annotated[App, Depends(get_owned_app)]


def get_owned_release(release_id: int, app: OwnedAppDep, db: DbDep) -> Release:
    release = db.scalar(select(Release).where(Release.id == release_id, Release.app_id == app.id))
    if release is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Release not found")
    return release


def get_owned_domain(domain_id: int, app: OwnedAppDep, db: DbDep) -> Domain:
    domain = db.scalar(select(Domain).where(Domain.id == domain_id, Domain.app_id == app.id))
    if domain is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Domain not found")
    return domain


OwnedReleaseDep = Annotated[Release, Depends(get_owned_release)]
OwnedDomainDep = Annotated[Domain, Depends(get_owned_domain)]
