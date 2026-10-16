# fx-mcp: currency conversion MCP server

Converts amounts between currencies using static reference rates from a ConfigMap. MCP over Streamable
HTTP (`POST /mcp`, stateless, JSON responses). **Requires `X-API-Key: <key>`**, the "header" upstream
credential type that the gateway injects. Internal only, and it needs **no network at all**: its
NetworkPolicy denies all egress, DNS included.

## Tools

| Tool | What it does |
|---|---|
| `convert_currency` | `amount` (≥ 0), `from_currency`, `to_currency` (3-letter codes, any case) → rate (6 dp) and converted amount (2 dp). |
| `list_rates` | Value of 1 unit of every currency, expressed in `base` (default INR). |

## Rates
ConfigMap `fx-rates` (from `values.fxMcp.rates`) is mounted at `/config/rates.json`:

```json
{"base": "INR", "as_of": "2026-10-01", "rates": {"USD": "84.10", "EUR": "91.50"}}
```

This means 1 USD = 84.10 INR. Cross rates go through the base: USD→EUR = 84.10 / 91.50.
- **Exact arithmetic.** All maths uses `Decimal`, and float inputs are converted via `str()`, so 0.1 stays 0.1.
- **Rates update without a restart.** The file is **re-read on every call**, and Kubernetes refreshes mounted ConfigMaps in place.
  So `helm upgrade` with new rates takes effect within about a minute, with no pod restart.

## Configuration
| Var | Notes |
|---|---|
| `FX_API_KEY` | Key clients must send as `X-API-Key` (Secret `fx-mcp-auth`). |
| `RATES_FILE` | Default `/config/rates.json`. |
| `MCP_ALLOWED_HOSTS` | Accepted `Host` headers (DNS-rebinding protection). |

Auth middleware: same as notes-mcp (`fx_mcp/auth.py`, constant-time compare, `/healthz` open).
