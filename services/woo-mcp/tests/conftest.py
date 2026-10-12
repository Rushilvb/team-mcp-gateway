import json
from datetime import datetime, timezone

import pytest
import respx
from mcp import Client

from woo_mcp.config import Settings
from woo_mcp.server import build_server
from woo_mcp.woo_client import WooClient

BASE = "http://store-wordpress.store.svc.cluster.local"
API = BASE + "/wp-json/wc/v3"

SETTINGS = Settings(
    woo_base_url=BASE,
    public_host="shop.localtest.me",
    consumer_key="ck_test",
    consumer_secret="cs_test",
    timezone="Asia/Kolkata",
    timeout_seconds=2,
)

# 00:30 on 12 Oct in India = 19:00 on 11 Oct UTC: the edge where "today" in
# UTC and "today" in the store's timezone are different days.
FROZEN_NOW = datetime(2026, 10, 11, 19, 0, tzinfo=timezone.utc)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def store():
    """Mocked WooCommerce REST API. Unmatched requests fail the test."""
    with respx.mock(base_url=API, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def mcp_client(store):
    """A real MCP client session connected in-memory to our server."""
    server = build_server(SETTINGS, WooClient(SETTINGS), now=lambda: FROZEN_NOW)
    async with Client(server) as client:
        yield client


async def call(client: Client, tool: str, args: dict | None = None):
    """Call a tool; return (is_error, parsed JSON result or error text)."""
    result = await client.call_tool(tool, args or {})
    text = result.content[0].text
    if result.is_error:
        return True, text
    return False, result.structured_content or json.loads(text)
