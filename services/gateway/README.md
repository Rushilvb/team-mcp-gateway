# gateway: the team MCP gateway

One MCP endpoint (`/mcp`, Streamable HTTP) that exposes the tools of every registered upstream MCP server.
Each team member authenticates with **their own API key**. The gateway calls each upstream with **that
upstream's own credential**, applies timeouts, isolates failures, and writes an audit row for every tool call.
The REST API for the dashboard (`/api`) arrives in Phase 5, and the chat agent in Phase 6.

```
client (Claude Code / Cursor / Inspector)            gateway (namespace platform)                   upstreams
  Authorization: Bearer urk_...  ──► Traefik ──► McpAuth ──► MCP server ──► Registry ──► woo-mcp    (no auth)
                                     /mcp         key → user  list / call    pool per    ──► notes-mcp  (Bearer <token>)
                                                  401/denied  audit row      upstream    ──► fx-mcp     (X-API-Key: <key>)
```

## Request flow
1. **`auth.py`** (pure ASGI, before the MCP SDK sees anything): hashes the bearer key (SHA-256) and looks it
   up on *every* request (no cache, so revocation applies to the very next call). An active key of an active
   user puts a `Principal` into `scope["state"]`; anything else gets **401**. If a known-but-revoked key
   attempted a `tools/call`, a `denied` audit row records who tried what.
2. **`mcp_server.py`** (the SDK's low-level `Server`, so the tool list is computed per request):
   - `tools/list` returns the merged tool cache; it never calls an upstream.
   - `tools/call` splits the prefix, routes to the upstream, then audits the result.
3. **`upstreams.py`**:
   - `Registry` loads upstreams from the DB and health-checks them **concurrently** every 15s. Each check runs `tools/list` with a 5s timeout, then updates the tool cache and the status (`connected` / `unreachable` + last error / `disabled`).
   - `UpstreamConnection` keeps one long-lived HTTP pool per upstream that carries the upstream credential. It is created lazily, rebuilt after a transport failure, and the SSRF check re-runs on every rebuild.
   - Each call is a stateless MCP request with a 15s timeout.

## Tool naming: `<server_slug>__<tool>`
Slugs match `^[a-z0-9-]+$`, so they never contain `_` and splitting on the **first** `__` is unambiguous.
Both woo and notes have `get_info`, which become `woo__get_info` and `notes__get_info`. The prefix makes
collisions impossible, and it tells the model (and the audit log) which server a tool runs on.

## Failure handling
| Situation | What the client sees | Audit outcome |
|---|---|---|
| Upstream down | Its tools disappear within one health interval; others unaffected. A direct call → `upstream 'fx' is unreachable: ConnectError: ...` | `error` |
| Upstream slow | `upstream 'notes' timed out after 15s` | `timeout` |
| Upstream disabled by an admin | Tools disappear; a direct call → `upstream 'fx' is disabled` | `denied` |
| Tool returned `isError` | The upstream's result, unchanged | `error` |
| Missing / unknown key | HTTP 401 | none (no user to attribute to) |
| Revoked key / disabled user | HTTP 401 | `denied` (if it was a `tools/call`) |

**`tools/list_changed`.** When the health loop sees the tool set change, it publishes an event.
- **Modern clients** (protocol `2026-07-28`) that opened `subscriptions/listen` receive `notifications/tools/list_changed`.
- **Older clients** (`2025-11-25` and earlier) have no server→client stream in stateless mode, so they see the change when they next call `tools/list`.

## Credentials and secrets
- **Member API keys:** `urk_` + 32 random bytes (base64url). We store only the SHA-256 hash + a 12-char prefix for display, and show the full key once. A fast hash is fine for 256-bit random keys; passwords use argon2id.
- **Upstream credentials:** encrypted at rest with Fernet. The key is in the `gateway-fernet` Secret, not the DB.
  - They are only decrypted in memory, to build the upstream's HTTP headers.
  - They never appear in responses or error messages; errors are scrubbed as defense in depth.
- **Client keys are never forwarded upstream.** Upstreams only ever see the gateway's credential for them (tested).

## SSRF guard (`ssrf.py`)
Runs when an upstream is registered and before every new connection:
- **Request shape:** only `http`/`https` URLs, with no `user:pass@` part.
- **Cluster-internal names** (no dot, or ending in `.svc` or `.cluster.local`) are allowed only if they're in `SSRF_ALLOWED_HOSTS`.
- **Every resolved address is checked:**
  - Loopback, link-local (including the cloud metadata address `169.254.169.254`), unspecified and multicast addresses are always refused.
  - Private and reserved ranges, which include pod and Service IPs, are refused unless the hostname is allowlisted.

The gateway's NetworkPolicy adds a second layer: internet egress is allowed, but private ranges and link-local are excluded.

## Data model (Alembic migration `0001_initial`)
- **Tables:** `users`, `api_keys`, `upstream_servers` and `audit_log`, as specified in CLAUDE.md, with CHECK constraints for the enum-like columns.
- **Migrations:** an initContainer runs `alembic upgrade head` before the app starts. A test (`alembic check`) fails if the models and migrations drift apart.

## Operating (until the dashboard exists)
```bash
make gateway-admin                      # bootstrap admin email + password
make gateway-user EMAIL=b@example.com   # ROLE=admin for admins
make gateway-key EMAIL=b@example.com    # prints urk_... once
make gateway-keys / gateway-revoke PREFIX=urk_xxxxxxxx
make gateway-upstreams                  # status + last error per upstream
make gateway-audit                      # latest audit rows
```

## Tests
`make test` runs 50 gateway tests against a real Postgres. They use small fake upstream MCP servers on
localhost (a no-auth one, a bearer one, a slow header one and a dead one), the real gateway over HTTP, and a
real MCP client. Coverage includes prefixing/routing, collisions, timeouts, dead and dying upstreams,
disabling, `list_changed`, revoked keys, audit attribution, credentials never leaking, the SSRF rules, and
migrations matching the models.
