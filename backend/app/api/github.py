from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.github import GitHubError
from app.deps import AdaptersDep, DbDep, UserDep
from app.models import GithubInstallation
from app.schemas import InstallationClaim, InstallationOut, RepoOut

router = APIRouter(prefix="/api/github", tags=["github"])


def tenant_installations(db: Session, tenant_id: int) -> list[GithubInstallation]:
    return list(
        db.scalars(
            select(GithubInstallation)
            .where(GithubInstallation.tenant_id == tenant_id)
            .order_by(GithubInstallation.account_login)
        )
    )


@router.post("/installations", response_model=InstallationOut, status_code=status.HTTP_201_CREATED)
def claim_installation(
    body: InstallationClaim, db: DbDep, user: UserDep, adapters: AdaptersDep
) -> GithubInstallation:
    """Link a GitHub App installation to the caller's tenant.

    The installation id arrives in a redirect URL, which anyone can forge. The OAuth
    code from the same redirect proves that the person in front of the browser can
    actually access that installation on GitHub.
    """
    try:
        accessible = adapters.github.installations_for_code(body.code)
    except GitHubError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    match = next((i for i in accessible if i.installation_id == body.installation_id), None)
    if match is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Your GitHub account cannot access this installation"
        )

    existing = db.scalar(
        select(GithubInstallation).where(GithubInstallation.installation_id == body.installation_id)
    )
    if existing is not None:
        if existing.tenant_id != user.tenant_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Installation is linked to another workspace"
            )
        return existing

    installation = GithubInstallation(
        tenant_id=user.tenant_id,
        installation_id=match.installation_id,
        account_login=match.account_login,
    )
    db.add(installation)
    db.commit()
    return installation


@router.get("/installations", response_model=list[InstallationOut])
def list_installations(db: DbDep, user: UserDep) -> list[GithubInstallation]:
    return tenant_installations(db, user.tenant_id)


@router.get("/repos", response_model=list[RepoOut])
def list_repos(db: DbDep, user: UserDep, adapters: AdaptersDep) -> list[RepoOut]:
    repos: list[RepoOut] = []
    for installation in tenant_installations(db, user.tenant_id):
        try:
            found = adapters.github.list_repos(installation.installation_id)
        except GitHubError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
        repos += [
            RepoOut(
                installation_id=installation.installation_id,
                full_name=repo.full_name,
                default_branch=repo.default_branch,
                private=repo.private,
            )
            for repo in found
        ]
    return repos
