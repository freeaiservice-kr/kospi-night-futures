"""leader_scorer.py — 주도주 스코어링 엔진.

Phase 1 지표 (거래량 중심, 7개):
  vol_surge_1h  × 15  거래증가율 1h
  vol_surge_2h  × 10  거래증가율 2h
  vol_surge_4h  ×  8  거래증가율 4h
  buy_power     × 10  체결강도 (보너스: ≥120% → +5)
  w52_proximity ×  7  52주 신고가 근접 (≥90% → +점수)
  turnover      ×  5  거래량 회전율 (≥5% → 보너스)
  abs_change    ×  5  등락률 절대값 (방향 무관)
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from backend.leader_store import LeaderStore

logger = logging.getLogger(__name__)


class LeaderScorer:
    """매 폴링 사이클 후 전체 종목 스코어를 재계산."""

    def __init__(self, store: LeaderStore) -> None:
        self._store = store

    async def update_scores(self, current_ts: int) -> None:
        """전체 활성 종목 스코어 재계산 → leader_scores upsert."""
        stocks = await self._store.get_all_stocks()
        for stock in stocks:
            try:
                score, detail = await self._calculate_score(stock, current_ts)
                await self._store.upsert_leader_score(stock, score, detail, current_ts)
            except Exception as e:
                logger.warning("Score calc failed for %s: %s", stock["code"], e)

    async def _calculate_score(
        self, stock: dict, ts: int
    ) -> tuple[float, dict]:
        # AF-2: 4h lookback 안전 (prune cutoff = 4h+10min)
        since_4h = ts - 4 * 3600
        snapshots = await self._store.get_snapshots_since(stock["code"], since_4h)

        if not snapshots:
            return 0.0, {}

        latest = snapshots[-1]

        # -- 거래증가율 --
        vol_surge_1h = _calc_vol_surge(snapshots, ts, 3600)
        vol_surge_2h = _calc_vol_surge(snapshots, ts, 7200)
        vol_surge_4h = _calc_vol_surge(snapshots, ts, 14400)

        # -- 체결강도 --
        buy = latest.get("buy_power") or 0
        sell = latest.get("sell_power") or 0
        buy_power_ratio = (buy / sell * 100) if sell > 0 else 100.0
        buy_power_score = min(buy_power_ratio / 100.0, 2.0)  # 0~2
        buy_power_bonus = 0.5 if buy_power_ratio >= 120 else 0.0

        # -- 52주 신고가 근접률 --
        price = latest.get("price") or 0
        w52h = latest.get("w52_hgpr") or 0
        w52_proximity = (price / w52h) if w52h > 0 else 0.0
        w52_score = max(0.0, w52_proximity - 0.5) * 2.0  # 0.5이상 → 선형 증가

        # -- 거래량 회전율 --
        listed_shares = stock.get("listed_shares") or 0
        acml_vol = latest.get("acml_vol") or 0
        turnover = (acml_vol / listed_shares) if listed_shares > 0 else 0.0
        turnover_score = min(turnover / 0.05, 1.0)  # 5%가 만점 기준
        turnover_bonus = 0.5 if turnover >= 0.05 else 0.0

        # -- 등락률 절대값 --
        change_pct = latest.get("change_pct") or 0.0
        abs_change_score = min(abs(change_pct) / 5.0, 1.0)  # 5%가 만점 기준

        # -- 가중 합산 --
        total = (
            _norm_surge(vol_surge_1h)  * 15.0
            + _norm_surge(vol_surge_2h) * 10.0
            + _norm_surge(vol_surge_4h) *  8.0
            + buy_power_score           * 10.0
            + buy_power_bonus           *  5.0
            + w52_score                 *  7.0
            + turnover_score            *  5.0
            + turnover_bonus            *  2.5
            + abs_change_score          *  5.0
        )

        detail = {
            "vol_surge_1h":    round(vol_surge_1h, 4) if vol_surge_1h is not None else None,
            "vol_surge_2h":    round(vol_surge_2h, 4) if vol_surge_2h is not None else None,
            "vol_surge_4h":    round(vol_surge_4h, 4) if vol_surge_4h is not None else None,
            "buy_power_ratio": round(buy_power_ratio, 2),
            "w52_proximity":   round(w52_proximity, 4),
            "turnover":        round(turnover, 4),
            "change_pct":      round(change_pct, 2),
            "snapshot_count":  len(snapshots),
        }
        return round(total, 4), detail


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _calc_vol_surge(
    snapshots: list[dict], current_ts: int, window_seconds: int
) -> Optional[float]:
    """현재 acml_vol 대비 window_seconds 전 acml_vol 증가율.

    Returns None if baseline snapshot not found.
    """
    target_ts = current_ts - window_seconds
    # 목표 시간에 가장 가까운 스냅샷 찾기 (이전 방향)
    baseline: Optional[dict] = None
    for snap in snapshots:
        if snap["ts"] <= target_ts:
            baseline = snap
        else:
            break

    if baseline is None:
        # 가용 데이터로 부분 계산 (서비스 시작 직후)
        if len(snapshots) < 2:
            return None
        baseline = snapshots[0]

    base_vol = baseline.get("acml_vol") or 0
    current_vol = snapshots[-1].get("acml_vol") or 0

    if base_vol <= 0:
        return None
    return (current_vol - base_vol) / base_vol


def _norm_surge(surge: Optional[float]) -> float:
    """거래증가율 → 0~2 정규화 (200% 증가 = 2.0 만점)."""
    if surge is None:
        return 0.0
    return min(max(surge, 0.0), 2.0)
