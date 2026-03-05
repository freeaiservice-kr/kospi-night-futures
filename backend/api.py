import asyncio
import logging
from collections import defaultdict

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from backend.config import settings
from backend.market_status import get_market_status

logger = logging.getLogger(__name__)
router = APIRouter()

# WebSocket connection counter: IP -> count of active connections
_ws_connections: dict[str, int] = defaultdict(int)
_WS_MAX_PER_IP = 5
_WS_MAX_MSG_BYTES = 1024
_WS_IDLE_TIMEOUT = 300  # 5 minutes in seconds


def _get_market_data(request: Request):
    return request.app.state.market_data


def _extract_api_token(
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str | None:
    if x_api_token:
        return x_api_token.strip()
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return authorization.strip()


def _require_api_token(token: str | None = Depends(_extract_api_token)) -> None:
    if settings.is_api_token_valid(token):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API token",
    )


def _extract_ws_token(websocket: WebSocket) -> str | None:
    token = websocket.query_params.get("token")
    if token:
        return token.strip()
    auth = websocket.headers.get("authorization") or websocket.headers.get("Authorization")
    if not auth:
        return None
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth.strip()


def _require_ws_token(websocket: WebSocket) -> bool:
    if not settings.api_auth_enabled:
        return True
    token = _extract_ws_token(websocket)
    return settings.is_api_token_valid(token)


def _is_allowed_origin(origin: str | None) -> bool:
    """Check if WebSocket origin is in allowed CORS origins."""
    if origin is None:
        # No Origin header -- allow server-to-server / CLI tools
        return True
    allowed = settings.cors_origins_list
    return origin in allowed


async def _ws_receive_with_timeout(websocket: WebSocket) -> str | None:
    """Receive text with idle timeout. Returns None on timeout."""
    try:
        data = await asyncio.wait_for(websocket.receive_text(), timeout=_WS_IDLE_TIMEOUT)
        return data
    except asyncio.TimeoutError:
        return None


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@router.get("/api/v1/futures/status")
async def get_market_session_status(request: Request, _=Depends(_require_api_token)):
    """Get current market session status."""
    status = get_market_status()
    return {
        "is_open": status.is_open,
        "session_name": status.session_name,
        "next_open": status.next_open.isoformat() if status.next_open else None,
        "next_close": status.next_close.isoformat() if status.next_close else None,
    }


@router.get("/api/v1/futures/latest")
async def get_futures_latest(request: Request, _=Depends(_require_api_token)):
    """Get latest futures quote snapshot for polling clients."""
    market_data = request.app.state.market_data.get_latest_snapshot()
    if not market_data:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Futures data not available yet",
        )
    return market_data


@router.websocket("/ws/futures")
async def ws_futures(websocket: WebSocket):
    """WebSocket endpoint: real-time futures stream to browsers."""
    if not _require_ws_token(websocket):
        logger.warning("WS /ws/futures rejected: invalid token")
        await websocket.close(code=4401)
        return

    origin = websocket.headers.get("origin")
    if not _is_allowed_origin(origin):
        logger.warning("WS /ws/futures rejected: origin=%s", origin)
        await websocket.close(code=4003)
        return

    client_ip = websocket.client.host if websocket.client else "unknown"
    if _ws_connections[client_ip] >= _WS_MAX_PER_IP:
        logger.warning("WS /ws/futures rejected: too many connections from %s", client_ip)
        await websocket.close(code=4029)
        return

    await websocket.accept()
    _ws_connections[client_ip] += 1
    market_data = websocket.app.state.market_data
    await market_data.add_client(websocket)
    try:
        while True:
            data = await _ws_receive_with_timeout(websocket)
            if data is None:
                # Idle timeout
                logger.debug("WS /ws/futures idle timeout for %s", client_ip)
                await websocket.close(code=1001)
                break
            if len(data.encode()) > _WS_MAX_MSG_BYTES:
                logger.warning("WS /ws/futures oversized message from %s", client_ip)
                continue
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WS client error: %s", e)
    finally:
        _ws_connections[client_ip] = max(0, _ws_connections[client_ip] - 1)
        market_data.remove_client(websocket)


def _get_options_data(request: Request):
    return request.app.state.options_data


@router.get("/api/v1/options/status")
async def get_options_status(_: None = Depends(_require_api_token)):
    """Get current options market session status."""
    from backend.market_status import get_options_market_status
    status = get_options_market_status()
    return {
        "is_open": status.is_open,
        "session_name": status.session_name,
        "next_open": status.next_open.isoformat() if status.next_open else None,
        "next_close": status.next_close.isoformat() if status.next_close else None,
    }


@router.get("/api/v1/options/futures-history")
async def get_futures_history(request: Request, limit: int = 120, _=Depends(_require_api_token)):
    """Get KOSPI200 underlying price history for intraday trend display."""
    options_data = request.app.state.options_data
    rows = await options_data.futures_store.get_history(limit=limit)
    return {"rows": rows}


@router.get("/api/v1/options/investor-history")
async def get_investor_history(
    request: Request,
    product: str = "WKI",
    limit: int = 60,
    _=Depends(_require_api_token),
):
    """Get investor flow history for intraday trend display."""
    options_data = request.app.state.options_data
    rows = await options_data.investor_store.get_history(product, limit=limit)
    return {"product": product, "rows": rows}


@router.get("/api/v1/options/latest")
async def get_options_latest(request: Request, product: str = "WKI", _=Depends(_require_api_token)):
    """Get latest options board/investor/futures payload for polling clients."""
    options_data = request.app.state.options_data
    snapshot = options_data.get_latest_snapshot(product=product)
    if not snapshot:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Options data not available yet",
        )
    return snapshot


@router.websocket("/ws/options")
async def ws_options(websocket: WebSocket, product: str = "WKI"):
    """WebSocket endpoint: real-time options stream to browsers."""
    if not _require_ws_token(websocket):
        logger.warning("WS /ws/options rejected: invalid token")
        await websocket.close(code=4401)
        return

    origin = websocket.headers.get("origin")
    if not _is_allowed_origin(origin):
        logger.warning("WS /ws/options rejected: origin=%s", origin)
        await websocket.close(code=4003)
        return

    client_ip = websocket.client.host if websocket.client else "unknown"
    if _ws_connections[client_ip] >= _WS_MAX_PER_IP:
        logger.warning("WS /ws/options rejected: too many connections from %s", client_ip)
        await websocket.close(code=4029)
        return

    await websocket.accept()
    _ws_connections[client_ip] += 1
    options_data = websocket.app.state.options_data
    await options_data.add_client(websocket, product=product)
    try:
        while True:
            data = await _ws_receive_with_timeout(websocket)
            if data is None:
                logger.debug("WS /ws/options idle timeout for %s", client_ip)
                await websocket.close(code=1001)
                break
            if len(data.encode()) > _WS_MAX_MSG_BYTES:
                logger.warning("WS /ws/options oversized message from %s", client_ip)
                continue
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Options WS client error: %s", e)
    finally:
        _ws_connections[client_ip] = max(0, _ws_connections[client_ip] - 1)
        options_data.remove_client(websocket)
