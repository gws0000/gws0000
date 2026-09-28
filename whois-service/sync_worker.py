"""Background worker: `python sync_worker.py` runs the sync loop,
`python sync_worker.py --check` is the container healthcheck."""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from config import get_settings
from db import init_db, make_engine, make_sessionmaker
from graph import GraphClient
from models import SyncState
from sync import run_once, run_requested

log = logging.getLogger("sync_worker")


def healthcheck() -> int:
    """Healthy if a sync round started within two intervals (plus slack)."""
    settings = get_settings()
    make_session = make_sessionmaker(make_engine(settings.database_url))
    with make_session() as session:
        last_run = session.scalar(select(func.max(SyncState.last_run_at)))
    if last_run is None:
        return 1
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=UTC)
    limit = timedelta(seconds=settings.sync_interval_seconds * 2 + 300)
    return 0 if datetime.now(UTC) - last_run < limit else 1


def _requested(make_session) -> bool:
    try:
        return run_requested(make_session)
    except Exception:
        log.exception("could not read sync_state")
        return False


def main() -> int:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    engine = make_engine(settings.database_url)
    init_db(engine)
    make_session = make_sessionmaker(engine)
    graph = GraphClient(settings)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log.info("worker started, interval=%ss", settings.sync_interval_seconds)
    try:
        while not stop.is_set():
            run_once(make_session, graph)
            next_run = time.monotonic() + settings.sync_interval_seconds
            while not stop.wait(settings.sync_poll_seconds):
                if time.monotonic() >= next_run or _requested(make_session):
                    break
    finally:
        graph.close()
        engine.dispose()
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(healthcheck() if "--check" in sys.argv[1:] else main())
