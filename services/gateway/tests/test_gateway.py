"""End-to-end: real gateway over HTTP, real MCP client, fake upstreams, real Postgres."""

import asyncio
import json
import time
from datetime import datetime, timezone

import httpx2
import mcp.types as t
import pytest
from sqlalchemy import select, update

from conftest import SECRETS, SLOW_KEY, BETA_TOKEN, ThreadServer, create_user_key, fake_upstream, mcp_client, wait_until
from gateway.models import ApiKey, AuditLog, UpstreamServer

pytestmark = pytest.mark.anyio


def text_of(result: t.CallToolResult) -> str:
    return result.content[0].text


async def tool_names(client) -> set[str]:
    return {tool.name for tool in (await client.list_tools()).tools}


async def audit_rows(sm) -> list[AuditLog]:
    async with sm() as s:
        return list((await s.scalars(select(AuditLog).order_by(AuditLog.id))).all())


async def raw_call(gateway, key: str | None, tool: str) -> httpx2.Response:
    """A tools/call as raw HTTP, to see the status code and body exactly."""
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": {"text": "x"}}}
    async with httpx2.AsyncClient() as http:
        return await http.post(f"{gateway.base}/mcp", json=body, headers=headers)


# --- prefixing, routing, collisions -------------------------------------------

async def test_tools_from_all_reachable_upstreams_are_prefixed(gateway, db):
    _, key, _ = await create_user_key(db, "a@example.com")
    async with mcp_client(gateway, key) as client:
        assert await tool_names(client) == {
            "alpha__get_info", "alpha__echo",
            "beta__get_info", "beta__echo", "beta__fail",
            "slow__get_info", "slow__echo", "slow__nap",
        }   # nothing from "dead"


async def test_routing_and_name_collisions(gateway, db):
    # All three upstreams have a tool literally called get_info / echo.
    _, key, _ = await create_user_key(db, "a@example.com")
    async with mcp_client(gateway, key) as client:
        assert text_of(await client.call_tool("alpha__get_info", {})) == "alpha"
        assert text_of(await client.call_tool("beta__get_info", {})) == "beta"
        assert text_of(await client.call_tool("slow__get_info", {})) == "slow"
        assert text_of(await client.call_tool("beta__echo", {"text": "hi"})) == "beta:hi"


async def test_unknown_tool_and_server(gateway, db):
    _, key, _ = await create_user_key(db, "a@example.com")
    async with mcp_client(gateway, key) as client:
        r = await client.call_tool("echo", {"text": "x"})
        assert r.is_error and "expected <server>__<tool>" in text_of(r)
        r = await client.call_tool("nope__echo", {"text": "x"})
        assert r.is_error and "unknown server 'nope'" in text_of(r)


# --- credentials ----------------------------------------------------------------

async def test_upstream_credentials_injected_and_client_key_never_forwarded(gateway, db, upstreams):
    _, key, _ = await create_user_key(db, "a@example.com")
    async with mcp_client(gateway, key) as client:
        for slug in ("alpha", "beta", "slow"):
            assert not (await client.call_tool(f"{slug}__echo", {"text": "x"})).is_error
    beta_seen = upstreams["beta"].app.seen
    slow_seen = upstreams["slow"].app.seen
    alpha_seen = upstreams["alpha"].app.seen
    assert beta_seen and all(h.get(b"authorization") == f"Bearer {BETA_TOKEN}".encode() for h in beta_seen)
    assert slow_seen and all(h.get(b"x-api-key") == SLOW_KEY.encode() for h in slow_seen)
    assert alpha_seen and all(b"authorization" not in h for h in alpha_seen)
    every_header_value = b" ".join(v for h in alpha_seen + beta_seen + slow_seen for v in h.values())
    assert key.encode() not in every_header_value and b"urk_" not in every_header_value


async def test_credentials_never_appear_in_responses(gateway, db):
    _, key, _ = await create_user_key(db, "a@example.com")
    seen: list[str] = []
    async with mcp_client(gateway, key) as client:
        seen.append((await client.list_tools()).model_dump_json())
        for name, args in [("beta__echo", {"text": "x"}), ("beta__fail", {}), ("slow__nap", {"seconds": 3}),
                           ("dead__echo", {"text": "x"}), ("bogus", {})]:
            seen.append((await client.call_tool(name, args)).model_dump_json())
    seen.append((await raw_call(gateway, None, "beta__echo")).text)
    seen.append((await raw_call(gateway, "urk_wrong", "beta__echo")).text)
    async with db() as s:
        stored = (await s.scalars(select(UpstreamServer.credential_encrypted))).all()
        seen.extend(str(row.status) + str(row.last_error) for row in (await s.scalars(select(UpstreamServer))).all())
    for secret in SECRETS:
        assert all(secret not in blob for blob in seen), secret
        assert all(secret not in (c or "") for c in stored)    # encrypted at rest


# --- failure handling --------------------------------------------------------------

async def test_slow_upstream_times_out_without_affecting_others(gateway, db):
    user_id, key, _ = await create_user_key(db, "a@example.com")
    async with mcp_client(gateway, key) as client:
        t0 = time.monotonic()
        r = await client.call_tool("slow__nap", {"seconds": 3})
        assert r.is_error and text_of(r) == "upstream 'slow' timed out after 1s"
        assert time.monotonic() - t0 < 2.5
        assert text_of(await client.call_tool("alpha__echo", {"text": "still fine"})) == "alpha:still fine"
    timeouts = [a for a in await audit_rows(db) if a.outcome == "timeout"]
    assert [(a.user_id, a.server_slug, a.tool) for a in timeouts] == [(user_id, "slow", "slow__nap")]


