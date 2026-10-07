from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.github import GitHubError
from app.deps import AdaptersDep, DbDep, DeployerDep, OwnedAppDep, SettingsDep, UserDep
from app.models import App, GithubInstallation, Release
from app.schemas import AppCreate, AppOut
from app.services.manifests import default_hostname

router = APIRouter(prefix="/api/apps", tags=["apps"])


def app_out(db: Session, app: App, base_domain: str) -> AppOut:
    latest = db.scalar(
        select(Release).where(Release.app_id == app.id).order_by(Release.version.desc()).limit(1)
    )
    current = db.get(Release, app.current_release_id) if app.current_release_id else None
    return AppOut(
        id=app.id,
        name=app.name,
        repo_full_name=app.repo_full_name,
        branch=app.branch,
        port=app.port,
        health_path=app.health_path,
        min_replicas=app.min_replicas,
        max_replicas=app.max_replicas,
        hostname=default_hostname(app.name, app.tenant.slug, base_domain),
        current_release_id=app.current_release_id,
        current_version=current.version if current else None,
        latest_status=latest.status if latest else None,
        created_at=app.created_at,
    )


@router.get("", response_model=list[AppOut])
def list_apps(db: DbDep, user: UserDep, settings: SettingsDep) -> list[AppOut]:
    apps = db.scalars(select(App).where(App.tenant_id == user.tenant_id).order_by(App.name))
    return [app_out(db, app, settings.base_domain) for app in apps]


@router.post("", response_model=AppOut, status_code=status.HTTP_201_CREATED)
def create_app(
    body: AppCreate, db: DbDep, user: UserDep, settings: SettingsDep, adapters: AdaptersDep
) -> AppOut:
    installation = db.scalar(
        select(GithubInstallation).where(
            GithubInstallation.installation_id == body.installation_id,
            GithubInstallation.tenant_id == user.tenant_id,
        )
    )
    if installation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "GitHub installation not found")
    try:
        repos = adapters.github.list_repos(installation.installation_id)
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    if body.repo_full_name not in {repo.full_name for repo in repos}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The GitHub App installation has no access to this repository",
        )

    app = App(tenant_id=user.tenant_id, **body.model_dump())
    db.add(app)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An app with this name already exists"
        ) from None
    return app_out(db, app, settings.base_domain)


@router.get("/{app_id}", response_model=AppOut)
def get_app(app: OwnedAppDep, db: DbDep, settings: SettingsDep) -> AppOut:
    return app_out(db, app, settings.base_domain)


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_app(app: OwnedAppDep, db: DbDep, deployer: DeployerDep) -> Response:
    # Cluster first: if it fails the row stays and the delete can be retried.
    deployer.delete_app(app)
    db.delete(app)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
