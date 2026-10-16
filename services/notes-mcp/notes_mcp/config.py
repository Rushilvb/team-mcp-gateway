"""Settings, read once from environment variables (set by the Helm chart).

Database connection settings are NOT here: asyncpg reads the standard libpq
variables (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD) itself, so the
password goes straight from the Kubernetes Secret into the driver.
"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    token: str                     # clients must send Authorization: Bearer <token>
    db_wait_seconds: float = 60    # how long to wait for Postgres at startup
    # Host headers the MCP endpoint accepts (DNS-rebinding protection).
    allowed_hosts: list[str] = field(default_factory=lambda: ["localhost:*", "127.0.0.1:*"])

    @classmethod
    def from_env(cls) -> "Settings":
        if not os.environ.get("NOTES_TOKEN"):
            raise RuntimeError("missing required environment variable: NOTES_TOKEN")
        return cls(
            token=os.environ["NOTES_TOKEN"],
            db_wait_seconds=float(os.environ.get("DB_WAIT_SECONDS", "60")),
            allowed_hosts=[h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(",") if h.strip()],
        )
