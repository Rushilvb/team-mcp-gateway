"""Upstream MCP servers: connections, health checks, tool cache, routing.

- UpstreamConnection: one per upstream. Holds a long-lived HTTP client
  (connection pool) that carries the upstream's OWN credential. Created lazily,
  thrown away after a transport failure and rebuilt on next use. Each MCP call
  is a stateless request over that pool.
- Registry: loads upstreams from the DB, health-checks them all concurrently
  every few seconds, caches their tools, and routes prefixed tool calls.
  tools/list is answered from the cache, so a dead upstream can never block it.
"""

import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import anyio
import httpx2
import mcp.types as t
from mcp import Client
from mcp.client.client import MODERN_PROTOCOL_VERSIONS
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from . import naming
from .models import UpstreamServer
from .security import CredentialBox

log = logging.getLogger(__name__)

UrlCheck = Callable[[str], Awaitable[None]]


class RoutingError(Exception):
    """A tool call the gateway refuses or cannot complete. `outcome` is the audit outcome."""

    def __init__(self, outcome: str, message: str, slug: str | None = None):
        super().__init__(message)
        self.outcome = outcome
        self.message = message
        self.slug = slug


def describe(exc: BaseException) -> str:
    """Short, single-line error description (unwraps anyio's ExceptionGroups)."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    text = f"{exc.__class__.__name__}: {exc}" if str(exc) else exc.__class__.__name__
    return " ".join(text.split())[:300]


@dataclass(frozen=True)
class UpstreamConfig:
    slug: str
    name: str
    url: str
    auth_type: str                  # none | bearer | header
    header_name: str | None
    credential: str | None          # decrypted; lives only in memory
    enabled: bool


class UpstreamConnection:
    def __init__(self, config: UpstreamConfig, check_url: UrlCheck):
        self.config = config
        self._check_url = check_url
        self._http: httpx2.AsyncClient | None = None
        # "auto" until the first successful contact; then the negotiated
        # protocol version, so later calls skip the discovery round trip.
        self._mode = "auto"

    def _auth_headers(self) -> dict[str, str]:
        c = self.config
        if c.auth_type == "bearer":
            return {"Authorization": f"Bearer {c.credential}"}
        if c.auth_type == "header":
            return {c.header_name: c.credential}
        return {}

    async def _pool(self) -> httpx2.AsyncClient:
        if self._http is None:
            # Re-check on every (re)connect: catches DNS answers that changed.
            await self._check_url(self.config.url)
            self._http = httpx2.AsyncClient(headers=self._auth_headers(), follow_redirects=False)
        return self._http

    def scrub(self, message: str) -> str:
        """Defence in depth: never let the upstream credential leak into an error."""
        cred = self.config.credential
        return message.replace(cred, "***") if cred else message

    @asynccontextmanager
    async def _client(self):
        http = await self._pool()
        try:
            async with Client(streamable_http_client(self.config.url, http_client=http), mode=self._mode) as client:
                if self._mode == "auto":
                    v = client.protocol_version
                    self._mode = v if v in MODERN_PROTOCOL_VERSIONS else "legacy"
                yield client
        except MCPError:
            raise                       # a protocol-level answer: the connection itself is fine
        except BaseException:
            await self.reset()          # transport trouble (or cancellation): rebuild next time
            raise

    async def list_tools(self) -> list[t.Tool]:
        tools: list[t.Tool] = []
        async with self._client() as client:
            cursor = None
            while True:
                page = await client.list_tools(cursor=cursor) if cursor else await client.list_tools()
                tools.extend(page.tools)
                cursor = page.next_cursor
                if not cursor:
                    return tools

    async def call_tool(self, name: str, arguments: dict) -> t.CallToolResult:
        async with self._client() as client:
            return await client.call_tool(name, arguments)

    async def reset(self) -> None:
        http, self._http, self._mode = self._http, None, "auto"
        if http is not None:
            with anyio.CancelScope(shield=True):
                await http.aclose()


@dataclass
class UpstreamState:
    conn: UpstreamConnection
    tools: list[t.Tool] = field(default_factory=list)
    status: str = "unknown"
    last_error: str | None = None

    @property
    def config(self) -> UpstreamConfig:
        return self.conn.config


class Registry:
    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        box: CredentialBox,
        check_url: UrlCheck,
        *,
        call_timeout: float,
        health_timeout: float,
        health_interval: float,
    ):
        self._sm = sessionmaker
        self._box = box
        self._check_url = check_url
        self.call_timeout = call_timeout
        self.health_timeout = health_timeout
        self.health_interval = health_interval
        self._states: dict[str, UpstreamState] = {}   # slug -> state, in registration order
        self._tool_names: frozenset[str] = frozenset()
        self.on_tools_changed: Callable[[], Awaitable[None]] | None = None

    # --- loading -------------------------------------------------------------

    async def _load(self) -> None:
        """Sync in-memory state with the DB (admins may have added/edited/removed upstreams)."""
        async with self._sm() as session:
            rows = (await session.scalars(select(UpstreamServer).order_by(UpstreamServer.id))).all()
        states: dict[str, UpstreamState] = {}
        for row in rows:
            try:
                credential = self._box.decrypt(row.credential_encrypted) if row.credential_encrypted else None
            except ValueError as exc:
                log.error("upstream %s: %s", row.slug, exc)
                credential = None
            config = UpstreamConfig(row.slug, row.name, row.url, row.auth_type, row.header_name, credential, row.enabled)
            old = self._states.get(row.slug)
            if old is not None and old.config == config:
                states[row.slug] = old                         # unchanged: keep pool + cache
            else:
                if old is not None:
                    await old.conn.reset()                     # config changed: drop old pool
                states[row.slug] = UpstreamState(UpstreamConnection(config, self._check_url))
        for slug, old in self._states.items():
            if slug not in states:
                await old.conn.reset()                         # removed upstream
        self._states = states

    # --- health --------------------------------------------------------------

    async def _check(self, state: UpstreamState) -> None:
        if not state.config.enabled:
            state.tools, state.status, state.last_error = [], "disabled", None
            await state.conn.reset()
            return
        try:
            with anyio.fail_after(self.health_timeout):
                tools = await state.conn.list_tools()
            state.tools, state.status, state.last_error = tools, "connected", None
        except TimeoutError:
            state.tools, state.status = [], "unreachable"
            state.last_error = f"health check timed out after {self.health_timeout:g}s"
        except Exception as exc:
            state.tools, state.status = [], "unreachable"
            state.last_error = state.conn.scrub(describe(exc))

    async def refresh(self) -> None:
        """Reload config, check every upstream concurrently, persist status, notify on change."""
        await self._load()
        async with anyio.create_task_group() as tg:
            for state in self._states.values():
                tg.start_soon(self._check, state)

        now = datetime.now(timezone.utc)
        async with self._sm() as session:
            for slug, s in self._states.items():
                await session.execute(
                    update(UpstreamServer).where(UpstreamServer.slug == slug)
                    .values(status=s.status, last_error=s.last_error, last_checked_at=now))
            await session.commit()

        names = frozenset(tool.name for tool in self.tools())
        if names != self._tool_names:
            self._tool_names = names
            log.info("tool set changed: %d tools (%s)", len(names),
                     ", ".join(f"{s}={st.status}" for s, st in self._states.items()))
            if self.on_tools_changed:
                await self.on_tools_changed()

    async def run_forever(self) -> None:
        while True:
            await anyio.sleep(self.health_interval)
            try:
                await self.refresh()
            except Exception:
                log.exception("health refresh failed")

    # --- serving -------------------------------------------------------------

    def tools(self) -> list[t.Tool]:
        """All tools of enabled + reachable upstreams, renamed <slug>__<tool>."""
        merged = []
        for slug, state in self._states.items():
            if state.status == "connected":
                merged.extend(tool.model_copy(update={"name": naming.prefixed(slug, tool.name)})
                              for tool in state.tools)
        return merged

    def statuses(self) -> dict[str, tuple[str, str | None]]:
        return {slug: (s.status, s.last_error) for slug, s in self._states.items()}

    async def call(self, prefixed_name: str, arguments: dict) -> t.CallToolResult:
        parsed = naming.split(prefixed_name)
        if parsed is None:
            raise RoutingError("error", f"unknown tool {prefixed_name!r}: expected <server>__<tool>")
        slug, tool = parsed
        state = self._states.get(slug)
        if state is None:
            raise RoutingError("error", f"unknown server {slug!r}", slug)
        if not state.config.enabled:
            raise RoutingError("denied", f"upstream {slug!r} is disabled", slug)
        try:
            with anyio.fail_after(self.call_timeout):
                return await state.conn.call_tool(tool, arguments)
        except TimeoutError:
            raise RoutingError("timeout", f"upstream {slug!r} timed out after {self.call_timeout:g}s", slug)
        except MCPError as exc:
            raise RoutingError("error", f"upstream {slug!r} returned an error: {state.conn.scrub(describe(exc))}", slug)
        except Exception as exc:
            raise RoutingError("error", f"upstream {slug!r} is unreachable: {state.conn.scrub(describe(exc))}", slug)

    async def close(self) -> None:
        for state in self._states.values():
            await state.conn.reset()
