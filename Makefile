# Team MCP Gateway - task runner. Run from WSL (bash), repo root.
# Each target is a thin wrapper so every command is visible and explainable.

CLUSTER      := urumi
CLUSTER_CFG  := k3d/cluster.yaml

# Image tag = hash of the service's source files (tracked + untracked, minus
# gitignored). It changes exactly when the code changes, so Helm sees a new tag
# and rolls the pod; rebuilding unchanged code gives the same tag.
# (2>/dev/null: files deleted but not yet committed are listed by git but don't exist.)
tag = $(shell cd services/$(1) && git ls-files -co --exclude-standard | sort | xargs sha1sum 2>/dev/null | sha1sum | cut -c1-12)

# Every service has a Dockerfile with `test` and `runtime` stages, so they all
# build, test and load the same way (the dashboard's test stage type-checks).
SERVICES    := woo-mcp notes-mcp fx-mcp gateway dashboard
IMAGES      := $(foreach s,$(SERVICES),urumi/$(s):$(call tag,$(s)))

# Throwaway Postgres for tests that need a real database (notes-mcp, gateway).
TEST_NET := urumi-test
TEST_PG  := urumi-test-pg

.PHONY: help prereqs cluster-up status teardown test build load-images lint namespaces deploy-local store-admin store-logs mcp-tokens gateway-admin gateway-user gateway-key gateway-keys gateway-revoke gateway-upstreams gateway-audit api-role-check llm-secret chat-check netpol-check security-check reset-demo deploy-prod dod-check

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

prereqs: ## Check docker, k3d, kubectl, helm are installed
	bash scripts/check-prereqs.sh

cluster-up: ## Create the local k3d cluster (no-op if it already exists)
	@if k3d cluster list $(CLUSTER) >/dev/null 2>&1; then \
	  echo "Cluster '$(CLUSTER)' already exists - starting it if stopped"; \
	  k3d cluster start $(CLUSTER); \
	else \
	  k3d cluster create --config $(CLUSTER_CFG); \
	fi
	kubectl config use-context k3d-$(CLUSTER)
	kubectl wait --for=condition=Ready nodes --all --timeout=120s
	@echo "Waiting for Traefik (k3s installs it a few seconds after boot)..."
	@until kubectl -n kube-system get deploy traefik >/dev/null 2>&1; do sleep 3; done
	kubectl -n kube-system rollout status deploy/traefik --timeout=180s

status: ## Show nodes and ingress controller
	kubectl get nodes -o wide
	kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik

teardown: ## Delete the local cluster (destroys all data in it)
	k3d cluster delete $(CLUSTER)

test: ## Run all service tests in Docker (with a throwaway Postgres)
	docker network create $(TEST_NET) >/dev/null 2>&1 || true
	docker rm -f $(TEST_PG) >/dev/null 2>&1 || true
	docker run -d --rm --name $(TEST_PG) --network $(TEST_NET) -e POSTGRES_PASSWORD=test postgres:18.4 >/dev/null
	@status=0; for s in $(SERVICES); do \
	  echo "=== $$s"; \
	  docker build -q --target test -t urumi/$$s:test services/$$s >/dev/null && \
	  docker run --rm --network $(TEST_NET) \
	    -e TEST_DATABASE_URL=postgresql://postgres:test@$(TEST_PG)/postgres urumi/$$s:test || status=1; \
	done; docker rm -f $(TEST_PG) >/dev/null; exit $$status

build: ## Build service images (tag = hash of each service's source)
	$(foreach s,$(SERVICES),docker build --target runtime -t urumi/$(s):$(call tag,$(s)) services/$(s) && ) true

load-images: ## Import local images into the k3d cluster (no registry needed)
	k3d image import $(IMAGES) -c $(CLUSTER)

lint: ## Lint both charts (dummy key values: lookup has no cluster during lint)
	helm lint charts/store -f charts/store/values-local.yaml
	helm lint charts/platform -f charts/platform/values-local.yaml \
	  --set wooMcp.image.tag=lint --set notesMcp.image.tag=lint --set fxMcp.image.tag=lint --set gateway.image.tag=lint --set dashboard.image.tag=lint \
	  --set wooMcp.store.consumerKey=x --set wooMcp.store.consumerSecret=x

namespaces: ## Create the namespaces with their Pod Security Admission levels (idempotent)
	@# platform: every pod meets "restricted" (non-root, seccomp, no capabilities...).
	@# store: "baseline" is enforced because WordPress needs one CHOWN-only root
	@# init container (local-path volumes); "restricted" violations are still reported.
	kubectl create namespace platform --dry-run=client -o yaml | kubectl apply -f -
	kubectl label namespace platform --overwrite \
	  pod-security.kubernetes.io/enforce=restricted \
	  pod-security.kubernetes.io/warn=restricted pod-security.kubernetes.io/audit=restricted
	kubectl create namespace store --dry-run=client -o yaml | kubectl apply -f -
	kubectl label namespace store --overwrite \
	  pod-security.kubernetes.io/enforce=baseline \
	  pod-security.kubernetes.io/warn=restricted pod-security.kubernetes.io/audit=restricted

