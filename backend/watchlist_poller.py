"""watchlist_poller.py — 종목 리스트 연속 순회 폴링 엔진.

- throttle_group="leader" 전용 10 req/s (v1 default 그룹과 독립)
- 장중(09:00~15:30 KST)에만 동작
- 서비스 시작 시 Phase 1.5 daily_bars 배치 로딩
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from backend.kis_client import KISClient, KISDailyLimitError, KISRateLimitError
from backend.leader_scorer import LeaderScorer
from backend.leader_store import LeaderStore
from backend.stock_master import load_seed_stocks

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
_MARKET_OPEN  = (9,  0)
_MARKET_CLOSE = (15, 30)


class WatchlistPoller:
    """종목 리스트를 연속 순회하며 현재가를 수집하는 폴링 엔진."""

    def __init__(
        self,
        kis_client: KISClient,
        store: LeaderStore,
        scorer: LeaderScorer,
    ) -> None:
        self._kis = kis_client
        self._store = store
        self._scorer = scorer
        self._stocks: list[dict] = []
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._cycle_count = 0
        self._last_cycle_ts: int = 0
        self._last_cycle_duration: float = 0.0
        self._api_errors_last_cycle: int = 0

    async def start(self) -> None:
        self._stocks = load_seed_stocks()
        await self._store.upsert_stocks(self._stocks)
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("WatchlistPoller started with %d stocks", len(self._stocks))

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("WatchlistPoller stopped")

    def get_status(self) -> dict:
        return {
            "running":              self._running,
            "stock_count":          len(self._stocks),
            "cycle_count":          self._cycle_count,
            "last_cycle_ts":        self._last_cycle_ts,
            "last_cycle_duration_s": round(self._last_cycle_duration, 2),
            "api_errors_last_cycle": self._api_errors_last_cycle,
            "daily_call_count":     getattr(self._kis, "_daily_call_count", 0),
        }

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        # Phase 1.5: 서비스 시작 시 daily_bars 배치 로딩 (1회)
        await self._load_daily_bars()

        while self._running:
            if not self._is_market_hours():
                await asyncio.sleep(60)
                continue

            cycle_start = time.monotonic()
            await self._run_cycle()
            self._last_cycle_duration = time.monotonic() - cycle_start
            self._cycle_count += 1
            self._last_cycle_ts = int(time.time())

            logger.debug(
                "Poller cycle %d done in %.1fs, errors=%d",
                self._cycle_count,
                self._last_cycle_duration,
                self._api_errors_last_cycle,
            )

            # 주기적으로 오래된 스냅샷 정리
            if self._cycle_count % 60 == 0:
                await self._store._prune_old()

    async def _run_cycle(self) -> None:
        """1사이클: 전체 종목 순회 → 스냅샷 저장 → 스코어 갱신."""
        ts = int(time.time())
        records: list[dict] = []
        self._api_errors_last_cycle = 0

        for stock in self._stocks:
            try:
                # AF-1: throttle_group="leader" (전용 10 req/s)
                data = await self._kis.get_stock_price(
                    stock["code"], throttle_group="leader"
                )
                rec = _parse_snapshot(stock["code"], data)
                records.append(rec)

                # 상장주수 업데이트 (첫 폴링 시)
                listed = _safe_int(data.get("lstn_stcn"))
                if listed and not stock.get("listed_shares"):
                    stock["listed_shares"] = listed
                    await self._store.update_listed_shares(stock["code"], listed)

            except KISDailyLimitError:
                logger.error("Daily API limit reached, stopping cycle")
                self._api_errors_last_cycle += 1
                break
            except KISRateLimitError as e:
                logger.warning("Rate limit for %s: %s, backoff 2s", stock["code"], e)
                self._api_errors_last_cycle += 1
                await asyncio.sleep(2.0)
            except Exception as e:
                logger.debug("Poll %s failed: %s", stock["code"], e)
                self._api_errors_last_cycle += 1

        if records:
            await self._store.save_snapshots(ts, records)

        await self._scorer.update_scores(ts)

    # ------------------------------------------------------------------
    # Phase 1.5: daily_bars 배치 로딩
    # ------------------------------------------------------------------

    async def _load_daily_bars(self) -> None:
        """서비스 시작 시 1회: 48종목 × 최대 2 API = ~96 calls, ~10초."""
        logger.info("Phase 1.5: Loading daily bars for %d stocks...", len(self._stocks))
        loaded = 0
        skipped = 0
        today_str = datetime.now(KST).strftime("%Y-%m-%d")

        for stock in self._stocks:
            try:
                latest = await self._store.get_latest_daily_bar_date(stock["code"])
                if latest and latest >= today_str[:8]:  # 오늘 이미 로딩
                    skipped += 1
                    continue

                bars = await self._kis.get_daily_price(
                    stock["code"], period=120, throttle_group="leader"
                )
                if bars:
                    await self._store.save_daily_bars(stock["code"], bars)
                    loaded += 1
            except KISDailyLimitError:
                logger.error("Daily limit hit during daily_bars loading, aborting")
                break
            except Exception as e:
                logger.warning("daily_bars load failed for %s: %s", stock["code"], e)

        logger.info(
            "Phase 1.5 done: loaded=%d, skipped=%d (already up-to-date)",
            loaded, skipped,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_market_hours() -> bool:
        now = datetime.now(KST)
        if now.weekday() >= 5:  # 토/일
            return False
        t = (now.hour, now.minute)
        return _MARKET_OPEN <= t < _MARKET_CLOSE


def _parse_snapshot(code: str, data: dict) -> dict:
    """FHKST01010100 output → snapshot record."""
    return {
        "code":        code,
        "price":       _safe_int(data.get("stck_prpr")),
        "change_pct":  _safe_float(data.get("prdy_ctrt")),
        "acml_vol":    _safe_int(data.get("acml_vol")),
        "acml_tr_pbmn": _safe_float(data.get("acml_tr_pbmn")),
        "buy_power":   _safe_int(data.get("shnu_cnqn_smtn")),
        "sell_power":  _safe_int(data.get("seln_cnqn_smtn")),
        "w52_hgpr":    _safe_int(data.get("w52_hgpr")),
        "high_price":  _safe_int(data.get("stck_hgpr")),
        "low_price":   _safe_int(data.get("stck_lwpr")),
    }


def _safe_int(v: object) -> int | None:
    try:
        return int(v) if v is not None and v != "" else None  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None


def _safe_float(v: object) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
