import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from backend.market_status import get_market_status
from backend.models import SymbolInfo
from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_market_data(request: Request):
    return request.app.state.market_data


@router.get("/health")
async def health_check(request: Request):
    """Health check with provider connectivity status."""
    market_data = _get_market_data(request)
    return {
        "status": "ok",
        "provider_connected": market_data.is_connected,
        "symbol": market_data._symbol,
    }


@router.get("/api/v1/futures/price")
async def get_futures_price(request: Request):
    """REST fallback: get latest futures price snapshot."""
    market_data = _get_market_data(request)
    if not market_data._last_quote:
        return {"error": "No data available yet"}
    q = market_data._last_quote
    return {
        "symbol": q.symbol,
        "price": q.price,
        "change": q.change,
        "change_pct": q.change_pct,
        "volume": q.volume,
        "open_price": q.open_price,
        "high_price": q.high_price,
        "low_price": q.low_price,
        "timestamp": q.timestamp.isoformat(),
        "provider": q.provider,
    }


@router.get("/api/v1/futures/status")
async def get_market_session_status():
    """Get current market session status."""
    status = get_market_status()
    return {
        "is_open": status.is_open,
        "session_name": status.session_name,
        "next_open": status.next_open.isoformat() if status.next_open else None,
        "next_close": status.next_close.isoformat() if status.next_close else None,
    }


@router.get("/api/v1/futures/symbol")
async def get_symbol_info(request: Request):
    """Get current futures symbol and expiry information."""
    market_data = _get_market_data(request)
    symbol = market_data._symbol
    if market_data._kis_client:
        info = await market_data._kis_client.get_symbol_info(symbol)
        return {
            "symbol": info.symbol,
            "expires_at": info.expires_at.isoformat() if info.expires_at else None,
            "days_to_expiry": info.days_to_expiry,
            "expiry_warning": info.expiry_warning,
        }
    return {"symbol": symbol, "expires_at": None, "days_to_expiry": None, "expiry_warning": False}


@router.websocket("/ws/futures")
async def ws_futures(websocket: WebSocket, request: Request):
    """WebSocket endpoint: real-time futures stream to browsers."""
    await websocket.accept()
    market_data = _get_market_data(request)
    await market_data.add_client(websocket)
    try:
        while True:
            # Keep connection alive; client can send pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WS client error: %s", e)
    finally:
        market_data.remove_client(websocket)
