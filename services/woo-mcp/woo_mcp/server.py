"""WooCommerce MCP server: tools over the store's REST API, served over Streamable HTTP.

Run: uvicorn woo_mcp.server:create_app --factory --host 0.0.0.0 --port 8000
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from . import __version__
from .config import Settings
from .woo_client import WooClient, WooError

OrderStatus = Literal["pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"]
READ_ONLY = ToolAnnotations(readOnlyHint=True)

PerPage = Annotated[int, Field(ge=1, le=100, description="Results per page (1-100).")]
Page = Annotated[int, Field(ge=1, description="Page number, starting at 1.")]


def build_server(
    settings: Settings,
    client: WooClient,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> MCPServer:
    """Create the MCP server. `client` and `now` are injectable for tests."""
    store_tz = ZoneInfo(settings.timezone)
    mcp = MCPServer(
        "woo-mcp",
        version=__version__,
        instructions=f"Manage the WooCommerce store at {settings.public_host}. Prices are strings in the store currency.",
    )

    async def call(method: str, path: str, *, not_found: str | None = None, **kwargs: Any) -> Any:
        try:
            return await client.request(method, path, **kwargs)
        except WooError as exc:
            # ToolError -> the model receives isError=true with this message.
            if exc.status == 404 and not_found:
                raise ToolError(not_found) from exc
            raise ToolError(exc.message) from exc

    def to_utc(value: str, name: str) -> str:
        """ISO date/datetime -> UTC string. Times without an offset are store-local."""
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ToolError(f"{name} must be an ISO date or datetime like 2026-10-12 or 2026-10-12T09:30, got {value!r}")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=store_tz)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    @mcp.tool(annotations=READ_ONLY)
    async def get_info() -> dict[str, Any]:
        """Return this MCP server's name and version and which store it manages."""
        return {"name": "woo-mcp", "version": __version__, "store": settings.public_host}

    @mcp.tool(annotations=READ_ONLY)
    async def list_products(
        search: Annotated[str | None, Field(description="Only products whose name or description contains this text.")] = None,
        page: Page = 1,
        per_page: PerPage = 20,
    ) -> dict[str, Any]:
        """List products in the store (newest first) with price, stock and storefront link."""
        products = await call("GET", "/products", params={"search": search, "page": page, "per_page": per_page})
        return {"products": [_product(p) for p in products], "page": page}

    @mcp.tool()
    async def create_product(
        name: Annotated[str, Field(min_length=1, max_length=200, description="Product name shown on the storefront.")],
        regular_price: Annotated[str, Field(description="Price in the store currency as a decimal string, e.g. \"499\" or \"499.50\".")],
        description: Annotated[str | None, Field(description="Optional product description (plain text or HTML).")] = None,
        stock_quantity: Annotated[int | None, Field(ge=0, description="Optional stock count. If omitted, stock is not tracked.")] = None,
    ) -> dict[str, Any]:
        """Create and publish a simple product. It appears on the storefront immediately."""
        try:
            price = Decimal(regular_price)
        except InvalidOperation:
            raise ToolError(f"regular_price must be a number like \"499\" or \"499.50\", got {regular_price!r}")
        if not price.is_finite() or price < 0:
            raise ToolError(f"regular_price must be zero or positive, got {regular_price!r}")

        body: dict[str, Any] = {"name": name, "type": "simple", "status": "publish", "regular_price": str(price)}
        if description:
            body["description"] = description
        if stock_quantity is not None:
            body["manage_stock"] = True
            body["stock_quantity"] = stock_quantity
        return _product(await call("POST", "/products", json=body))

    @mcp.tool(annotations=READ_ONLY)
    async def list_orders(
        status: Annotated[OrderStatus | None, Field(description="Only orders with this status.")] = None,
        date: Annotated[Literal["today"] | None, Field(description=f"'today' = orders created today in the store's timezone ({settings.timezone}). Cannot be combined with after/before.")] = None,
        after: Annotated[str | None, Field(description="Only orders created after this ISO date/datetime (store timezone unless an offset is given).")] = None,
        before: Annotated[str | None, Field(description="Only orders created before this ISO date/datetime (store timezone unless an offset is given).")] = None,
        page: Page = 1,
        per_page: PerPage = 20,
    ) -> dict[str, Any]:
        """List orders (newest first) with status, total and items. Use date='today' for today's orders."""
        if date and (after or before):
            raise ToolError("use either date='today' or after/before, not both")
        params: dict[str, Any] = {"status": status, "page": page, "per_page": per_page}
        if date == "today":
            start = now().astimezone(store_tz).replace(hour=0, minute=0, second=0, microsecond=0)
            after_utc = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            before_utc = (start + timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        else:
            after_utc = to_utc(after, "after") if after else None
            before_utc = to_utc(before, "before") if before else None
        if after_utc or before_utc:
            # Send UTC and say so: unambiguous whatever the WordPress timezone is.
            params.update({"after": after_utc, "before": before_utc, "dates_are_gmt": "true"})
        orders = await call("GET", "/orders", params=params)
        return {"orders": [_order_summary(o) for o in orders], "page": page}

    @mcp.tool(annotations=READ_ONLY)
    async def get_order(order_id: Annotated[int, Field(ge=1, description="The order ID.")]) -> dict[str, Any]:
        """Get one order's details: items, totals, payment method, customer name and city."""
        return _order_detail(await call("GET", f"/orders/{order_id}", not_found=f"Order {order_id} not found"))

    @mcp.tool()
    async def update_order_status(
        order_id: Annotated[int, Field(ge=1, description="The order ID.")],
        status: Annotated[OrderStatus, Field(description="The new status.")],
    ) -> dict[str, Any]:
        """Change an order's status (e.g. processing -> completed). Returns the old and new status."""
        old = await call("GET", f"/orders/{order_id}", not_found=f"Order {order_id} not found")
        new = await call("PUT", f"/orders/{order_id}", json={"status": status})
        return {"id": order_id, "old_status": old["status"], "new_status": new["status"]}

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> PlainTextResponse:
        # Liveness/readiness: is this process serving? Deliberately does NOT
        # call the store; a store outage is reported per tool call instead.
        return PlainTextResponse("ok")

    return mcp


def _product(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p["id"],
        "name": p["name"],
        "sku": p.get("sku") or None,
        "price": p.get("price"),
        "regular_price": p.get("regular_price"),
        "status": p.get("status"),
        "stock_status": p.get("stock_status"),
        "stock_quantity": p.get("stock_quantity"),
        "permalink": p.get("permalink"),
    }


def _customer_name(o: dict[str, Any]) -> str:
    billing = o.get("billing") or {}
    return f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()


def _order_summary(o: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": o["id"],
        "status": o["status"],
        "date_created": o.get("date_created"),  # store-local time, as WooCommerce returns it
        "total": o.get("total"),
        "currency": o.get("currency"),
        "customer": _customer_name(o),
        "items": [f"{i['quantity']} x {i['name']}" for i in o.get("line_items", [])],
    }


def _order_detail(o: dict[str, Any]) -> dict[str, Any]:
    billing = o.get("billing") or {}
    return {
        **_order_summary(o),
        "payment_method": o.get("payment_method_title"),
        "customer_email": billing.get("email"),
        "customer_city": billing.get("city"),
        "customer_note": o.get("customer_note") or None,
        "line_items": [
            {"name": i["name"], "sku": i.get("sku") or None, "quantity": i["quantity"], "total": i.get("total")}
            for i in o.get("line_items", [])
        ],
    }


def create_app():
    """ASGI app factory used by uvicorn (reads settings from the environment)."""
    settings = Settings.from_env()
    mcp = build_server(settings, WooClient(settings))
    return mcp.streamable_http_app(
        # Stateless: every request stands alone, so a restart loses nothing and
        # any replica can answer. JSON responses: plain request/response, easy to debug.
        stateless_http=True,
        json_response=True,
        # Explicit, because the SDK's default only accepts Host: localhost, and
        # the gateway will call us as woo-mcp.platform.svc.cluster.local.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
        ),
    )
