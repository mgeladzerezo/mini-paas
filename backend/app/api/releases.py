from fastapi import APIRouter, HTTPException, status

from app.adapters.github import GitHubError
from app.deps import AdaptersDep, BoxDep, DbDep, OwnedAppDep, OwnedReleaseDep, UserDep
from app.models import Release
from app.schemas import DeployRequest, ReleaseDetail, ReleaseOut
from app.services import releases as release_service

router = APIRouter(prefix="/api/apps/{app_id}/releases", tags=["releases"])


@router.get("", response_model=list[ReleaseOut])
def list_releases(app: OwnedAppDep) -> list[Release]:
    return app.releases[:50]


@router.post("", response_model=ReleaseOut, status_code=status.HTTP_201_CREATED)
def deploy(
    body: DeployRequest,
    app: OwnedAppDep,
    db: DbDep,
    user: UserDep,
    box: BoxDep,
    adapters: AdaptersDep,
) -> Release:
    try:
        if body.reuse_image:
            release = release_service.create_config_release(db, app, box, user.email)
        else:
            head = adapters.github.branch_head(app.installation_id, app.repo_full_name, app.branch)
            release = release_service.create_build_release(
                db, app, box, head.sha, head.message, user.email
            )
    except release_service.ReleaseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    db.commit()
    return release


@router.get("/{release_id}", response_model=ReleaseDetail)
def get_release(release: OwnedReleaseDep) -> Release:
    return release


@router.post("/{release_id}/rollback", response_model=ReleaseOut, status_code=201)
def rollback(app: OwnedAppDep, release: OwnedReleaseDep, db: DbDep, user: UserDep) -> Release:
    try:
        created = release_service.create_rollback_release(db, app, release, user.email)
    except release_service.ReleaseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return created
