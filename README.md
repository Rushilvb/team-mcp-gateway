# Team MCP Gateway on Kubernetes

A WooCommerce store on Kubernetes, plus a team MCP gateway: admins register MCP servers once,
and every team member uses all of their tools through one MCP endpoint and a chat agent.
The same Helm charts run locally (k3d) and on a VPS (k3s); only values files differ.

> Status: complete (Phases 0-8). Runs locally on k3d and on a k3s VPS with the same charts; `make dod-check` passes.

## Repository layout

| Path | What lives there |
|---|---|
| `k3d/cluster.yaml` | Local cluster definition (k3s version, port mappings) |
| `charts/store` | Helm chart: MariaDB, WordPress + WooCommerce, bootstrap Job, Ingress (namespace `store`) |
| `charts/platform` | Helm chart: Postgres, gateway, dashboard, MCP servers, NetworkPolicies (namespace `platform`) |
| `services/woo-mcp` | Our WooCommerce MCP server (Python, Streamable HTTP) |
| `services/notes-mcp` | Team notes MCP server (bearer-token protected) |
| `services/fx-mcp` | Currency conversion MCP server (`X-API-Key` protected) |
| `services/gateway` | FastAPI backend: MCP aggregator, REST API, chat agent |
| `services/dashboard` | React + Vite dashboard |
| `scripts/` | Helper scripts (prereq check, DoD check) |
| `docs/` | Design, threat model, learning log |

## Prerequisites (Windows + WSL2, or Linux/macOS)

