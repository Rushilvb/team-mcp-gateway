"""REST API (/api): sessions, invites, roles, keys, servers, audit.

Roles are checked through the API directly (not the UI), as CLAUDE.md asks."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from conftest import BETA_TOKEN, SECRETS, api_client, create_user, create_user_key, mcp_client
from gateway.models import User, UserSession

pytestmark = pytest.mark.anyio

PW = "correct horse battery"
ORIGIN = {"Origin": "http://app.localtest.me"}      # the configured public URL


async def tool_names(gateway, key) -> set[str]:
    async with mcp_client(gateway, key) as client:
        return {t.name for t in (await client.list_tools()).tools}


# --- login / sessions -------------------------------------------------------------

async def test_login_me_logout(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    async with api_client(gateway) as c:
        assert (await c.get("/api/auth/me")).status_code == 401
        r = await c.post("/api/auth/login", json={"email": "admin@example.com", "password": PW})
        assert r.status_code == 200 and r.json()["role"] == "admin"
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
        token = c.cookies.get("gw_session")
        assert (await c.get("/api/auth/me")).json()["email"] == "admin@example.com"
        assert (await c.post("/api/auth/logout")).status_code == 204
        assert (await c.get("/api/auth/me")).status_code == 401
    # The old cookie value is dead server-side, not just deleted in the browser.
    async with api_client(gateway) as c:
        c.cookies.set("gw_session", token)
        assert (await c.get("/api/auth/me")).status_code == 401
    async with db() as s:
        assert token not in [row.token_hash for row in (await s.scalars(select(UserSession))).all()]


async def test_bad_login_same_error_for_wrong_password_and_unknown_user(gateway, db):
    await create_user(db, "a@example.com")
    async with api_client(gateway) as c:
        wrong = await c.post("/api/auth/login", json={"email": "a@example.com", "password": "nope"})
        unknown = await c.post("/api/auth/login", json={"email": "ghost@example.com", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "invalid email or password"}


async def test_login_rate_limit(gateway, db):
    await create_user(db, "a@example.com")
    async with api_client(gateway) as c:
        for _ in range(5):
            assert (await c.post("/api/auth/login", json={"email": "a@example.com", "password": "x"})).status_code == 401
        # Locked out now, even with the right password.
        assert (await c.post("/api/auth/login", json={"email": "a@example.com", "password": PW})).status_code == 429


async def test_expired_session_and_disabled_user_are_rejected(gateway, db):
    uid = await create_user(db, "a@example.com")
    async with api_client(gateway, "a@example.com") as c:
        async with db() as s:
            await s.execute(update(UserSession).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            await s.commit()
        assert (await c.get("/api/auth/me")).status_code == 401
    async with api_client(gateway, "a@example.com") as c:
        async with db() as s:
            await s.execute(update(User).where(User.id == uid).values(status="disabled"))
            await s.commit()
        assert (await c.get("/api/auth/me")).status_code == 401


# --- invites ------------------------------------------------------------------------

async def test_invite_flow(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    async with api_client(gateway, "admin@example.com") as admin:
        r = await admin.post("/api/users", json={"email": "new@example.com", "role": "member"})
        assert r.status_code == 201
        assert r.json()["user"]["status"] == "invited"
        url = r.json()["invite_url"]
    assert url.startswith("http://app.localtest.me/invite/")
    token = url.rsplit("/", 1)[1]
    async with db() as s:   # only a hash is stored
        stored = await s.scalar(select(User.invite_token_hash).where(User.email == "new@example.com"))
    assert stored and token != stored

    async with api_client(gateway) as c:
        # Invited users can't log in before accepting.
        assert (await c.post("/api/auth/login", json={"email": "new@example.com", "password": PW})).status_code == 401
        assert (await c.get(f"/api/invites/{token}")).json() == {"email": "new@example.com"}
        assert (await c.post(f"/api/invites/{token}/accept", json={"password": "short"})).status_code == 422
        r = await c.post(f"/api/invites/{token}/accept", json={"password": PW})
        assert r.status_code == 200 and r.json()["email"] == "new@example.com"
        assert (await c.get("/api/auth/me")).json()["role"] == "member"   # logged in by accepting
        # Single use.
        assert (await c.get(f"/api/invites/{token}")).status_code == 404
    async with api_client(gateway, "new@example.com") as c:
        assert (await c.get("/api/auth/me")).status_code == 200


async def test_expired_invite_is_rejected(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    async with api_client(gateway, "admin@example.com") as admin:
        url = (await admin.post("/api/users", json={"email": "late@example.com"})).json()["invite_url"]
    async with db() as s:
        await s.execute(update(User).where(User.email == "late@example.com")
                        .values(invite_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await s.commit()
    async with api_client(gateway) as c:
        token = url.rsplit("/", 1)[1]
        assert (await c.post(f"/api/invites/{token}/accept", json={"password": PW})).status_code == 404


async def test_reissued_invite_replaces_the_old_link(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    async with api_client(gateway, "admin@example.com") as admin:
        r = await admin.post("/api/users", json={"email": "slow@example.com"})
        old, uid = r.json()["invite_url"].rsplit("/", 1)[1], r.json()["user"]["id"]
        r = await admin.post(f"/api/users/{uid}/invite")
        assert r.status_code == 200
        new = r.json()["invite_url"].rsplit("/", 1)[1]
        # Only pending invites can be re-issued.
        assert (await admin.post("/api/users/1/invite")).status_code == 404
    assert new != old
    async with api_client(gateway) as c:
        assert (await c.get(f"/api/invites/{old}")).status_code == 404        # old link is dead
        assert (await c.post(f"/api/invites/{new}/accept", json={"password": PW})).status_code == 200


# --- roles, enforced by the API ------------------------------------------------------

ADMIN_ONLY = [
    ("GET", "/api/users", None),
    ("POST", "/api/users", {"email": "x@example.com"}),
    ("PATCH", "/api/users/1", {"role": "member"}),
    ("POST", "/api/users/1/invite", None),
    ("GET", "/api/users/1/keys", None),
    ("POST", "/api/users/1/keys", {"name": "x"}),
    ("POST", "/api/servers", {"slug": "evil", "name": "Evil", "url": "https://mcp.example.com/mcp"}),
    ("PATCH", "/api/servers/alpha", {"enabled": False}),
    ("DELETE", "/api/servers/alpha", None),
    ("POST", "/api/servers/alpha/test", None),
]


async def test_member_is_refused_every_admin_endpoint(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    await create_user(db, "member@example.com")
    async with api_client(gateway, "member@example.com") as c:
        for method, path, body in ADMIN_ONLY:
            r = await c.request(method, path, json=body)
            assert r.status_code == 403, (method, path, r.status_code, r.text)
        # Read-only access a member does have:
        assert (await c.get("/api/servers")).status_code == 200
    async with api_client(gateway) as anon:
        for method, path, body in ADMIN_ONLY + [("GET", "/api/servers", None), ("GET", "/api/audit", None),
                                                ("GET", "/api/me/keys", None)]:
            assert (await anon.request(method, path, json=body)).status_code == 401, (method, path)
    async with db() as s:        # nothing was changed by the refused calls
        assert (await s.scalar(select(User.role).where(User.email == "admin@example.com"))) == "admin"


async def test_member_cannot_touch_other_users_keys_or_audit(gateway, db):
    a_id, a_key, _ = await create_user_key(db, "a@example.com")
    await create_user(db, "b@example.com")
    async with mcp_client(gateway, a_key) as client:          # A makes a call -> an audit row for A
        await client.call_tool("alpha__echo", {"text": "x"})
    async with api_client(gateway, "b@example.com") as b:
        # A's key id is 1. To B it looks like it doesn't exist.
        assert (await b.delete("/api/keys/1")).status_code == 404
        # Asking for A's audit rows still only returns B's (none).
        r = await b.get(f"/api/audit?user_id={a_id}")
        assert r.status_code == 200 and r.json()["total"] == 0
    assert "alpha__echo" in await tool_names(gateway, a_key)    # A's key still works


async def test_last_admin_cannot_be_demoted_or_disabled(gateway, db):
    admin_id = await create_user(db, "admin@example.com", "admin")
    async with api_client(gateway, "admin@example.com") as c:
        assert (await c.patch(f"/api/users/{admin_id}", json={"role": "member"})).status_code == 409
        assert (await c.patch(f"/api/users/{admin_id}", json={"status": "disabled"})).status_code == 409
        second = await create_user(db, "admin2@example.com", "admin")
        assert (await c.patch(f"/api/users/{second}", json={"role": "member"})).status_code == 200


async def test_disabling_a_user_blocks_their_api_keys(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    uid, key, _ = await create_user_key(db, "m@example.com")
    assert "alpha__echo" in await tool_names(gateway, key)
    async with api_client(gateway, "admin@example.com") as c:
        assert (await c.patch(f"/api/users/{uid}", json={"status": "disabled"})).json()["status"] == "disabled"
    async with api_client(gateway) as raw:
        r = await raw.post("/mcp", headers={"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream"},
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 401


# --- CSRF ---------------------------------------------------------------------------

async def test_cross_origin_writes_are_refused(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    async with api_client(gateway, "admin@example.com") as c:
        evil = await c.post("/api/me/keys", json={"name": "x"}, headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403
        assert (await c.post("/api/me/keys", json={"name": "x"}, headers=ORIGIN)).status_code == 201
        assert (await c.get("/api/me/keys", headers={"Origin": "https://evil.example"})).status_code == 200  # reads ok


# --- keys --------------------------------------------------------------------------

async def test_key_shown_once_and_revocation_applies_to_mcp(gateway, db):
    await create_user(db, "m@example.com")
    async with api_client(gateway, "m@example.com") as c:
        created = (await c.post("/api/me/keys", json={"name": "laptop"})).json()
        key, item = created["key"], created["item"]
        assert key.startswith(item["prefix"]) and key.startswith("urk_")
        listed = (await c.get("/api/me/keys")).json()
        assert [k["prefix"] for k in listed] == [item["prefix"]]
        assert key not in str(listed)                       # never shown again
        assert "alpha__echo" in await tool_names(gateway, key)
        assert (await c.delete(f"/api/keys/{item['id']}")).status_code == 204
        assert (await c.get("/api/me/keys")).json()[0]["revoked_at"] is not None
    async with api_client(gateway) as raw:
        r = await raw.post("/mcp", headers={"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream"},
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 401


async def test_admin_issues_key_for_member(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    member_id = await create_user(db, "m@example.com")
    async with api_client(gateway, "admin@example.com") as admin:
        r = await admin.post(f"/api/users/{member_id}/keys", json={"name": ""})
        assert r.status_code == 201
        key, item = r.json()["key"], r.json()["item"]
        assert item["user_id"] == member_id and item["name"] == "issued by admin@example.com"
        assert (await admin.post("/api/users/999/keys", json={})).status_code == 404
        invited = (await admin.post("/api/users", json={"email": "inv@example.com"})).json()["user"]["id"]
        assert (await admin.post(f"/api/users/{invited}/keys", json={})).status_code == 400   # wouldn't work yet
    # The key belongs to the member: it works for MCP and calls are audited under the member.
    async with mcp_client(gateway, key) as client:
        assert not (await client.call_tool("alpha__echo", {"text": "x"})).is_error
    async with api_client(gateway, "m@example.com") as m:
        mine = (await m.get("/api/me/keys")).json()
        assert [k["prefix"] for k in mine] == [item["prefix"]] and key not in str(mine)
        audit = (await m.get("/api/audit")).json()
        assert audit["total"] == 1 and audit["items"][0]["user_id"] == member_id


# --- servers -------------------------------------------------------------------------

async def test_servers_list_has_status_tools_and_never_credentials(gateway, db):
    await create_user(db, "m@example.com")
    async with api_client(gateway, "m@example.com") as c:
        r = await c.get("/api/servers")
    servers = {s["slug"]: s for s in r.json()}
    assert list(servers) == ["alpha", "beta", "slow", "legacy", "dead"]          # registration order
    assert servers["beta"]["credential_set"] is True and servers["alpha"]["credential_set"] is False
    assert servers["alpha"]["status"] == "connected" and {t["name"] for t in servers["alpha"]["tools"]} == {"alpha__echo", "alpha__get_info"}
    assert servers["dead"]["status"] == "unreachable" and servers["dead"]["last_error"] and servers["dead"]["tools"] == []
    assert all(secret not in r.text for secret in SECRETS)


async def test_enable_disable_edit_remove_change_tools_immediately(quiet_gateway, db, upstreams):
    # The health loop is 60s here: every change below must come from the API's
    # immediate refresh, checked right after the response (no waiting).
    gw = quiet_gateway
    await create_user(db, "admin@example.com", "admin")
    _, key, _ = await create_user_key(db, "m@example.com")
    async with api_client(gw, "admin@example.com") as c:
        assert (await c.patch("/api/servers/alpha", json={"enabled": False})).json()["status"] == "disabled"
        assert "alpha__echo" not in await tool_names(gw, key)
        assert (await c.patch("/api/servers/alpha", json={"enabled": True})).json()["status"] == "connected"
        assert "alpha__echo" in await tool_names(gw, key)

        # Edit: point "dead" at a live upstream -> its tools appear at once.
        r = await c.patch("/api/servers/dead", json={"url": f"{upstreams['alpha'].base}/mcp"})
        assert r.json()["status"] == "connected" and "dead__echo" in await tool_names(gw, key)

        # Add a new server -> tools appear; remove it -> gone.
        r = await c.post("/api/servers", json={"slug": "beta2", "name": "Beta again", "url": f"{upstreams['beta'].base}/mcp",
                                                "auth_type": "bearer", "credential": BETA_TOKEN})
        assert r.status_code == 201 and r.json()["status"] == "connected" and BETA_TOKEN not in r.text
        assert "beta2__echo" in await tool_names(gw, key)
        assert (await c.delete("/api/servers/beta2")).status_code == 204
        assert "beta2__echo" not in await tool_names(gw, key)


async def test_credential_edit_semantics(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    async with api_client(gateway, "admin@example.com") as c:
        r = await c.patch("/api/servers/beta", json={"name": "Beta renamed"})        # credential omitted: kept
        assert r.json()["credential_set"] is True and r.json()["status"] == "connected"
        r = await c.patch("/api/servers/beta", json={"credential": "wrong-token"})  # replaced
        assert r.json()["status"] == "unreachable" and "wrong-token" not in r.text
        r = await c.patch("/api/servers/beta", json={"credential": None})           # cleared -> invalid for bearer
        assert r.status_code == 422
        r = await c.patch("/api/servers/beta", json={"credential": BETA_TOKEN})
        assert r.json()["status"] == "connected"


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data/", "http://localhost:8000/mcp",
                                 "http://10.0.0.5/mcp", "ftp://mcp.example.com/"])
async def test_create_server_refuses_unsafe_urls(gateway, db, url):
    # This gateway uses the test's permissive URL check, so swap in the real guard for this request.
    from functools import partial
    from gateway.ssrf import check_upstream_url
    await create_user(db, "admin@example.com", "admin")
    async with api_client(gateway, "admin@example.com") as c:
        app_state = gateway.app.state
        original = app_state.check_url
        app_state.check_url = partial(check_upstream_url, allowlist=frozenset())
        try:
            r = await c.post("/api/servers", json={"slug": "bad", "name": "Bad", "url": url})
        finally:
            app_state.check_url = original
    assert r.status_code == 400 and r.json()["detail"].startswith("URL refused")


async def test_server_validation(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    async with api_client(gateway, "admin@example.com") as c:
        base = {"name": "X", "url": "https://mcp.example.com/mcp"}
        assert (await c.post("/api/servers", json={**base, "slug": "Bad_Slug"})).status_code == 422
        assert (await c.post("/api/servers", json={**base, "slug": "alpha"})).status_code == 409
        assert (await c.post("/api/servers", json={**base, "slug": "h", "auth_type": "header", "credential": "x"})).status_code == 422
        assert (await c.post("/api/servers", json={**base, "slug": "h", "auth_type": "header", "header_name": "Host",
                                                   "credential": "x"})).status_code == 422
        assert (await c.post("/api/servers", json={**base, "slug": "b", "auth_type": "bearer"})).status_code == 422
        # Slug is not editable (it is the tool prefix and appears in audit rows).
        r = await c.patch("/api/servers/alpha", json={"slug": "renamed"})
        assert r.status_code == 200 and r.json()["slug"] == "alpha"


async def test_test_connection(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    async with api_client(gateway, "admin@example.com") as c:
        ok = (await c.post("/api/servers/beta/test")).json()
        dead = (await c.post("/api/servers/dead/test")).json()
    assert ok == {"slug": "beta", "status": "connected", "last_error": None, "tool_count": 3}
    assert dead["status"] == "unreachable" and dead["last_error"] and dead["tool_count"] == 0


# --- audit ------------------------------------------------------------------------

async def test_audit_filters_and_pagination(gateway, db):
    await create_user(db, "admin@example.com", "admin")
    a_id, a_key, _ = await create_user_key(db, "a@example.com")
    async with mcp_client(gateway, a_key) as client:
        for i in range(3):
            await client.call_tool("alpha__echo", {"text": str(i)})
        await client.call_tool("beta__fail", {})
    async with api_client(gateway, "admin@example.com") as c:
        page = (await c.get("/api/audit?limit=2")).json()
        assert page["total"] == 4 and len(page["items"]) == 2 and page["items"][0]["tool"] == "beta__fail"  # newest first
        assert (await c.get("/api/audit?limit=2&offset=2")).json()["items"][-1]["tool"] == "alpha__echo"
        assert (await c.get("/api/audit?server=beta")).json()["total"] == 1
        assert (await c.get("/api/audit?outcome=error")).json()["items"][0]["user_email"] == "a@example.com"
        assert (await c.get(f"/api/audit?user_id={a_id}&channel=api")).json()["total"] == 4
        assert (await c.get("/api/audit?channel=chat")).json()["total"] == 0
        assert (await c.get("/api/audit?limit=500")).status_code == 422
