from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import App, Release, ReleaseKind, ReleaseStatus
from app.security import SecretBox


class ReleaseError(Exception):
    pass


def current_env(app: App, box: SecretBox) -> dict[str, str]:
    return {var.key: box.decrypt(var.value_encrypted) for var in app.env_vars}


def _next_version(session: Session, app: App) -> int:
    latest = session.scalar(select(func.max(Release.version)).where(Release.app_id == app.id))
    return (latest or 0) + 1


def _add_release(session: Session, app: App, **fields) -> Release:
    release = Release(
        app_id=app.id,
        version=_next_version(session, app),
        status=ReleaseStatus.PENDING,
        **fields,
    )
    session.add(release)
    session.flush()
    return release


def create_build_release(
    session: Session,
    app: App,
    box: SecretBox,
    commit_sha: str,
    commit_message: str,
    triggered_by: str,
) -> Release:
    return _add_release(
        session,
        app,
        kind=ReleaseKind.BUILD,
        commit_sha=commit_sha,
        commit_message=commit_message[:500],
        triggered_by=triggered_by,
        env_encrypted=box.encrypt_env(current_env(app, box)),
    )


def create_config_release(session: Session, app: App, box: SecretBox, triggered_by: str) -> Release:
    """Redeploy the image that is live now with the app's current env vars."""
    current = session.get(Release, app.current_release_id) if app.current_release_id else None
    if current is None:
        raise ReleaseError("the app has no live release to redeploy")
    return _add_release(
        session,
        app,
        kind=ReleaseKind.CONFIG,
        commit_sha=current.commit_sha,
        commit_message=current.commit_message,
        triggered_by=triggered_by,
        image=current.image,
        env_encrypted=box.encrypt_env(current_env(app, box)),
        source_release_id=current.id,
    )


def create_rollback_release(
    session: Session, app: App, target: Release, triggered_by: str
) -> Release:
    """Queue a release that reuses the target's image and env snapshot unchanged.

    A rollback is a new release rather than a pointer move, so history stays linear
    and a rollback can itself be rolled back.
    """
    if target.app_id != app.id:
        raise ReleaseError("release does not belong to this app")
    if target.healthy_at is None or target.image is None:
        raise ReleaseError("only releases that were healthy can be rolled back to")
    if target.id == app.current_release_id:
        raise ReleaseError("this release is already live")
    return _add_release(
        session,
        app,
        kind=ReleaseKind.ROLLBACK,
        commit_sha=target.commit_sha,
        commit_message=target.commit_message,
        triggered_by=triggered_by,
        image=target.image,
        env_encrypted=target.env_encrypted,
        source_release_id=target.id,
    )
