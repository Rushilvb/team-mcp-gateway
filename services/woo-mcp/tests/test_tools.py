import json

import httpx
import pytest

from conftest import call

pytestmark = pytest.mark.anyio

PRODUCT = {
    "id": 11, "name": "Masala Chai Tin", "sku": "CHAI-001", "price": "349", "regular_price": "349",
    "status": "publish", "stock_status": "instock", "stock_quantity": None,
    "permalink": "http://shop.localtest.me/product/masala-chai-tin/", "description": "<p>ignored</p>",
}
ORDER = {
    "id": 42, "status": "processing", "date_created": "2026-10-12T00:10:00", "total": "349.00", "currency": "INR",
    "billing": {"first_name": "Asha", "last_name": "Rao", "email": "asha@example.com", "city": "Bengaluru"},
    "payment_method_title": "Cash on delivery", "customer_note": "",
    "line_items": [{"name": "Masala Chai Tin", "sku": "CHAI-001", "quantity": 1, "total": "349.00"}],
}


# --- tool discovery ---------------------------------------------------------

async def test_lists_all_tools_with_schemas(mcp_client):
    tools = {t.name: t for t in (await mcp_client.list_tools()).tools}
    assert set(tools) == {"get_info", "list_products", "create_product", "list_orders", "get_order", "update_order_status"}
    assert set(tools["create_product"].input_schema["required"]) == {"name", "regular_price"}
    assert tools["list_orders"].input_schema["properties"]["per_page"]["maximum"] == 100
    assert all(t.description for t in tools.values())


async def test_get_info(mcp_client):
    assert await call(mcp_client, "get_info") == (False, {"name": "woo-mcp", "version": "0.1.0", "store": "shop.localtest.me"})


# --- requests: signing, Host header, redirects, errors -----------------------

async def test_requests_are_signed_and_sent_with_public_host(mcp_client, store):
    route = store.get("/products").respond(json=[PRODUCT])
    await call(mcp_client, "list_products")
    request = route.calls.last.request
    assert request.headers["host"] == "shop.localtest.me"
    assert request.url.host == "store-wordpress.store.svc.cluster.local"
    assert request.url.params["oauth_consumer_key"] == "ck_test"
    assert "oauth_signature" in request.url.params


async def test_redirect_is_reported_not_followed(mcp_client, store):
    store.get("/products").respond(301, headers={"location": "http://elsewhere.example/"})
    is_error, text = await call(mcp_client, "list_products")
    assert is_error and "redirected" in text and "WOO_PUBLIC_HOST" in text


async def test_store_timeout_is_a_clean_tool_error(mcp_client, store):
    store.get("/products").mock(side_effect=httpx.ConnectTimeout("slow"))
    is_error, text = await call(mcp_client, "list_products")
    assert is_error and "store unreachable: timed out after 2s" in text


async def test_missing_order_gives_clear_message(mcp_client, store):
    store.get("/orders/999").respond(404, json={"code": "woocommerce_rest_shop_order_invalid_id", "message": "Invalid ID.", "data": {"status": 404}})
    is_error, text = await call(mcp_client, "get_order", {"order_id": 999})
    assert is_error and "Order 999 not found" in text


async def test_woocommerce_error_message_is_passed_through(mcp_client, store):
    store.post("/products").respond(400, json={"code": "woocommerce_rest_product_invalid", "message": "Invalid or duplicated SKU.", "data": {"status": 400}})
    is_error, text = await call(mcp_client, "create_product", {"name": "X", "regular_price": "1"})
    assert is_error and "Invalid or duplicated SKU." in text


# --- products ---------------------------------------------------------------

async def test_list_products_maps_fields_and_passes_filters(mcp_client, store):
    route = store.get("/products").respond(json=[PRODUCT])
    is_error, result = await call(mcp_client, "list_products", {"search": "chai", "per_page": 5})
    assert not is_error
    assert result["products"] == [{
        "id": 11, "name": "Masala Chai Tin", "sku": "CHAI-001", "price": "349", "regular_price": "349",
        "status": "publish", "stock_status": "instock", "stock_quantity": None,
        "permalink": "http://shop.localtest.me/product/masala-chai-tin/",
    }]
    params = route.calls.last.request.url.params
    assert (params["search"], params["per_page"], params["page"]) == ("chai", "5", "1")


