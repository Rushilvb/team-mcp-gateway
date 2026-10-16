"""FX MCP server: currency conversion from static rates, served over Streamable HTTP.

Rates live in a ConfigMap mounted as a JSON file:
    {"base": "INR", "as_of": "2026-10-01", "rates": {"USD": "84.10", ...}}
meaning "1 USD is worth 84.10 INR". The file is re-read on every call, so a
`helm upgrade` with new rates applies without restarting the pod (Kubernetes
refreshes mounted ConfigMaps in place, usually within a minute).

Run: uvicorn fx_mcp.server:create_app --factory --host 0.0.0.0 --port 8000
"""

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

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

READ_ONLY = ToolAnnotations(readOnlyHint=True)
CENTS = Decimal("0.01")
RATE_PLACES = Decimal("0.000001")

Currency = Annotated[str, Field(pattern=r"^[A-Za-z]{3}$", description="ISO 4217 currency code, e.g. INR, USD, EUR.")]


@dataclass(frozen=True)
class Rates:
    base: str
    as_of: str
    value_in_base: dict[str, Decimal]  # code -> value of 1 unit in the base currency

    def get(self, code: str) -> Decimal:
        code = code.upper()
        if code not in self.value_in_base:
            raise ToolError(f"unsupported currency {code!r}; supported: {', '.join(sorted(self.value_in_base))}")
        return self.value_in_base[code]


def load_rates(path: str) -> Rates:
    try:
        with open(path) as f:
            data = json.load(f)
        base = data["base"].upper()
        values = {code.upper(): Decimal(str(v)) for code, v in data["rates"].items()}
    except (OSError, ValueError, KeyError, ArithmeticError) as exc:
        raise ToolError(f"exchange rates unavailable: {exc.__class__.__name__}") from exc
    values[base] = Decimal(1)
    return Rates(base=base, as_of=str(data.get("as_of", "unknown")), value_in_base=values)


def build_server(settings: Settings) -> MCPServer:
    mcp = MCPServer(
        "fx-mcp",
        version=__version__,
        instructions="Convert amounts between currencies using the team's reference rates (static, not live market rates).",
    )

    @mcp.tool(annotations=READ_ONLY)
    async def convert_currency(
        amount: Annotated[float, Field(ge=0, description="Amount in from_currency.")],
        from_currency: Currency,
        to_currency: Currency,
    ) -> dict[str, Any]:
        """Convert an amount from one currency to another using the reference rates. Result is rounded to 2 decimals."""
        rates = load_rates(settings.rates_file)
        # Cross rate through the base: 1 FROM = (FROM in base) / (TO in base) TO.
        rate = rates.get(from_currency) / rates.get(to_currency)
        value = Decimal(str(amount))  # via str: avoid binary-float noise like 0.1 -> 0.1000000000000000055
        return {
            "amount": format(value.normalize(), "f"),  # 1598.0 -> "1598" (and never "1.6E+3")
            "from": from_currency.upper(),
            "to": to_currency.upper(),
            "rate": str(rate.quantize(RATE_PLACES, ROUND_HALF_UP)),
            "converted": str((value * rate).quantize(CENTS, ROUND_HALF_UP)),
            "rates_as_of": rates.as_of,
        }

    @mcp.tool(annotations=READ_ONLY)
    async def list_rates(base: Currency = "INR") -> dict[str, Any]:
        """List all supported currencies with the value of 1 unit expressed in `base`."""
        rates = load_rates(settings.rates_file)
        base_value = rates.get(base)
        return {
            "base": base.upper(),
            "rates_as_of": rates.as_of,
            "rates": {code: str((v / base_value).quantize(RATE_PLACES, ROUND_HALF_UP))
                      for code, v in sorted(rates.value_in_base.items())},
        }

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return mcp


def build_app(settings: Settings):
    mcp = build_server(settings)
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
        ),
    )
    # Credential type "header": the gateway will inject X-API-Key for us.
    return RequireHeader(app, "x-api-key", settings.api_key)


def create_app():
    """ASGI app factory used by uvicorn (reads settings from the environment)."""
    return build_app(Settings.from_env())
