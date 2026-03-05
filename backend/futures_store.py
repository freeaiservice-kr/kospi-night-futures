"""
KOSPI200 underlying (기초자산) price history storage backed by SQLite.

Stores 30-second snapshots of KOSPI200 index price.
Used to display intraday price trend on the options dashboard.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "futures_history.db"


class FuturesStore:
    """SQLite-backed KOSPI200 price snapshot storage."""

    def __init__(self):
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(DB_PATH))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute(
            "CREATE TABLE IF NOT EXISTS futures_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER NOT NULL, "
            "price REAL, "
            "change REAL, "
            "change_pct REAL"
            ")"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_futures_ts "
            "ON futures_snapshots(ts)"
        )
        await self._conn.commit()
        await self._prune_old()
        logger.info("FuturesStore initialized at %s", DB_PATH)

    async def _prune_old(self) -> None:
        """Keep only today's data."""
        from datetime import date
        today_start = int(datetime.combine(date.today(), datetime.min.time()).timestamp())
        if self._conn:
            await self._conn.execute(
                "DELETE FROM futures_snapshots WHERE ts < ?", (today_start,)
            )
            await self._conn.commit()

    async def save(self, price: Optional[float], change: Optional[float], change_pct: Optional[float]) -> None:
        """Store a price snapshot."""
        if not self._conn or price is None:
            return
        ts = int(datetime.now(timezone.utc).timestamp())
        await self._conn.execute(
            "INSERT INTO futures_snapshots (ts, price, change, change_pct) VALUES (?, ?, ?, ?)",
            (ts, price, change, change_pct)
        )
        await self._conn.commit()

    async def get_history(self, limit: int = 120) -> list[dict]:
        """Return last N snapshots (newest first)."""
        if not self._conn:
            return []
        async with self._conn.execute(
            "SELECT ts, price, change, change_pct FROM futures_snapshots "
            "ORDER BY ts DESC LIMIT ?",
            (limit,)
        ) as cur:
            rows = await cur.fetchall()

        result = []
        from zoneinfo import ZoneInfo
        for row in rows:
            ts, price, change, change_pct = row
            kst_time = datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Seoul")).strftime("%H:%M:%S")
            result.append({
                "ts": ts,
                "time": kst_time,
                "price": price,
                "change": change,
                "change_pct": change_pct,
            })
        return result

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
