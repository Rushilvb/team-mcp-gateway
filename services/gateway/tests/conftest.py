"""Test fixtures.

- A real Postgres database `gateway_test`, schema created by the real Alembic
  migration (`make test` provides the server via TEST_DATABASE_URL).
- Fake upstream MCP servers (small MCPServer apps) on localhost ports:
    alpha  no auth                  echo, get_info
    beta   Authorization: Bearer    echo, get_info, fail
    slow   X-API-Key                nap
    dead   (nothing listening)
  Each records the request headers it receives.
- The real gateway app, served by uvicorn in a background thread.
"""

import asyncio
import os
import socket
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx2
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import text

from gateway.app import create_app
from gateway.config import Settings
from gateway.db import make_engine, make_sessionmaker
from gateway.models import ApiKey, User
from gateway.security import new_api_key

BETA_TOKEN = "beta-secret-token-0123456789"
SLOW_KEY = "slow-secret-key-0123456789"
SECRETS = (BETA_TOKEN, SLOW_KEY)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- database ---------------------------------------------------------------

def _urls() -> tuple[str, str]:
    """(admin URL for asyncpg, SQLAlchemy URL of the gateway_test database)."""
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.skip("TEST_DATABASE_URL not set (run via `make test`)")
    parts = urlsplit(base)
    test_db = urlunsplit(parts._replace(scheme="postgresql+asyncpg", path="/gateway_test"))
    return base, test_db


def alembic_config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def database_url() -> str:
    admin_url, test_url = _urls()

    async def recreate():
        deadline = time.monotonic() + 60
        while True:   # the throwaway Postgres container may still be starting
            try:
                conn = await asyncpg.connect(admin_url)
                break
            except (OSError, asyncpg.PostgresError):
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(1)
        await conn.execute("DROP DATABASE IF EXISTS gateway_test WITH (FORCE)")
        await conn.execute("CREATE DATABASE gateway_test")
        await conn.close()

    asyncio.run(recreate())
    command.upgrade(alembic_config(test_url), "head")   # the real migration
    return test_url


@pytest.fixture
async def db(database_url):
    engine = make_engine(database_url)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE audit_log, api_keys, upstream_servers, users RESTART IDENTITY CASCADE"))
    yield make_sessionmaker(engine)
    await engine.dispose()


async def create_user_key(sm, email: str, role: str = "member") -> tuple[int, str, str]:
    """Insert a user + API key directly; return (user_id, key, prefix)."""
    async with sm() as s:
        user = User(email=email, role=role, status="active")
        s.add(user)
        await s.flush()
        key, prefix, key_hash = new_api_key()
        s.add(ApiKey(user_id=user.id, name="test", prefix=prefix, key_hash=key_hash))
        await s.commit()
        return user.id, key, prefix


# --- servers in background threads -------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ThreadServer:
    def __init__(self, app, port: int | None = None):
        self.app = app
        self.port = port or free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> "ThreadServer":
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("server failed to start")
            time.sleep(0.05)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class RecordingAuth:
    """Fake upstream auth: records every request's headers, enforces one header."""

    def __init__(self, app, required: tuple[bytes, bytes] | None):
        self.app, self.required, self.seen = app, required, []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            self.seen.append(headers)
            if self.required and headers.get(self.required[0]) != self.required[1]:
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def fake_upstream(name: str, required: tuple[bytes, bytes] | None = None, *, slow: bool = False, fail: bool = False):
    server = MCPServer(f"fake-{name}")

    @server.tool()
    async def get_info() -> str:
        """Which fake upstream this is."""
        return name

    @server.tool()
    async def echo(text: str) -> str:
        """Echo text back, prefixed with the server name."""
        return f"{name}:{text}"

    if fail:
        @server.tool()
        async def fail() -> str:
            """Always fails."""
            raise ToolError(f"{name} failed on purpose")

    if slow:
        @server.tool()
        async def nap(seconds: float) -> str:
            """Sleep, then answer."""
            await asyncio.sleep(seconds)
            return "rested"

    app = server.streamable_http_app(stateless_http=True, json_response=True,
                                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return RecordingAuth(app, required)


@pytest.fixture(scope="session")
def upstreams():
    servers = {
        "alpha": ThreadServer(fake_upstream("alpha")).start(),
        "beta": ThreadServer(fake_upstream("beta", (b"authorization", f"Bearer {BETA_TOKEN}".encode()), fail=True)).start(),
        "slow": ThreadServer(fake_upstream("slow", (b"x-api-key", SLOW_KEY.encode()), slow=True)).start(),
    }
    yield servers
    for s in servers.values():
        s.stop()


async def allow_any_url(url: str) -> None:
    """Tests run fake upstreams on localhost, which the real SSRF guard refuses."""


@pytest.fixture
def gateway(db, database_url, upstreams, monkeypatch):
    monkeypatch.setenv("CRED_BETA", BETA_TOKEN)
    monkeypatch.setenv("CRED_SLOW", SLOW_KEY)
    settings = Settings(
        database_url=database_url,
        fernet_key=Fernet.generate_key().decode(),
        call_timeout_seconds=1,
        health_interval_seconds=0.3,
        health_timeout_seconds=1,
        mcp_allowed_hosts=["127.0.0.1:*", "localhost:*"],
        bootstrap_upstreams=[
            {"slug": "alpha", "name": "Alpha", "url": f"{upstreams['alpha'].base}/mcp", "auth_type": "none"},
            {"slug": "beta", "name": "Beta", "url": f"{upstreams['beta'].base}/mcp", "auth_type": "bearer", "credential_env": "CRED_BETA"},
            {"slug": "slow", "name": "Slow", "url": f"{upstreams['slow'].base}/mcp", "auth_type": "header",
             "header_name": "X-API-Key", "credential_env": "CRED_SLOW"},
            {"slug": "dead", "name": "Dead", "url": f"http://127.0.0.1:{free_port()}/mcp", "auth_type": "none"},
        ],
    )
    for s in upstreams.values():
        s.app.seen.clear()
    server = ThreadServer(create_app(settings, check_url=allow_any_url)).start()
    yield server
    server.stop()


@asynccontextmanager
async def mcp_client(gateway: ThreadServer, key: str | None):
    """An MCP client session to the gateway, authenticated with `key`."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx2.AsyncClient(headers=headers, timeout=10) as http:   # Client doesn't close it for us
        async with Client(streamable_http_client(f"{gateway.base}/mcp", http_client=http)) as client:
            yield client


async def wait_until(predicate, timeout: float = 5.0, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    while True:
        value = await predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(interval)
