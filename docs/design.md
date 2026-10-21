# Design

A WooCommerce store plus a **team MCP gateway** on Kubernetes. Admins register MCP servers once. Every team
member uses all their tools through **one endpoint with their own API key**, or through a chat agent in the
dashboard, and every call is audited. The same Helm charts run locally (k3d) and on a VPS (k3s).

> Sections marked **TODO(Rushil)** are the reasoning I'll explain in my own words. The bullets under them are
> the points to cover, not the answer. Everything else is a factual description of what is built.

## 1. Architecture

```
                           Internet / browser / MCP clients
                                         │  :80/:443
                                  ┌──────▼──────┐   k3s Traefik (ingress controller)
                                  │   Traefik   │   TLS termination in prod (cert-manager)
                                  └──┬───────┬──┘
                shop.<domain>        │       │        app.<domain>
        ┌────────────────────────────┘       └───────────────┬──────────────┬─────────────┐
        │  namespace: store                                   │ /            │ /api, /mcp  │
 ┌──────▼──────┐   ┌──────────┐            namespace:  ┌──────▼────┐  ┌──────▼──────────────────────────┐
 │  WordPress  ├──►│ MariaDB  │            platform    │ dashboard │  │ gateway (FastAPI)                │
 │ +WooCommerce│   │(StatefulS│                        │ nginx+SPA │  │  REST API · MCP server · chat    │
 └──────▲──────┘   └──────────┘                        └───────────┘  │  registry+health loop · audit    │
        │ REST v3 (OAuth1, in-cluster http)                            └──┬──────┬───────┬──────┬───────┘
        │                                                                 │      │       │      │ Gemini (https)
 ┌──────┴──────┐   MCP (Streamable HTTP) + upstream credential            │      │       │      ▼
 │   woo-mcp   │◄─────────────────────────────────────────────────────────┘      │       │
 └─────────────┘                    ┌───────────┐  Bearer token                  │       │
                                    │ notes-mcp │◄───────────────────────────────┘       │
                                    └─────┬─────┘                ┌────────┐  X-API-Key   │
                                          └──────────►┌──────────┤ fx-mcp │◄─────────────┘
                                                      │ Postgres │└────────┘
                                                      │ notes db │ (gateway db: users, keys, upstreams, audit, sessions)
                                                      └──────────┘
```

| Component | What it is | Where |
|---|---|---|
| Store | WordPress + WooCommerce 11.2 (official images), MariaDB StatefulSet, idempotent wp-cli bootstrap Job | `charts/store` |
| woo-mcp | Our MCP server for the store (6 tools) over the WooCommerce REST API | `services/woo-mcp` |
| notes-mcp | Team notes in Postgres (4 tools), bearer-token protected | `services/notes-mcp` |
| fx-mcp | Currency conversion from ConfigMap rates (2 tools), `X-API-Key` protected | `services/fx-mcp` |
| gateway | FastAPI app: MCP aggregation endpoint `/mcp`, REST API `/api`, chat agent, audit | `services/gateway` |
| dashboard | React + TypeScript SPA served by unprivileged nginx | `services/dashboard` |
| Postgres | One instance, separate databases and owner roles: `gateway`, `notes` | `charts/platform` |

## 2. Request flows

**External MCP client** (Claude Code, Cursor, Inspector) → `POST https://app.<domain>/mcp` with
`Authorization: Bearer urk_…`:
1. Traefik → gateway Service → `McpAuth` (`gateway/auth.py`). The key is SHA-256-hashed and looked up on every request; the key and its user must be active, otherwise 401 (plus a `denied` audit row if it was a `tools/call`).
2. The MCP SDK's low-level server (`gateway/mcp_server.py`) gets `tools/list` or `tools/call`; the caller is in `ctx.request.state.principal`.
3. `tools/list` → merged tools from the registry cache. `tools/call` → `Registry.call()` (`gateway/upstreams.py`):
   1. Split `<slug>__<tool>`; check the upstream is enabled.
   2. Call it over its own HTTP pool, which carries **that upstream's credential**, with a 15s timeout.
   3. Return the result unchanged.
4. One audit row: user, channel `api`, server, tool, start time, duration, outcome, error.

**Chat** → `POST /api/chat` (session cookie):
1. The gateway mints a 5-minute signed `urc_` token for the user.
2. It opens an MCP client to its **own** `/mcp` at `http://127.0.0.1:8000` (same pod), so the call goes through steps 1–4 above with channel `chat`.
3. It loops: model (Gemini) proposes tool calls → executed through the gateway → results fed back, for at most 8 steps.
4. It returns the answer plus the tool-call chips.

**Dashboard** → static SPA from nginx; the browser calls `/api/*` on the same origin with an `httpOnly`
`SameSite=Strict` session cookie. Roles are enforced per route on the server (`gateway/api/deps.py`).

## 3. Aggregation, routing, naming
- **Registry** (`gateway/upstreams.py`):
  - Loads upstreams from the DB.
  - **Health-checks them all concurrently every 15s** (`tools/list`, 5s timeout each).
  - Caches each one's tools, and persists `status` (`connected` / `unreachable` + `last_error` / `disabled`).
  - Admin changes trigger an immediate refresh under a lock shared with the loop.
  - When the tool set changes, it publishes `ToolsListChanged` (`notifications/tools/list_changed` for clients that subscribed).
