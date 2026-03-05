"""
Options data service: polls KIS options board + investor flow,
broadcasts to browser WebSocket clients.

Market hours: 08:45 ~ 15:45 KST weekdays.
Poll intervals: board every 5s, investor every 30s.
"""
import asyncio
import json
import logging
from datetime import date as _date, datetime
from typing import Optional

from fastapi import WebSocket
from backend.config import settings
from backend.kis_client import KISClient, KISAuthError
from backend.market_status import get_options_market_status

logger = logging.getLogger(__name__)

BOARD_POLL_INTERVAL = 5   # seconds
INVESTOR_POLL_INTERVAL = 30  # seconds

# Product config: (display_name, market_iscd, call_iscd2, put_iscd2, board_product_code)
PRODUCTS = {
    "KOSPI200": ("KOSPI200", "K2I", "OC01", "OP01", ""),
    "WKI":      ("위클리(목)", "WKI", "OC04", "OP04", "WKI"),
    "WKM":      ("위클리(월)", "WKM", "OC05", "OP05", "WKM"),
    "MKI":      ("미니KOSPI200", "MKI", "OC02", "OP02", "MKI"),
    "KQI":      ("KOSDAQ150", "KQI", "OC03", "OP03", "KQI"),
}


def _compute_expiry_code(product_key: str, ref_date: Optional[_date] = None) -> str:
    """Compute current front expiry code for a given product."""
    if ref_date is None:
        ref_date = _date.today()

    if product_key == "WKI":
        # Current week's Thursday in YYMMWW format
        # Find the Thursday of the current week
        days_until_thu = (3 - ref_date.weekday()) % 7
        thu = ref_date if ref_date.weekday() == 3 else ref_date.replace(
            day=ref_date.day + days_until_thu if days_until_thu >= 0 else ref_date.day + days_until_thu + 7
        )
        # Week number of month (1-indexed)
        week_num = (thu.day - 1) // 7 + 1
        return f"{thu.strftime('%y%m')}{week_num:02d}"

    if product_key == "WKM":
        # Current week's Monday in YYMMWW format
        days_until_mon = (0 - ref_date.weekday()) % 7
        mon = ref_date if ref_date.weekday() == 0 else ref_date.replace(
            day=ref_date.day + days_until_mon
        )
        week_num = (mon.day - 1) // 7 + 1
        return f"{mon.strftime('%y%m')}{week_num:02d}"

    # Monthly: YYYYMM format - find front month (current or next if expired)
    from calendar import monthcalendar, THURSDAY
    year, month = ref_date.year, ref_date.month
    for _ in range(4):
        weeks = monthcalendar(year, month)
        thursdays = [w[THURSDAY] for w in weeks if w[THURSDAY] != 0]
        exp_day = thursdays[1] if len(thursdays) >= 2 else thursdays[0]
        exp_date = _date(year, month, exp_day)
        if ref_date <= exp_date:
            return f"{year}{month:02d}"
        month += 1
        if month > 12:
            month = 1
            year += 1
    return f"{ref_date.year}{ref_date.month:02d}"


def _serialize_strike(row: dict) -> dict:
    """Extract only needed fields from options board row."""
    return {
        "acpr": row.get("acpr", ""),
        "optn_prpr": row.get("optn_prpr", ""),
        "optn_prdy_vrss": row.get("optn_prdy_vrss", ""),
        "prdy_vrss_sign": row.get("prdy_vrss_sign", "3"),
        "optn_bidp": row.get("optn_bidp", ""),
        "optn_askp": row.get("optn_askp", ""),
        "acml_vol": row.get("acml_vol", "0"),
        "hts_ints_vltl": row.get("hts_ints_vltl", ""),
        "delta_val": row.get("delta_val", ""),
        "atm_cls_name": row.get("atm_cls_name", ""),
    }


def _serialize_investor(inv: dict) -> dict:
    """Extract investor fields (외국인/개인/기관계)."""
    return {
        "frgn_ntby": inv.get("frgn_ntby_qty", "0"),
        "prsn_ntby": inv.get("prsn_ntby_qty", "0"),
        "orgn_ntby": inv.get("orgn_ntby_qty", "0"),
        "frgn_seln": inv.get("frgn_seln_vol", "0"),
        "frgn_shnu": inv.get("frgn_shnu_vol", "0"),
        "prsn_seln": inv.get("prsn_seln_vol", "0"),
        "prsn_shnu": inv.get("prsn_shnu_vol", "0"),
        "orgn_seln": inv.get("orgn_seln_vol", "0"),
        "orgn_shnu": inv.get("orgn_shnu_vol", "0"),
    }


