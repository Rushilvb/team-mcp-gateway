"""Settings, read once from environment variables (set by the Helm chart)."""

import json
import os
from dataclasses import dataclass, field

from sqlalchemy import URL


def database_url_from_env() -> str:
    """Build the SQLAlchemy URL from the standard libpq variables (PGHOST, ...).
    URL.create() escapes the password properly; never build URLs with f-strings."""
    if os.environ.get("DATABASE_URL"):          # tests / local override
        return os.environ["DATABASE_URL"]
    return URL.create(
        "postgresql+asyncpg",
        username=os.environ.get("PGUSER", "gateway"),
        password=os.environ.get("PGPASSWORD"),
        host=os.environ.get("PGHOST", "postgres"),
        port=int(os.environ.get("PGPORT", "5432")),
        database=os.environ.get("PGDATABASE", "gateway"),
    ).render_as_string(hide_password=False)


def _csv(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str
    fernet_key: str                       # encrypts upstream credentials at rest
    admin_email: str | None = None        # bootstrap admin (created once if missing)
    admin_password: str | None = None
    call_timeout_seconds: float = 15      # per upstream tools/call
    health_interval_seconds: float = 15   # how often upstreams are checked
    health_timeout_seconds: float = 5     # per upstream health check
    # Cluster-internal hostnames admins may register (SSRF guard, see ssrf.py).
    ssrf_allowed_hosts: frozenset[str] = frozenset()
    # Host headers the /mcp endpoint accepts (MCP SDK DNS-rebinding protection).
    mcp_allowed_hosts: list[str] = field(default_factory=lambda: ["localhost:*", "127.0.0.1:*"])
    # Upstreams registered on first start: [{"slug", "name", "url", "auth_type",
    # "header_name", "credential_env"}]; the credential itself is read from the
    # env var named by credential_env (which Kubernetes fills from a Secret).
    bootstrap_upstreams: list[dict] = field(default_factory=list)
    # Dashboard / REST API
    public_url: str = "http://app.localtest.me"   # base of invite links; also the only allowed Origin for writes
    cookie_secure: bool = False                    # True in prod (HTTPS): browser sends the cookie only over TLS
    session_hours: float = 12

    @classmethod
    def from_env(cls) -> "Settings":
        if not os.environ.get("GATEWAY_FERNET_KEY"):
            raise RuntimeError("missing required environment variable: GATEWAY_FERNET_KEY")
        return cls(
            database_url=database_url_from_env(),
            fernet_key=os.environ["GATEWAY_FERNET_KEY"],
            admin_email=os.environ.get("GATEWAY_ADMIN_EMAIL") or None,
            admin_password=os.environ.get("GATEWAY_ADMIN_PASSWORD") or None,
            call_timeout_seconds=float(os.environ.get("CALL_TIMEOUT_SECONDS", "15")),
            health_interval_seconds=float(os.environ.get("HEALTH_INTERVAL_SECONDS", "15")),
            health_timeout_seconds=float(os.environ.get("HEALTH_TIMEOUT_SECONDS", "5")),
            ssrf_allowed_hosts=frozenset(h.lower() for h in _csv("SSRF_ALLOWED_HOSTS")),
            mcp_allowed_hosts=_csv("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*"),
            bootstrap_upstreams=json.loads(os.environ.get("BOOTSTRAP_UPSTREAMS", "[]")),
            public_url=os.environ.get("PUBLIC_URL", "http://app.localtest.me").rstrip("/"),
            cookie_secure=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
            session_hours=float(os.environ.get("SESSION_HOURS", "12")),
        )
