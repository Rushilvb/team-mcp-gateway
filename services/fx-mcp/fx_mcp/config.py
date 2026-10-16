"""Settings, read once from environment variables (set by the Helm chart)."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    api_key: str                          # clients must send X-API-Key: <api_key>
    rates_file: str = "/config/rates.json"  # mounted from the fx-rates ConfigMap
    # Host headers the MCP endpoint accepts (DNS-rebinding protection).
    allowed_hosts: list[str] = field(default_factory=lambda: ["localhost:*", "127.0.0.1:*"])

    @classmethod
    def from_env(cls) -> "Settings":
        if not os.environ.get("FX_API_KEY"):
            raise RuntimeError("missing required environment variable: FX_API_KEY")
        return cls(
            api_key=os.environ["FX_API_KEY"],
            rates_file=os.environ.get("RATES_FILE", "/config/rates.json"),
            allowed_hosts=[h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(",") if h.strip()],
        )
