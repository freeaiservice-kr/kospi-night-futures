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
    # Night session: WebSocket data from MarketDataService
    market_data = request.app.state.market_data.get_latest_snapshot()
    if market_data:
        return market_data
    # Day session fallback: REST poll data from OptionsDataService
    day_futures = request.app.state.options_data._last_futures
    if day_futures:
        return day_futures
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Futures data not available yet",
    )


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
    try:
        ban_manager = websocket.app.state.ban_manager
        if ban_manager.is_banned(client_ip):
            await websocket.close(code=4429)
            return
    except Exception:
        pass

    if _ws_connections[client_ip] >= _WS_MAX_PER_IP:
        logger.warning("WS /ws/futures rejected: too many connections from %s", client_ip)
        try:
            websocket.app.state.ban_manager.record_violation(client_ip, "ws_spam")
        except Exception:
            pass
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


@router.get("/api/v1/options/products")
async def get_options_products(_: None = Depends(_require_api_token)):
    """Return all available products sorted by nearest expiry date."""
    from backend.options_data import PRODUCTS, _compute_expiry_code, _compute_expiry_date
    from datetime import date
    today = date.today()
    items = []
    for key, cfg in PRODUCTS.items():
        display_name = cfg[0]
        code = _compute_expiry_code(key, today)
        exp = _compute_expiry_date(key, code)
        items.append({
            "key": key,
            "label": display_name,
            "expiry_code": code,
            "expiry_date": exp.isoformat() if exp else None,
        })
    items.sort(key=lambda x: x["expiry_date"] or "9999-99-99")
    return {"products": items, "default": items[0]["key"] if items else "WKI"}


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
    """Get KOSPI200 price history: night session from IntradayStore, day from FuturesStore."""
    from backend.market_status import get_market_status, get_session_start_ts
    market_data = request.app.state.market_data
    options_data = request.app.state.options_data
    if get_market_status().is_open:
        # Night session: return intraday WebSocket ticks
        session_ts = get_session_start_ts()
        rows = await market_data._store.get_session_ticks(session_ts)
        return {"rows": rows[-limit:]}
    # Day session: return REST-polled snapshots
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
    # Fill futures if null: try day REST cache then night WebSocket cache
    if snapshot.get("futures", {}).get("price") is None:
        day_fut = options_data._last_futures
        night_snap = request.app.state.market_data.get_latest_snapshot()
        if day_fut and day_fut.get("price") is not None:
            snapshot["futures"] = day_fut
        elif night_snap:
            d = night_snap.get("data", {})
            snapshot["futures"] = {
                "type": "futures_price",
                "symbol": d.get("symbol"),
                "price": night_snap.get("last_trade_price"),
                "change": d.get("change"),
                "change_pct": d.get("change_pct"),
                "high": d.get("high_price"),
                "low": d.get("low_price"),
                "open": d.get("open_price"),
            }
    return snapshot


@router.get("/api/v1/sector/latest")
async def get_sector_latest(request: Request, _=Depends(_require_api_token)):
    """Get latest sector snapshot (most recent 5-min poll)."""
    sector_data = request.app.state.sector_data
    snapshot = sector_data.get_latest_snapshot()
    if snapshot:
        return snapshot
    # Fallback: query store
    rows = await sector_data.store.get_latest_snapshots()
    if rows:
        return {"type": "sector_update", "ts": rows[0]["ts"] if rows else 0, "sectors": rows}
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Sector data not available yet",
    )


@router.get("/api/v1/sector/history")
async def get_sector_history(
    request: Request,
    sector_code: str,
    limit: int = 60,
    _=Depends(_require_api_token),
):
    """Get intraday history for a specific sector code."""
    sector_data = request.app.state.sector_data
    rows = await sector_data.store.get_history(sector_code, limit=limit)
    return {"sector_code": sector_code, "rows": rows}


