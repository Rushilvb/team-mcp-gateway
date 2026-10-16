"""Tests run against a REAL Postgres (a mock would hide SQL bugs).
`make test` starts a throwaway postgres container and sets TEST_DATABASE_URL."""

import json
import os

import pytest
from mcp import Client

from notes_mcp.config import Settings
from notes_mcp.db import NotesDB
from notes_mcp.server import build_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set (run via `make test`)")
    return NotesDB(url)


@pytest.fixture
async def mcp_client(db):
    server = build_server(Settings(token="test", db_wait_seconds=30), db)
    async with Client(server) as client:   # runs the lifespan: connect + create table
        await db.delete_all()
        yield client


async def call(client: Client, tool: str, args: dict | None = None):
    """Call a tool; return (is_error, parsed JSON result or error text)."""
    result = await client.call_tool(tool, args or {})
    text = result.content[0].text
    if result.is_error:
        return True, text
    return False, result.structured_content or json.loads(text)
