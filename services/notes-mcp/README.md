# notes-mcp: team notes MCP server

Shared team notes stored in Postgres, exposed as MCP tools over Streamable HTTP (`POST /mcp`, stateless,
JSON responses). **Requires `Authorization: Bearer <token>`**, the "bearer" upstream credential type
that the gateway injects. Internal only: ClusterIP, no Ingress, NetworkPolicy (gateway in, Postgres out).

## Tools

| Tool | What it does |
|---|---|
| `get_info` | Server name/version. (woo-mcp has a tool with the same name; the gateway's prefixing keeps them apart.) |
| `add_note` | Save a note: `text`, optional `title`. Returns id + timestamp. |
| `list_notes` | Newest first; `limit` (1-100), `offset`. |
| `search_notes` | Case-insensitive substring match on title and text. `%` and `_` are matched literally, not as wildcards. |

## Configuration

| Var | Notes |
|---|---|
| `NOTES_TOKEN` | Bearer token clients must send (Secret `notes-mcp-auth`). |
| `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` | Standard libpq variables, read by asyncpg directly. User `notes` owns only database `notes`. |
| `DB_WAIT_SECONDS` | How long to wait for Postgres at startup (default 60). |
| `MCP_ALLOWED_HOSTS` | Accepted `Host` headers (DNS-rebinding protection). |

## Design notes
- **Auth** (`notes_mcp/auth.py`) is a pure-ASGI wrapper that compares the header in constant time
  (`hmac.compare_digest`). It returns 401 with `WWW-Authenticate: Bearer`. `/healthz` is open for probes.
  The same file lives in fx-mcp: duplicating ~40 lines is simpler than a shared package across Docker build contexts.
- **Startup**: the MCP server's lifespan (entered once per process) opens the asyncpg pool, retrying while
  Postgres starts, and runs `CREATE TABLE IF NOT EXISTS`. Uvicorn only starts listening after that, so the
  pod isn't Ready until the database is.
- **DB failures** become a tool error, `notes database unavailable (...)`, without SQL or connection details.
- **What notes-mcp can't know**: which team member is calling. It only ever sees the gateway's token.
  Per-user attribution lives in the gateway's audit log.

## Develop
`make test` runs the tests against a real throwaway Postgres container (`TEST_DATABASE_URL`).
