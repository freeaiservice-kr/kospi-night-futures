import asyncio
import json
import logging
from calendar import monthcalendar, THURSDAY
from datetime import date as _date, datetime, timezone
from typing import Optional

from fastapi import WebSocket
from backend.config import settings
from backend.intraday_store import IntradayStore
from backend.kis_client import KISClient, KISAuthError
from backend.kis_websocket import KISWebSocketClient, ConnectionState
from backend.market_status import get_market_status, get_session_start_ts
from backend.models import FuturesQuote

logger = logging.getLogger(__name__)

STALENESS_SECONDS = 60
REST_POLL_INTERVAL = 1  # 1 req/sec (well within 20 req/s KIS limit)
ROLLOVER_CHECK_INTERVAL = 15 * 60  # 15 minutes


def _second_thursday(year: int, month: int) -> _date:
    """Return the date of the 2nd Thursday of the given year/month."""
    weeks = monthcalendar(year, month)
    thursdays = [w[THURSDAY] for w in weeks if w[THURSDAY] != 0]
    day = thursdays[1] if len(thursdays) >= 2 else thursdays[0]
    return _date(year, month, day)


def _next_symbol(from_date: _date) -> str:
    """Compute the front-month CME night futures symbol (A01[Y][MM]) for from_date."""
    for month in (3, 6, 9, 12):
        expiry = _second_thursday(from_date.year, month)
        if from_date <= expiry:
            return f"A01{from_date.year % 10}{month:02d}"
    return f"A01{(from_date.year + 1) % 10}03"


