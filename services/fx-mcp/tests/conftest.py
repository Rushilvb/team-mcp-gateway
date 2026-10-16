import json

import pytest
from mcp import Client

from fx_mcp.config import Settings
from fx_mcp.server import build_server

RATES = {"base": "INR", "as_of": "2026-10-01", "rates": {"USD": "84.10", "EUR": "91.50", "JPY": "0.56"}}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def rates_file(tmp_path):
    path = tmp_path / "rates.json"
    path.write_text(json.dumps(RATES))
    return path


@pytest.fixture
async def mcp_client(rates_file):
    server = build_server(Settings(api_key="test-key", rates_file=str(rates_file)))
    async with Client(server) as client:
        yield client


async def call(client: Client, tool: str, args: dict | None = None):
    """Call a tool; return (is_error, parsed JSON result or error text)."""
    result = await client.call_tool(tool, args or {})
    text = result.content[0].text
    if result.is_error:
        return True, text
    return False, result.structured_content or json.loads(text)
