"""leader_store.py — 주도주 탐지 SQLite 스토어.

테이블: stocks, stock_snapshots (4h+10m), daily_bars (120일), leader_scores
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "data" / "leader_analysis.db"

_DDL = [
    """CREATE TABLE IF NOT EXISTS stocks (
        code            TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        market          TEXT NOT NULL,
        sector_code     TEXT,
        sector_category TEXT,
        listed_shares   INTEGER,
        is_active       INTEGER DEFAULT 1,
        updated_at      INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS stock_snapshots (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ts            INTEGER NOT NULL,
        code          TEXT    NOT NULL,
        price         INTEGER,
        change_pct    REAL,
        acml_vol      INTEGER,
        acml_tr_pbmn  REAL,
        buy_power     INTEGER,
        sell_power    INTEGER,
        w52_hgpr      INTEGER,
        high_price    INTEGER,
        low_price     INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ss_code_ts ON stock_snapshots(code, ts)",
    "CREATE INDEX IF NOT EXISTS idx_ss_ts      ON stock_snapshots(ts)",
    """CREATE TABLE IF NOT EXISTS daily_bars (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        code      TEXT NOT NULL,
        date      TEXT NOT NULL,
        open      INTEGER,
        high      INTEGER,
        low       INTEGER,
        close     INTEGER,
        volume    INTEGER,
        tr_pbmn   REAL,
        UNIQUE(code, date)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_db_code_date ON daily_bars(code, date)",
    """CREATE TABLE IF NOT EXISTS leader_scores (
        code            TEXT PRIMARY KEY,
        name            TEXT,
        sector_code     TEXT,
        sector_category TEXT,
        score           REAL,
        score_detail    TEXT,
        change_pct      REAL,
        price           INTEGER,
        acml_vol        INTEGER,
        vol_surge_1h    REAL,
        vol_surge_1d    REAL,
        updated_at      INTEGER
    )""",
]

# 4시간 10분 (AF-2: scorer 4h lookback에 2 사이클 마진)
_SNAPSHOT_PRUNE_SECONDS = 4 * 3600 + 600


class LeaderStore:
    """SQLite store for leader detection: stocks, snapshots, daily bars, scores."""

    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        for stmt in _DDL:
            await self._db.execute(stmt)
        await self._db.commit()
        await self._prune_old()
        logger.info("LeaderStore initialized at %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Stocks master
    # ------------------------------------------------------------------

    async def upsert_stocks(self, stocks: list[dict]) -> None:
        """시드/전종목 마스터 upsert."""
        if not self._db:
            return
        now = int(time.time())
        await self._db.executemany(
            """INSERT OR REPLACE INTO stocks
               (code, name, market, sector_code, sector_category, is_active, updated_at)
               VALUES (?,?,?,?,?,1,?)""",
            [
                (s["code"], s["name"], s["market"],
                 s.get("sector_code"), s.get("sector_category"), now)
                for s in stocks
            ],
        )
        await self._db.commit()
        logger.info("Upserted %d stocks into master", len(stocks))

    async def update_listed_shares(self, code: str, listed_shares: int) -> None:
        """폴링 시 상장주수 업데이트."""
        if not self._db:
            return
        await self._db.execute(
            "UPDATE stocks SET listed_shares=? WHERE code=?",
            (listed_shares, code),
        )
        await self._db.commit()

    async def get_all_stocks(self) -> list[dict]:
        """활성 종목 전체 반환."""
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT code, name, market, sector_code, sector_category, listed_shares "
            "FROM stocks WHERE is_active=1 ORDER BY code"
        ) as cur:
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
        return [dict(zip(cols, r)) for r in rows]

    # ------------------------------------------------------------------
    # Intraday snapshots
    # ------------------------------------------------------------------

    async def save_snapshots(self, ts: int, records: list[dict]) -> None:
        """종목 스냅샷 bulk insert."""
        if not self._db or not records:
            return
        await self._db.executemany(
            """INSERT INTO stock_snapshots
               (ts, code, price, change_pct, acml_vol, acml_tr_pbmn,
                buy_power, sell_power, w52_hgpr, high_price, low_price)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    ts,
                    r["code"],
                    r.get("price"),
                    r.get("change_pct"),
                    r.get("acml_vol"),
                    r.get("acml_tr_pbmn"),
                    r.get("buy_power"),
                    r.get("sell_power"),
                    r.get("w52_hgpr"),
                    r.get("high_price"),
                    r.get("low_price"),
                )
                for r in records
            ],
        )
        await self._db.commit()

    async def get_snapshots_since(self, code: str, since_ts: int) -> list[dict]:
        """code 의 since_ts 이후 스냅샷 목록 (오름차순)."""
        if not self._db:
            return []
        async with self._db.execute(
            """SELECT ts, price, change_pct, acml_vol, acml_tr_pbmn,
                      buy_power, sell_power, w52_hgpr, high_price, low_price
               FROM stock_snapshots WHERE code=? AND ts>=?
               ORDER BY ts ASC""",
            (code, since_ts),
        ) as cur:
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
        return [dict(zip(cols, r)) for r in rows]

    async def get_latest_snapshot(self, code: str) -> Optional[dict]:
        """code 의 가장 최신 스냅샷 1건."""
        if not self._db:
            return None
        async with self._db.execute(
            """SELECT ts, price, change_pct, acml_vol, acml_tr_pbmn,
                      buy_power, sell_power, w52_hgpr, high_price, low_price
               FROM stock_snapshots WHERE code=?
               ORDER BY ts DESC LIMIT 1""",
            (code,),
        ) as cur:
            cols = [d[0] for d in cur.description]
            row = await cur.fetchone()
        if row is None:
            return None
        return dict(zip(cols, row))

    # ------------------------------------------------------------------
    # Daily bars (Phase 1.5)
    # ------------------------------------------------------------------

    async def save_daily_bars(self, code: str, bars: list[dict]) -> None:
        """일봉 데이터 bulk upsert."""
        if not self._db or not bars:
            return
        rows = []
        for b in bars:
            date_raw = b.get("stck_bsop_date", "")
            if len(date_raw) == 8:
                date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
            else:
                date = date_raw
            rows.append((
                code, date,
                _safe_int(b.get("stck_oprc")),
                _safe_int(b.get("stck_hgpr")),
                _safe_int(b.get("stck_lwpr")),
                _safe_int(b.get("stck_clpr")),
                _safe_int(b.get("acml_vol")),
                _safe_float(b.get("acml_tr_pbmn")),
            ))
        await self._db.executemany(
            """INSERT OR REPLACE INTO daily_bars
               (code, date, open, high, low, close, volume, tr_pbmn)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )
        await self._db.commit()

    async def get_latest_daily_bar_date(self, code: str) -> Optional[str]:
        """code 의 가장 최신 일봉 날짜 (YYYY-MM-DD). 없으면 None."""
        if not self._db:
            return None
        async with self._db.execute(
            "SELECT MAX(date) FROM daily_bars WHERE code=?", (code,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row and row[0] else None

    # ------------------------------------------------------------------
    # Leader scores
    # ------------------------------------------------------------------

    async def upsert_leader_score(
        self, stock: dict, score: float, detail: dict, ts: int
    ) -> None:
        if not self._db:
            return
        latest = await self.get_latest_snapshot(stock["code"])
        await self._db.execute(
            """INSERT OR REPLACE INTO leader_scores
               (code, name, sector_code, sector_category, score, score_detail,
                change_pct, price, acml_vol, vol_surge_1h, vol_surge_1d, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                stock["code"],
                stock["name"],
                stock.get("sector_code"),
                stock.get("sector_category"),
                score,
                json.dumps(detail, ensure_ascii=False),
                latest.get("change_pct") if latest else None,
                latest.get("price") if latest else None,
                latest.get("acml_vol") if latest else None,
                detail.get("vol_surge_1h"),
                detail.get("vol_surge_1d"),
                ts,
            ),
        )
        await self._db.commit()

    async def get_latest_scores(self, top_n: int = 50) -> list[dict]:
        """스코어 내림차순 상위 N개."""
        if not self._db:
            return []
        async with self._db.execute(
            """SELECT code, name, sector_code, sector_category, score, score_detail,
                      change_pct, price, acml_vol, vol_surge_1h, vol_surge_1d, updated_at
               FROM leader_scores ORDER BY score DESC LIMIT ?""",
            (top_n,),
        ) as cur:
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            d["score_detail"] = json.loads(d.get("score_detail") or "{}")
            result.append(d)
        return result

    async def get_scores_by_sector(self, sector_code: str) -> list[dict]:
        """섹터별 주도주 (스코어 내림차순)."""
        if not self._db:
            return []
        async with self._db.execute(
            """SELECT code, name, sector_code, sector_category, score, score_detail,
                      change_pct, price, acml_vol, vol_surge_1h, updated_at
               FROM leader_scores WHERE sector_code=? ORDER BY score DESC""",
            (sector_code,),
        ) as cur:
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(zip(cols, row))
            d["score_detail"] = json.loads(d.get("score_detail") or "{}")
            result.append(d)
        return result

    async def get_score_detail(self, code: str) -> Optional[dict]:
        """종목 상세 (스코어 + 최근 스냅샷 히스토리)."""
        if not self._db:
            return None
        async with self._db.execute(
            "SELECT * FROM leader_scores WHERE code=?", (code,)
        ) as cur:
            cols = [d[0] for d in cur.description]
            row = await cur.fetchone()
        if not row:
            return None
        d = dict(zip(cols, row))
        d["score_detail"] = json.loads(d.get("score_detail") or "{}")
        # 최근 1시간 스냅샷
        since = int(time.time()) - 3600
        d["snapshots"] = await self.get_snapshots_since(code, since)
        return d

    # ------------------------------------------------------------------
    # Prune
    # ------------------------------------------------------------------

    async def _prune_old(self) -> None:
        """4시간 10분 이상 된 stock_snapshots 삭제 (AF-2)."""
        if not self._db:
            return
        cutoff = int(time.time()) - _SNAPSHOT_PRUNE_SECONDS
        await self._db.execute(
            "DELETE FROM stock_snapshots WHERE ts < ?", (cutoff,)
        )
        await self._db.commit()


def _safe_int(v: object) -> Optional[int]:
    try:
        return int(v) if v is not None and v != "" else None  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None


def _safe_float(v: object) -> Optional[float]:
    try:
        return float(v) if v is not None and v != "" else None  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
