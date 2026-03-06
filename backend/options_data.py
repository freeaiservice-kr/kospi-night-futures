"""
Options data service: polls KIS options board + investor flow,
broadcasts to browser WebSocket clients.

Market hours: 08:45 ~ 15:45 KST weekdays.
Poll intervals: board every 5s, investor every 30s.
"""
import asyncio
import json
import logging
from calendar import THURSDAY, monthcalendar
from datetime import date as _date
from datetime import datetime
from typing import Optional

from fastapi import WebSocket

from backend.config import settings
from backend.futures_store import FuturesStore
from backend.investor_store import InvestorStore
from backend.kis_client import KISAuthError, KISClient
from backend.market_status import get_market_status, get_options_market_status

# Products that trade on the night session (18:00~05:00 KST)
_NIGHT_PRODUCTS = {"WKI", "WKM"}

logger = logging.getLogger(__name__)

BOARD_POLL_INTERVAL = 5   # seconds
INVESTOR_POLL_INTERVAL = 30  # seconds
FUTURES_POLL_INTERVAL = 2  # seconds — KOSPI200 futures price

# Product config: (display_name, market_iscd, call_iscd2, put_iscd2, board_product_code)
PRODUCTS = {
    "KOSPI200": ("KOSPI200", "K2I", "OC01", "OP01", ""),
    "WKI":      ("위클리(목)", "WKI", "OC04", "OP04", "WKI"),
    "WKM":      ("위클리(월)", "WKM", "OC05", "OP05", "WKM"),
    "MKI":      ("미니KOSPI200", "MKI", "OC02", "OP02", "MKI"),
    "KQI":      ("KOSDAQ150", "KQI", "OC03", "OP03", "KQI"),
}

_KO_WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]


def _compute_kospi200_futures_symbol(ref_date: Optional[_date] = None) -> str:
    """Compute front-month KOSPI200 daytime futures short code (101V{Y}{MM})."""
    if ref_date is None:
        ref_date = _date.today()
    for month in (3, 6, 9, 12):
        weeks = monthcalendar(ref_date.year, month)
        thursdays = [w[THURSDAY] for w in weeks if w[THURSDAY] != 0]
        exp_day = thursdays[1] if len(thursdays) >= 2 else thursdays[0]
        exp_date = _date(ref_date.year, month, exp_day)
        if ref_date <= exp_date:
            return f"101V{ref_date.year % 10}{month:02d}"
    return f"101V{(ref_date.year + 1) % 10}03"


def _compute_expiry_code(product_key: str, ref_date: Optional[_date] = None) -> str:
    """Compute current front expiry code for a given product."""
    if ref_date is None:
        ref_date = _date.today()

    if product_key == "WKI":
        # Current week's Thursday in YYMMWW format
        days_until_thu = (3 - ref_date.weekday()) % 7
        if days_until_thu == 0 and ref_date.weekday() != 3:
            days_until_thu = 7
        thu = ref_date if ref_date.weekday() == 3 else _date(
            ref_date.year, ref_date.month, ref_date.day + days_until_thu
        ) if (ref_date.day + days_until_thu) <= 31 else ref_date
        # Safer calculation
        from datetime import timedelta
        days_to_thu = (3 - ref_date.weekday()) % 7
        thu = ref_date + timedelta(days=days_to_thu)
        week_num = (thu.day - 1) // 7 + 1
        return f"{thu.strftime('%y%m')}{week_num:02d}"

    if product_key == "WKM":
        # Current week's Monday in YYMMWW format
        from datetime import timedelta
        days_to_mon = (0 - ref_date.weekday()) % 7
        mon = ref_date + timedelta(days=days_to_mon)
        week_num = (mon.day - 1) // 7 + 1
        return f"{mon.strftime('%y%m')}{week_num:02d}"

    # Monthly: YYYYMM format - find front month (current or next if expired)
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


def _compute_expiry_date(product_key: str, expiry_code: str) -> Optional[_date]:
    """Convert expiry code to actual calendar date."""
    try:
        if product_key in ("WKI", "WKM"):
            # YYMMWW → find Nth Thursday (WKI) or Monday (WKM)
            yy = int(expiry_code[:2])
            mm = int(expiry_code[2:4])
            ww = int(expiry_code[4:6])
            year = 2000 + yy
            target_weekday = 3 if product_key == "WKI" else 0  # Thu=3, Mon=0
            count = 0
            for day in range(1, 32):
                try:
                    d = _date(year, mm, day)
                    if d.weekday() == target_weekday:
                        count += 1
                        if count == ww:
                            return d
                except ValueError:
                    break
        else:
            # YYYYMM → second Thursday
            year = int(expiry_code[:4])
            month = int(expiry_code[4:6])
            weeks = monthcalendar(year, month)
            thursdays = [w[THURSDAY] for w in weeks if w[THURSDAY] != 0]
            day = thursdays[1] if len(thursdays) >= 2 else thursdays[0]
            return _date(year, month, day)
    except Exception:
        pass
    return None