async def test_per_page_limit_is_enforced_by_schema(mcp_client, store):
    is_error, _ = await call(mcp_client, "list_products", {"per_page": 500})
    assert is_error
    assert not store.calls  # rejected before any HTTP request


async def test_create_product_sends_published_simple_product(mcp_client, store):
    route = store.post("/products").respond(201, json={**PRODUCT, "id": 99, "name": "Test Kurta", "regular_price": "999"})
    is_error, result = await call(mcp_client, "create_product", {"name": "Test Kurta", "regular_price": "999", "stock_quantity": 5})
    assert not is_error and result["id"] == 99
    assert json.loads(route.calls.last.request.content) == {
        "name": "Test Kurta", "type": "simple", "status": "publish", "regular_price": "999",
        "manage_stock": True, "stock_quantity": 5,
    }


async def test_create_product_rejects_bad_prices(mcp_client, store):
    for bad in ("abc", "-5", "NaN"):
        is_error, text = await call(mcp_client, "create_product", {"name": "X", "regular_price": bad})
        assert is_error and "regular_price" in text, bad
    assert not store.calls


# --- orders -----------------------------------------------------------------

async def test_list_orders_today_uses_store_timezone(mcp_client, store):
    # FROZEN_NOW is 00:30 on 12 Oct in Asia/Kolkata (UTC+5:30), so "today"
    # is 12 Oct 00:00 -> 13 Oct 00:00 IST = 11 Oct 18:30 -> 12 Oct 18:30 UTC.
    route = store.get("/orders").respond(json=[ORDER])
    is_error, result = await call(mcp_client, "list_orders", {"date": "today"})
    assert not is_error
    params = route.calls.last.request.url.params
    assert params["after"] == "2026-10-11T18:30:00"
    assert params["before"] == "2026-10-12T18:30:00"
    assert params["dates_are_gmt"] == "true"
    assert result["orders"] == [{
        "id": 42, "status": "processing", "date_created": "2026-10-12T00:10:00", "total": "349.00",
        "currency": "INR", "customer": "Asha Rao", "items": ["1 x Masala Chai Tin"],
    }]


async def test_list_orders_after_without_offset_is_store_local(mcp_client, store):
    route = store.get("/orders").respond(json=[])
    await call(mcp_client, "list_orders", {"after": "2026-10-01", "status": "completed"})
    params = route.calls.last.request.url.params
    assert params["after"] == "2026-09-30T18:30:00"
    assert params["status"] == "completed"
    assert "before" not in params


async def test_list_orders_rejects_today_plus_range(mcp_client, store):
    is_error, text = await call(mcp_client, "list_orders", {"date": "today", "after": "2026-10-01"})
    assert is_error and "not both" in text
    assert not store.calls


async def test_list_orders_rejects_unparseable_date(mcp_client, store):
    is_error, text = await call(mcp_client, "list_orders", {"after": "last tuesday"})
    assert is_error and "ISO date" in text


async def test_get_order_details(mcp_client, store):
    store.get("/orders/42").respond(json=ORDER)
    is_error, result = await call(mcp_client, "get_order", {"order_id": 42})
    assert not is_error
    assert result["payment_method"] == "Cash on delivery"
    assert result["customer_city"] == "Bengaluru"
    assert result["line_items"] == [{"name": "Masala Chai Tin", "sku": "CHAI-001", "quantity": 1, "total": "349.00"}]


async def test_update_order_status(mcp_client, store):
    store.get("/orders/42").respond(json=ORDER)
    put = store.put("/orders/42").respond(json={**ORDER, "status": "completed"})
    is_error, result = await call(mcp_client, "update_order_status", {"order_id": 42, "status": "completed"})
    assert not is_error
    assert result == {"id": 42, "old_status": "processing", "new_status": "completed"}
    assert json.loads(put.calls.last.request.content) == {"status": "completed"}


async def test_update_order_status_rejects_unknown_status(mcp_client, store):
    is_error, _ = await call(mcp_client, "update_order_status", {"order_id": 42, "status": "shipped"})
    assert is_error
    assert not store.calls
