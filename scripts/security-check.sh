#!/usr/bin/env bash
# Shows how every running container is locked down, and asks the API server
# whether the pods meet the Kubernetes "restricted" Pod Security Standard.
#   make security-check
set -uo pipefail
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT

echo "== Containers (effective settings: container value, else pod value)"
kubectl get pods -n store -o json > "$TMP.store"
kubectl get pods -n platform -o json > "$TMP.platform"
python3 - "$TMP.store" "$TMP.platform" <<'PY'
import json, sys
pods = [p for path in sys.argv[1:] for p in json.load(open(path))["items"]]
print(f"  {'NAMESPACE':9} {'POD':34} {'CONTAINER':24} {'UID':>5} {'NONROOT':7} {'RO-FS':5} {'SECCOMP':14} CAPABILITIES")
for p in pods:
    if p["status"].get("phase") not in ("Running", "Succeeded"):
        continue
    spec, psc = p["spec"], p["spec"].get("securityContext", {})
    for kind in ("initContainers", "containers"):
        for c in spec.get(kind, []):
            sc = c.get("securityContext", {})
            uid = sc.get("runAsUser", psc.get("runAsUser", "image"))
            nonroot = sc.get("runAsNonRoot", psc.get("runAsNonRoot", False))
            ro = sc.get("readOnlyRootFilesystem", False)
            seccomp = (sc.get("seccompProfile") or psc.get("seccompProfile") or {}).get("type", "-")
            caps = sc.get("capabilities", {})
            capstr = ("drop " + ",".join(caps["drop"])) if caps.get("drop") else "default"
            if caps.get("add"):
                capstr += " add " + ",".join(caps["add"])
            name = c["name"] + (" (init)" if kind == "initContainers" else "")
            meta = p["metadata"]
            print(f"  {meta['namespace']:9} {meta['name'][:34]:34} {name[:24]:24} {str(uid):>5} "
                  f"{str(nonroot):7} {str(ro):5} {seccomp:14} {capstr}")
PY
rm -f "$TMP.store" "$TMP.platform"

echo
echo "== Pod Security Admission levels (enforced by the API server when pods are created)"
for ns in platform store; do
  kubectl get ns "$ns" -o json > "$TMP"
  python3 - "$ns" "$TMP" <<'PY'
import json, sys
labels = json.load(open(sys.argv[2]))["metadata"].get("labels", {})
levels = ", ".join(f"{k.split('/')[1]}={v}" for k, v in sorted(labels.items()) if k.startswith("pod-security"))
print(f"  {sys.argv[1]:9} {levels}")
PY
done

echo
echo "== Would the running pods pass \"restricted\"? (server-side dry run, nothing is changed)"
for ns in platform store; do
  warnings=$(kubectl label --dry-run=server --overwrite ns "$ns" pod-security.kubernetes.io/enforce=restricted 2>&1 >/dev/null \
             | grep -i "warning" || true)
  if [ -z "$warnings" ]; then
    echo "  $ns: all pods meet the restricted standard"
  else
    echo "  $ns:"
    echo "$warnings" | sed -E 's/^.*Warning: /    /'
  fi
done
echo
echo "Expected: platform has none; store has only WordPress's CHOWN-only root init"
echo "container (local-path volumes are root-owned), which is why store enforces baseline."
