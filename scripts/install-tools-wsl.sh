#!/usr/bin/env bash
# One-shot installer for kubectl, helm, k3d and make inside WSL2 Ubuntu.
# Docker itself comes from Docker Desktop (Settings > Resources > WSL integration).
# Uses each project's official install method. Safe to re-run.
set -euo pipefail

sudo apt-get update -y
sudo apt-get install -y make curl ca-certificates

if ! command -v kubectl >/dev/null; then
  # Official binary from dl.k8s.io, latest stable.
  ver="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
  curl -fsSLo /tmp/kubectl "https://dl.k8s.io/release/${ver}/bin/linux/amd64/kubectl"
  sudo install -m 0755 /tmp/kubectl /usr/local/bin/kubectl
fi

if ! command -v helm >/dev/null; then
  # Official Helm 3 install script.
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

if ! command -v k3d >/dev/null; then
  # Official k3d install script.
  curl -fsSL https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
fi

bash "$(dirname "$0")/check-prereqs.sh"
