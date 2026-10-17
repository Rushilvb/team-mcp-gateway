"""Short-lived chat tokens: how the chat agent authenticates to /mcp AS the user.

    urc_<base64url(json {uid, exp, jti})>.<base64url(HMAC-SHA256(key, payload))>

Minted for one chat request (5 minutes), verified by McpAuth. The HMAC proves
the gateway made it, and nobody can forge one for another user without the key.

The key is 32 random bytes generated when the gateway starts. That is enough
because the token is minted and checked by the same process (the agent calls
the gateway's own /mcp over loopback). Tokens simply die on restart. With
several replicas the key would have to come from a shared Secret instead.

Why not use the user's API key? They may not have one, and keys are only
stored hashed. A separate token also marks the calls as channel "chat" in the audit log.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

PREFIX = "urc_"
TTL_SECONDS = 300


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_key() -> bytes:
    return secrets.token_bytes(32)


def mint(key: bytes, user_id: int, ttl: int = TTL_SECONDS) -> str:
    payload = _b64(json.dumps({"uid": user_id, "exp": int(time.time()) + ttl, "jti": secrets.token_hex(8)},
                              separators=(",", ":")).encode())
    signature = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    return f"{PREFIX}{payload}.{signature}"


def verify(key: bytes, token: str) -> int | None:
    """The user id if the token is genuine and unexpired, else None."""
    if not token.startswith(PREFIX):
        return None
    payload, _, signature = token[len(PREFIX):].partition(".")
    expected = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    if not signature or not hmac.compare_digest(signature, expected):
        return None
    try:
        claims = json.loads(_unb64(payload))
        if int(claims["exp"]) < time.time():
            return None
        return int(claims["uid"])
    except (ValueError, KeyError, TypeError):
        return None