async def test_dead_upstream_does_not_break_listing(gateway, db):
    _, key, _ = await create_user_key(db, "a@example.com")
    async with db() as s:
        dead = await s.scalar(select(UpstreamServer).where(UpstreamServer.slug == "dead"))
    assert dead.status == "unreachable" and dead.last_error
    async with mcp_client(gateway, key) as client:
        assert "alpha__echo" in await tool_names(client)
        r = await client.call_tool("dead__echo", {"text": "x"})
        assert r.is_error and "upstream 'dead' is unreachable" in text_of(r)


async def test_upstream_that_dies_drops_out_and_comes_back(gateway, db):
    _, key, _ = await create_user_key(db, "a@example.com")
    gamma = ThreadServer(fake_upstream("gamma")).start()
    async with db() as s:
        s.add(UpstreamServer(slug="gamma", name="Gamma", url=f"{gamma.base}/mcp", auth_type="none",
                             enabled=True, status="unknown"))
        await s.commit()
    async with mcp_client(gateway, key) as client:
        await wait_until(lambda: _has(client, "gamma__echo"))
        gamma.stop()                                                     # kill the upstream
        await wait_until(lambda: _lacks(client, "gamma__echo"))
        assert "alpha__echo" in await tool_names(client)                 # others unaffected

        async def gamma_unreachable():
            # The in-memory cache (what tools/list reads) updates a moment before
            # the status is persisted, so wait for the DB too.
            async with db() as s:
                row = await s.scalar(select(UpstreamServer).where(UpstreamServer.slug == "gamma"))
            return row.status == "unreachable" and row.last_error
        await wait_until(gamma_unreachable)
        gamma = ThreadServer(fake_upstream("gamma"), port=gamma.port).start()   # restart on same port
        await wait_until(lambda: _has(client, "gamma__echo"))
        gamma.stop()


async def _has(client, name):
    return name in await tool_names(client)


async def _lacks(client, name):
    return name not in await tool_names(client)


async def test_disabled_upstream_disappears_and_calls_are_denied(gateway, db):
    user_id, key, _ = await create_user_key(db, "a@example.com")
    async with db() as s:
        await s.execute(update(UpstreamServer).where(UpstreamServer.slug == "beta").values(enabled=False))
        await s.commit()
    async with mcp_client(gateway, key) as client:
        await wait_until(lambda: _lacks(client, "beta__echo"))
        r = await client.call_tool("beta__echo", {"text": "x"})
        assert r.is_error and text_of(r) == "upstream 'beta' is disabled"
    denied = [a for a in await audit_rows(db) if a.outcome == "denied"]
    assert [(a.user_id, a.server_slug) for a in denied] == [(user_id, "beta")]
    async with db() as s:
        assert await s.scalar(select(UpstreamServer.status).where(UpstreamServer.slug == "beta")) == "disabled"


async def test_tools_list_changed_is_pushed_to_listening_clients(gateway, db):
    _, key, _ = await create_user_key(db, "a@example.com")
    async with mcp_client(gateway, key) as client:
        assert client.protocol_version == "2026-07-28"
        async with client.listen(tools_list_changed=True) as events:
            async with db() as s:
                await s.execute(update(UpstreamServer).where(UpstreamServer.slug == "alpha").values(enabled=False))
                await s.commit()
            async with asyncio.timeout(5):
                async for event in events:
                    assert type(event).__name__ == "ToolsListChanged"
                    break
        assert "alpha__echo" not in await tool_names(client)


# --- authentication + audit ----------------------------------------------------------

async def test_missing_or_unknown_key_is_rejected(gateway, db):
    for key in (None, "urk_not_a_real_key", "not-even-a-key"):
        response = await raw_call(gateway, key, "alpha__echo")
        assert response.status_code == 401 and response.headers["www-authenticate"].startswith("Bearer")
    assert await audit_rows(db) == []          # nobody to attribute these to


async def test_revoked_key_is_rejected_on_the_very_next_call(gateway, db):
    user_id, key, prefix = await create_user_key(db, "b@example.com")
    async with mcp_client(gateway, key) as client:
        assert not (await client.call_tool("alpha__echo", {"text": "before"})).is_error
    async with db() as s:
        await s.execute(update(ApiKey).where(ApiKey.prefix == prefix).values(revoked_at=datetime.now(timezone.utc)))
        await s.commit()
    response = await raw_call(gateway, key, "alpha__echo")
    assert response.status_code == 401 and json.loads(response.text)["message"] == "API key revoked"
    rows = await audit_rows(db)
    assert [(a.user_id, a.tool, a.outcome) for a in rows] == [
        (user_id, "alpha__echo", "success"),
        (user_id, "alpha__echo", "denied"),
    ]


async def test_every_call_is_audited_with_user_and_channel(gateway, db):
    a_id, a_key, _ = await create_user_key(db, "a@example.com")
    b_id, b_key, _ = await create_user_key(db, "b@example.com")
    async with mcp_client(gateway, a_key) as client:
        await client.call_tool("alpha__echo", {"text": "x"})
    async with mcp_client(gateway, b_key) as client:
        await client.call_tool("beta__fail", {})
    rows = await audit_rows(db)
    assert [(a.user_id, a.channel, a.server_slug, a.tool, a.outcome) for a in rows] == [
        (a_id, "api", "alpha", "alpha__echo", "success"),
        (b_id, "api", "beta", "beta__fail", "error"),
    ]
    assert "beta failed on purpose" in rows[1].error_message
    assert all(a.duration_ms >= 0 and a.started_at for a in rows)
    async with db() as s:
        assert all(k.last_used_at is not None for k in (await s.scalars(select(ApiKey))).all())