def _format_expiry_date(expiry_date: Optional[_date]) -> str:
    """Format expiry date as 'YYYY/MM/DD(요일)'."""
    if expiry_date is None:
        return "—"
    dow = _KO_WEEKDAY[expiry_date.weekday()]
    return f"{expiry_date.strftime('%Y/%m/%d')}({dow})"


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
        "gama": row.get("gama", ""),
        "vega": row.get("vega", ""),
        "theta": row.get("theta", ""),
        "hts_otst_stpl_qty": row.get("hts_otst_stpl_qty", "0"),
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
        self._client_products: dict[WebSocket, str] = {}  # per-client product
        self._kis_client: Optional[KISClient] = None
        self._board_task: Optional[asyncio.Task] = None
        self._investor_task: Optional[asyncio.Task] = None
        self._futures_task: Optional[asyncio.Task] = None
        self._running = False
        # Per-product caches
        self._last_board: dict[str, dict] = {}
        self._last_investor: dict[str, dict] = {}
        self._last_futures: Optional[dict] = None
        self._investor_store = InvestorStore()
        self._futures_store = FuturesStore()
        self._last_futures_save_ts: float = 0  # throttle saves to every 30s

    async def start(self):
        self._running = True
        self._kis_client = KISClient()
        await self._investor_store.init()
        await self._futures_store.init()

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
        self._futures_task = asyncio.create_task(self._futures_poll_loop())

    async def stop(self):
        self._running = False
        if self._board_task:
            self._board_task.cancel()
        if self._investor_task:
            self._investor_task.cancel()
        if self._futures_task:
            self._futures_task.cancel()
        if self._kis_client:
            await self._kis_client.close()
        await self._investor_store.close()
        await self._futures_store.close()

    async def add_client(self, ws: WebSocket, product: str = "WKI"):
        product = product if product in PRODUCTS else "WKI"
        self._clients.add(ws)
        self._client_products[ws] = product
        logger.info("Options client connected (product=%s). Total: %d", product, len(self._clients))

        # Send cached state for this product immediately
        if product in self._last_board:
            try:
                await ws.send_text(json.dumps(self._last_board[product]))
            except Exception:
                pass
        if product in self._last_investor:
            try:
                await ws.send_text(json.dumps(self._last_investor[product]))
            except Exception:
                pass

        # Send last known futures price
        if self._last_futures:
            try:
                await ws.send_text(json.dumps(self._last_futures))
            except Exception:
                pass

        # Send current market status
        status = get_options_market_status()
        try:
            await ws.send_text(json.dumps({"type": "options_status", "is_open": status.is_open}))
        except Exception:
            pass

    @property
    def investor_store(self) -> InvestorStore:
        return self._investor_store

    @property
    def futures_store(self) -> FuturesStore:
        return self._futures_store

    def get_latest_snapshot(self, product: str = "WKI") -> dict:
        """Return latest options + investor + futures payload for polling clients."""
        product_key = product if product in PRODUCTS else "WKI"

        board = self._last_board.get(product_key, {})
        investor = self._last_investor.get(product_key, {})
        futures = self._last_futures or {}

        return {
            "type": "options_latest",
            "product": product_key,
            "board": board or {
                "type": "options_board",
                "product": product_key,
                "calls": [],
                "puts": [],
                "expiry": "",
                "expiry_date": None,
                "updated_at": "",
            },
            "investor": investor or {
                "type": "investor_flow",
                "product": product_key,
                "call_investor": {},
                "put_investor": {},
                "delta": None,
            },
            "futures": futures or {
                "type": "futures_price",
                "symbol": None,
                "price": None,
                "change": None,
                "change_pct": None,
                "high": None,
                "low": None,
                "open": None,
            },
        }

    def remove_client(self, ws: WebSocket):
        self._clients.discard(ws)
        self._client_products.pop(ws, None)
        logger.info("Options client disconnected. Total: %d", len(self._clients))

    def _active_products(self) -> set[str]:
        """Unique products among connected clients; fallback to WKI."""
        products = set(self._client_products.values())
        return products if products else {"WKI"}

    async def _broadcast_to_product(self, product: str, payload: str):
        """Send payload only to clients subscribed to a given product."""
        disconnected = set()
        for ws in list(self._clients):
            if self._client_products.get(ws) != product:
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.add(ws)
        for ws in disconnected:
            self._clients.discard(ws)
            self._client_products.pop(ws, None)

    def _is_product_session_open(self, product_key: str) -> bool:
        """Return True if the market session for this product is currently open."""
        if product_key in _NIGHT_PRODUCTS:
            return get_market_status().is_open
        return get_options_market_status().is_open

    async def _board_poll_loop(self):
        while self._running:
            if self._kis_client:
                for product_key in self._active_products():
                    if not self._is_product_session_open(product_key):
                        continue
                    try:
                        cfg = PRODUCTS.get(product_key, PRODUCTS["WKI"])
                        _, market_iscd, _, _, board_code = cfg
                        expiry = _compute_expiry_code(product_key)
                        expiry_date = _compute_expiry_date(product_key, expiry)
                        calls_raw, puts_raw = await self._kis_client.get_options_board(
                            board_code,
                            expiry,
                        )
                        calls = [_serialize_strike(r) for r in calls_raw]
                        puts = [_serialize_strike(r) for r in puts_raw]
                        from datetime import timezone
                        now_kst = datetime.now(timezone.utc).astimezone(
                            __import__('zoneinfo', fromlist=['ZoneInfo']).ZoneInfo('Asia/Seoul')
                        )
                        msg = {
                            "type": "options_board",
                            "product": product_key,
                            "expiry": expiry,
                            "expiry_date": expiry_date.isoformat() if expiry_date else None,
                            "updated_at": now_kst.strftime("%H:%M:%S"),
                            "calls": calls,
                            "puts": puts,
                        }
                        self._last_board[product_key] = msg
                        await self._broadcast_to_product(product_key, json.dumps(msg))
                    except Exception as e:
                        logger.warning("Options board poll error (product=%s): %s", product_key, e)
            await asyncio.sleep(BOARD_POLL_INTERVAL)

    async def _investor_poll_loop(self):
        while self._running:
            if self._kis_client:
                for product_key in self._active_products():
                    if not self._is_product_session_open(product_key):
                        continue
                    try:
                        cfg = PRODUCTS.get(product_key, PRODUCTS["WKI"])
                        _, market_iscd, call_iscd2, put_iscd2, _ = cfg
                        inv = await self._kis_client.get_options_investor(
                            market_iscd,
                            call_iscd2,
                            put_iscd2,
                        )
                        call_inv = _serialize_investor(inv.get("call", {}))
                        put_inv = _serialize_investor(inv.get("put", {}))
                        delta = await self._investor_store.save(product_key, call_inv, put_inv)
                        msg = {
                            "type": "investor_flow",
                            "product": product_key,
                            "call_investor": call_inv,
                            "put_investor": put_inv,
                            "delta": delta,
                        }
                        self._last_investor[product_key] = msg
                        await self._broadcast_to_product(product_key, json.dumps(msg))
                    except Exception as e:
                        logger.warning(
                            "Options investor poll error (product=%s): %s",
                            product_key,
                            e,
                        )
            await asyncio.sleep(INVESTOR_POLL_INTERVAL)

    async def _futures_poll_loop(self):
        """Poll KOSPI200 daytime futures price every 2s via REST (H0ZFCNT0 equivalent)."""
        symbol = _compute_kospi200_futures_symbol()
        while self._running:
            if get_options_market_status().is_open and self._kis_client:
                # Recompute symbol daily in case of rollover
                symbol = _compute_kospi200_futures_symbol()
                try:
                    import time as _time
                    data = await self._kis_client.get_day_futures_price(symbol)
                    msg = {"type": "futures_price", **data}
                    self._last_futures = msg
                    await self._broadcast_all(json.dumps(msg))
                    # Save snapshot every 30s
                    now = _time.monotonic()
                    if now - self._last_futures_save_ts >= 30:
                        await self._futures_store.save(
                            data.get("price"), data.get("change"), data.get("change_pct")
                        )
                        self._last_futures_save_ts = now
                except Exception as e:
                    logger.debug("Futures price poll error: %s", e)
            await asyncio.sleep(FUTURES_POLL_INTERVAL)

    async def _broadcast_all(self, payload: str):
        """Send payload to all connected clients regardless of product."""
        disconnected = set()
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.add(ws)
        for ws in disconnected:
            self._clients.discard(ws)
            self._client_products.pop(ws, None)
