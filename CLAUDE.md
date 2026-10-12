# Project: Team MCP Gateway on Kubernetes (Urumi FDE Round 1)

You are my pair programmer for a take-home assignment. Deadline: **26 Oct 2026, 11:59 PM IST**.
In Round 2 I must walk through this code live and defend every choice, so **I must understand everything you build**. Speed matters, but my understanding matters more.

## About me
- Strong: Python, FastAPI, Docker, SQL, LLM tool loops (LangGraph, Gemini).
- New to me: Kubernetes, Helm, the MCP protocol internals, React, WordPress/WooCommerce internals.
- For anything in the "new" list, explain the *why* before or right after writing it, in plain language.

## How we work (follow these every session)
1. Work **one phase at a time** (see Phases). Use plan mode at the start of each phase and show me the plan before writing code.
2. At the end of each phase: run the verification steps, tell me exactly what to run or click to check it myself, and **stop**. Do not start the next phase until I say so.
3. After each phase, append to `docs/learning-log.md`: what was built, the key concepts involved, the 3 questions an interviewer is most likely to ask about it, and short answers.
4. Prefer simple, boring, explainable code over clever code. No unnecessary abstractions or frameworks.
5. Never hardcode secrets. All credentials come from Kubernetes Secrets via env vars. Provide `.example` files for local secret values; real ones are gitignored.
6. Do **not** install any existing WooCommerce MCP server or plugin. I write that server (with you).
7. If a requirement below is ambiguous or you think a decision is wrong, say so and ask before deviating.
8. Commit after each working step with clear messages. Never commit secrets.
9. When unsure about a library's current API (especially the MCP Python SDK), check the installed version / official docs instead of guessing.

## What we're building (summary of the brief)
A WooCommerce store on Kubernetes plus a team MCP gateway:
1. **Store**: WordPress + WooCommerce + MariaDB, persistent storage, exposed via Ingress.
2. **WooCommerce MCP server** (written by us): MCP over Streamable HTTP, internal-only.
3. **Gateway + dashboard**: backend is an MCP *client* to every registered upstream and an MCP *server* exposing all their tools at one endpoint. React dashboard for admins and members.
4. **Chat agent**: chat in the dashboard that uses an LLM to call tools *through the gateway* as the logged-in user.

Everything runs in the cluster. Same Helm charts run locally (k3d) and on a VPS (k3s); differences only via values files.

## Tech decisions (already made — follow unless you raise a concern)

### Cluster and Helm
- Local: **k3d** (k3s in Docker). VPS: **k3s**. Both ship Traefik ingress and enforce NetworkPolicies, so local and prod behave the same.
- Helm only (no Kustomize). Two charts:
  - `charts/store` → namespace `store`: MariaDB StatefulSet + PVC, WordPress Deployment + PVC for wp-content, bootstrap Job, Ingress.
  - `charts/platform` → namespace `platform`: Postgres StatefulSet + PVC, gateway, dashboard, WooCommerce MCP server, notes MCP server, FX MCP server, Ingress, NetworkPolicies.
- Values files per chart: `values.yaml` (defaults), `values-local.yaml`, `values-prod.yaml`. Differences: hostnames, TLS, storage class, image repo/tag/pullPolicy, resource sizes, replica counts.
- Local domains: `shop.localtest.me` (storefront) and `app.localtest.me` (dashboard + API + MCP endpoint). `*.localtest.me` resolves to 127.0.0.1 with no /etc/hosts edits. Document this in README.
- Ingress routing on `app.<domain>`: `/api` and `/mcp` → gateway service, `/` → dashboard.
- Every workload: readiness + liveness probes, resource requests + limits. Run containers as non-root where the image allows it.
- Do not depend on Bitnami charts/images; use official upstream images (`wordpress`, `wordpress:cli`, `mariadb`, `postgres`) and our own templates so I can explain every resource.
- Local images: build with Docker, load into k3d with `k3d image import`. Prod: push to GHCR, set repo/tag in `values-prod.yaml`.
- Provide a `Makefile` (or `scripts/`) with: `cluster-up`, `build`, `load-images`, `deploy-local`, `deploy-prod`, `teardown`, `dod-check`.

### Store bootstrap
- A Helm post-install/post-upgrade Job using the `wordpress:cli` image that idempotently: installs WordPress, installs + activates WooCommerce, sets site URL to the Ingress host, sets pretty permalinks, sets timezone to Asia/Kolkata, sets currency to INR, enables Cash on Delivery, creates a few sample products, and registers a WooCommerce REST API key.
- REST API key approach: generate consumer key/secret into a Kubernetes Secret via Helm (random, preserved across upgrades with `lookup`), and have the Job insert them into WooCommerce's API keys table with `wp eval` (verify the exact hashing WooCommerce uses for consumer keys). This avoids giving the Job RBAC to write Secrets. The same Secret is copied/readable for the MCP server in the `platform` namespace (decide how: duplicate Secret via values, or document the choice).
- Everything must be safe to re-run.

