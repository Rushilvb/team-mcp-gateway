# dashboard: React UI for the gateway

A small single-page app (React 19 + TypeScript, built by Vite, styled by Pico CSS) served as static
files by nginx. It talks only to the gateway's REST API at `/api` on the **same host**
(`app.localtest.me`), so the browser sends the session cookie automatically and no CORS is needed.

## How it fits together
```
browser ── GET /servers ──► Traefik ──► dashboard (nginx) ──► index.html + /assets/*.js   (the app)
browser ── fetch /api/... ─► Traefik ──► gateway (FastAPI) ──► Postgres / registry        (the data)
```
nginx answers every unknown path with `index.html` (the "SPA fallback"), and React Router then shows the
page for that URL. The dashboard pod has no secrets and no network access of its own (NetworkPolicy: egress none).

## Where things are (`src/`)
| File | What it does |
|---|---|
| `main.tsx` | Entry point: loads the CSS and renders `<App/>` into `index.html`'s `#root`. |
| `App.tsx` | Routes (URL → page) and the nav bar. `RequireLogin` sends you to `/login` if there's no session; admin-only pages redirect members. |
| `auth.tsx` | React **context**: one shared "current user" for all pages. On load it asks `/api/auth/me`, because the cookie is httpOnly and JS can't read it. |
| `api.ts` | **The only file that calls the server.** A `fetch` wrapper that turns FastAPI errors into readable messages, plus one typed function per endpoint. |
| `components/ui.tsx` | Status badge, copy-to-clipboard box (with a fallback, because `navigator.clipboard` needs HTTPS), code block. |
| `pages/*.tsx` | One file per page: `Login`, `AcceptInvite`, `Servers`, `Members`, `ApiKeys` (+ "Connect your MCP client"), `Audit`, `Chat` (Phase 6). |

**Data flow of one action**, e.g. disabling a server:
1. A click in `Servers.tsx` calls `api.updateServer("fx", {enabled: false})`.
2. That becomes `PATCH /api/servers/fx` with the session cookie.
3. The gateway checks you're an admin, saves, and refreshes its tool list.
4. It returns the updated server; the page re-fetches the list, and React re-renders with the new badge.

**React in one paragraph.** A component is a function that returns HTML-like JSX. `useState` holds a value
that, when changed, re-renders the component. `useEffect(fn, [deps])` runs `fn` after rendering, which is
where we fetch data. That's all these pages use: no Redux or other state library.

**The UI hides admin buttons from members only for convenience.** Security is enforced by the gateway on every request
(proved by `make api-role-check` and the API tests).

## Security
- **CSP and headers** (`nginx.conf`):
  - `Content-Security-Policy: default-src 'self'` allows no inline scripts and nothing from other origins, so an injected `<script>` can't run. Inline styles are allowed (`style-src 'self' 'unsafe-inline'`) so `style="..."` or a library's `<style>` never breaks the page; CSS can't execute code, so that's a small, deliberate trade-off.
  - `frame-ancestors 'none'` and `X-Frame-Options` prevent clickjacking.
  - `nosniff` and `no-referrer` are also set.
- **No secrets in the browser:**
  - The session cookie is `httpOnly` + `SameSite=Strict`, so JS can't steal it and other sites can't use it.
  - A newly created API key is shown once from memory and never stored by the UI.
  - Upstream credentials are write-only.

## Build and run
- `make build` uses the multi-stage `Dockerfile`:
  1. Node runs `npm ci` (exact versions from `package-lock.json`).
  2. `tsc --noEmit` type-checks the code.
  3. `vite build` writes `dist/`.
  4. Only `dist/` is copied into `nginx-unprivileged` (non-root, port 8080). No Node.js in production.
- `make test` runs the type-checker.
- Local UI development (optional) runs against the cluster's gateway:
  ```bash
  cd services/dashboard && npm install && npm run dev   # http://localhost:5173, /api proxied to app.localtest.me
  ```
