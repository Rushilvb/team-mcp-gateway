#!/bin/sh
# Store bootstrap. Runs as a Helm post-install/post-upgrade Job.
# Every step checks state first, so re-running it is always safe.
set -eu

log() { echo "[bootstrap] $*"; }
wpa() { wp --user="$WP_ADMIN_USER" "$@"; }   # wp-cli as the admin (needed for `wp wc ...`)

# 1. Wait for the database --------------------------------------------------
log "waiting for database at $WORDPRESS_DB_HOST"
i=0
until php -r 'exit(@mysqli_connect(getenv("WORDPRESS_DB_HOST"), getenv("WORDPRESS_DB_USER"), getenv("WORDPRESS_DB_PASSWORD"), getenv("WORDPRESS_DB_NAME")) ? 0 : 1);'; do
  i=$((i + 1))
  [ "$i" -ge 60 ] && { log "database not reachable after 5 minutes"; exit 1; }
  sleep 5
done
log "database reachable"

# The WordPress pod copies the default themes into wp-content (our PVC) on its
# first start. Wait for that so `core install` finds a theme to activate.
i=0
until [ -d wp-content/themes ] && [ -n "$(ls wp-content/themes)" ]; do
  i=$((i + 1))
  [ "$i" -ge 60 ] && { log "wp-content not seeded by the WordPress pod after 5 minutes"; exit 1; }
  sleep 5
done

# 2. WordPress core install (creates tables + admin user) -------------------
if wp core is-installed; then
  log "core: already installed"
else
  wp core install --url="$SITE_URL" --title="$SITE_TITLE" \
    --admin_user="$WP_ADMIN_USER" --admin_password="$WP_ADMIN_PASSWORD" \
    --admin_email="$WP_ADMIN_EMAIL" --skip-email
fi

# 3. Site settings -----------------------------------------------------------
wp option update siteurl "$SITE_URL"
wp option update home "$SITE_URL"
wp option update timezone_string "$STORE_TIMEZONE"
wp rewrite structure '/%postname%/'          # pretty permalinks (also flushes rules)

# 4. WooCommerce plugin (pinned version) ------------------------------------
current="$(wp plugin get woocommerce --field=version 2>/dev/null || true)"
if [ "$current" = "$WOO_VERSION" ]; then
  log "woocommerce: $WOO_VERSION already installed"
else
  log "woocommerce: installing $WOO_VERSION (found: ${current:-none})"
  wp plugin install woocommerce --version="$WOO_VERSION" --force
fi
wp plugin is-active woocommerce || wp plugin activate woocommerce
wpa wc update                                 # run any pending WooCommerce DB migrations
wpa wc tool run install_pages >/dev/null      # Shop/Cart/Checkout/My account (skips existing)
# Home page = the WooCommerce Shop page, so products show at the site root
# instead of WordPress's default blog front page.
shop_page="$(wp option get woocommerce_shop_page_id)"
wp option update show_on_front page
wp option update page_on_front "$shop_page"

# 5. Store settings ----------------------------------------------------------
wp option update woocommerce_currency "$STORE_CURRENCY"
wp option update woocommerce_default_country "$STORE_COUNTRY"
wp option update woocommerce_coming_soon no          # new stores start hidden ("coming soon")
wp option update woocommerce_store_pages_only no
wp option update woocommerce_ship_to_countries disabled   # no shipping zones needed to check out
wp option update woocommerce_onboarding_profile '{"skipped":true}' --format=json
# Cash on Delivery payment gateway
wp option update woocommerce_cod_settings --format=json \
  '{"enabled":"yes","title":"Cash on delivery","description":"Pay with cash upon delivery.","instructions":"Pay with cash upon delivery.","enable_for_methods":[],"enable_for_virtual":"yes"}'

# 6. Sample products (skip any whose SKU already exists) --------------------
while IFS='|' read -r sku price name description; do
  [ -z "$sku" ] && continue
  if [ -n "$(wpa wc product list --sku="$sku" --field=id)" ]; then
    log "product $sku: already exists"
  else
    wpa wc product create --sku="$sku" --name="$name" --regular_price="$price" \
      --description="$description" --status=publish --porcelain >/dev/null
    log "product $sku: created"
  fi
done < /bootstrap/products.txt

# 7. Low-privilege user that owns the REST API key --------------------------
if wp user get "$WOO_API_USER" --field=ID >/dev/null 2>&1; then
  log "user $WOO_API_USER: already exists"
else
  wp user create "$WOO_API_USER" "$WOO_API_EMAIL" --role=shop_manager \
    --user_pass="$(head -c 32 /dev/urandom | base64)" --porcelain >/dev/null
  log "user $WOO_API_USER: created"
fi
wp user set-role "$WOO_API_USER" shop_manager

# 8. REST API key (values come from the Kubernetes Secret) ------------------
wp eval-file /bootstrap/api-key.php

log "done"