class OptionsDataService:
    """Poll KIS options board + investor flow, broadcast to browser WebSocket clients."""

    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._kis_client: Optional[KISClient] = None
        self._board_task: Optional[asyncio.Task] = None
        self._investor_task: Optional[asyncio.Task] = None
        self._running = False
        self._active_product = "WKI"  # default
        self._last_board: Optional[dict] = None
        self._last_investor: Optional[dict] = None

    async def start(self):
        self._running = True
        self._kis_client = KISClient()

        if not settings.kis_app_key or settings.kis_app_key == "your_app_key_here":
            logger.warning("KIS_APP_KEY not configured. Options service running without live data.")
            return

        try:
            await self._kis_client._get_token()
        except KISAuthError as e:
            logger.error("KIS auth failed for options service: %s", e)
            return
        except Exception as e:
            logger.error("Options service auth error: %s", e)
            return

        self._board_task = asyncio.create_task(self._board_poll_loop())
        self._investor_task = asyncio.create_task(self._investor_poll_loop())

    async def stop(self):
        self._running = False
        if self._board_task:
            self._board_task.cancel()
        if self._investor_task:
            self._investor_task.cancel()
        if self._kis_client:
            await self._kis_client.close()

    async def add_client(self, ws: WebSocket):
        self._clients.add(ws)
        logger.info("Options client connected. Total: %d", len(self._clients))
        # Send last known state immediately
        if self._last_board:
            try:
                await ws.send_text(json.dumps(self._last_board))
            except Exception:
                pass
        if self._last_investor:
            try:
                await ws.send_text(json.dumps(self._last_investor))
            except Exception:
                pass
        # Send current market status
        status = get_options_market_status()
        try:
            await ws.send_text(json.dumps({"type": "options_status", "is_open": status.is_open}))
        except Exception:
            pass

    def remove_client(self, ws: WebSocket):
        self._clients.discard(ws)
        logger.info("Options client disconnected. Total: %d", len(self._clients))

    async def _broadcast(self, payload: str):
        disconnected = set()
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.add(ws)
        self._clients -= disconnected

    async def _board_poll_loop(self):
        while self._running:
            status = get_options_market_status()
            if status.is_open and self._kis_client:
                try:
                    product_key = self._active_product
                    cfg = PRODUCTS.get(product_key, PRODUCTS["WKI"])
                    _, market_iscd, _, _, board_code = cfg
                    expiry = _compute_expiry_code(product_key)
                    calls_raw, puts_raw = await self._kis_client.get_options_board(board_code, expiry)
                    calls = [_serialize_strike(r) for r in calls_raw]
                    puts = [_serialize_strike(r) for r in puts_raw]
                    msg = {
                        "type": "options_board",
                        "product": product_key,
                        "expiry": expiry,
                        "calls": calls,
                        "puts": puts,
                    }
                    self._last_board = msg
                    await self._broadcast(json.dumps(msg))
                except Exception as e:
                    logger.warning("Options board poll error: %s", e)
            await asyncio.sleep(BOARD_POLL_INTERVAL)

    async def _investor_poll_loop(self):
        while self._running:
            status = get_options_market_status()
            if status.is_open and self._kis_client:
                try:
                    product_key = self._active_product
                    cfg = PRODUCTS.get(product_key, PRODUCTS["WKI"])
                    _, market_iscd, call_iscd2, put_iscd2, _ = cfg
                    inv = await self._kis_client.get_options_investor(market_iscd, call_iscd2, put_iscd2)
                    msg = {
                        "type": "investor_flow",
                        "product": product_key,
                        "call_investor": _serialize_investor(inv.get("call", {})),
                        "put_investor": _serialize_investor(inv.get("put", {})),
                    }
                    self._last_investor = msg
                    await self._broadcast(json.dumps(msg))
                except Exception as e:
                    logger.warning("Options investor poll error: %s", e)
            await asyncio.sleep(INVESTOR_POLL_INTERVAL)
