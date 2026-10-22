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

**Why one aggregating endpoint.** Without a gateway, every team member would configure every MCP server on their own laptop and keep a copy of each server's secret. With the gateway, a member adds one URL and their own personal key, and gets every enabled server's tools. It also gives one place to control access: revoking a key, disabling a server, auditing every call and blocking dangerous URLs all happen centrally instead of on every laptop. The cost is that the gateway becomes a dependency, so if it's down, nobody can use any tool. Here it runs as a single replica; for production I'd run several (see trade-offs).

**The naming scheme.** When two servers expose the same tool name (both woo and notes have `get_info`), something has to give. "First one wins" silently hides a tool, and renaming on collision (`get_info_2`) makes names depend on which servers happen to be registered. Prefixing every tool with its server's slug, like `woo__get_info`, is stable and predictable. Slugs can only contain lowercase letters, digits and hyphens, never an underscore, so the gateway can always split at the first `__` and know exactly which server and tool are meant. As a bonus, the model and the audit log both see which server a tool belongs to.

**Cache-based listing.** `tools/list` is answered from a cache that the health loop refreshes every 15 seconds, so listing never waits on an upstream. If one server is slow or dead, the list still comes back instantly with everyone else's tools. The trade-off is that the list can be up to one interval out of date. That's acceptable, because calling a tool on a server that just died still fails cleanly with a clear error. Admin changes (enable, disable, edit, remove) refresh the cache immediately, so they show up at once. Newer clients are told the list changed through `list_changed`; older clients see it the next time they re-list.

**Stateless upstream calls.** Each tool call is a self-contained MCP request sent over a long-lived HTTP connection pool, one pool per upstream. The newest MCP protocol has no session, and our servers are stateless, so keeping a long-lived session would only add tricky concurrency code. Because nothing is shared between calls, one failed call can't break another, and a broken connection is simply rebuilt. The pool still reuses TCP connections, so it stays efficient. Older servers that require a session handshake still work: the gateway detects this once and remembers it.

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

**Server-side sessions and hashed secrets.** Dashboard logins use a random token stored as a hash in a sessions table, instead of a JWT. Every request checks that table and the user's current status, so logging out, disabling a user or changing their role takes effect on the very next request; a JWT would stay valid until it expired. API keys and session tokens are long random values, so a fast SHA-256 hash is enough, since nobody can brute-force them. Passwords are chosen by people and can be weak, so they use argon2, which is deliberately slow to make guessing expensive. Upstream credentials are different again: the gateway must send the real value to the upstream, so they're encrypted (reversible, with a key from a Kubernetes Secret) rather than hashed.

## 6. Local vs production
Only values files differ (`charts/*/values-local.yaml` vs `values-prod.yaml`): hostnames, TLS via cert-manager,
`Secure` cookies, image registry (k3d import vs GHCR), volume sizes, resources. Same k3s version, same
NetworkPolicies, same Pod Security levels, same Secrets strategy. Details: README "Deploying to a VPS".

**Why k3d locally and k3s on the VPS.** k3d runs k3s inside Docker, so locally and on the VPS I run the same Kubernetes distribution at the same version. Traefik, the local-path storage and NetworkPolicy enforcement behave the same in both places. So if it works locally, the only things that change in production are the values files: domain, TLS, image registry and sizes. It also surfaced real problems early, like the volume-ownership bug, which would have appeared on the VPS too.

## 7. Trade-offs and what I'd do with more time

- **Single gateway replica.** The health loop, `list_changed` notifications, login rate limiter and chat-token signing key live in one process's memory. To run several replicas, I'd move them to shared storage such as Redis and use a shared signing key from a Secret.
- **Shared upstream credentials.** Upstreams see the gateway's credential, not which member is calling, so per-member identity exists only in the gateway's audit log. With more time, I'd pass per-user identity upstream (for example, OAuth per user).
- **No per-member tool permissions.** Every member sees every enabled tool. Tool-level permissions (for example, only admins can create products) are the next feature I'd add.
- **LLM quota.** The Gemini free tier ran out quickly during testing. In production, I'd use a paid tier with a fallback provider.
- **Operations.** Backups are manual and there are no metrics yet. I'd add scheduled backups and metrics for calls, errors and latency per server.
