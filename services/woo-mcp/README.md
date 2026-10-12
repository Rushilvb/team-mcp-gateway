# woo-mcp: WooCommerce MCP server

Our own MCP server for the store. It exposes WooCommerce as MCP tools over **Streamable HTTP**
(`POST /mcp`, stateless, JSON responses). It runs in namespace `platform` as a ClusterIP Service with
**no Ingress**. A NetworkPolicy lets only gateway pods call it, and lets it reach only the store's
WordPress pods and DNS.

## Tools

| Tool | What it does |
|---|---|
| `get_info` | Server name/version and which store it manages. (Same name exists on notes-mcp, which demonstrates the gateway's collision handling.) |
| `list_products` | Products with price, stock, permalink. Filters: `search`, `page`, `per_page` (1-100). |
| `create_product` | Create + publish a simple product: `name`, `regular_price` (decimal string), optional `description`, `stock_quantity`. |
| `list_orders` | Order summaries. Filters: `status`, `date="today"` (store timezone), or `after`/`before` ISO dates. |
| `get_order` | One order: items, totals, payment method, customer name/city/email. |
| `update_order_status` | Change status (enum of WooCommerce statuses). Returns old → new. |

Errors (store down, timeout, unknown order, bad input) come back as MCP tool errors (`isError: true`)
with a readable message, never as a stack trace.

## Configuration (env vars, set by `charts/platform`)

| Var | Example | Notes |
|---|---|---|
| `WOO_BASE_URL` | `http://store-wordpress.store.svc.cluster.local` | Where we connect (in-cluster, plain HTTP). |
| `WOO_PUBLIC_HOST` | `shop.localtest.me` | WordPress's site URL host. Sent as `Host` and used in the signature. |
| `WOO_CONSUMER_KEY` / `WOO_CONSUMER_SECRET` | from Secret `woo-mcp-store-api` | Copied by Helm from `store/store-woo-api`. |
| `STORE_TIMEZONE` | `Asia/Kolkata` | Defines "today". |
| `WOO_TIMEOUT_SECONDS` | `10` | Per REST call. |
| `MCP_ALLOWED_HOSTS` | `woo-mcp.platform.svc.cluster.local:*,localhost:*` | DNS-rebinding protection: accepted `Host` headers. |

## Design notes

**Why the REST API and not WP-CLI.** REST is WooCommerce's stable, versioned contract (`/wc/v3`). WP-CLI
would need shell access (`kubectl exec`) into the WordPress pod, which means Kubernetes RBAC for this
server and a much bigger blast radius. The REST key belongs to a dedicated `shop_manager` user, so this
server can do exactly what a shop manager can and nothing more (no plugin installs, no user admin).

**Why OAuth 1.0a signing.** WooCommerce refuses HTTP Basic auth unless the request arrived over HTTPS,
and in-cluster traffic is plain HTTP. With OAuth 1.0a, each request carries an HMAC signature made with
the consumer secret, so the secret itself never travels. `woo_mcp/oauth.py` is about 30 lines that
mirror `WC_REST_Authentication::check_oauth_signature()`. The test's golden signature was checked with
WooCommerce's own PHP verifier inside the store pod.

**Two gotchas.**
1. WooCommerce rebuilds the signed URL from its *home URL*, not the address we connected to. So we sign
   `http://shop.localtest.me/wp-json/...` while actually sending to `http://store-wordpress.store.svc...`.
2. WordPress can redirect requests whose `Host` doesn't match its site URL. We send `Host: <public host>`,
   and we never follow redirects: a redirect is reported as a configuration error.

**Stateless Streamable HTTP.** No MCP session is kept on the server, so a pod restart loses nothing and
any replica can answer any request. JSON responses (not SSE) keep it debuggable with plain HTTP.

**Health.** `/healthz` only says "this process is serving". It does not check the store. Restarting
woo-mcp would not fix a store outage, and tool calls already report "store unreachable".

## Develop

```bash
make test                          # pytest inside Docker (REST API mocked with respx)
make build load-images deploy-local
kubectl -n platform port-forward svc/woo-mcp 8000:8000   # then MCP Inspector -> http://localhost:8000/mcp
```
