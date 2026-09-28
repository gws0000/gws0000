from __future__ import annotations

from sync import GROUPS_DELTA_URL, USERS_DELTA_URL, run_once
from tests.conftest import FakeGraph
from tests.test_sync import ALICE, BOB, GROUP

KEY = {"X-API-Key": "test-key"}
ADMIN = {
    "x-authentik-uid": "abc",
    "x-authentik-groups": "staff|platform-admins",
    "x-proxy-secret": "s3cret",
}


def _seed(make_session, disabled_bob=False):
    bob = {**BOB, "accountEnabled": not disabled_bob}
    run_once(
        make_session, FakeGraph({USERS_DELTA_URL: [[ALICE, bob]], GROUPS_DELTA_URL: [[GROUP]]})
    )


def test_api_key_required(client):
    assert client.get("/whois/users").status_code == 401
    assert client.get("/whois/users", headers={"X-API-Key": "nope"}).status_code == 401
    assert (
        client.get("/whois/users", headers={"Authorization": "Bearer old-key"}).status_code == 200
    )


def test_search_and_detail(client, make_session):
    _seed(make_session, disabled_bob=True)
    body = client.get("/whois/users", params={"q": "ali"}, headers=KEY).json()
    assert body["total"] == 1 and body["items"][0]["id"] == "u1"

    assert client.get("/whois/users", headers=KEY).json()["total"] == 1  # Bob disabled
    assert (
        client.get("/whois/users", params={"include_disabled": True}, headers=KEY).json()["total"]
        == 2
    )
    assert client.get("/whois/users", params={"q": "%"}, headers=KEY).json()["total"] == 0

    detail = client.get("/whois/users/ALICE@x.com", headers=KEY).json()
    assert detail["id"] == "u1" and detail["groups"][0]["id"] == "g1"
    assert client.get("/whois/users/missing", headers=KEY).status_code == 404

    group = client.get("/whois/groups/g1", headers=KEY).json()
    assert {m["display_name"] for m in group["members"]} == {"Alice", "Bob"}
    assert client.get("/whois/groups", params={"q": "eng"}, headers=KEY).json()["total"] == 1


def test_admin_auth(client):
    assert client.get("/admin/sync").status_code == 401
    assert client.get("/admin/sync", headers={**ADMIN, "x-proxy-secret": "x"}).status_code == 401
    assert (
        client.get("/admin/sync", headers={**ADMIN, "x-authentik-groups": "staff"}).status_code
        == 403
    )
    assert client.get("/admin/sync", headers=ADMIN).status_code == 200


def test_admin_full_resync_request(client, make_session):
    _seed(make_session)
    before = {s["resource"]: s for s in client.get("/admin/sync", headers=ADMIN).json()}
    assert before["users"]["has_delta_link"]

    resp = client.post("/admin/sync", params={"full": True}, headers=ADMIN)
    assert resp.status_code == 202
    after = {s["resource"]: s for s in resp.json()}
    assert not after["users"]["has_delta_link"] and after["users"]["requested_at"]

    from sync import run_requested

    assert run_requested(make_session)


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
