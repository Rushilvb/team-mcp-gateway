"""Notes storage in Postgres (asyncpg, plain SQL; one table, no ORM needed)."""

import asyncio
import logging
import time

import asyncpg

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title       text,
    body        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
)
"""


class NotesDB:
    def __init__(self, dsn: str | None = None):
        # dsn=None: asyncpg uses the PG* environment variables.
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self, wait_seconds: float = 60) -> None:
        """Open the pool, retrying while Postgres starts, then ensure the table exists."""
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
                break
            except (OSError, asyncpg.PostgresError) as exc:
                if time.monotonic() > deadline:
                    raise
                log.warning("database not ready (%s), retrying", exc.__class__.__name__)
                await asyncio.sleep(2)
        await self._pool.execute(SCHEMA)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("NotesDB.connect() was not called")
        return self._pool

    async def add(self, body: str, title: str | None) -> asyncpg.Record:
        return await self.pool.fetchrow(
            "INSERT INTO notes (title, body) VALUES ($1, $2) RETURNING id, title, body, created_at", title, body)

    async def list_recent(self, limit: int, offset: int) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT id, title, body, created_at FROM notes ORDER BY created_at DESC, id DESC LIMIT $1 OFFSET $2",
            limit, offset)

    async def search(self, query: str, limit: int) -> list[asyncpg.Record]:
        # Case-insensitive substring match. Escape LIKE wildcards so a search
        # for "50%" means the text "50%", not "50 followed by anything".
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return await self.pool.fetch(
            """SELECT id, title, body, created_at FROM notes
               WHERE body ILIKE '%' || $1 || '%' OR title ILIKE '%' || $1 || '%'
               ORDER BY created_at DESC, id DESC LIMIT $2""",
            escaped, limit)

    async def delete_all(self) -> None:
        """Tests only."""
        await self.pool.execute("TRUNCATE notes RESTART IDENTITY")
