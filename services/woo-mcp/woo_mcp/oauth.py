"""OAuth 1.0a one-legged request signing, exactly as WooCommerce verifies it.

WooCommerce refuses HTTP Basic auth over plain HTTP (it would send the secret in
clear text), and inside the cluster we talk plain HTTP. OAuth 1.0a instead
sends a signature: an HMAC of the request made with the consumer secret, which
itself never goes over the wire.

Mirrors WC_REST_Authentication::check_oauth_signature() (WooCommerce 11.2):
  base string = METHOD & enc(url) & enc(sorted "k=v" pairs joined by "&")
  key         = consumer_secret + "&"
  signature   = base64(HMAC-SHA256(key, base string))
where enc() is RFC 3986 percent-encoding (PHP's rawurlencode). Only query
parameters are signed, never the JSON body.

Gotcha: WooCommerce rebuilds the URL from WordPress's *home URL*, not from the
address we actually connected to, so `url` must use the store's public host.
"""

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote


def _enc(value: str) -> str:
    return quote(value, safe="-._~")


def sign(
    method: str,
    url: str,
    query: dict[str, str],
    consumer_key: str,
    consumer_secret: str,
    *,
    nonce: str | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Return `query` plus the oauth_* parameters, including oauth_signature."""
    params = {
        **query,
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),  # WooCommerce rejects reused nonces
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": str(timestamp if timestamp is not None else int(time.time())),
    }
    pairs = [_enc(f"{_enc(k)}={_enc(v)}") for k, v in sorted(params.items())]
    base_string = "&".join([method.upper(), _enc(url), "%26".join(pairs)])
    digest = hmac.new(f"{consumer_secret}&".encode(), base_string.encode(), hashlib.sha256).digest()
    return {**params, "oauth_signature": base64.b64encode(digest).decode()}
