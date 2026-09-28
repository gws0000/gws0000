from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool

import config
from db import init_db, make_engine, make_sessionmaker


@pytest.fixture
def engine():
    eng = make_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(eng, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def make_session(engine):
    return make_sessionmaker(engine)


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("WHOIS_API_KEY", "old-key,test-key")
    monkeypatch.setenv("ADMIN_ALLOWED_GROUPS", "platform-admins")
    monkeypatch.setenv("ADMIN_PROXY_SHARED_SECRET", "s3cret")
    config.get_settings.cache_clear()
    yield config.get_settings()
    config.get_settings.cache_clear()


@pytest.fixture
def client(engine, settings):
    from main import create_app

    with TestClient(create_app(engine)) as c:
        yield c


class FakeGraph:
    """Serves scripted delta pages keyed by the URL requested."""

    def __init__(self, rounds: dict[str, list[list[dict]]]):
        self.rounds = rounds
        self.calls: list[str] = []
        self.expired: set[str] = set()

    def iter_delta(self, url):
        from graph import DeltaExpired

        self.calls.append(url)
        if url in self.expired:
            raise DeltaExpired(url)
        pages = self.rounds[url]
        for i, items in enumerate(pages):
            last = i == len(pages) - 1
            yield items, (f"delta:{url}:{len(self.calls)}" if last else None)
