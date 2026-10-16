# Team MCP Gateway - task runner. Run from WSL (bash), repo root.
# Each target is a thin wrapper so every command is visible and explainable.

CLUSTER      := urumi
CLUSTER_CFG  := k3d/cluster.yaml

# Image tag = hash of the service's source files (tracked + untracked, minus
# gitignored). It changes exactly when the code changes, so Helm sees a new tag
# and rolls the pod; rebuilding unchanged code gives the same tag.
tag = $(shell cd services/$(1) && git ls-files -co --exclude-standard | sort | xargs sha1sum | sha1sum | cut -c1-12)

PY_SERVICES := woo-mcp notes-mcp fx-mcp gateway
IMAGES      := $(foreach s,$(PY_SERVICES),urumi/$(s):$(call tag,$(s)))

# Throwaway Postgres for tests that need a real database (notes-mcp).
TEST_NET := urumi-test
TEST_PG  := urumi-test-pg

.PHONY: help prereqs cluster-up status teardown test build load-images lint deploy-local store-admin store-logs mcp-tokens gateway-admin gateway-user gateway-key gateway-keys gateway-revoke gateway-upstreams gateway-audit deploy-prod dod-check

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

test: ## Run service tests in Docker (starts a throwaway Postgres for notes-mcp)
	docker network create $(TEST_NET) >/dev/null 2>&1 || true
	docker rm -f $(TEST_PG) >/dev/null 2>&1 || true
	docker run -d --rm --name $(TEST_PG) --network $(TEST_NET) -e POSTGRES_PASSWORD=test postgres:18.4 >/dev/null
	@status=0; for s in $(PY_SERVICES); do \
	  echo "=== $$s"; \
	  docker build -q --target test -t urumi/$$s:test services/$$s >/dev/null && \
	  docker run --rm --network $(TEST_NET) \
	    -e TEST_DATABASE_URL=postgresql://postgres:test@$(TEST_PG)/postgres urumi/$$s:test || status=1; \
	done; docker rm -f $(TEST_PG) >/dev/null; exit $$status

build: ## Build service images (tag = hash of each service's source)
	$(foreach s,$(PY_SERVICES),docker build --target runtime -t urumi/$(s):$(call tag,$(s)) services/$(s) && ) true

load-images: ## Import local images into the k3d cluster (no registry needed)
	k3d image import $(IMAGES) -c $(CLUSTER)

lint: ## Lint both charts (dummy key values: lookup has no cluster during lint)
	helm lint charts/store -f charts/store/values-local.yaml
	helm lint charts/platform -f charts/platform/values-local.yaml \
	  --set wooMcp.image.tag=lint --set notesMcp.image.tag=lint --set fxMcp.image.tag=lint --set gateway.image.tag=lint \
	  --set wooMcp.store.consumerKey=x --set wooMcp.store.consumerSecret=x

deploy-local: ## Install/upgrade the charts on the local cluster (values-local.yaml)
	helm upgrade --install store charts/store -n store --create-namespace \
	  -f charts/store/values-local.yaml --wait --timeout 15m
	helm upgrade --install platform charts/platform -n platform --create-namespace \
	  -f charts/platform/values-local.yaml \
	  --set wooMcp.image.tag=$(call tag,woo-mcp) \
	  --set notesMcp.image.tag=$(call tag,notes-mcp) \
	  --set fxMcp.image.tag=$(call tag,fx-mcp) \
	  --set gateway.image.tag=$(call tag,gateway) \
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

# --- Gateway admin CLI (until the dashboard exists). Usage examples:
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

deploy-prod: ## helm upgrade --install charts with values-prod.yaml (Phase 8)
	@echo "deploy-prod: implemented in Phase 8"

dod-check: ## Run the Definition-of-Done script (Phase 7)
	@echo "dod-check: implemented in Phase 7"
