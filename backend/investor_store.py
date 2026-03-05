"""
Investor flow history storage backed by SQLite.

Stores 30-second snapshots of options investor net-buy data per product.
Used to display intraday trend of investor positions.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "investor_history.db"

_COLS = ["call_frgn", "call_prsn", "call_orgn", "put_frgn", "put_prsn", "put_orgn"]


class InvestorStore:
    """SQLite-backed investor flow snapshot storage."""

    def __init__(self):
        self._conn: Optional[aiosqlite.Connection] = None
        # In-memory last snapshot per product for delta computation
        self._last: dict[str, dict] = {}

    async def init(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(DB_PATH))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute(
            "CREATE TABLE IF NOT EXISTS investor_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER NOT NULL, "
            "product TEXT NOT NULL, "
            "call_frgn INTEGER, call_prsn INTEGER, call_orgn INTEGER, "
            "put_frgn INTEGER, put_prsn INTEGER, put_orgn INTEGER"
            ")"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inv_product_ts "
            "ON investor_snapshots(product, ts)"
        )
        await self._conn.commit()
        # Prune records older than today (keep only current trading day)
        await self._prune_old()
        # Load last snapshot per product into memory
        await self._load_last()
        logger.info("InvestorStore initialized at %s", DB_PATH)

    async def _prune_old(self) -> None:
        """Keep only today's data."""
        from datetime import date
        today_start = int(datetime.combine(date.today(), datetime.min.time()).timestamp())
        if self._conn:
            await self._conn.execute(
                "DELETE FROM investor_snapshots WHERE ts < ?", (today_start,)
            )
            await self._conn.commit()

    async def _load_last(self) -> None:
        """Load the most recent snapshot per product into memory."""
        if not self._conn:
            return
        async with self._conn.execute(
            "SELECT product, ts, call_frgn, call_prsn, call_orgn, "
            "put_frgn, put_prsn, put_orgn "
            "FROM investor_snapshots "
            "WHERE id IN (SELECT MAX(id) FROM investor_snapshots GROUP BY product)"
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            product = row[0]
            self._last[product] = {
                "ts": row[1],
                "call_frgn": row[2], "call_prsn": row[3], "call_orgn": row[4],
                "put_frgn": row[5], "put_prsn": row[6], "put_orgn": row[7],
            }

    async def save(self, product: str, call_inv: dict, put_inv: dict) -> Optional[dict]:
        """
        Store a snapshot. Returns delta vs previous snapshot, or None if first record.
        Delta keys: call_frgn, call_prsn, call_orgn, put_frgn, put_prsn, put_orgn.
        """
        def _i(d: dict, k: str) -> int:
            return int(d.get(k, 0) or 0)

        ts = int(datetime.now(timezone.utc).timestamp())
        snapshot = {
            "ts": ts,
            "call_frgn": _i(call_inv, "frgn_ntby"),
            "call_prsn": _i(call_inv, "prsn_ntby"),
            "call_orgn": _i(call_inv, "orgn_ntby"),
            "put_frgn": _i(put_inv, "frgn_ntby"),
            "put_prsn": _i(put_inv, "prsn_ntby"),
            "put_orgn": _i(put_inv, "orgn_ntby"),
        }

        if self._conn:
            await self._conn.execute(
                "INSERT INTO investor_snapshots "
                "(ts, product, call_frgn, call_prsn, call_orgn, put_frgn, put_prsn, put_orgn) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, product, snapshot["call_frgn"], snapshot["call_prsn"],
                 snapshot["call_orgn"], snapshot["put_frgn"],
                 snapshot["put_prsn"], snapshot["put_orgn"])
            )
            await self._conn.commit()

        # Compute delta
        delta = None
        prev = self._last.get(product)
        if prev:
            delta = {k: snapshot[k] - prev[k] for k in _COLS}

        self._last[product] = snapshot
        return delta

    async def get_history(self, product: str, limit: int = 60) -> list[dict]:
        """Return last N snapshots for a product (newest first)."""
        if not self._conn:
            return []
        async with self._conn.execute(
            "SELECT ts, call_frgn, call_prsn, call_orgn, put_frgn, put_prsn, put_orgn "
            "FROM investor_snapshots WHERE product = ? "
            "ORDER BY ts DESC LIMIT ?",
            (product, limit)
        ) as cur:
            rows = await cur.fetchall()

        result = []
        for i, row in enumerate(rows):
            ts, cf, cp, co, pf, pp, po = row
            # Delta vs next older record (rows are newest-first)
            delta = None
            if i + 1 < len(rows):
                prev = rows[i + 1]
                delta = {
                    "call_frgn": cf - prev[1], "call_prsn": cp - prev[2], "call_orgn": co - prev[3],
                    "put_frgn": pf - prev[4], "put_prsn": pp - prev[5], "put_orgn": po - prev[6],
                }
            from zoneinfo import ZoneInfo
            kst_time = datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Seoul")).strftime("%H:%M:%S")
            result.append({
                "ts": ts,
                "time": kst_time,
                "call_frgn": cf, "call_prsn": cp, "call_orgn": co,
                "put_frgn": pf, "put_prsn": pp, "put_orgn": po,
                "delta": delta,
            })
        return result

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
