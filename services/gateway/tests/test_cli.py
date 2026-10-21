"""The admin CLI's destructive command: reset-demo keeps exactly the bootstrap admin."""

import argparse

import pytest
from sqlalchemy import func, select

from conftest import create_user, create_user_key
from gateway import cli
from gateway.audit import Timer
from gateway.models import ApiKey, AuditLog, UpstreamServer, User, UserSession

pytestmark = pytest.mark.anyio


async def count(sm, model) -> int:
    async with sm() as s:
        return await s.scalar(select(func.count()).select_from(model))


async def test_reset_demo_keeps_only_the_bootstrap_admin(db, database_url, monkeypatch):
    admin_id = await create_user(db, "admin@example.com", "admin")
    await create_user_key(db, "m1@example.com")
    await create_user_key(db, "m2@example.com")
    async with db() as s:
        s.add(AuditLog(user_id=admin_id, channel="api", server_slug="woo", tool="woo__get_info",
                       started_at=Timer().started_at, duration_ms=1, outcome="success"))
        s.add(UpstreamServer(slug="fx", name="FX", url="http://fx/mcp", auth_type="none", enabled=False, status="disabled"))
        await s.commit()

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("GATEWAY_ADMIN_EMAIL", "admin@example.com")
    await cli.main(argparse.Namespace(cmd="reset-demo"))

    async with db() as s:
        assert (await s.scalars(select(User.email))).all() == ["admin@example.com"]
        assert (await s.scalar(select(UpstreamServer.enabled).where(UpstreamServer.slug == "fx"))) is True
    assert await count(db, ApiKey) == 0
    assert await count(db, AuditLog) == 0
    assert await count(db, UserSession) == 0


async def test_reset_demo_refuses_without_admin_email(db, database_url, monkeypatch):
    await create_user(db, "someone@example.com")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("GATEWAY_ADMIN_EMAIL", raising=False)
    with pytest.raises(SystemExit):
        await cli.main(argparse.Namespace(cmd="reset-demo"))
    assert await count(db, User) == 1          # nothing deleted
