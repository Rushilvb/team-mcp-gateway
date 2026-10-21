#!/usr/bin/env bash
# Proves the NetworkPolicies with real connections. A throwaway busybox pod per
# "role" (optionally wearing a role's labels) tries TCP connects; we compare
# with what the policies say should happen.
#   make netpol-check
# Note: policies match pods by LABEL, so a pod that wears the gateway's label is
# treated like the gateway. That's why "who may create pods" (RBAC) matters in prod.
set -uo pipefail
FAILED=0
PLAT=platform.svc.cluster.local
STORE=store.svc.cluster.local

# The probe pods are themselves "restricted"-compliant (non-root, seccomp, no
# capabilities), so the platform namespace's Pod Security level admits them.
overrides() { # $1 = shell command
  cat <<JSON
{"spec":{"automountServiceAccountToken":false,
  "securityContext":{"runAsNonRoot":true,"runAsUser":65534,"seccompProfile":{"type":"RuntimeDefault"}},
  "containers":[{"name":"probe","image":"busybox:1.37","command":["sh","-c",$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')],
    "securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}
JSON
}

# probe <namespace> <labels> <description> <expect: OPEN|BLOCKED> <host:port>...
probe() {
  local ns=$1 labels=$2 who=$3 expect=$4; shift 4
  local cmd=""
  for target in "$@"; do
    cmd+="if nc -z -w 3 ${target%:*} ${target##*:}; then echo \"$target OPEN\"; else echo \"$target BLOCKED\"; fi; "
  done
  local out
  out=$(kubectl -n "$ns" run "np-probe-$RANDOM" --rm -i --restart=Never --quiet --image=busybox:1.37 \
        --labels="$labels" --overrides="$(overrides "$cmd")" </dev/null 2>&1)
  for target in "$@"; do
    local got
    got=$(echo "$out" | grep -E "^$target (OPEN|BLOCKED)$" | awk '{print $2}')
    if [ "$got" = "$expect" ]; then
      printf '  PASS  %-34s -> %-48s %s\n' "$who" "$target" "$got"
    else
      printf '  FAIL  %-34s -> %-48s expected %s, got %s\n' "$who" "$target" "$expect" "${got:-<no result>}"
      FAILED=1
    fi
  done
}

echo "== platform: only the gateway may reach the MCP servers and Postgres"
probe platform app=netpol-probe "random pod (platform)" BLOCKED \
  "woo-mcp.$PLAT:8000" "notes-mcp.$PLAT:8000" "fx-mcp.$PLAT:8000" "postgres.$PLAT:5432" \
  "gateway.$PLAT:8000" "dashboard.$PLAT:8080"
probe platform app.kubernetes.io/name=gateway "pod labelled as gateway" OPEN \
  "woo-mcp.$PLAT:8000" "notes-mcp.$PLAT:8000" "fx-mcp.$PLAT:8000" "postgres.$PLAT:5432"

echo "== store: only Traefik and woo-mcp may reach WordPress; only WordPress/bootstrap reach MariaDB"
probe store app=netpol-probe "random pod (store)" BLOCKED \
  "store-wordpress.$STORE:80" "store-mariadb.$STORE:3306"
probe platform app.kubernetes.io/name=woo-mcp "pod labelled as woo-mcp (platform)" OPEN \
  "store-wordpress.$STORE:80"
probe platform app.kubernetes.io/name=woo-mcp "pod labelled as woo-mcp (platform)" BLOCKED \
  "store-mariadb.$STORE:3306"

echo "== egress: fx-mcp may not open any connection (not even DNS)"
out=$(kubectl -n platform exec deploy/fx-mcp -- python -c '
import socket
for host, port in [("10.43.0.10", 53), ("postgres", 5432), ("1.1.1.1", 443)]:
    try:
        socket.create_connection((host, port), timeout=3); print(f"{host}:{port} OPEN")
    except Exception:
        print(f"{host}:{port} BLOCKED")' 2>&1)
while read -r target got; do
  if [ "$got" = BLOCKED ]; then printf '  PASS  %-34s -> %-48s %s\n' "fx-mcp (real pod)" "$target" "$got"
  else printf '  FAIL  %-34s -> %-48s expected BLOCKED, got %s\n' "fx-mcp (real pod)" "$target" "$got"; FAILED=1; fi
done <<< "$out"

echo "== the public path still works (browser -> Traefik -> Service)"
for url in http://shop.localtest.me/ http://app.localtest.me/healthz http://app.localtest.me/login; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$url")
  if [ "$code" = 200 ]; then printf '  PASS  %-34s -> %-48s %s\n' "Traefik" "$url" "$code"
  else printf '  FAIL  %-34s -> %-48s HTTP %s\n' "Traefik" "$url" "$code"; FAILED=1; fi
done

[ "$FAILED" = 0 ] && echo "ALL NETWORK POLICY CHECKS PASSED" || echo "SOME NETWORK POLICY CHECKS FAILED"
exit "$FAILED"
