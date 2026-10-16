"""Async database engine and sessions (SQLAlchemy 2 + asyncpg)."""

import asyncio
import logging
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

log = logging.getLogger(__name__)


def make_engine(url: str) -> AsyncEngine:
    # pool_pre_ping: transparently replace connections that died (e.g. Postgres restarted).
    return create_async_engine(url, pool_size=5, max_overflow=5, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def wait_for_db(engine: AsyncEngine, seconds: float = 60) -> None:
    """Retry until Postgres answers (it may still be starting)."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as exc:
            if time.monotonic() > deadline:
                raise
            log.warning("database not ready (%s), retrying", exc.__class__.__name__)
            await asyncio.sleep(2)
