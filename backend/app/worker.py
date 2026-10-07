"""Standalone release worker: `python -m app.worker`.

Run exactly one replica. Ticks are not coordinated across processes, so two workers
could both start the same build.
"""

import logging
import signal
import threading

from app.adapters import build_adapters
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.services.worker import ReleaseWorker

logger = logging.getLogger("app.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    worker = ReleaseWorker(
        make_session_factory(make_engine(settings.database_url)),
        build_adapters(settings),
        settings,
    )

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    logger.info("worker started, interval %ss", settings.worker_interval_seconds)
    while not stop.wait(settings.worker_interval_seconds):
        try:
            worker.tick()
        except Exception:
            logger.exception("tick failed")
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
