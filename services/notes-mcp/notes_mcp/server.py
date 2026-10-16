"""Team notes MCP server (Postgres), served over Streamable HTTP behind a bearer token.

Run: uvicorn notes_mcp.server:create_app --factory --host 0.0.0.0 --port 8000
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, TypeVar

import asyncpg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from . import __version__
from .auth import RequireHeader
from .config import Settings
from .db import NotesDB

READ_ONLY = ToolAnnotations(readOnlyHint=True)
Limit = Annotated[int, Field(ge=1, le=100, description="Maximum number of notes to return (1-100).")]
T = TypeVar("T")


def _note(row: asyncpg.Record) -> dict[str, Any]:
    return {"id": row["id"], "title": row["title"], "text": row["body"], "created_at": row["created_at"].isoformat()}


def build_server(settings: Settings, db: NotesDB) -> MCPServer:
    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[None]:
        # Runs once per process (the Streamable HTTP session manager owns it),
        # before uvicorn accepts traffic, so the pod isn't Ready until the DB is.
        await db.connect(settings.db_wait_seconds)
        try:
            yield
        finally:
            await db.close()

    mcp = MCPServer(
        "notes-mcp",
        version=__version__,
        instructions="Shared team notes: save, list and search short notes.",
        lifespan=lifespan,
    )

    async def run_db(op: Callable[[], Awaitable[T]]) -> T:
        try:
            return await op()
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            # Don't leak SQL or connection details to the model.
            raise ToolError(f"notes database unavailable ({exc.__class__.__name__})") from exc

    @mcp.tool(annotations=READ_ONLY)
    async def get_info() -> dict[str, Any]:
        """Return this MCP server's name and version."""
        return {"name": "notes-mcp", "version": __version__}

    @mcp.tool()
    async def add_note(
        text: Annotated[str, Field(min_length=1, max_length=10_000, description="The note's content.")],
        title: Annotated[str | None, Field(max_length=200, description="Optional short title.")] = None,
    ) -> dict[str, Any]:
        """Save a note for the team. Returns the saved note with its id and timestamp."""
        return _note(await run_db(lambda: db.add(text, title)))

    @mcp.tool(annotations=READ_ONLY)
    async def list_notes(
        limit: Limit = 20,
        offset: Annotated[int, Field(ge=0, description="Skip this many notes (for paging).")] = 0,
    ) -> dict[str, Any]:
        """List team notes, newest first."""
        return {"notes": [_note(r) for r in await run_db(lambda: db.list_recent(limit, offset))]}

    @mcp.tool(annotations=READ_ONLY)
    async def search_notes(
        query: Annotated[str, Field(min_length=1, max_length=200, description="Text to look for (case-insensitive) in titles and content.")],
        limit: Limit = 20,
    ) -> dict[str, Any]:
        """Find notes whose title or text contains the query (case-insensitive), newest first."""
        return {"notes": [_note(r) for r in await run_db(lambda: db.search(query, limit))]}

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> PlainTextResponse:
        # Process-only, like woo-mcp: a DB outage is reported per tool call.
        return PlainTextResponse("ok")

    return mcp


def build_app(settings: Settings, db: NotesDB):
    mcp = build_server(settings, db)
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
        ),
    )
    # Credential type "bearer": the gateway will inject Authorization: Bearer <token>.
    return RequireHeader(app, "authorization", settings.token, scheme="Bearer")


def create_app():
    """ASGI app factory used by uvicorn (reads settings from the environment)."""
    return build_app(Settings.from_env(), NotesDB())
