# Hardening inventory

What is locked down, where it's defined, and how to check it. Every claim here is checked by a script:
`make netpol-check`, `make security-check`, `make dod-check`, `make api-role-check`.

## Network: NetworkPolicies (enforced by k3s's built-in controller)

Both namespaces have a **`default-deny-ingress` backstop**: any pod not explicitly allowed below receives no
traffic, including pods added in the future. Egress is restricted per workload.

| Workload | Ingress allowed from | Egress allowed to |
|---|---|---|
| **store/wordpress** | Traefik :80; woo-mcp (platform) :80 | mariadb :3306, DNS, internet 80/443 (no private/link-local) |
| **store/mariadb** | wordpress, bootstrap Job :3306 | nothing |
| **store/bootstrap Job** | nothing | mariadb :3306, DNS, internet 80/443 (downloads WooCommerce) |
| **platform/gateway** | Traefik :8000 | postgres :5432, woo/notes/fx :8000, DNS, internet 80/443 (no private/link-local; Gemini, external MCP servers) |
| **platform/dashboard** | Traefik :8080 | nothing |
| **platform/woo-mcp** | gateway :8000 | store wordpress :80, DNS |
| **platform/notes-mcp** | gateway :8000 | postgres :5432, DNS |
| **platform/fx-mcp** | gateway :8000 | **nothing at all** (not even DNS) |
| **platform/postgres** | notes-mcp, gateway :5432 | nothing |

What the policies don't affect:
- **Kubelet health probes** reach every pod.
- **The chat agent** calls the gateway's own `/mcp` over `127.0.0.1`. Loopback stays inside the pod, so NetworkPolicy doesn't apply to it. That's why the agent uses loopback: the gateway's own Service name is refused by its ingress policy.

`make netpol-check` proves the table with throwaway pods (21 checks).
- **Blocked:** a random pod reaching woo-mcp (the CLAUDE.md check), the other MCP servers, postgres, the gateway or the dashboard.
- **Allowed:** a pod wearing the gateway's label reaching them.
- **Store:** a random store pod can't reach WordPress or MariaDB. A woo-mcp-labelled pod reaches WordPress but not MariaDB.
- **Egress and public path:** fx-mcp can't open any connection, and the public Traefik paths still work.

## Pods: security context and Pod Security Admission

| Workload | User | Root FS | Capabilities | seccomp | SA token |
|---|---|---|---|---|---|
| gateway (+ `migrate` init) | 10001 | read-only | drop ALL | RuntimeDefault | not mounted |
| woo-mcp, notes-mcp, fx-mcp | 10001 | read-only | drop ALL | RuntimeDefault | not mounted |
| dashboard (nginx-unprivileged) | 101 | read-only (+ emptyDir /tmp) | drop ALL | RuntimeDefault | not mounted |
| postgres | 999 | writable (needs its run dir) | drop ALL | RuntimeDefault | not mounted |
| mariadb | 999 | writable | drop ALL | RuntimeDefault | not mounted |
| wordpress | 33 | writable (image entrypoint copies core at start) | drop ALL | RuntimeDefault | not mounted |
| wordpress `volume-permissions` init | **0** | n/a | drop ALL, **add CHOWN** | RuntimeDefault | not mounted |
| bootstrap Job (2 containers) | 33 | writable | drop ALL | RuntimeDefault | not mounted |

Every pod also sets `allowPrivilegeEscalation: false`.

**Pod Security Admission levels.** These are namespace labels, enforced by the API server when a pod is created. They're set by `make namespaces` before Helm runs.
- **`platform`:** `enforce=restricted`, the strictest built-in standard. `make security-check` asks the API server (server-side dry run): "all pods meet the restricted standard".
- **`store`:** `enforce=baseline`, `warn/audit=restricted`. The only restricted violation is the WordPress init container.

## Residual exceptions (deliberate, with reasons)
1. **WordPress `volume-permissions` init container runs as root** with only `CHOWN`, and only to run `chown 33:33` on its volume. k3s local-path volumes are root-owned host directories, and `fsGroup` doesn't apply to them, so non-root WordPress couldn't write its own `wp-content`. On storage that honours `fsGroup`, set `volumePermissions.enabled=false` and the store can enforce `restricted` too.
2. **NetworkPolicies trust labels.** Anyone allowed to create pods in a namespace can label a pod `app.kubernetes.io/name: gateway` and inherit its network access (`netpol-check` does exactly that, to test). In production, restrict who can create pods via RBAC.
3. **Writable root filesystems** for mariadb, postgres and WordPress. The official images write to their own directories at start; making those read-only would require several emptyDir mounts per image for little gain.
4. **Single replica** for the gateway: the health loop, `list_changed` bus, login rate limiter and chat-token key live in memory. Multiple replicas would need Redis (or similar) and a shared chat-token key (out of scope).

## Application-level controls (see the service READMEs)
- **Gateway (`services/gateway/README.md`):**
  - API keys are stored hashed; passwords use argon2id.
  - Upstream credentials are Fernet-encrypted and write-only.
  - Client keys are never forwarded upstream.
  - SSRF guard; Host-header (DNS-rebinding) protection.
  - Sessions: server-side, `httpOnly` + `SameSite=Strict`, with an Origin check for CSRF.
  - Login rate limit; roles enforced server-side; a last-admin guard.
  - Audit of every tool call (including denied ones).
  - Chat: short-lived signed tokens; tool output treated as untrusted; step, time and size limits.
- **Dashboard (`services/dashboard/README.md`):** strict CSP for scripts, `frame-ancestors 'none'`, nosniff, no-referrer.
- **Upstreams:** woo-mcp signs requests with OAuth 1.0a (no secret on the wire). notes and fx compare credentials in constant time.
