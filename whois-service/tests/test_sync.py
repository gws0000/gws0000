from __future__ import annotations

from sqlalchemy import select

from models import Group, GroupMember, SyncState, User
from sync import GROUPS_DELTA_URL, USERS_DELTA_URL, run_once, sync_resource
from tests.conftest import FakeGraph

ALICE = {
    "id": "u1",
    "displayName": "Alice",
    "mail": "alice@x.com",
    "department": "Eng",
    "userPrincipalName": "alice@x.com",
    "accountEnabled": True,
    "businessPhones": ["1"],
}
BOB = {"id": "u2", "displayName": "Bob", "mail": "bob@x.com", "accountEnabled": True}
GROUP = {
    "id": "g1",
    "displayName": "Engineers",
    "securityEnabled": True,
    "members@delta": [
        {"@odata.type": "#microsoft.graph.user", "id": "u1"},
        {"@odata.type": "#microsoft.graph.user", "id": "u2"},
    ],
}


def test_full_then_delta(make_session):
    graph = FakeGraph({USERS_DELTA_URL: [[ALICE], [BOB]], GROUPS_DELTA_URL: [[GROUP]]})
    assert run_once(make_session, graph) == {"users": 2, "groups": 1}

    with make_session() as s:
        users_link = s.get(SyncState, "users").delta_link
        groups_link = s.get(SyncState, "groups").delta_link
        assert s.get(SyncState, "users").last_full_sync_at is not None
        assert s.get(User, "u1").business_phones == ["1"]
        assert len(s.scalars(select(GroupMember)).all()) == 2

    # Delta round: partial Alice update, Bob deleted, Bob removed from group.
    graph.rounds[users_link] = [
        [{"id": "u1", "jobTitle": "Lead"}, {"id": "u2", "@removed": {"reason": "deleted"}}]
    ]
    graph.rounds[groups_link] = [
        [
            {
                "id": "g1",
                "members@delta": [
                    {
                        "@odata.type": "#microsoft.graph.user",
                        "id": "u2",
                        "@removed": {"reason": "deleted"},
                    }
                ],
            }
        ]
    ]
    run_once(make_session, graph)

    with make_session() as s:
        alice = s.get(User, "u1")
        assert alice.job_title == "Lead"
        assert alice.display_name == "Alice"  # untouched by the partial payload
        assert s.get(User, "u2") is None
        members = s.scalars(select(GroupMember.member_id)).all()
        assert members == ["u1"]
        assert s.get(Group, "g1").display_name == "Engineers"


def test_expired_delta_falls_back_to_full_and_purges(make_session):
    graph = FakeGraph({USERS_DELTA_URL: [[ALICE, BOB]], GROUPS_DELTA_URL: [[GROUP]]})
    run_once(make_session, graph)
    with make_session() as s:
        link = s.get(SyncState, "users").delta_link

    graph.expired.add(link)
    graph.rounds[USERS_DELTA_URL] = [[ALICE]]  # Bob gone from the tenant
    with make_session() as s:
        assert sync_resource(s, graph, "users") == 1
    with make_session() as s:
        assert s.get(User, "u2") is None
        assert s.scalars(select(GroupMember.member_id)).all() == ["u1"]


def test_failure_is_recorded_and_other_resource_still_runs(make_session):
    graph = FakeGraph({GROUPS_DELTA_URL: [[GROUP]]})  # users URL missing -> KeyError
    assert run_once(make_session, graph) == {"users": None, "groups": 1}
    with make_session() as s:
        assert "KeyError" in s.get(SyncState, "users").last_error
        assert s.get(SyncState, "users").delta_link is None
