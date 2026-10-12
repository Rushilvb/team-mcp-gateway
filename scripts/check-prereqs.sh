#!/usr/bin/env bash
# Verifies the CLI tools needed for local development are installed and Docker is reachable.
set -u
fail=0

check() {
  local name="$1"; shift
  if command -v "$name" >/dev/null 2>&1; then
    printf "  %-8s OK   %s\n" "$name" "$("$@" 2>&1 | head -n1)"
  else
    printf "  %-8s MISSING\n" "$name"; fail=1
  fi
}

echo "Checking prerequisites:"
check docker  docker --version
check k3d     k3d version
check kubectl kubectl version --client
check helm    helm version --short
check make    make --version

if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
  echo "  docker daemon not reachable. Is Docker Desktop running with WSL integration enabled for this distro?"
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "Some prerequisites are missing. See README > Prerequisites, or run: bash scripts/install-tools-wsl.sh"
  exit 1
fi
echo "All prerequisites present."
