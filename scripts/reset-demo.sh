#!/usr/bin/env bash
# Reset the LOCAL cluster to clean demo data, e.g. before recording.
#   make reset-demo          (asks you to type "reset")
#   make reset-demo YES=1    (no prompt)
#
# Wipes:  gateway users (except the bootstrap admin), API keys, sessions, audit log;
#         team notes; all WooCommerce orders; all products except the 3 samples.
# Keeps:  the admin login, the LLM key Secret, all other Secrets, the upstream
#         servers (all re-enabled), store settings, the WooCommerce REST API key.
set -euo pipefail
cd "$(dirname "$0")/.."

CONTEXT=$(kubectl config current-context)
if [ "$CONTEXT" != "k3d-urumi" ]; then
  echo "Refusing: kubectl context is '$CONTEXT', not the local 'k3d-urumi' cluster."
  echo "reset-demo deletes data and is for local demo recording only."
  exit 1
fi

if [ "${YES:-}" != "1" ]; then
  echo "This deletes, on the LOCAL cluster: all users except the admin, all API keys,"
  echo "the audit log, all team notes, all orders, and all non-sample products."
  read -r -p 'Type "reset" to continue: ' answer
  [ "$answer" = "reset" ] || { echo "Cancelled."; exit 1; }
fi

if ! kubectl -n store get configmap store-bootstrap -o jsonpath='{.data}' | grep -q 'reset-demo.sh'; then
  echo "The deployed store chart predates reset-demo; run 'make deploy-local' first."
  exit 1
fi

echo "== gateway: users, keys, sessions, audit"
kubectl -n platform exec deploy/gateway -c gateway -- python -m gateway.cli reset-demo

echo "== notes"
kubectl -n platform exec postgres-0 -- psql -U postgres -d notes -qtAc 'TRUNCATE notes RESTART IDENTITY' \
  && echo "all notes deleted"

echo "== store: orders and non-sample products (one-off wp-cli Job)"
kubectl -n store delete job store-reset-demo --ignore-not-found >/dev/null
helm template store charts/store -n store -f charts/store/values-local.yaml \
  --set resetDemo.enabled=true --show-only templates/reset-demo-job.yaml | kubectl -n store apply -f - >/dev/null
if kubectl -n store wait --for=condition=complete job/store-reset-demo --timeout=600s >/dev/null; then
  kubectl -n store logs job/store-reset-demo -c wpcli | grep '\[reset-demo\]'
else
  kubectl -n store logs job/store-reset-demo --all-containers | tail -20
  echo "store reset FAILED"; exit 1
fi

echo
echo "Done. Log in again at http://app.localtest.me (sessions were cleared):"
echo "  make gateway-admin    # admin email + password (unchanged)"
echo "The LLM key (gateway-llm Secret) was not touched."
