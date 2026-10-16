import pytest

from conftest import call

pytestmark = pytest.mark.anyio


async def add(client, text, title=None):
    args = {"text": text} | ({"title": title} if title else {})
    is_error, note = await call(client, "add_note", args)
    assert not is_error, note
    return note


async def test_lists_tools_with_schemas(mcp_client):
    tools = {t.name: t for t in (await mcp_client.list_tools()).tools}
    assert set(tools) == {"get_info", "add_note", "list_notes", "search_notes"}
    assert tools["add_note"].input_schema["required"] == ["text"]
    assert tools["search_notes"].input_schema["required"] == ["query"]


async def test_get_info(mcp_client):
    assert await call(mcp_client, "get_info") == (False, {"name": "notes-mcp", "version": "0.1.0"})


async def test_add_note_returns_saved_note(mcp_client):
    note = await add(mcp_client, "Restock chai by Friday", title="Inventory")
    assert note["id"] == 1
    assert (note["title"], note["text"]) == ("Inventory", "Restock chai by Friday")
    assert note["created_at"].startswith("20")


async def test_list_is_newest_first_with_paging(mcp_client):
    for i in range(1, 4):
        await add(mcp_client, f"note {i}")
    _, result = await call(mcp_client, "list_notes")
    assert [n["text"] for n in result["notes"]] == ["note 3", "note 2", "note 1"]
    _, result = await call(mcp_client, "list_notes", {"limit": 1, "offset": 1})
    assert [n["text"] for n in result["notes"]] == ["note 2"]


async def test_search_is_case_insensitive_over_title_and_text(mcp_client):
    await add(mcp_client, "Order #15 shipped", title="Logistics")
    await add(mcp_client, "Call the supplier", title="CHAI restock")
    await add(mcp_client, "Unrelated")
    _, result = await call(mcp_client, "search_notes", {"query": "chai"})
    assert [n["title"] for n in result["notes"]] == ["CHAI restock"]
    _, result = await call(mcp_client, "search_notes", {"query": "SHIPPED"})
    assert [n["text"] for n in result["notes"]] == ["Order #15 shipped"]


async def test_search_treats_wildcards_literally(mcp_client):
    await add(mcp_client, "Diwali sale: 50% off")
    await add(mcp_client, "Ordered 500 units")
    await add(mcp_client, "file_name.txt")
    await add(mcp_client, "filename.txt")
    _, result = await call(mcp_client, "search_notes", {"query": "50%"})
    assert [n["text"] for n in result["notes"]] == ["Diwali sale: 50% off"]
    _, result = await call(mcp_client, "search_notes", {"query": "file_"})
    assert [n["text"] for n in result["notes"]] == ["file_name.txt"]


async def test_search_without_matches_is_empty(mcp_client):
    await add(mcp_client, "something")
    assert await call(mcp_client, "search_notes", {"query": "nothing like this"}) == (False, {"notes": []})


async def test_invalid_input_rejected_by_schema(mcp_client):
    for tool, args in [("add_note", {"text": ""}), ("list_notes", {"limit": 0}), ("search_notes", {"query": ""})]:
        is_error, _ = await call(mcp_client, tool, args)
        assert is_error, (tool, args)


async def test_database_outage_is_a_clean_tool_error(mcp_client, db):
    await db.close()   # simulate the DB going away
    is_error, text = await call(mcp_client, "list_notes")
    assert is_error and "notes database unavailable" in text
