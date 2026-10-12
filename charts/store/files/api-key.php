<?php
/**
 * Registers the WooCommerce REST API key whose plain values live in the
 * Kubernetes Secret "<release>-woo-api". Mirrors WC_Auth::create_keys():
 *   consumer_key    = wc_api_hash(ck)  -> hash_hmac('sha256', ck, 'wc-api')
 *   consumer_secret = cs (stored plain; WooCommerce needs it to verify signatures)
 *   truncated_key   = last 7 chars of ck (shown in WP admin)
 * Idempotent: if a row with this hash already exists we do nothing; otherwise
 * we replace our managed row (identified by its description).
 */
global $wpdb;

$ck    = getenv('WOO_CONSUMER_KEY');
$cs    = getenv('WOO_CONSUMER_SECRET');
$login = getenv('WOO_API_USER');
$desc  = 'woo-mcp (managed by helm)';
$table = $wpdb->prefix . 'woocommerce_api_keys';

if (!$ck || !$cs) {
    WP_CLI::error('WOO_CONSUMER_KEY / WOO_CONSUMER_SECRET not set');
}
$user = get_user_by('login', $login);
if (!$user) {
    WP_CLI::error("user '$login' not found");
}

$hash = wc_api_hash($ck);
$existing = $wpdb->get_var($wpdb->prepare(
    "SELECT key_id FROM $table WHERE consumer_key = %s AND consumer_secret = %s AND user_id = %d",
    $hash, $cs, $user->ID
));
if ($existing) {
    WP_CLI::log("[bootstrap] api key ...".substr($ck, -7).": already registered (key_id $existing)");
    return;
}

$wpdb->delete($table, ['description' => $desc]);
$ok = $wpdb->insert($table, [
    'user_id'         => $user->ID,
    'description'     => $desc,
    'permissions'     => 'read_write',
    'consumer_key'    => $hash,
    'consumer_secret' => $cs,
    'truncated_key'   => substr($ck, -7),
], ['%d', '%s', '%s', '%s', '%s', '%s']);

if (!$ok) {
    WP_CLI::error('insert failed: ' . $wpdb->last_error);
}
WP_CLI::log("[bootstrap] api key ...".substr($ck, -7).": registered for user $login (key_id {$wpdb->insert_id})");
