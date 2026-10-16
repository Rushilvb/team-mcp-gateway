"""Audit log: one row per tool call through the gateway, including failures and denials."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import AuditLog

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    """Who is calling, as established by auth.py."""
    user_id: int
    email: str
    role: str                 # admin | member
    channel: str = "api"      # api (own API key) | chat (dashboard chat agent, Phase 6)
    api_key_id: int | None = None


class Timer:
    def __init__(self):
        self.started_at = datetime.now(timezone.utc)
        self._t0 = time.monotonic()

    @property
    def ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)


class Audit:
    def __init__(self, sessionmaker: async_sessionmaker):
        self._sm = sessionmaker

    async def record(self, *, user_id: int | None, channel: str, server_slug: str | None, tool: str,
                     timer: Timer, outcome: str, error: str | None = None) -> None:
        try:
            async with self._sm() as session:
                session.add(AuditLog(
                    user_id=user_id, channel=channel, server_slug=server_slug, tool=tool[:200],
                    started_at=timer.started_at, duration_ms=timer.ms, outcome=outcome,
                    error_message=error[:1000] if error else None,
                ))
                await session.commit()
        except Exception:
            # Don't turn a successful tool call into a failure because the audit
            # write failed, but make it loud.
            log.exception("AUDIT WRITE FAILED: user=%s tool=%s outcome=%s", user_id, tool, outcome)
