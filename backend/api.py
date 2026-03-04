import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from backend.market_status import get_market_status

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_market_data(request: Request):
    return request.app.state.market_data


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


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



@router.websocket("/ws/futures")
async def ws_futures(websocket: WebSocket):
    """WebSocket endpoint: real-time futures stream to browsers."""
    await websocket.accept()
    market_data = websocket.app.state.market_data
    await market_data.add_client(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WS client error: %s", e)
    finally:
        market_data.remove_client(websocket)