class MarketDataService:
    """
    Manages KIS data provider and broadcasts to browser WebSocket clients.

    Flow:
      KIS WebSocket → tick callback → broadcast to all browsers
      If WS disconnects: fall back to REST polling every 5s
      Staleness: no ticks for 60s during market hours → send stale status
    """

    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._kis_client: Optional[KISClient] = None
        self._ws_client: Optional[KISWebSocketClient] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._staleness_task: Optional[asyncio.Task] = None
        self._chart_tick_task: Optional[asyncio.Task] = None
        self._rollover_task: Optional[asyncio.Task] = None
        self._session_monitor_task: Optional[asyncio.Task] = None
        self._last_quote: Optional[FuturesQuote] = None
        self._last_trade_price: Optional[float] = None  # H0MFCNT0 체결가만 추적
        self._last_tick_time: float = 0.0
        self._symbol: str = settings.futures_symbol
        self._running = False
        self._store: Optional[IntradayStore] = None

    @property
    def is_connected(self) -> bool:
        if self._ws_client:
            return self._ws_client.is_connected
        return False

    async def start(self):
        """Start the market data service: connect to KIS, start streaming."""
        self._running = True
        self._kis_client = KISClient()

        # Always initialize intraday storage
        self._store = IntradayStore()
        await self._store.init(get_session_start_ts())

        if not settings.kis_app_key or settings.kis_app_key == "your_app_key_here":
            logger.warning(
                "KIS_APP_KEY not configured. Running without live data. "
                "Set KIS_APP_KEY, KIS_APP_SECRET in .env to enable live data."
            )
            return

        try:
            # Resolve symbol
            if self._symbol == "auto":
                self._symbol = await self._auto_detect_symbol()

            # Only connect to KIS when market is open
            if get_market_status().is_open:
                await self._start_websocket()
            else:
                logger.info("Market is closed. WebSocket will start at next session open.")
        except KISAuthError as e:
            logger.error("KIS auth failed: %s. Service running without live data.", e)
        except Exception as e:
            logger.error("Failed to start KIS streaming: %s", e)

        # Always start background monitors
        self._staleness_task = asyncio.create_task(self._staleness_monitor())
        self._chart_tick_task = asyncio.create_task(self._chart_tick_broadcaster())
        self._rollover_task = asyncio.create_task(self._rollover_monitor())
        self._session_monitor_task = asyncio.create_task(self._session_monitor())

    async def stop(self):
        """Gracefully stop all tasks and connections."""
        self._running = False

        if self._ws_client:
            await self._ws_client.stop()

        if self._poll_task:
            self._poll_task.cancel()

        if self._staleness_task:
            self._staleness_task.cancel()

        if self._chart_tick_task:
            self._chart_tick_task.cancel()

        if self._rollover_task:
            self._rollover_task.cancel()

        if self._session_monitor_task:
            self._session_monitor_task.cancel()

        if self._kis_client:
            await self._kis_client.close()

        if self._store:
            await self._store.close()

    async def _auto_detect_symbol(self) -> str:
        """Detect the nearest active KOSPI200 futures contract using expiry comparison."""
        return _next_symbol(_date.today())

    async def _start_websocket(self):
        """Acquire approval key and start KIS WebSocket client."""
        approval_key = await self._kis_client.get_approval_key()  # type: ignore
        self._ws_client = KISWebSocketClient(
            ws_url=settings.kis_ws_url,
            approval_key=approval_key,
            symbol=self._symbol,
            callback=self._on_tick,
            orderbook_callback=self._on_orderbook,
        )
        await self._ws_client.start()
        logger.info("KIS WebSocket streaming started for symbol: %s", self._symbol)

        # Monitor WS state and fall back to REST if disconnected
        asyncio.create_task(self._ws_monitor())

    async def _ws_monitor(self):
        """Monitor WebSocket state and activate REST fallback when disconnected."""
        while self._running:
            await asyncio.sleep(10)
            if self._ws_client and not self._ws_client.is_connected and self._running:
                if get_market_status().is_open and (self._poll_task is None or self._poll_task.done()):
                    logger.info("WS disconnected, activating REST poll fallback")
                    self._poll_task = asyncio.create_task(self._rest_poll_loop())
            elif self._ws_client and self._ws_client.is_connected:
                if self._poll_task and not self._poll_task.done():
                    logger.info("WS reconnected, stopping REST poll fallback")
                    self._poll_task.cancel()
                    self._poll_task = None

    async def _rest_poll_loop(self):
        """Poll REST API every 1s as fallback when WebSocket is unavailable."""
        while self._running:
            if get_market_status().is_open and self._kis_client:
                try:
                    quote = await self._kis_client.get_current_price(self._symbol)
                    self._on_tick(quote)
                except Exception as e:
                    logger.warning("REST poll error: %s", e)
            await asyncio.sleep(REST_POLL_INTERVAL)

    def _on_tick(self, quote: FuturesQuote):
        """Called on each price tick from provider."""
        import time
        self._last_quote = quote
        self._last_tick_time = time.time()
        if quote.provider == "kis":  # H0MFCNT0 실제 체결가만 추적
            self._last_trade_price = quote.price
        asyncio.create_task(self._broadcast_quote(quote))

    def _on_orderbook(self, data: dict):
        """Called when H0MFASP0 5-level orderbook frame arrives."""
        asyncio.create_task(self._broadcast_raw(json.dumps(data)))

    async def _broadcast_quote(self, quote: FuturesQuote):
        """Broadcast FuturesQuote to all connected browser clients."""
        payload = json.dumps({
            "type": "quote",
            "data": {
                "symbol": quote.symbol,
                "price": quote.price,
                "change": quote.change,
                "change_pct": quote.change_pct,
                "volume": quote.volume,
                "open_price": quote.open_price,
                "high_price": quote.high_price,
                "low_price": quote.low_price,
                "timestamp": quote.timestamp.isoformat(),
                "provider": quote.provider,
                "cttr": quote.cttr,
                "basis": quote.basis,
                "open_interest": quote.open_interest,
                "oi_change": quote.oi_change,
            },
        })
        await self._broadcast_raw(payload)

    async def _broadcast_raw(self, payload: str):
        """Send raw JSON string to all connected clients."""
        disconnected = set()
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.add(ws)
        self._clients -= disconnected

    async def _staleness_monitor(self):
        """Detect when no ticks arrive for 60s during market hours."""
        import time
        while self._running:
            await asyncio.sleep(10)
            status = get_market_status()
            if status.is_open and self._last_tick_time > 0:
                elapsed = time.time() - self._last_tick_time
                if elapsed > STALENESS_SECONDS:
                    await self._broadcast_raw(json.dumps({
                        "type": "status",
                        "state": "stale",
                        "message": f"No data for {int(elapsed)}s",
                    }))

    async def add_client(self, ws: WebSocket):
        """Register a new browser WebSocket client."""
        self._clients.add(ws)
        logger.info("Browser client connected. Total: %d", len(self._clients))

        # Send chart history for current session
        if self._store:
            ticks = await self._store.get_session_ticks(get_session_start_ts())
            if ticks:
                loop = asyncio.get_event_loop()
                payload = await loop.run_in_executor(
                    None, json.dumps, {"type": "chart_history", "ticks": ticks}
                )
                await ws.send_text(payload)

        # Send current quote immediately if available
        if self._last_quote:
            await self._broadcast_quote(self._last_quote)

        # Send connection state
        ws_state = self._ws_client.state.value if self._ws_client else "DISCONNECTED"
        await ws.send_text(json.dumps({
            "type": "connected",
            "ws_state": ws_state,
            "symbol": self._symbol,
        }))

    def remove_client(self, ws: WebSocket):
        """Remove a disconnected browser WebSocket client."""
        self._clients.discard(ws)
        logger.info("Browser client disconnected. Total: %d", len(self._clients))

    async def _chart_tick_broadcaster(self):
        """Broadcast 1-second chart ticks using last known 체결가 (trade price)."""
        while self._running:
            await asyncio.sleep(1)
            if not get_market_status().is_open:
                continue
            price = self._last_trade_price
            if price is None and self._last_quote:
                price = self._last_quote.price  # fallback: orderbook mid-price
            if price is not None:
                ts_dt = datetime.now(timezone.utc).replace(microsecond=0)
                ts_iso = ts_dt.isoformat()
                if self._store:
                    await self._store.insert(int(ts_dt.timestamp()), price)
                await self._broadcast_raw(json.dumps({
                    "type": "chart_tick",
                    "price": price,
                    "timestamp": ts_iso,
                }))

    async def _session_monitor(self):
        """Detect market open/close transitions and manage connections accordingly."""
        was_open = get_market_status().is_open
        while self._running:
            await asyncio.sleep(30)
            status = get_market_status()
            is_open = status.is_open

            if not was_open and is_open:
                logger.info("Market session opened. Starting data stream.")
                if self._store:
                    await self._store.init(get_session_start_ts())
                if not self.is_connected and self._kis_client:
                    try:
                        await self._start_websocket()
                    except Exception as e:
                        logger.error("WebSocket start at session open failed: %s", e)

            elif was_open and not is_open:
                logger.info("Market session closed. Pausing data stream.")
                if self._poll_task and not self._poll_task.done():
                    self._poll_task.cancel()
                    self._poll_task = None

            was_open = is_open

    async def _rollover_monitor(self):
        """Every 15 minutes, check if the current symbol has expired and roll over."""
        while self._running:
            await asyncio.sleep(ROLLOVER_CHECK_INTERVAL)
            await self._check_rollover()

    async def _check_rollover(self):
        """Roll over to next contract if current symbol has passed its expiry date."""
        sym = self._symbol
        if len(sym) != 6 or not sym.startswith("A01"):
            return
        try:
            year_digit = int(sym[3])
            month = int(sym[4:6])
            base_year = (_date.today().year // 10) * 10 + year_digit
            expiry = _second_thursday(base_year, month)
            if _date.today() > expiry:
                new_symbol = _next_symbol(_date.today())
                if new_symbol != self._symbol:
                    logger.info("Futures rollover: %s expired → %s", sym, new_symbol)
                    await self._do_rollover(new_symbol)
        except Exception as e:
            logger.warning("Rollover check error: %s", e)

    async def _do_rollover(self, new_symbol: str):
        """Switch WebSocket subscription to new_symbol after contract expiry."""
        self._symbol = new_symbol
        self._last_trade_price = None

        if self._ws_client:
            await self._ws_client.stop()
            self._ws_client = None
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None

        # Notify all browser clients to reset their chart
        await self._broadcast_raw(json.dumps({
            "type": "rollover",
            "symbol": new_symbol,
        }))

        try:
            await self._start_websocket()
        except Exception as e:
            logger.error("WebSocket restart after rollover failed: %s", e)
