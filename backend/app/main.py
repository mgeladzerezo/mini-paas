import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.adapters import Adapters, build_adapters
from app.api import apps, auth, domains, env, github, releases, webhooks
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.metrics import install_metrics
from app.services.worker import ReleaseWorker

logger = logging.getLogger(__name__)


def _run_worker(worker: ReleaseWorker, interval: float, stop: threading.Event) -> None:
    while not stop.wait(interval):
        try:
            worker.tick()
        except Exception:
            logger.exception("worker tick failed")


def create_app(
    settings: Settings | None = None,
    adapters: Adapters | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        if settings.run_worker:
            worker = ReleaseWorker(app.state.session_factory, app.state.adapters, settings)
            threading.Thread(
                target=_run_worker,
                args=(worker, settings.worker_interval_seconds, stop),
                daemon=True,
            ).start()
        yield
        stop.set()

    app = FastAPI(title="mini-paas", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.adapters = adapters or build_adapters(settings)
    app.state.session_factory = session_factory or make_session_factory(
        make_engine(settings.database_url)
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    install_metrics(app)

    for module in (auth, github, apps, releases, env, domains, webhooks):
        app.include_router(module.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz(request: Request) -> dict:
        try:
            with request.app.state.session_factory() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable"
            ) from None
        return {"status": "ready"}

    return app
