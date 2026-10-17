# gateway: the team MCP gateway

One MCP endpoint (`/mcp`, Streamable HTTP) that exposes the tools of every registered upstream MCP server.
Each team member authenticates with **their own API key**. The gateway calls each upstream with **that
upstream's own credential**, applies timeouts, isolates failures, and writes an audit row for every tool call.
It also serves the REST API for the dashboard (`/api`, see below). The chat agent arrives in Phase 6.

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

## Protocol compatibility
Upstreams may speak either MCP era:
- **2026-07-28 (modern):** stateless, self-contained requests. Our woo, notes and fx servers use this.
- **Pre-2026 (legacy):** an `initialize` handshake and an `Mcp-Session-Id`, which is what most third-party servers still use.

On first contact the client probes `server/discover`. A legacy server rejects the probe, and the client falls back to the handshake.
`UpstreamConnection` **remembers the era it found**, so later calls and health checks go straight to the right path instead of re-probing each time.
The connection forgets it on reset, so an upgraded server is re-detected.
This is tested with a session-based fake upstream that rejects modern requests the way an old server does. The test asserts exactly one probe.

## Bootstrap
On start the gateway creates the admin from the `gateway-admin` Secret if no user has that email.
**Upstreams are seeded only into an empty registry** (the very first start). From then on the database
is the source of truth: a server an admin removed or edited stays that way after restarts and upgrades (tested).
Deleting *every* upstream and restarting re-seeds the defaults; that's the only "factory reset".

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

## REST API (`/api`, for the dashboard)
FastAPI routers in `gateway/api/`. Request and response shapes live in `api/schemas.py`. Those Pydantic models decide
exactly which fields leave the server, so no hash or credential ever does.

| Endpoint | Who |
|---|---|
| `POST /api/auth/login` · `POST /api/auth/logout` · `GET /api/auth/me` | anyone / logged in |
| `GET /api/invites/{token}` · `POST /api/invites/{token}/accept` | holder of the invite link |
| `GET/POST /api/users` · `PATCH /api/users/{id}` · `POST /api/users/{id}/invite` (new link, old one dies) · `GET/POST /api/users/{id}/keys` (list / issue a key for a member) | admin |
| `GET/POST /api/me/keys` · `DELETE /api/keys/{id}` (own; admins: any) | logged in |
| `GET /api/servers` | logged in (read-only) |
| `POST /api/servers` · `PATCH/DELETE /api/servers/{slug}` · `POST /api/servers/{slug}/test` | admin |
| `GET /api/audit` (filters + pagination; members are forced to their own rows) | logged in |

- **Sessions:**
  - Login creates a random token. The browser keeps it in an `httpOnly; SameSite=Strict` cookie (`Secure` in prod); the DB keeps only its SHA-256 (`user_sessions`, migration `0002`), and sessions expire after 12h.
  - The user is re-read on every request, so logout, disabling and role changes apply immediately.
- **Login hardening:**
  - The same error for a wrong password and an unknown email.
  - Unknown emails still run one argon2 check against a dummy hash, so timing doesn't reveal accounts.
  - 5 failures per email per 15 min → 429.
- **CSRF:** `SameSite=Strict`, plus every POST/PATCH/DELETE that carries an `Origin` header must match `PUBLIC_URL`.
- **Roles:** enforced by FastAPI dependencies (`deps.current_user`, `deps.require_admin`).
  - Members only manage their own keys and see their own audit rows; another user's key ID answers 404, not 403.
  - The last active admin can't be demoted or disabled.
- **Invites:** a one-time link (random token, SHA-256 stored, 7 days, single use). Accepting sets an argon2 password and logs in.
- **Servers:**
  - Credentials are write-only (`credential_set: true/false`; on edit, omitted = keep, `null` = clear).
  - The slug is immutable.
  - URLs go through the SSRF guard.
  - Every change awaits `registry.refresh()` before responding, so tools appear or disappear for every client immediately. A lock keeps that from interleaving with the health loop.
- **Proof:** `make api-role-check` logs in as a throwaway member through the real Ingress and asserts 403 on every admin endpoint. `tests/test_api.py` covers the same and more.

## Requirements for later phases (recorded so they aren't forgotten)
- **Phase 5 (done):** enable/disable/edit/remove refresh the tool cache before the API responds. Tested with a
  60s health interval, and verified live: fx tools gone 0.06s after disabling.
- **Phase 6:** the chat agent calls the gateway's own MCP endpoint at **`http://127.0.0.1:8000/mcp`**,
  which is the same pod over loopback.
  - `127.0.0.1:*` and `localhost:*` are already in `MCP_ALLOWED_HOSTS` (verified live: HTTP 200).
  - The Service name `gateway.platform.svc.cluster.local` is deliberately unusable from inside the pod, because the gateway's NetworkPolicy only admits Traefik (verified: connection refused).

## Data model (Alembic migrations `0001_initial`, `0002_sessions_invites`)
- **Tables:** `users`, `api_keys`, `upstream_servers` and `audit_log`, as specified in CLAUDE.md, with CHECK constraints for the enum-like columns.
- **Migrations:** an initContainer runs `alembic upgrade head` before the app starts. A test (`alembic check`) fails if the models and migrations drift apart.

## Operating from the command line (the dashboard does the same)
```bash
make gateway-admin                      # bootstrap admin email + password
make gateway-user EMAIL=b@example.com   # ROLE=admin for admins
make gateway-key EMAIL=b@example.com    # prints urk_... once
make gateway-keys / gateway-revoke PREFIX=urk_xxxxxxxx
make gateway-upstreams                  # status + last error per upstream
make gateway-audit                      # latest audit rows
```

## Tests
`make test` runs 75 gateway tests (MCP + REST API) against a real Postgres. They use small fake upstream MCP servers on
localhost (no-auth, bearer, slow header, session-based legacy, and dead), the real gateway over HTTP, and a
real MCP client. Coverage includes prefixing/routing, collisions, legacy-protocol upstreams, timeouts, dead and dying upstreams,
disabling, bootstrap-once, `list_changed`, Host-header protection, revoked keys, audit attribution, credentials never leaking, the SSRF rules, and
migrations matching the models.
