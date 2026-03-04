import asyncio
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from backend.models import FuturesQuote

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    STOPPED = "STOPPED"


class KISWebSocketClient:
    """KIS real-time futures data via WebSocket (binary/pipe-delimited frames)."""

    TR_ID = "H0MFCNT0"     # 야간선물 실시간 체결 (KRX night futures real-time ccnl)
    ASK_TR_ID = "H0MFASP0"  # 야간선물 실시간 호가 (order book, 0.2s filter)

    def __init__(self, ws_url: str, approval_key: str, symbol: str, callback: Callable[[FuturesQuote], None]):
        self._ws_url = ws_url
        self._approval_key = approval_key
        self._symbol = symbol
        self._callback = callback
        self._state = ConnectionState.DISCONNECTED
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._last_trade: Optional[FuturesQuote] = None  # cached from H0MFCNT0

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED

    def _set_state(self, state: ConnectionState):
        old = self._state
        self._state = state
        logger.info("WebSocket state: %s -> %s", old.value, state.value)

    async def start(self):
        """Start the WebSocket streaming loop in background."""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """Gracefully stop the WebSocket streaming."""
        self._stop_event.set()
        self._set_state(ConnectionState.STOPPED)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self):
        """Main loop with exponential backoff reconnection."""
        backoff = 1
        max_backoff = 60

        while not self._stop_event.is_set():
            try:
                self._set_state(ConnectionState.CONNECTING)
                await self._connect_and_stream()
                backoff = 1  # reset on successful connection
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._stop_event.is_set():
                    break
                logger.warning("WebSocket error: %s. Reconnecting in %ds...", e, backoff)
                self._set_state(ConnectionState.RECONNECTING)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break  # stop_event set during wait
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, max_backoff)

        self._set_state(ConnectionState.DISCONNECTED)

    async def _connect_and_stream(self):
        """Connect to KIS WebSocket, subscribe, and stream frames."""
        async with websockets.connect(
            self._ws_url,
            ping_interval=30,
            ping_timeout=10,
            open_timeout=15,
        ) as ws:
            self._set_state(ConnectionState.CONNECTED)
            logger.info("Connected to KIS WebSocket: %s", self._ws_url)

            def _sub_msg(tr_id: str, tr_key: str) -> str:
                return json.dumps({
                    "header": {
                        "approval_key": self._approval_key,
                        "custtype": "P",
                        "tr_type": "1",
                        "content-type": "utf-8",
                    },
                    "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
                })

            # Subscribe to trade ticks
            await ws.send(_sub_msg(self.TR_ID, self._symbol))
            logger.info("Subscribed to %s (체결) for symbol %s", self.TR_ID, self._symbol)

            # Subscribe to order book (bid/ask) — updates even with no trades
            await ws.send(_sub_msg(self.ASK_TR_ID, self._symbol))
            logger.info("Subscribed to %s (호가) for symbol %s", self.ASK_TR_ID, self._symbol)

            async for message in ws:
                if self._stop_event.is_set():
                    break
                await self._handle_message(ws, message)

    async def _handle_message(self, ws, message: str | bytes):
        """Parse incoming WebSocket frame and invoke callback."""
        if isinstance(message, bytes):
            message = message.decode("utf-8")

        # JSON frames: subscription ack, PINGPONG, or error
        if message.startswith("{"):
            try:
                data = json.loads(message)
                header = data.get("header", {})
                body = data.get("body", {})

                # KIS app-level keepalive — must echo back to stay connected
                if header.get("tr_id") == "PINGPONG":
                    logger.debug("KIS WS PINGPONG → echoing back")
                    await ws.send(message)
                    return

                rt_cd = body.get("rt_cd", "")
                if rt_cd == "0":
                    logger.info("KIS WS subscription OK: %s", body.get("msg1", ""))
                else:
                    logger.debug("KIS WS JSON frame: header=%s body=%s", header, body)
            except json.JSONDecodeError:
                logger.warning("Could not parse JSON frame: %s", message[:100])
            return

        # Pipe-delimited data frame
        quote = self._parse_pipe_frame(message)
        if quote:
            try:
                self._callback(quote)
            except Exception as e:
                logger.error("Callback error: %s", e)

    def _parse_pipe_frame(self, frame: str) -> Optional[FuturesQuote]:
        """
        Parse KIS real-time WebSocket frame.
        Header format: enc_flag|tr_id|data_count|<data>
        Data format:   field0^field1^field2^...^fieldN  (caret-delimited)
        Official doc example:
          0|H0MFCNT0|001|101V06^190215^0.75^2^0.20^367.30^...
        """
        parts = frame.split("|", 3)  # split header only; keep data intact
        if len(parts) < 4:
            return None

        enc_flag = parts[0]
        tr_id = parts[1]
        # data_count = parts[2]  # number of ticks (usually 1)
        fields = parts[3].split("^")  # data section is ^-delimited

        if tr_id == self.ASK_TR_ID:
            return self._parse_orderbook_frame(fields)

        if tr_id != self.TR_ID:
            logger.debug("Ignoring tr_id: %s", tr_id)
            return None

        if len(fields) < 11:
            logger.warning("H0MFCNT0 frame too short: %d fields", len(fields))
            return None

        try:
            # H0MFCNT0 field layout (from official KIS open-trading-api samples):
            # [0]  futs_shrn_iscd   선물 단축 종목코드
            # [1]  bsop_hour        영업시간 HHMMSS
            # [2]  futs_prdy_vrss   전일대비
            # [3]  prdy_vrss_sign   전일대비 부호 (1=상한,2=상승,3=보합,4=하한,5=하락)
            # [4]  futs_prdy_ctrt   전일대비율
            # [5]  futs_prpr        현재가
            # [6]  futs_oprc        시가
            # [7]  futs_hgpr        고가
            # [8]  futs_lwpr        저가
            # [9]  last_cnqn        최종체결량 (this tick)
            # [10] acml_vol         누적체결량
            def _f(idx: int) -> float:
                return float(fields[idx]) if fields[idx] else 0.0

            def _i(idx: int) -> int:
                return int(fields[idx]) if fields[idx] else 0

            symbol = fields[0]
            time_str = fields[1].zfill(6)   # HHMMSS
            change = _f(2)                   # futs_prdy_vrss
            change_sign = fields[3]          # prdy_vrss_sign
            change_pct = _f(4)               # futs_prdy_ctrt
            price = _f(5)                    # futs_prpr
            open_price = _f(6)               # futs_oprc
            high_price = _f(7)               # futs_hgpr
            low_price = _f(8)                # futs_lwpr
            volume = _i(10)                  # acml_vol (cumulative)

            # Apply sign: 4=하한, 5=하락 → negative
            if change_sign in ("4", "5"):
                change = -abs(change)
                change_pct = -abs(change_pct)

            now = datetime.now()
            try:
                ts = now.replace(
                    hour=int(time_str[:2]),
                    minute=int(time_str[2:4]),
                    second=int(time_str[4:6]),
                    microsecond=0,
                )
            except ValueError:
                ts = now

            quote = FuturesQuote(
                symbol=symbol or self._symbol,
                price=price,
                change=change,
                change_pct=change_pct,
                volume=volume,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                timestamp=ts,
                provider="kis",
            )
            self._last_trade = quote  # cache for orderbook-based quotes
            return quote
        except (IndexError, ValueError) as e:
            logger.warning("Failed to parse H0MFCNT0 frame: %s | frame[:100]: %s", e, frame[:100])
            return None

    def _parse_orderbook_frame(self, fields: list[str]) -> Optional[FuturesQuote]:
        """
        Parse H0MFASP0 (야간선물 실시간 호가) frame.
        H0MFASP0 field layout (from KRX야간선물 실시간호가 [실시간-065].xlsx):
          [0]  FUTS_SHRN_ISCD  선물 단축 종목코드
          [1]  BSOP_HOUR       영업 시간 HHMMSS
          [2]  FUTS_ASKP1      매도호가1 (best ask)
          [3]  FUTS_ASKP2
          [4]  FUTS_ASKP3
          [5]  FUTS_ASKP4
          [6]  FUTS_ASKP5
          [7]  FUTS_BIDP1      매수호가1 (best bid)
          ...
        Uses best bid as price proxy; inherits change/volume from last trade tick.
        """
        if len(fields) < 8:
            return None
        try:
            def _f(idx: int) -> float:
                return float(fields[idx]) if fields[idx] else 0.0

            ask1 = _f(2)
            bid1 = _f(7)

            if bid1 == 0.0 and ask1 == 0.0:
                return None

            # Use mid-price; fall back to whichever side is non-zero
            if bid1 > 0 and ask1 > 0:
                price = (bid1 + ask1) / 2
            else:
                price = bid1 or ask1

            time_str = fields[1].zfill(6)
            now = datetime.now()
            try:
                ts = now.replace(
                    hour=int(time_str[:2]),
                    minute=int(time_str[2:4]),
                    second=int(time_str[4:6]),
                    microsecond=0,
                )
            except ValueError:
                ts = now

            # Inherit change/volume/open/high/low from last trade if available
            t = self._last_trade
            return FuturesQuote(
                symbol=fields[0] or self._symbol,
                price=price,
                change=t.change if t else 0.0,
                change_pct=t.change_pct if t else 0.0,
                volume=t.volume if t else 0,
                open_price=t.open_price if t else 0.0,
                high_price=max(t.high_price, price) if t else price,
                low_price=min(t.low_price, price) if t and t.low_price > 0 else price,
                timestamp=ts,
                provider="kis_orderbook",
            )
        except (IndexError, ValueError) as e:
            logger.warning("Failed to parse H0MFASP0 frame: %s", e)
            return None
