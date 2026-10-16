"""The gateway's own MCP server (low-level SDK API, so the tool list is computed per request).

tools/list  -> merged, prefixed tools of every enabled + reachable upstream (from cache)
tools/call  -> route by prefix, call the upstream with ITS credential, audit, return
list_changed: when the health loop sees the tool set change it publishes an
event; clients on the 2026-07-28 protocol that called subscriptions/listen get
notifications/tools/list_changed. Older clients have no such stream in
stateless mode and simply re-list.
"""

import mcp.types as t
from mcp.server.lowlevel.server import Server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ToolsListChanged

from . import __version__, naming
from .audit import Audit, Principal, Timer
from .upstreams import Registry, RoutingError


def _error(message: str) -> t.CallToolResult:
    return t.CallToolResult(content=[t.TextContent(type="text", text=message)], is_error=True)


def _first_text(result: t.CallToolResult) -> str | None:
    for block in result.content:
        if isinstance(block, t.TextContent):
            return block.text
    return None


def build_mcp_server(registry: Registry, audit: Audit) -> Server:
    bus = InMemorySubscriptionBus()

    async def notify_tools_changed() -> None:
        await bus.publish(ToolsListChanged())

    registry.on_tools_changed = notify_tools_changed

    async def list_tools(ctx, params) -> t.ListToolsResult:
        return t.ListToolsResult(tools=registry.tools())

    async def call_tool(ctx, params: t.CallToolRequestParams) -> t.CallToolResult:
        principal: Principal | None = getattr(getattr(ctx.request, "state", None), "principal", None)
        if principal is None:           # cannot happen behind McpAuth; refuse rather than guess
            return _error("unauthenticated")
        timer = Timer()
        parsed = naming.split(params.name)
        slug = parsed[0] if parsed else None
        try:
            result = await registry.call(params.name, params.arguments or {})
        except RoutingError as exc:
            await audit.record(user_id=principal.user_id, channel=principal.channel, server_slug=exc.slug or slug,
                               tool=params.name, timer=timer, outcome=exc.outcome, error=exc.message)
            return _error(exc.message)
        # The upstream answered. A tool-level error (isError) is passed through
        # unchanged so the model can read it, and audited as "error".
        await audit.record(user_id=principal.user_id, channel=principal.channel, server_slug=slug,
                           tool=params.name, timer=timer,
                           outcome="error" if result.is_error else "success",
                           error=_first_text(result) if result.is_error else None)
        return result

    return Server(
        "team-mcp-gateway",
        version=__version__,
        instructions="Team MCP gateway. Tools are named <server>__<tool>; the prefix tells you which upstream server runs it.",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_subscriptions_listen=ListenHandler(bus),
    )
