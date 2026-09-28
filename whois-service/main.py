"""Directory lookup API. Reads only; the worker owns all writes except sync requests."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import Engine, func, or_, select, text
from sqlalchemy.orm import Session

from auth import require_admin, require_api_key
from config import Settings, get_settings
from db import init_db, make_engine, make_sessionmaker
from models import Group, GroupMember, SyncState, User
from schemas import (
    GroupDetail,
    GroupSummary,
    Member,
    Page,
    SyncStatus,
    UserDetail,
    UserSummary,
)
from sync import RESOURCES

log = logging.getLogger(__name__)


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.make_session() as session:
        yield session


whois = APIRouter(prefix="/whois", dependencies=[Depends(require_api_key)], tags=["whois"])
admin = APIRouter(prefix="/admin", tags=["admin"])


def _limit(settings: Settings = Depends(get_settings), limit: int = Query(50, ge=1)) -> int:
    return min(limit, settings.page_size_max)


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@whois.get("/users", response_model=Page[UserSummary])
def list_users(
    q: str | None = Query(None, description="Matches name, mail or UPN"),
    department: str | None = None,
    include_disabled: bool = False,
    limit: int = Depends(_limit),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    stmt = select(User)
    if q:
        pattern = _like(q)
        stmt = stmt.where(
            or_(
                User.display_name.ilike(pattern, escape="\\"),
                User.mail.ilike(pattern, escape="\\"),
                User.user_principal_name.ilike(pattern, escape="\\"),
            )
        )
    if department:
        stmt = stmt.where(func.lower(User.department) == department.lower())
    if not include_disabled:
        stmt = stmt.where(User.account_enabled.is_not(False))
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = session.scalars(stmt.order_by(User.display_name).limit(limit).offset(offset)).all()
    return Page[UserSummary](total=total or 0, limit=limit, offset=offset, items=rows)


@whois.get("/users/{key}", response_model=UserDetail)
def get_user(key: str, session: Session = Depends(get_session)):
    """`key` is the Entra object id, UPN or primary mail address."""
    lowered = key.lower()
    user = session.scalars(
        select(User).where(
            or_(
                User.id == key,
                func.lower(User.user_principal_name) == lowered,
                func.lower(User.mail) == lowered,
            )
        )
    ).first()
    if user is None:
        raise HTTPException(404, "User not found")
    groups = session.scalars(
        select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.member_id == user.id)
        .order_by(Group.display_name)
    ).all()
    return UserDetail(
        **{c: getattr(user, c) for c in UserDetail.model_fields if c != "groups"},
        groups=[GroupSummary.model_validate(g) for g in groups],
    )


@whois.get("/groups", response_model=Page[GroupSummary])
def list_groups(
    q: str | None = None,
    limit: int = Depends(_limit),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    stmt = select(Group)
    if q:
        pattern = _like(q)
        stmt = stmt.where(
            or_(
                Group.display_name.ilike(pattern, escape="\\"),
                Group.mail.ilike(pattern, escape="\\"),
            )
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = session.scalars(stmt.order_by(Group.display_name).limit(limit).offset(offset)).all()
    return Page[GroupSummary](total=total or 0, limit=limit, offset=offset, items=rows)


@whois.get("/groups/{group_id}", response_model=GroupDetail)
def get_group(group_id: str, session: Session = Depends(get_session)):
    group = session.get(Group, group_id)
    if group is None:
        raise HTTPException(404, "Group not found")
    rows = session.execute(
        select(GroupMember, User)
        .outerjoin(User, User.id == GroupMember.member_id)
        .where(GroupMember.group_id == group_id)
        .order_by(User.display_name)
    ).all()
    members = [
        Member(
            id=m.member_id,
            type=m.member_type,
            display_name=u.display_name if u else None,
            mail=u.mail if u else None,
        )
        for m, u in rows
    ]
    return GroupDetail(
        **{c: getattr(group, c) for c in GroupDetail.model_fields if c != "members"},
        members=members,
    )


def _status(session: Session) -> list[SyncStatus]:
    states = {s.resource: s for s in session.scalars(select(SyncState))}
    out = []
    for resource in RESOURCES:
        s = states.get(resource)
        out.append(
            SyncStatus(
                resource=resource,
                has_delta_link=bool(s and s.delta_link),
                requested_at=s and s.requested_at,
                last_run_at=s and s.last_run_at,
                last_success_at=s and s.last_success_at,
                last_full_sync_at=s and s.last_full_sync_at,
                last_item_count=s and s.last_item_count,
                last_error=s and s.last_error,
            )
        )
    return out


@admin.get("/sync", response_model=list[SyncStatus])
def sync_status(_: str = Depends(require_admin), session: Session = Depends(get_session)):
    return _status(session)


@admin.post("/sync", response_model=list[SyncStatus], status_code=202)
def request_sync(
    full: bool = Query(False, description="Discard delta links and resync everything"),
    identity: str = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Ask the worker to run on its next poll (default every 30s)."""
    now = datetime.now(UTC)
    for resource in RESOURCES:
        state = session.get(SyncState, resource) or SyncState(resource=resource)
        state.requested_at = now
        if full:
            state.delta_link = None
        session.add(state)
    session.commit()
    log.info("sync requested by %s (full=%s)", identity, full)
    return _status(session)


def create_app(engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        eng = engine or make_engine(get_settings().database_url)
        init_db(eng)
        app.state.make_session = make_sessionmaker(eng)
        yield
        if engine is None:
            eng.dispose()

    app = FastAPI(title="whois-service", lifespan=lifespan)
    app.include_router(whois)
    app.include_router(admin)

    @app.get("/healthz", include_in_schema=False)
    def healthz(session: Session = Depends(get_session)) -> dict[str, str]:
        session.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


logging.basicConfig(level=get_settings().log_level)
app = create_app()
