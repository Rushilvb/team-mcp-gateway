# Team MCP Gateway - task runner. Run from WSL (bash), repo root.
# Each target is a thin wrapper so every command is visible and explainable.

CLUSTER      := urumi
CLUSTER_CFG  := k3d/cluster.yaml

# Image tag = hash of the service's source files (tracked + untracked, minus
# gitignored). It changes exactly when the code changes, so Helm sees a new tag
# and rolls the pod; rebuilding unchanged code gives the same tag.
src_hash = $(shell cd services/$(1) && git ls-files -co --exclude-standard | sort | xargs sha1sum | sha1sum | cut -c1-12)
TAG_WOO  := $(call src_hash,woo-mcp)

.PHONY: help prereqs cluster-up status teardown test build load-images lint deploy-local store-admin store-logs deploy-prod dod-check

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

test: ## Run service unit tests (inside Docker; no local Python needed)
	docker build --target test services/woo-mcp

build: ## Build service images
	docker build --target runtime -t urumi/woo-mcp:$(TAG_WOO) services/woo-mcp

load-images: ## Import local images into the k3d cluster (no registry needed)
	k3d image import urumi/woo-mcp:$(TAG_WOO) -c $(CLUSTER)

lint: ## Lint both charts (dummy key values: lookup has no cluster during lint)
	helm lint charts/store -f charts/store/values-local.yaml
	helm lint charts/platform -f charts/platform/values-local.yaml \
	  --set wooMcp.image.tag=lint --set wooMcp.store.consumerKey=x --set wooMcp.store.consumerSecret=x

deploy-local: ## Install/upgrade the charts on the local cluster (values-local.yaml)
	helm upgrade --install store charts/store -n store --create-namespace \
	  -f charts/store/values-local.yaml --wait --timeout 15m
	helm upgrade --install platform charts/platform -n platform --create-namespace \
	  -f charts/platform/values-local.yaml --set wooMcp.image.tag=$(TAG_WOO) --wait --timeout 5m

store-admin: ## Print the WordPress admin URL, user and password
	@echo "URL:      http://shop.localtest.me/wp-admin"
	@echo "User:     $$(kubectl -n store get secret store-wp-admin -o jsonpath='{.data.username}' | base64 -d)"
	@echo "Password: $$(kubectl -n store get secret store-wp-admin -o jsonpath='{.data.password}' | base64 -d)"

store-logs: ## Show the store bootstrap Job logs
	kubectl -n store logs job/store-bootstrap --all-containers

deploy-prod: ## helm upgrade --install charts with values-prod.yaml (Phase 8)
	@echo "deploy-prod: implemented in Phase 8"

dod-check: ## Run the Definition-of-Done script (Phase 7)
	@echo "dod-check: implemented in Phase 7"
