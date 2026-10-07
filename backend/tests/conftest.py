from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.adapters import Adapters
from app.adapters.fake import FakeBuilder, FakeDns, FakeGitHub, FakeKube
from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.main import create_app
from app.services.worker import ReleaseWorker
from tests.helpers import Workspace


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        adapters="fake",
        base_domain="apps.example.test",
        ingress_target="ingress.example.test",
        registry_repository="registry.test/apps",
        github_webhook_secret="test-webhook-secret",
    )


@pytest.fixture
def adapters(settings: Settings) -> Adapters:
    return Adapters(
        kube=FakeKube(),
        builder=FakeBuilder(settings.registry_repository),
        github=FakeGitHub(),
        dns=FakeDns(),
    )


@pytest.fixture
def session_factory(settings: Settings):
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def client(settings, adapters, session_factory) -> TestClient:
    return TestClient(create_app(settings, adapters, session_factory))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def worker(settings, adapters, session_factory, clock) -> ReleaseWorker:
    return ReleaseWorker(session_factory, adapters, settings, now=clock)


@pytest.fixture
def acme(client, adapters) -> Workspace:
    return Workspace.create(client, adapters, slug="acme", installation_id=101)


@pytest.fixture
def globex(client, adapters) -> Workspace:
    return Workspace.create(client, adapters, slug="globex", installation_id=202)