- **Naming:** `<server_slug>__<tool>`. Slugs match `^[a-z0-9-]+$`, with no underscore, so splitting on the first `__` is unambiguous. Two servers' `get_info` become `woo__get_info` and `notes__get_info`.
- **Upstream connections:** one long-lived HTTP pool per upstream with the credential header, created lazily and rebuilt after transport errors. Each call is a stateless MCP request.
  - The protocol era (2026-07-28 modern vs legacy session-based) is probed once and remembered.
  - The SSRF check re-runs on every (re)connect.

> **TODO(Rushil): rewrite in my words: why one aggregating endpoint**
> - What a team gets: one URL + one personal key vs N servers × shared secrets on every laptop
> - Central place for revocation, disabling, audit, SSRF/egress control
> - Cost: the gateway is a dependency and a single replica here

> **TODO(Rushil): rewrite in my words: the naming scheme**
> - Why prefixing beats "first one wins" or renaming on collision
> - Why `__` + slug without underscores makes parsing unambiguous
> - The model sees which server a tool belongs to (helps it pick, helps audit)

> **TODO(Rushil): rewrite in my words: cache-based listing**
> - `tools/list` never waits on an upstream → a dead upstream can't block or slow listing
> - Staleness ≤ one health interval; why that's acceptable; immediate refresh on admin changes
> - `list_changed` for modern clients; older clients re-list

> **TODO(Rushil): rewrite in my words: stateless upstream calls instead of long-lived sessions**
> - The 2026-07-28 MCP protocol has no session; pool per upstream keeps TCP reuse
> - Failure isolation per call, nothing shared to corrupt, simpler code
> - Legacy upstreams still work (era remembered after one probe)

## 4. Failure handling

| Situation | Behaviour | Audit |
|---|---|---|
| Upstream down / pod killed | Tools vanish within one health interval; status Unreachable + error; others unaffected; a deleted pod is replaced by its Deployment | direct call → `error` |
| Upstream slow | `upstream 'x' timed out after 15s` | `timeout` |
| Upstream disabled by admin | Tools vanish immediately; direct call → `upstream 'x' is disabled` | `denied` |
| Upstream tool error | Passed through unchanged (`isError`) | `error` |
| Revoked key / disabled user | HTTP 401 on the very next request | `denied` |
| Gateway restart | Stateless calls, sessions in DB; MCP clients just reconnect | n/a |
| LLM provider error / quota | 502, or 429 with `Retry-After`; one automatic wait if ≤ 30s | n/a |
| Postgres restart | Data on PVC; pools reconnect (`pool_pre_ping`, asyncpg pool) | n/a |

## 5. Data model (gateway database, Alembic `0001`, `0002`)
- **`users`:** email, role admin|member, argon2 password hash, status active|invited|disabled, invite token hash + expiry.
- **`api_keys`:** user, name, 12-char prefix, SHA-256 hash (unique), created / revoked / last used.
- **`upstream_servers`:** slug (unique, immutable), name, url, auth_type none|bearer|header, header name, **Fernet-encrypted credential**, enabled, status, last error, last checked.
- **`audit_log`:** user (`SET NULL` on delete), channel api|chat, server slug, tool, started at, duration ms, outcome success|error|timeout|denied, error message.
- **`user_sessions`:** user, token SHA-256 (unique), expiry.

> **TODO(Rushil): rewrite in my words: server-side sessions and hashed secrets**
> - Why sessions in a table instead of JWTs (immediate logout/disable/role change)
> - Why SHA-256 for keys/session tokens but argon2 for passwords (entropy)
> - Why upstream credentials are encrypted (reversible) instead of hashed

## 6. Local vs production
Only values files differ (`charts/*/values-local.yaml` vs `values-prod.yaml`): hostnames, TLS via cert-manager,
`Secure` cookies, image registry (k3d import vs GHCR), volume sizes, resources. Same k3s version, same
NetworkPolicies, same Pod Security levels, same Secrets strategy. Details: README "Deploying to a VPS".

> **TODO(Rushil): rewrite in my words: why k3d locally and k3s on the VPS**
> - Same distribution: Traefik, local-path, NetworkPolicy controller behave identically
> - "Works locally" ≈ "works in prod" with values-only differences

## 7. Trade-offs and what I'd do with more time

> **TODO(Rushil): rewrite in my words**
> - Single gateway replica: in-memory health loop, list_changed bus, login limiter, chat-token key (→ Redis + shared key for HA)
> - Shared upstream credentials: upstreams can't tell members apart (→ per-user upstream tokens / OAuth on upstreams)
> - No per-member tool permissions (every member sees every enabled tool) → tool-level RBAC
> - Gemini free tier quota made chat flaky in testing → billing / provider fallback
> - Backups are manual; no metrics/tracing yet (OpenTelemetry hooks exist in the MCP SDK)
> - OAuth for the gateway itself (brief: out of scope)
