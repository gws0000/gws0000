"""Graph delta sync for users and groups.

Each resource keeps its own deltaLink in `sync_state`. With no link the round is
a full sync: every row touched gets `last_seen_at = round start`, and rows not
touched are purged at the end. An expired link (HTTP 410) falls back to a full
sync. Only the properties present in a delta item are written, so partial
"changed properties" payloads never blank out stored columns.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from db import upsert
from graph import DeltaExpired
from models import Group, GroupMember, SyncState, User

log = logging.getLogger(__name__)

USER_FIELDS = {
    "displayName": "display_name",
    "givenName": "given_name",
    "surname": "surname",
    "mail": "mail",
    "userPrincipalName": "user_principal_name",
    "jobTitle": "job_title",
    "department": "department",
    "officeLocation": "office_location",
    "companyName": "company_name",
    "employeeId": "employee_id",
    "mobilePhone": "mobile_phone",
    "businessPhones": "business_phones",
    "accountEnabled": "account_enabled",
}
GROUP_FIELDS = {
    "displayName": "display_name",
    "description": "description",
    "mail": "mail",
    "mailEnabled": "mail_enabled",
    "securityEnabled": "security_enabled",
    "groupTypes": "group_types",
}
USERS_DELTA_URL = "/users/delta?$select=id," + ",".join(USER_FIELDS)
GROUPS_DELTA_URL = "/groups/delta?$select=id," + ",".join(GROUP_FIELDS) + ",members"

Item = dict[str, Any]


class DeltaSource(Protocol):
    def iter_delta(self, url: str) -> Any: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _pick(item: Item, fields: dict[str, str]) -> dict[str, Any]:
    return {col: item[key] for key, col in fields.items() if key in item}


# -- per-item handlers ---------------------------------------------------------
def apply_user(session: Session, item: Item, seen_at: datetime) -> None:
    if "@removed" in item:
        session.execute(delete(GroupMember).where(GroupMember.member_id == item["id"]))
        session.execute(delete(User).where(User.id == item["id"]))
        return
    upsert(session, User, {"id": item["id"], **_pick(item, USER_FIELDS), "last_seen_at": seen_at})


def apply_group(session: Session, item: Item, seen_at: datetime) -> None:
    group_id = item["id"]
    if "@removed" in item:
        session.execute(delete(GroupMember).where(GroupMember.group_id == group_id))
        session.execute(delete(Group).where(Group.id == group_id))
        return
    upsert(session, Group, {"id": group_id, **_pick(item, GROUP_FIELDS), "last_seen_at": seen_at})
    for member in item.get("members@delta", []):
        if "@removed" in member:
            session.execute(
                delete(GroupMember).where(
                    GroupMember.group_id == group_id, GroupMember.member_id == member["id"]
                )
            )
            continue
        odata_type = member.get("@odata.type", "#microsoft.graph.directoryObject")
        upsert(
            session,
            GroupMember,
            {
                "group_id": group_id,
                "member_id": member["id"],
                "member_type": odata_type.rsplit(".", 1)[-1],
                "last_seen_at": seen_at,
            },
        )


def purge_users(session: Session, before: datetime) -> None:
    stale = select(User.id).where(User.last_seen_at < before)
    session.execute(delete(GroupMember).where(GroupMember.member_id.in_(stale)))
    session.execute(delete(User).where(User.last_seen_at < before))


def purge_groups(session: Session, before: datetime) -> None:
    stale = select(Group.id).where(Group.last_seen_at < before)
    session.execute(delete(GroupMember).where(GroupMember.group_id.in_(stale)))
    session.execute(delete(GroupMember).where(GroupMember.last_seen_at < before))
    session.execute(delete(Group).where(Group.last_seen_at < before))


RESOURCES: dict[str, tuple[str, Callable[..., None], Callable[..., None]]] = {
    "users": (USERS_DELTA_URL, apply_user, purge_users),
    "groups": (GROUPS_DELTA_URL, apply_group, purge_groups),
}


# -- driver --------------------------------------------------------------------
def sync_resource(session: Session, graph: DeltaSource, resource: str) -> int:
    initial_url, apply, purge = RESOURCES[resource]
    state = session.get(SyncState, resource) or SyncState(resource=resource)
    session.add(state)
    state.last_run_at = started = _now()
    state.requested_at = None
    session.commit()

    try:
        count, delta_link = _run_round(
            session, graph, state.delta_link or initial_url, apply, started
        )
        full = state.delta_link is None
    except DeltaExpired:
        log.warning("%s: delta link expired, running a full sync", resource)
        session.rollback()
        started = _now()
        count, delta_link = _run_round(session, graph, initial_url, apply, started)
        full = True

    if full:
        purge(session, started)
        state.last_full_sync_at = started
    state.delta_link = delta_link
    state.last_success_at = _now()
    state.last_error = None
    state.last_item_count = count
    session.commit()
    log.info("%s: synced %d items (%s)", resource, count, "full" if full else "delta")
    return count


def _run_round(
    session: Session, graph: DeltaSource, url: str, apply: Callable[..., None], seen_at: datetime
) -> tuple[int, str | None]:
    count, delta_link = 0, None
    for items, page_delta_link in graph.iter_delta(url):
        for item in items:
            apply(session, item, seen_at)
        count += len(items)
        delta_link = page_delta_link or delta_link
        session.commit()  # page at a time; upserts are idempotent if the round restarts
    return count, delta_link


def run_once(make_session: sessionmaker[Session], graph: DeltaSource) -> dict[str, int | None]:
    """Sync users then groups. A failure in one is recorded and does not stop the other."""
    results: dict[str, int | None] = {}
    for resource in RESOURCES:
        with make_session() as session:
            try:
                results[resource] = sync_resource(session, graph, resource)
            except Exception as exc:
                log.exception("%s: sync failed", resource)
                session.rollback()
                state = session.get(SyncState, resource) or SyncState(resource=resource)
                state.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                session.add(state)
                session.commit()
                results[resource] = None
    return results


def run_requested(make_session: sessionmaker[Session]) -> bool:
    """True when an admin has asked for a run via POST /admin/sync."""
    with make_session() as session:
        return (
            session.scalar(
                select(SyncState.resource).where(SyncState.requested_at.is_not(None)).limit(1)
            )
            is not None
        )
