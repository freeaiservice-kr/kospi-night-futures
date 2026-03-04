import asyncio
import json
import logging
from typing import Optional

from fastapi import WebSocket
from backend.config import settings
from backend.kis_client import KISClient, KISAuthError
from backend.kis_websocket import KISWebSocketClient, ConnectionState
from backend.market_status import get_market_status
from backend.models import FuturesQuote

logger = logging.getLogger(__name__)

STALENESS_SECONDS = 60
REST_POLL_INTERVAL = 1  # 1 req/sec (well within 20 req/s KIS limit)


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
        self._last_quote: Optional[FuturesQuote] = None
        self._last_tick_time: float = 0.0
        self._symbol: str = settings.futures_symbol
        self._running = False

    @property
    def is_connected(self) -> bool:
        if self._ws_client:
            return self._ws_client.is_connected
        return False

    async def start(self):
        """Start the market data service: connect to KIS, start streaming."""
        self._running = True
        self._kis_client = KISClient()

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

            # Start WebSocket streaming
            await self._start_websocket()
        except KISAuthError as e:
            logger.error("KIS auth failed: %s. Service running without live data.", e)
        except Exception as e:
            logger.error("Failed to start KIS streaming: %s", e)

        # Always start staleness monitor
        self._staleness_task = asyncio.create_task(self._staleness_monitor())

    async def stop(self):
        """Gracefully stop all tasks and connections."""
        self._running = False

        if self._ws_client:
            await self._ws_client.stop()

        if self._poll_task:
            self._poll_task.cancel()

        if self._staleness_task:
            self._staleness_task.cancel()

        if self._kis_client:
            await self._kis_client.close()

    async def _auto_detect_symbol(self) -> str:
        """Detect the nearest active KOSPI200 futures contract."""
        # TODO: implement via KIS API lookup for active contracts
        # For now, return a placeholder and warn
        logger.warning(
            "Auto symbol detection not yet implemented. "
            "Please set FUTURES_SYMBOL explicitly in .env"
        )
        return "101V6"  # placeholder

    async def _start_websocket(self):
        """Acquire approval key and start KIS WebSocket client."""
        approval_key = await self._kis_client.get_approval_key()  # type: ignore
        self._ws_client = KISWebSocketClient(
            ws_url=settings.kis_ws_url,
            approval_key=approval_key,
            symbol=self._symbol,
            callback=self._on_tick,
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
                if self._poll_task is None or self._poll_task.done():
                    logger.info("WS disconnected, activating REST poll fallback")
                    self._poll_task = asyncio.create_task(self._rest_poll_loop())
            elif self._ws_client and self._ws_client.is_connected:
                if self._poll_task and not self._poll_task.done():
                    logger.info("WS reconnected, stopping REST poll fallback")
                    self._poll_task.cancel()
                    self._poll_task = None

    async def _rest_poll_loop(self):
        """Poll REST API every 5s as fallback when WebSocket is unavailable."""
        while self._running:
            try:
                if self._kis_client:
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
        asyncio.create_task(self._broadcast_quote(quote))

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
