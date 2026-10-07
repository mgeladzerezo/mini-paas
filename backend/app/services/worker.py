import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters import Adapters
from app.adapters.builder import BuildRequest, BuildState
from app.config import Settings
from app.metrics import RELEASE_DURATION, RELEASES_FINISHED, WORKER_TICK_ERRORS
from app.models import ACTIVE_STATUSES, Release, ReleaseStatus, utcnow
from app.security import SecretBox
from app.services.deployer import Deployer

logger = logging.getLogger(__name__)


class ReleaseWorker:
    """Advances releases through build, deploy and rollout, one step per tick.

    Each tick reads the state of the outside world (build job, Deployment status) and
    moves every in-flight release at most one step. All state lives in the database,
    so the worker can be restarted at any point and simply continues.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        adapters: Adapters,
        settings: Settings,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._adapters = adapters
        self._settings = settings
        self._now = now
        self._deployer = Deployer(adapters.kube, settings, SecretBox(settings.encryption_key))

    def tick(self) -> int:
        """Advance each app's oldest in-flight release. Returns how many were looked at."""
        with self._session_factory() as session:
            release_ids = session.scalars(
                select(func.min(Release.id))
                .where(Release.status.in_(ACTIVE_STATUSES))
                .group_by(Release.app_id)
            ).all()
            for release_id in release_ids:
                try:
                    self._advance(session, session.get(Release, release_id))
                    session.commit()
                except Exception:
                    logger.exception("could not advance release %s", release_id)
                    WORKER_TICK_ERRORS.inc()
                    session.rollback()
                    self._after_error(session, session.get(Release, release_id))
            return len(release_ids)

    def _after_error(self, session: Session, release: Release) -> None:
        # A build that cannot even start has changed nothing, so it fails outright.
        # Later steps are idempotent and are retried on the next tick until their
        # timeout, which rides out a short API server or registry outage.
        if release.status == ReleaseStatus.PENDING:
            self._fail(release, "could not start the build")
            session.commit()

    def _advance(self, session: Session, release: Release) -> None:
        if release.status == ReleaseStatus.PENDING:
            if release.image:
                self._enter_deploying(release)
            else:
                self._start_build(release)
        elif release.status == ReleaseStatus.BUILDING:
            self._check_build(release)
        elif release.status == ReleaseStatus.DEPLOYING and release.applied_at is None:
            self._apply(session, release)
        elif release.status == ReleaseStatus.DEPLOYING:
            self._check_rollout(session, release)

    def _start_build(self, release: Release) -> None:
        app = release.app
        request = BuildRequest(
            release_id=release.id,
            tenant_slug=app.tenant.slug,
            app_name=app.name,
            repo_full_name=app.repo_full_name,
            commit_sha=release.commit_sha,
            clone_token=self._adapters.github.clone_token(app.installation_id),
        )
        release.build_ref = self._adapters.builder.start(request)
        release.build_started_at = self._now()
        release.status = ReleaseStatus.BUILDING

    def _check_build(self, release: Release) -> None:
        builder = self._adapters.builder
        if self._expired(release.build_started_at, self._settings.build_timeout_seconds):
            builder.cleanup(release.build_ref)
            self._fail(release, "build timed out")
            return

        status = builder.status(release.build_ref)
        release.logs = status.logs
        if status.state == BuildState.RUNNING:
            return
        builder.cleanup(release.build_ref)
        if status.state == BuildState.FAILED:
            self._fail(release, "build failed")
            return
        release.image = status.image
        self._enter_deploying(release)

    def _enter_deploying(self, release: Release) -> None:
        release.status = ReleaseStatus.DEPLOYING
        release.deploy_started_at = self._now()

    def _apply(self, session: Session, release: Release) -> None:
        if self._deploy_expired(release):
            self._revert(session, release, "could not apply manifests")
            return
        self._deployer.apply_release(release.app, release)
        release.applied_at = self._now()
        self._log(release, f"deploying {release.image}")

    def _check_rollout(self, session: Session, release: Release) -> None:
        state = self._deployer.rollout_state(release.app)
        if state == "healthy":
            self._promote(session, release)
        elif state == "failed":
            self._revert(session, release, "rollout failed: new pods did not become ready")
        elif self._deploy_expired(release):
            self._revert(session, release, "rollout timed out")

    def _deploy_expired(self, release: Release) -> bool:
        return self._expired(release.deploy_started_at, self._settings.deploy_timeout_seconds)

    def _promote(self, session: Session, release: Release) -> None:
        app = release.app
        previous = session.get(Release, app.current_release_id) if app.current_release_id else None
        if previous is not None:
            previous.status = ReleaseStatus.SUPERSEDED
        now = self._now()
        release.status = ReleaseStatus.HEALTHY
        release.healthy_at = now
        release.finished_at = now
        app.current_release_id = release.id
        self._log(release, "rollout complete, release is live")
        self._deployer.prune_env_secrets(app, keep_version=release.version)
        RELEASES_FINISHED.labels(release.kind, "healthy").inc()
        RELEASE_DURATION.observe((now - release.created_at).total_seconds())

    def _revert(self, session: Session, release: Release, reason: str) -> None:
        """Fail the release and put the cluster back on the last healthy one.

        The rolling update keeps the old pods serving while the new ones are unready,
        so traffic was never interrupted; re-applying the previous release only removes
        the stuck rollout.
        """
        app = release.app
        previous = session.get(Release, app.current_release_id) if app.current_release_id else None
        if previous is not None:
            self._deployer.apply_release(app, previous)
            self._deployer.prune_env_secrets(app, keep_version=previous.version)
            reason += f"; still serving v{previous.version}"
        self._fail(release, reason)

    def _fail(self, release: Release, reason: str) -> None:
        release.status = ReleaseStatus.FAILED
        release.error = reason[:500]
        release.finished_at = self._now()
        self._log(release, reason)
        RELEASES_FINISHED.labels(release.kind, "failed").inc()

    def _expired(self, started_at: datetime | None, timeout_seconds: int) -> bool:
        if started_at is None:
            return False
        return (self._now() - started_at).total_seconds() > timeout_seconds

    @staticmethod
    def _log(release: Release, line: str) -> None:
        release.logs = f"{release.logs}\n{line}".lstrip("\n")
