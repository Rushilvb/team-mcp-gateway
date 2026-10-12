# Team MCP Gateway on Kubernetes

A WooCommerce store on Kubernetes, plus a team MCP gateway: admins register MCP servers once,
and every team member uses all of their tools through one MCP endpoint and a chat agent.
The same Helm charts run locally (k3d) and on a VPS (k3s); only values files differ.

> Status: Phase 1 (store). Sections marked _TODO_ are filled in by later phases.

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
make deploy-local   # store chart (first run ~5 min: image pulls + WooCommerce download)
make store-admin    # WordPress admin URL + password
make teardown       # deletes the cluster
```

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

## Connecting Claude Code / Cursor / MCP Inspector
_TODO (Phase 4/5)_

## Using the chat agent
_TODO (Phase 6)_

## Deploying to a VPS (k3s)
_TODO (Phase 8)_
