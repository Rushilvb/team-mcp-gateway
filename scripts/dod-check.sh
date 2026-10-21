#!/usr/bin/env bash
# Definition-of-Done check (see scripts/dod_check.py for the steps).
# Needs: a deployed local cluster, kubectl context k3d-urumi, python3 (stdlib only).
#   make dod-check
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/dod_check.py