### WooCommerce MCP server (`services/woo-mcp`)
- Python + the official MCP Python SDK, Streamable HTTP transport.
- Talks to the store via the **WooCommerce REST API v3** over the in-cluster service URL. Document why REST over WP-CLI (stable contract, no shell access to the WordPress pod, proper auth scoping).
- Known gotchas to handle explicitly: WooCommerce rejects HTTP Basic auth over plain HTTP, so use OAuth 1.0a one-legged signing for in-cluster HTTP (or justify an alternative); WordPress may redirect requests whose Host doesn't match the site URL, so set the Host header or configure accordingly.
- Tools (clear descriptions + JSON schemas): `list_products`, `create_product` (name, regular_price, optional description/stock), `list_orders` (filters: status, after/before dates, with a convenient "today" option in the store's timezone), `get_order`, `update_order_status`, plus `get_info` (returns server name/version — intentionally also exists on the notes server to demonstrate name collisions).
- No Ingress. ClusterIP only. NetworkPolicy: only gateway pods may reach it; it may only reach the store's WordPress service and DNS.

### The two other upstream servers (both ours, in-cluster, tiny)
- `services/notes-mcp`: team notes stored in Postgres (separate DB/schema from the gateway). Tools: `add_note`, `list_notes`, `search_notes`, `get_info`. **Requires a static bearer token** (from a Secret) so we demonstrate the gateway injecting upstream credentials.
- `services/fx-mcp`: currency conversion with static rates from a ConfigMap (no external network needed). Tools: `convert_currency`, `list_rates`. Requires a custom header credential (e.g. `X-API-Key`) to demonstrate the header credential type.
- Cross-server demo prompt: "List today's orders and convert the total to USD" (woo + fx), or "Summarize today's orders and save it as a note" (woo + notes).

### Gateway (`services/gateway`)
- Python, FastAPI, async SQLAlchemy + Alembic, Postgres.
- **Data model**: users (email, role admin|member, password hash, status), api_keys (user_id, prefix, sha256 hash, created_at, revoked_at, last_used_at), upstream_servers (slug, name, url, auth_type none|bearer|header, header_name, encrypted credential, enabled, status, last_error, last_checked_at), audit_log (user_id, channel api|chat, server_slug, tool, started_at, duration_ms, outcome success|error|timeout|denied, error_message).
- **API keys**: format `urk_<random 32+ bytes base64url>`; show once on creation; store only SHA-256 hash + short prefix for display. (High-entropy random keys make a fast hash appropriate; passwords use argon2/bcrypt.) Revocation takes effect on the very next call.
- **Upstream credentials**: encrypted at rest with Fernet, key from a Secret. Never returned by any API response (dashboard sees only "credential set: yes/no"). Never forwarded to clients.
- **Client tokens are never passed through to upstreams.** The gateway authenticates the client, then calls upstreams with the upstream's own credential.
- **MCP endpoint `/mcp`** (Streamable HTTP, auth via `Authorization: Bearer <api key or chat token>`):
  - `tools/list`: merged tools from all **enabled + reachable** upstreams, served from a per-upstream cache refreshed by a background health loop (every ~15s) so a dead upstream never blocks listing.
  - Tool naming: `<server_slug>__<tool_name>` (double underscore; slugs restricted to `[a-z0-9-]`). This makes collisions impossible and tells the model which server a tool belongs to. Document it.
  - `tools/call`: parse prefix → find upstream → check enabled → call with timeout (configurable, default 15s) → return result, or a clear MCP error (`upstream 'notes' timed out after 15s`, `upstream 'fx' is disabled`, etc.). Write an audit row for every call, including failures and denials.
  - Emit `tools/list_changed` notifications when the set of tools changes if the SDK supports it; otherwise document that clients must re-list.
  - Use the MCP SDK's low-level server so the tool list is dynamic per request.
- **Upstream connections**: one MCP client session per upstream, created lazily, recreated on failure; failures isolated per upstream.
- **Status**: Connected / Unreachable (with last error) / Disabled.
- **REST API `/api`** for the dashboard: auth (login/logout with httpOnly session cookie), users/invites, API keys (own keys for everyone; any user's keys for admins), upstream servers CRUD + test connection, audit log with filters + pagination, chat.
- **Roles**: admin manages servers and members; member can only manage own keys, use tools, use chat, and view their own audit entries (admins see all). Enforce on the server, not just in the UI.
- **Bootstrap**: on first start, create the admin user from a Secret and auto-register the WooCommerce MCP server (plus notes and fx) from values, so the dashboard shows Woo as the first server.
- **SSRF guard** when admins add a URL: block cloud metadata IPs (169.254.0.0/16), loopback, and link-local; allow cluster-internal service names only from a configurable allowlist. Document it.

### Chat agent (inside the gateway backend, module `chat/`)
- LLM: Gemini via the `google-genai` SDK; model name and API key from config/Secret. Keep a thin provider interface so another provider could be swapped in.
- The agent is an **MCP client to the gateway's own `/mcp` endpoint** (via the internal service URL), authenticated with a short-lived signed token minted for the logged-in user. So its calls go through the exact same auth, routing, and audit path, attributed to that user with channel `chat`.
- Tool loop: list tools → send to model → execute requested tool calls through the gateway → feed results back → repeat until a final answer or a max-steps limit (e.g. 8).
- The API returns the answer plus the list of tool calls made (name, args, short result/error) so the UI can show them.
- Treat tool outputs as untrusted data in the system prompt (prompt-injection mitigation; mention in the threat model).

### Dashboard (`services/dashboard`)
- React + Vite + TypeScript, served by nginx (non-root). Minimal clean UI; function over looks.
- Pages: Login; Servers (list with status badge, tools per server, add/edit/disable/remove/test — admin only); Members (invite, change role, list and revoke keys — admin only); My API keys (create → show once with copy button, revoke) plus a "Connect your MCP client" snippet showing the gateway URL and example Claude Code / Cursor config; Audit log (table with filters); Chat (messages + expandable tool-call chips).
- Invites: admin creates a member and gets a one-time invite link to set a password (no email sending needed).

### Security and networking
- NetworkPolicies: default-deny ingress in both namespaces, then explicit allows (Ingress controller → dashboard/gateway/wordpress; gateway → MCP servers and Postgres; woo-mcp → wordpress; wordpress → mariadb; DNS egress).
- Public: storefront + WP admin, dashboard, `/api`, `/mcp`. Internal-only: all MCP servers, databases.
- Secrets: generated by Helm (random, preserved via `lookup`) or supplied via `--set`/a gitignored values file for prod. Document the prod strategy.

## Phases
Do these in order. Each has a verification gate.

**Phase 0: Scaffold.** Repo structure, Makefile, k3d cluster with ports 80/443 mapped, `.gitignore`, README skeleton.
Verify: `kubectl get nodes` ready; Traefik running.

**Phase 1: Store.** `charts/store` with MariaDB, WordPress, PVCs, bootstrap Job, Ingress, probes, limits.
Verify: open `http://shop.localtest.me`, add to cart, checkout with COD, see the order in WP admin. Delete the WordPress pod and confirm data survives.

**Phase 2: WooCommerce MCP server.** Service + Dockerfile + chart templates + NetworkPolicy + unit tests (mock the REST API).
Verify: port-forward and test all tools with MCP Inspector; create a product and see it on the storefront.

**Phase 3: Notes + FX MCP servers.**
Verify: each works in MCP Inspector with its credential and fails without it.

**Phase 4: Gateway core** (DB, migrations, API keys, upstream registry, MCP aggregation, routing, collisions, timeouts, health loop, audit, SSRF guard) + pytest tests for: prefixing/routing, collision handling, timeout on a slow fake upstream, dead upstream not breaking list, revoked key rejected, credentials never in responses.
Verify: connect MCP Inspector to `http://app.localtest.me/mcp` with an API key; see all tools; call one from each server.

**Phase 5: Dashboard + REST API + auth/roles.**
Verify: admin can do all admin user stories; member cannot (check via the API directly, not just the UI).

**Phase 6: Chat agent.**
Verify: "create a product called X priced 499" works; after a manual order, "what orders came in today?" returns it; audit shows the calls under the right user with channel `chat`.

**Phase 7: Hardening + Definition-of-Done script.** NetworkPolicies verified (show a blocked curl from a non-gateway pod to woo-mcp), non-root where possible, `scripts/dod-check.sh` that exercises as much of the DoD flow as can be automated (two users, revoke, disable, kill pod, audit check).

**Phase 8: Production values + docs.** `values-prod.yaml` for k3s on a VPS (real domain, cert-manager TLS optional, storage class, image registry), upgrade/rollback instructions with `helm upgrade` / `helm rollback`.
Docs (draft them, but mark the reasoning sections with `TODO(Rushil): rewrite in my words` — I will write those myself):
- `README.md`: local setup, VPS setup, placing an order, connecting Claude Code/Cursor/MCP Inspector, using chat, local domain approach.
- `docs/design.md`: architecture, aggregation/routing/failure handling, naming scheme, local vs prod changes, tradeoffs.
- `docs/threat-model.md`: shared upstream credentials (what a member can do with creds they never see), why client tokens aren't passed upstream, malicious tool descriptions manipulating the model, what one member can do to another, malicious/compromised upstream server.
- `docs/standout.md`: list of extras implemented and where (encrypted creds, SSRF guard, NetworkPolicies, non-root, etc.).

## Definition of done (the demo must show all of this, with two users)
- Storefront checkout with COD; order visible in WooCommerce admin.
- User A (chat): creates a product → appears on storefront; manual order placed → "what orders came in today?" returns it.
- User B (Claude Code / Cursor / MCP Inspector, only gateway URL + their key): sees tools from every enabled server; asks the same orders question; runs one prompt needing two different upstreams.
- Revoke B's key → next call rejected.
- Disable one upstream → its tools disappear from chat and the external client.
- Kill one upstream pod → others keep working; dashboard shows it Unreachable.
- Every call above is in the audit log, attributed to the right user.

## Out of scope unless I ask
The multi-store provisioning bonus, OAuth on the gateway, multiple gateway replicas, hosted-by-image servers. A working core beats an unfinished bonus.
