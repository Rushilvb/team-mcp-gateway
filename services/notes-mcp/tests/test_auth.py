"""The credential check, tested around a tiny stand-in app."""

import httpx
import pytest

from notes_mcp.auth import RequireHeader

pytestmark = pytest.mark.anyio


async def inner_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"inner"})


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_api_key_header():
    app = RequireHeader(inner_app, "x-api-key", "s3cret")
    async with client(app) as c:
        assert (await c.post("/mcp")).status_code == 401
        assert (await c.post("/mcp", headers={"X-API-Key": "wrong"})).status_code == 401
        assert (await c.post("/mcp", headers={"X-API-Key": "s3cret"})).text == "inner"
        assert (await c.get("/healthz")).text == "inner"   # open path: probes need no secret
        denied = await c.post("/mcp")
        assert denied.json() == {"error": "unauthorized", "message": "missing or invalid x-api-key header"}
        assert "www-authenticate" not in denied.headers


async def test_bearer_scheme():
    app = RequireHeader(inner_app, "authorization", "tok", scheme="Bearer")
    async with client(app) as c:
        assert (await c.post("/mcp", headers={"Authorization": "Bearer tok"})).status_code == 200
        assert (await c.post("/mcp", headers={"Authorization": "bearer tok"})).status_code == 200   # scheme is case-insensitive
        assert (await c.post("/mcp", headers={"Authorization": "tok"})).status_code == 401
        assert (await c.post("/mcp", headers={"Authorization": "Basic tok"})).status_code == 401
        denied = await c.post("/mcp", headers={"Authorization": "Bearer nope"})
        assert denied.status_code == 401 and denied.headers["www-authenticate"] == "Bearer"


def test_empty_secret_is_refused_at_startup():
    with pytest.raises(ValueError):
        RequireHeader(inner_app, "x-api-key", "")
