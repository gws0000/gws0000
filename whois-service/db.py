"""Engine/session helpers shared by the API and the worker."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from models import Base

# Arbitrary constant; serialises schema creation when API and worker start together.
_SCHEMA_LOCK_KEY = 0x77686F6973  # "whois"


def make_engine(url: str, **kwargs: Any) -> Engine:
    return create_engine(url, pool_pre_ping=True, **kwargs)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
        Base.metadata.create_all(conn)


def upsert(session: Session, model: type[Base], values: dict[str, Any]) -> None:
    """INSERT ... ON CONFLICT (pk) DO UPDATE, touching only the keys supplied.

    Graph delta pages may carry only the changed properties, so columns absent
    from `values` must keep their stored value.
    """
    insert = postgresql.insert if session.bind.dialect.name == "postgresql" else sqlite.insert
    pk = [c.name for c in model.__table__.primary_key.columns]
    stmt = insert(model).values(**values)
    updates = {k: stmt.excluded[k] for k in values if k not in pk}
    stmt = (
        stmt.on_conflict_do_update(index_elements=pk, set_=updates)
        if updates
        else (stmt.on_conflict_do_nothing(index_elements=pk))
    )
    session.execute(stmt)