deploy-local: namespaces ## Install/upgrade the charts on the local cluster (values-local.yaml)
	helm upgrade --install store charts/store -n store \
	  -f charts/store/values-local.yaml --wait --timeout 15m
	helm upgrade --install platform charts/platform -n platform \
	  -f charts/platform/values-local.yaml \
	  --set wooMcp.image.tag=$(call tag,woo-mcp) \
	  --set notesMcp.image.tag=$(call tag,notes-mcp) \
	  --set fxMcp.image.tag=$(call tag,fx-mcp) \
	  --set gateway.image.tag=$(call tag,gateway) \
	  --set dashboard.image.tag=$(call tag,dashboard) \
	  --wait --timeout 5m

store-admin: ## Print the WordPress admin URL, user and password
	@echo "URL:      http://shop.localtest.me/wp-admin"
	@echo "User:     $$(kubectl -n store get secret store-wp-admin -o jsonpath='{.data.username}' | base64 -d)"
	@echo "Password: $$(kubectl -n store get secret store-wp-admin -o jsonpath='{.data.password}' | base64 -d)"

store-logs: ## Show the store bootstrap Job logs
	kubectl -n store logs job/store-bootstrap --all-containers

mcp-tokens: ## Print the notes/fx upstream credentials (for testing with MCP Inspector)
	@echo "notes-mcp  Authorization: Bearer $$(kubectl -n platform get secret notes-mcp-auth -o jsonpath='{.data.token}' | base64 -d)"
	@echo "fx-mcp     X-API-Key: $$(kubectl -n platform get secret fx-mcp-auth -o jsonpath='{.data.api-key}' | base64 -d)"

# --- Gateway admin CLI (handy for scripts; the dashboard does the same). Usage examples:
#   make gateway-user EMAIL=b@example.com [ROLE=member]
#   make gateway-key EMAIL=b@example.com
#   make gateway-revoke PREFIX=urk_Ab3dE6gH
GW_CLI := kubectl -n platform exec deploy/gateway -c gateway -- python -m gateway.cli
ROLE   ?= member

gateway-admin: ## Print the bootstrap admin's email and password
	@echo "Email:    $$(kubectl -n platform get secret gateway-admin -o jsonpath='{.data.email}' | base64 -d)"
	@echo "Password: $$(kubectl -n platform get secret gateway-admin -o jsonpath='{.data.password}' | base64 -d)"

gateway-user: ## Create a user: EMAIL=... [ROLE=member|admin]
	$(GW_CLI) create-user --email $(EMAIL) --role $(ROLE)

gateway-key: ## Create an API key (shown once): EMAIL=...
	$(GW_CLI) create-key --email $(EMAIL)

gateway-keys: ## List API keys
	$(GW_CLI) list-keys

gateway-revoke: ## Revoke an API key: PREFIX=urk_...
	$(GW_CLI) revoke-key --prefix $(PREFIX)

gateway-upstreams: ## Show upstream servers and their status
	$(GW_CLI) upstreams

gateway-audit: ## Show the latest audit log entries
	$(GW_CLI) audit --limit 30

llm-secret: ## Store your Gemini key in the cluster (reads GEMINI_API_KEY from your shell), restart the gateway
	@test -n "$$GEMINI_API_KEY" || { echo "Set it first:  export GEMINI_API_KEY=...   (never commit it)"; exit 1; }
	@kubectl -n platform create secret generic gateway-llm --from-literal=api-key="$$GEMINI_API_KEY" \
	  --dry-run=client -o yaml | kubectl apply -f -
	kubectl -n platform rollout restart deploy/gateway
	kubectl -n platform rollout status deploy/gateway --timeout=120s

chat-check: ## Send one prompt to the chat agent through the real API: PROMPT="..."
	bash scripts/chat-check.sh "$(PROMPT)"

api-role-check: ## Prove via the real API (through the Ingress) that members are refused admin actions
	bash scripts/api-role-check.sh

# Namespaces (with their Pod Security levels) must exist before Helm runs, on the
# VPS too: deploy-prod depends on `namespaces` just like deploy-local.
deploy-prod: namespaces ## helm upgrade --install charts with values-prod.yaml (Phase 8)
	@echo "deploy-prod: implemented in Phase 8"

netpol-check: ## Prove the NetworkPolicies: allowed and blocked connections, with test pods
	bash scripts/netpol-check.sh

security-check: ## Show every container's security settings + Pod Security Admission results
	bash scripts/security-check.sh

dod-check: ## Run the Definition-of-Done checklist end to end (two users, revoke, disable, kill, audit)
	bash scripts/dod-check.sh

reset-demo: ## LOCAL only: wipe users (except admin), keys, audit, notes, orders, non-sample products [YES=1]
	YES=$(YES) bash scripts/reset-demo.sh
