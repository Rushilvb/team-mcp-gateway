# Team MCP Gateway - task runner. Run from WSL (bash), repo root.
# Each target is a thin wrapper so every command is visible and explainable.

CLUSTER      := urumi
CLUSTER_CFG  := k3d/cluster.yaml

.PHONY: help prereqs cluster-up status teardown build load-images deploy-local deploy-prod dod-check

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

build: ## Build service images (Phase 2+)
	@echo "build: implemented from Phase 2"

load-images: ## Import local images into k3d (Phase 2+)
	@echo "load-images: implemented from Phase 2"

deploy-local: ## helm upgrade --install charts with values-local.yaml (Phase 1+)
	@echo "deploy-local: implemented from Phase 1"

deploy-prod: ## helm upgrade --install charts with values-prod.yaml (Phase 8)
	@echo "deploy-prod: implemented in Phase 8"

dod-check: ## Run the Definition-of-Done script (Phase 7)
	@echo "dod-check: implemented in Phase 7"
