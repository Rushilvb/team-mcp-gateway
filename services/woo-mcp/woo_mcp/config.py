"""Settings, read once from environment variables (set by the Helm chart)."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    # Where we connect: the in-cluster WordPress Service, e.g.
    # http://store-wordpress.store.svc.cluster.local
    woo_base_url: str
    # The store's public host (WordPress "home" URL host), e.g. shop.localtest.me.
    # Sent as the Host header and used in the OAuth signature (see oauth.py).
    public_host: str
    consumer_key: str
    consumer_secret: str
    timezone: str = "Asia/Kolkata"
    timeout_seconds: float = 10.0
    # Host headers the MCP endpoint accepts (DNS-rebinding protection).
    allowed_hosts: list[str] = field(default_factory=lambda: ["localhost:*", "127.0.0.1:*"])

    @classmethod
    def from_env(cls) -> "Settings":
        required = ["WOO_BASE_URL", "WOO_PUBLIC_HOST", "WOO_CONSUMER_KEY", "WOO_CONSUMER_SECRET"]
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"missing required environment variables: {', '.join(missing)}")
        return cls(
            woo_base_url=os.environ["WOO_BASE_URL"].rstrip("/"),
            public_host=os.environ["WOO_PUBLIC_HOST"],
            consumer_key=os.environ["WOO_CONSUMER_KEY"],
            consumer_secret=os.environ["WOO_CONSUMER_SECRET"],
            timezone=os.environ.get("STORE_TIMEZONE", "Asia/Kolkata"),
            timeout_seconds=float(os.environ.get("WOO_TIMEOUT_SECONDS", "10")),
            allowed_hosts=[h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(",") if h.strip()],
        )