@router.get("/api/v1/sector/daily")
async def get_sector_daily(
    request: Request,
    days: int = 30,
    _=Depends(_require_api_token),
):
    """Get recent N days of daily sector summaries."""
    sector_data = request.app.state.sector_data
    rows = await sector_data.store.get_daily_summaries(days=days)
    return {"rows": rows}


@router.websocket("/ws/sector")
async def ws_sector(websocket: WebSocket):
    """WebSocket endpoint: real-time sector analysis stream to browsers."""
    if not _require_ws_token(websocket):
        logger.warning("WS /ws/sector rejected: invalid token")
        await websocket.close(code=4401)
        return

    origin = websocket.headers.get("origin")
    if not _is_allowed_origin(origin):
        logger.warning("WS /ws/sector rejected: origin=%s", origin)
        await websocket.close(code=4003)
        return

    client_ip = websocket.client.host if websocket.client else "unknown"
    try:
        ban_manager = websocket.app.state.ban_manager
        if ban_manager.is_banned(client_ip):
            await websocket.close(code=4429)
            return
    except Exception:
        pass

    if _ws_connections[client_ip] >= _WS_MAX_PER_IP:
        logger.warning("WS /ws/sector rejected: too many connections from %s", client_ip)
        try:
            websocket.app.state.ban_manager.record_violation(client_ip, "ws_spam")
        except Exception:
            pass
        await websocket.close(code=4029)
        return

    await websocket.accept()
    _ws_connections[client_ip] += 1
    sector_data = websocket.app.state.sector_data
    await sector_data.add_client(websocket)
    try:
        while True:
            data = await _ws_receive_with_timeout(websocket)
            if data is None:
                logger.debug("WS /ws/sector idle timeout for %s", client_ip)
                await websocket.close(code=1001)
                break
            if len(data.encode()) > _WS_MAX_MSG_BYTES:
                logger.warning("WS /ws/sector oversized message from %s", client_ip)
                continue
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Sector WS client error: %s", e)
    finally:
        _ws_connections[client_ip] = max(0, _ws_connections[client_ip] - 1)
        sector_data.remove_client(websocket)


@router.get("/api/v1/leaders/top")
async def get_leaders_top(request: Request, n: int = 50, _=Depends(_require_api_token)):
    """주도주 스코어 상위 N개 (전체)."""
    store = request.app.state.leader_store
    rows = await store.get_latest_scores(top_n=n)
    return {"ts": int(__import__("time").time()), "leaders": rows, "count": len(rows)}


@router.get("/api/v1/leaders/sector")
async def get_leaders_by_sector(
    request: Request,
    code: str,
    _=Depends(_require_api_token),
):
    """섹터별 주도주 (sector_code 기준, 예: G25)."""
    store = request.app.state.leader_store
    rows = await store.get_scores_by_sector(code)
    return {"sector_code": code, "leaders": rows, "count": len(rows)}


@router.get("/api/v1/leaders/detail")
async def get_leader_detail(
    request: Request,
    code: str,
    _=Depends(_require_api_token),
):
    """종목 상세 (스코어 + 최근 1시간 스냅샷)."""
    store = request.app.state.leader_store
    detail = await store.get_score_detail(code)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No data for {code}",
        )
    return detail


@router.get("/api/v1/leaders/status")
async def get_leaders_status(request: Request, _=Depends(_require_api_token)):
    """폴러 상태 (사이클 수, 마지막 업데이트, daily_call_count)."""
    poller = request.app.state.watchlist_poller
    return poller.get_status()


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
    try:
        ban_manager = websocket.app.state.ban_manager
        if ban_manager.is_banned(client_ip):
            await websocket.close(code=4429)
            return
    except Exception:
        pass

    if _ws_connections[client_ip] >= _WS_MAX_PER_IP:
        logger.warning("WS /ws/options rejected: too many connections from %s", client_ip)
        try:
            websocket.app.state.ban_manager.record_violation(client_ip, "ws_spam")
        except Exception:
            pass
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
