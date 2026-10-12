import base64

from woo_mcp import oauth

# Golden vector: this exact signature was ACCEPTED by WooCommerce 11.2.1's own
# WC_REST_Authentication::check_oauth_signature(), run inside the store pod
# (and a wrong signature was rejected), so we know we match its quirks.
GOLDEN_SIGNATURE = "pBIIpJZtxQLq6LyWUM1l8TsrmiAVHuOTiGI0I2n3OSg="


def golden(**overrides):
    args = dict(
        method="GET",
        url="http://shop.localtest.me/wp-json/wc/v3/orders",
        query={"status": "processing", "after": "2026-10-11T18:30:00", "search": "masala chai~/+"},
        consumer_key="ck_test",
        consumer_secret="cs_test",
        nonce="abc123",
        timestamp=1760000000,
    )
    args.update(overrides)
    return oauth.sign(**args)


def test_matches_signature_accepted_by_woocommerce():
    assert golden()["oauth_signature"] == GOLDEN_SIGNATURE


def test_returns_query_plus_oauth_params():
    params = golden()
    assert params["status"] == "processing"
    assert params["oauth_consumer_key"] == "ck_test"
    assert params["oauth_signature_method"] == "HMAC-SHA256"
    assert len(base64.b64decode(params["oauth_signature"])) == 32  # SHA-256 digest


def test_signature_depends_on_host_method_params_and_secret():
    # WooCommerce signs with its PUBLIC home URL; signing the in-cluster
    # service URL instead would be rejected.
    assert golden(url="http://store-wordpress.store.svc.cluster.local/wp-json/wc/v3/orders")["oauth_signature"] != GOLDEN_SIGNATURE
    assert golden(method="POST")["oauth_signature"] != GOLDEN_SIGNATURE
    assert golden(query={"status": "completed"})["oauth_signature"] != GOLDEN_SIGNATURE
    assert golden(consumer_secret="cs_other")["oauth_signature"] != GOLDEN_SIGNATURE


def test_parameter_order_does_not_matter():
    reordered = {"search": "masala chai~/+", "after": "2026-10-11T18:30:00", "status": "processing"}
    assert golden(query=reordered)["oauth_signature"] == GOLDEN_SIGNATURE


def test_fresh_nonce_per_request():
    # WooCommerce stores used nonces and rejects repeats.
    a = oauth.sign("GET", "http://x/y", {}, "ck", "cs")
    b = oauth.sign("GET", "http://x/y", {}, "ck", "cs")
    assert a["oauth_nonce"] != b["oauth_nonce"]