- Docker (Docker Desktop on Windows with **WSL integration enabled** for your distro; give it at least 6 GB RAM)
- [k3d](https://k3d.io), [kubectl](https://kubernetes.io/docs/tasks/tools/), [Helm 3](https://helm.sh/docs/intro/install/), `make`

In WSL Ubuntu you can install everything except Docker with:

```bash
bash scripts/install-tools-wsl.sh
make prereqs
```

## Local quickstart

```bash
make cluster-up     # creates the k3d cluster "urumi" with ports 80/443 mapped to Traefik
make status         # nodes + Traefik
make test           # all service tests + dashboard type-check (run inside Docker)
make build load-images   # build our images and import them into k3d (no registry needed)
make deploy-local   # store + platform charts (first run ~5 min: image pulls + WooCommerce download)
make store-admin    # WordPress admin URL + password
make teardown       # deletes the cluster
```

Image tags are a hash of each service's source files, so `make build load-images deploy-local`
rolls exactly the services whose code changed.

Run `make help` for all targets.

## Local domains

We use `*.localtest.me`, a public DNS wildcard that resolves every subdomain to `127.0.0.1`.
No `/etc/hosts` or Windows `hosts` edits are needed.

| URL | Serves |
|---|---|
| `http://shop.localtest.me` | WooCommerce storefront and `/wp-admin` |
| `http://app.localtest.me` | Dashboard (`/`), REST API (`/api`), MCP endpoint (`/mcp`) |

Request path: browser → `127.0.0.1:80` → k3d loadbalancer container → Traefik (ingress controller) → Kubernetes Service → pod.

Troubleshooting: if `make cluster-up` reports that port 80 or 443 is already allocated, another
Windows program holds it. Find it with `netstat -ano | findstr :80` in PowerShell.

## Placing an order on the storefront

`make deploy-local` installs the `store` chart into namespace `store`. A bootstrap Job (Helm
post-install/upgrade hook) then installs WordPress and WooCommerce and configures the shop: INR,
Asia/Kolkata timezone, Cash on Delivery, three sample products, and a REST API key for our MCP server.
It is safe to re-run; `make store-logs` shows what it did.

1. Open `http://shop.localtest.me`, add a product to the cart, and go to checkout.
2. Fill in any billing details and choose **Cash on delivery**. Place the order.
3. Run `make store-admin` to get the admin password. Log in at `http://shop.localtest.me/wp-admin`
   and open **WooCommerce → Orders**. The order is there with status *Processing*.

Data lives on two PersistentVolumeClaims (MariaDB data, WordPress `wp-content`). Deleting either
pod (`kubectl -n store delete pod ...`) loses nothing. All passwords and the API key are generated
by Helm on first install and kept across upgrades. Nothing secret is in git.

## Testing an upstream MCP server directly (development only)

The MCP servers have no Ingress: in normal use only the gateway can reach them. To poke one directly,
port-forward it (port-forward goes through the kubelet, so NetworkPolicies don't apply):

```bash
kubectl -n platform port-forward svc/woo-mcp 8000:8000
```

Then run MCP Inspector (`npx @modelcontextprotocol/inspector@2.8.0`). Choose transport **Streamable HTTP**
and URL `http://localhost:8000/mcp`. See [services/woo-mcp/README.md](services/woo-mcp/README.md) for the tools.

notes-mcp and fx-mcp require a credential. `make mcp-tokens` prints both. Add it as a custom header in Inspector:

| Server | Port-forward | Header |
|---|---|---|
| notes-mcp ([README](services/notes-mcp/README.md)) | `kubectl -n platform port-forward svc/notes-mcp 8001:8000` | `Authorization: Bearer <token>` |
| fx-mcp ([README](services/fx-mcp/README.md)) | `kubectl -n platform port-forward svc/fx-mcp 8002:8000` | `X-API-Key: <key>` |

Without the header, both answer `401`. In normal use only the gateway holds these credentials.

## Using the dashboard

Open `http://app.localtest.me`. The first admin is created from a Kubernetes Secret on first start:

```bash
make gateway-admin     # prints the admin email + password
```

| Page | Who | What |
|---|---|---|
| **Servers** | everyone (admins edit) | Status of each upstream (Connected / Unreachable + error / Disabled) and its tools. Admins can add, edit, enable/disable, test and remove servers. Changes apply to every client immediately. |
| **API keys** | everyone | Create keys (shown once), revoke them, and copy-paste setup for Claude Code / Cursor / Inspector. |
| **Audit log** | everyone (members: own calls) | Every tool call, filterable by user, server, outcome, channel and date. |
| **Members** | admins | Invite people (one-time link, no email needed; re-issue a lost/expired link), change roles, disable accounts, issue a key on a member's behalf, revoke anyone's keys. |
| **Chat** | everyone | Ask the assistant; it uses the tools as you (audited, channel `chat`). |

**Inviting a team member:**
1. As admin, go to Members, enter the person's email, and click Invite.
2. Copy the one-time link (valid 7 days) and send it to them.
3. They open it, set a password, and land on their API keys page.

Roles are enforced by the gateway, not just hidden in the UI. `make api-role-check` proves it via the API.

## Connecting Claude Code / Cursor / MCP Inspector

All you need is the gateway URL, `http://app.localtest.me/mcp`, and **your own API key**. Create one on
the dashboard's **API keys** page, which also shows ready-to-paste snippets. In scripts you can use the CLI
instead (`make gateway-user EMAIL=... && make gateway-key EMAIL=...`).

Tools appear as `<server>__<tool>`, e.g. `woo__list_orders`, `notes__add_note`, `fx__convert_currency`.

- **MCP Inspector**: `npx @modelcontextprotocol/inspector@2.8.0` → transport *Streamable HTTP*, URL above,
  custom header `Authorization: Bearer urk_...`.
- **Claude Code**: `claude mcp add --transport http team-gateway http://app.localtest.me/mcp --header "Authorization: Bearer urk_..."`
- **Cursor** (`~/.cursor/mcp.json`):
  ```json
  { "mcpServers": { "team-gateway": { "url": "http://app.localtest.me/mcp",
                                      "headers": { "Authorization": "Bearer urk_..." } } } }
  ```

Revoking a key (dashboard, or `make gateway-revoke PREFIX=urk_xxxxxxxx`) takes effect on the next request.
Every call is recorded in the audit log. Details: [services/gateway/README.md](services/gateway/README.md).

## Using the chat agent

The dashboard's **Chat** page is an assistant (Gemini) that uses the team's tools on your behalf. It
calls them through the gateway's own `/mcp` as you, so every call is in the audit log under your name with
channel `chat`. Under each answer, expandable chips show the exact tool calls, arguments and results.

One-time setup (the key is stored as a Kubernetes Secret, never in git or Helm values):

```bash
export GEMINI_API_KEY=...     # from Google AI Studio
make llm-secret               # creates the gateway-llm Secret and restarts the gateway
make chat-check PROMPT="What orders came in today?"   # optional: one prompt via the real API
```

Try: "Create a product called USB-C Charging Cable priced 499", "What orders came in today?",
"List today's orders and convert the total to USD", "Summarize today's orders and save it as a note".
The model defaults to `gemini-3.6-flash` (`gateway.chat.model` in values). Without a key, Chat says
"not configured" and everything else works normally.

## Checking everything

| Command | What it proves |
|---|---|
| `make test` | Unit/integration tests of every service (real Postgres, fake upstreams, scripted LLM) + dashboard type-check |
| `make dod-check` | The whole Definition of Done, live (see below) |
| `make netpol-check` | NetworkPolicies, with real connections from throwaway pods (e.g. a random pod can't reach woo-mcp) |
| `make security-check` | Every container's user/capabilities/seccomp, and the API server's verdict against the "restricted" Pod Security Standard |
| `make api-role-check` | A member is refused every admin endpoint (via the real Ingress) |

`make dod-check` runs the CLAUDE.md Definition of Done end to end:
- A storefront checkout with Cash on Delivery.
- Two users invited and onboarded.
- User A uses chat: creates a product (it shows up on the storefront) and asks for today's orders.
- User B uses only the gateway URL + an API key: sees every server's tools, lists today's orders, and converts their total to USD (woo + fx).
- B's key is revoked → rejected.
- fx disabled → its tools vanish for MCP clients and the chat agent.
- The notes pod is deleted → Kubernetes replaces it and it works again on its own (self-healing).
- notes is then scaled to 0 → shown Unreachable while the others keep working.
- Every call is in the audit log under the right user and channel.

It prints a PASS/FAIL checklist and restores what it changes. Chat steps need the LLM key (`make llm-secret`). On Gemini's free tier, the check waits out the per-minute rate limit when needed.
What's locked down and why: [docs/hardening.md](docs/hardening.md).

### Clean data for a demo recording

Every `dod-check` run adds users, keys, orders, products and audit rows. To start a recording from a clean slate:

```bash
make reset-demo        # asks you to type "reset"; YES=1 skips the prompt
```

**Wipes:** all users except the bootstrap admin, all API keys and login sessions, the audit log, all team notes, all WooCommerce orders, and all products except the three samples.

**Keeps:** the admin login, the LLM key Secret (and every other Secret), the upstream servers (all re-enabled), and the store's settings.

It refuses to run unless kubectl points at the local `k3d-urumi` cluster.

## Deploying to a VPS (k3s)

The **same charts** run on a single k3s server. Only `values-prod.yaml` differs: real hostnames, HTTPS,
secure cookies, images from GHCR, bigger volumes and resources. Commands run from WSL in the repo root.
`KCTX` names the VPS's kube context, and every prod command requires it, so nothing ever lands on the
wrong cluster by accident.

**1. Server.** Ubuntu 24.04 LTS, 2 vCPU / 4 GB RAM or more. In the firewall, open only **22, 80, 443**.
Keep 6443 (the Kubernetes API) closed to the internet; we reach it through SSH.

**2. Install k3s, the same version as local:**
```bash
ssh root@<vps-ip> 'curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.36.5+k3s1 sh -'
```
k3s includes Traefik, local-path storage and NetworkPolicy enforcement, exactly like k3d locally.

**3. Kubeconfig on your machine, via an SSH tunnel to the API:**
```bash
scp root@<vps-ip>:/etc/rancher/k3s/k3s.yaml ~/.kube/vps.yaml
sed -i 's/: default$/: urumi-vps/' ~/.kube/vps.yaml        # name the cluster/context/user "urumi-vps"
ssh -fN -L 6443:127.0.0.1:6443 root@<vps-ip>               # keep this tunnel open while deploying
export KUBECONFIG=~/.kube/config:~/.kube/vps.yaml
kubectl --context urumi-vps get nodes                       # -> 1 node Ready
```

**4. DNS.** Create A records for `shop.<domain>` and `app.<domain>` pointing at the VPS IP.

**5. Images to GHCR.** Log in with a GitHub token that has `write:packages`, then build and push.
The tags are content hashes, so they're immutable.
```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
make build push REGISTRY=ghcr.io/<github-user>
```
Then make the five packages public (GitHub → Packages → Settings); they contain no secrets. Alternatively,
keep them private, create a `ghcr-pull` docker-registry Secret in `platform`, and set `imagePullSecrets` in
`charts/platform/values-prod.yaml`.

**6. TLS (optional, recommended).** This installs cert-manager v1.21.2 and a Let's Encrypt issuer. The Ingresses already
ask for certificates (`shop-tls`, `app-tls`), which arrive within a minute or two of the first deploy.
```bash
make cert-manager KCTX=urumi-vps EMAIL=you@example.org
```

**7. Namespaces first, with their Pod Security levels.** `platform` enforces `restricted`. `store` enforces `baseline`,
and warns on anything below `restricted`. They must exist *before* Helm installs anything, so the very first pod is
already checked. `deploy-prod` runs this automatically too, but it's the step that matters, so run it explicitly once:
```bash
make namespaces KCTX=urumi-vps
kubectl --context urumi-vps get ns store platform --show-labels   # pod-security.kubernetes.io/... labels
```

**8. Deploy both charts.** `--atomic`: if anything fails to become Ready, Helm rolls back by itself.
```bash
make deploy-prod KCTX=urumi-vps DOMAIN=<domain> REGISTRY=ghcr.io/<github-user>
```
This takes about 5 minutes the first time, while the bootstrap Job installs WooCommerce. All passwords and keys are generated
in the cluster and kept across upgrades.

**9. LLM key and first login:**
```bash
export GEMINI_API_KEY=...
make llm-secret KCTX=urumi-vps
make gateway-admin KCTX=urumi-vps        # admin email + password
```
Open `https://app.<domain>`, log in, and check that Servers shows woo/notes/fx Connected. The store is at `https://shop.<domain>`.

**10. Verify on the VPS.** The checks use the current context, so switch to it first:
```bash
kubectl config use-context urumi-vps
make netpol-check
APP_URL=https://app.<domain> SHOP_URL=https://shop.<domain> make dod-check
kubectl config use-context k3d-urumi     # back to local
```

### Upgrades and rollbacks
- **Upgrade:** after a code change, run `make build push REGISTRY=…`, then `make deploy-prod …`. Only services whose code
  changed get a new tag, so only they roll. The gateway's migration initContainer applies any new Alembic migration
  before the new version starts.
- **History:** `helm --kube-context urumi-vps history platform -n platform` (same for `store`).
- **Rollback:** `helm --kube-context urumi-vps rollback platform <revision> -n platform`. The old revision's
  image tags still exist in GHCR (immutable), so the pods come back exactly as they were.
- **Caveat: Helm doesn't roll back the database.** If the release you're leaving added an Alembic migration,
  run the downgrade **first**, while the new code is still running. Then roll back:
  ```bash
  kubectl --context urumi-vps -n platform exec deploy/gateway -c gateway -- alembic downgrade -1
  ```
- **Backups** (manual; not automated in this project):
  ```bash
  kubectl --context urumi-vps -n platform exec postgres-0 -- pg_dumpall -U postgres > platform-$(date +%F).sql
  ```
  For the store, use `mariadb-dump` via `kubectl exec store-mariadb-0`, with the root password from the `store-db` Secret.

### Local vs production

| | Local (k3d) | VPS (k3s) |
|---|---|---|
| Kubernetes | k3s v1.36.5 in Docker | k3s v1.36.5 native |
| Hosts | `shop.localtest.me`, `app.localtest.me` | `shop.<domain>`, `app.<domain>` (DNS A records) |
| TLS | none (http) | Let's Encrypt via cert-manager; Traefik terminates TLS |
| Session cookie | `Secure` off | `Secure` on (`gateway.cookieSecure`) |
| Images | `make build load-images` (k3d import) | `make build push` → GHCR, pulled by the node |
| Volumes | local-path, 2Gi | local-path, 5Gi |
| Namespaces | `make namespaces` (part of `deploy-local`) | `make namespaces KCTX=…` (part of `deploy-prod`) |
| Secrets | generated in-cluster; LLM key via `make llm-secret` | same |
| Everything else | identical charts, NetworkPolicies, Pod Security levels | identical |
