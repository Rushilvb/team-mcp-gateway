#!/bin/sh
# Wipe demo data from the store: every order, and every product except the
# sample ones from values.yaml (matched by SKU). Store settings, users, the
# REST API key and WooCommerce itself are untouched. Run by `make reset-demo`.
set -eu
log() { echo "[reset-demo] $*"; }
wpa() { wp --user="$WP_ADMIN_USER" "$@"; }

# Orders: collect all ids first (any status), then delete permanently.
ids=""
page=1
while :; do
  batch=$(wpa wc shop_order list --status=any --per_page=100 --page="$page" --field=id)
  [ -z "$batch" ] && break
  ids="$ids $batch"
  page=$((page + 1))
done
n=0
for id in $ids; do
  wpa wc shop_order delete "$id" --force=true >/dev/null
  n=$((n + 1))
done
log "deleted $n orders"

# Products: keep the sample SKUs listed in products.txt.
keep=" $(cut -d'|' -f1 /bootstrap/products.txt | tr '\n' ' ') "
rows=""
page=1
while :; do
  batch=$(wpa wc product list --status=any --per_page=100 --page="$page" --fields=id,sku --format=csv | tail -n +2)
  [ -z "$batch" ] && break
  rows="$rows
$batch"
  page=$((page + 1))
done
n=0
kept=0
for row in $rows; do
  id=${row%%,*}
  sku=${row#*,}
  sku=${sku#\"}; sku=${sku%\"}
  # Keep only a NON-empty SKU from the sample list. Products created through the
  # gateway have no SKU, and an empty one would otherwise match " " anywhere.
  if [ -n "$sku" ]; then
    case "$keep" in
      *" $sku "*) kept=$((kept + 1)); continue ;;
    esac
  fi
  wpa wc product delete "$id" --force=true >/dev/null
  n=$((n + 1))
done
log "deleted $n products, kept $kept sample products"
log "done"
