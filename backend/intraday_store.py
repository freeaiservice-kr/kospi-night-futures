"""
Intraday chart tick storage backed by SQLite.

Single persistent connection pattern — safe for asyncio single-threaded use.
WAL mode enabled to allow concurrent readers alongside the single writer.
"""

import logging
from datetime import datetime
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "intraday.db"


class IntradayStore:
    """SQLite-backed intraday chart tick storage.

    One persistent connection is kept alive for the process lifetime.
    Since asyncio is single-threaded, no additional locking is needed.
    """

    def __init__(self):
        self._conn: aiosqlite.Connection | None = None

    async def init(self, session_start_ts: int) -> None:
        """Initialize DB, create table, prune stale data from previous sessions."""
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(DB_PATH))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute(
            "CREATE TABLE IF NOT EXISTS chart_ticks "
            "(ts INTEGER PRIMARY KEY, price REAL)"
        )
        await self._conn.commit()
        await self.prune_old_sessions(session_start_ts)
        logger.info("IntradayStore initialized at %s", DB_PATH)

    async def insert(self, ts: int, price: float) -> None:
        """Insert or replace a chart tick."""
        if self._conn is None:
            return
        await self._conn.execute(
            "INSERT OR REPLACE INTO chart_ticks (ts, price) VALUES (?, ?)", (ts, price)
        )
        await self._conn.commit()

    async def get_session_ticks(self, session_start_ts: int) -> list[dict]:
        """Return all ticks since session_start_ts as [{timestamp, price}]."""
        if self._conn is None:
            return []
        async with self._conn.execute(
            "SELECT ts, price FROM chart_ticks WHERE ts >= ? ORDER BY ts ASC",
            (session_start_ts,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "timestamp": datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "price": price,
            }
            for ts, price in rows
        ]

    async def prune_old_sessions(self, session_start_ts: int) -> None:
        """Delete ticks from before the current session."""
        if self._conn is None:
            return
        await self._conn.execute(
            "DELETE FROM chart_ticks WHERE ts < ?", (session_start_ts,)
        )
        await self._conn.commit()
        logger.info("Pruned old chart ticks (before ts=%d)", session_start_ts)

    async def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
