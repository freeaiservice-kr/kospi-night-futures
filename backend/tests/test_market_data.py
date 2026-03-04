import pytest
from unittest.mock import AsyncMock
from datetime import datetime

from backend.market_data import MarketDataService
from backend.market_status import get_market_status


class TestMarketStatus:
    def test_night_session_open(self):
        now = datetime(2026, 3, 4, 20, 0, 0)  # Wednesday 20:00
        status = get_market_status(now)
        assert status.is_open is True
        assert status.session_name == "night"

    def test_night_session_after_midnight(self):
        now = datetime(2026, 3, 5, 2, 0, 0)  # Thursday 02:00
        status = get_market_status(now)
        assert status.is_open is True
        assert status.session_name == "night"

    def test_day_session(self):
        now = datetime(2026, 3, 4, 10, 0, 0)
        status = get_market_status(now)
        assert status.is_open is False
        assert status.session_name == "day"

    def test_pre_open_auction(self):
        now = datetime(2026, 3, 4, 17, 55, 0)
        status = get_market_status(now)
        assert status.is_open is False
        assert status.session_name == "auction_pre"

    def test_weekend_closed(self):
        now = datetime(2026, 3, 7, 20, 0, 0)  # Saturday
        status = get_market_status(now)
        assert status.is_open is False
        assert status.session_name == "closed"

    def test_holiday_closed(self):
        now = datetime(2026, 3, 1, 20, 0, 0)  # Independence Movement Day
        status = get_market_status(now)
        assert status.is_open is False


class TestMarketDataService:
    def test_init(self):
        service = MarketDataService()
        assert not service.is_connected
        assert len(service._clients) == 0

    @pytest.mark.asyncio
    async def test_add_and_remove_client(self):
        service = MarketDataService()
        mock_ws = AsyncMock()
        await service.add_client(mock_ws)
        assert mock_ws in service._clients
        service.remove_client(mock_ws)
        assert mock_ws not in service._clients

    @pytest.mark.asyncio
    async def test_broadcast_removes_disconnected_clients(self):
        service = MarketDataService()
        good_ws = AsyncMock()
        bad_ws = AsyncMock()
        bad_ws.send_text = AsyncMock(side_effect=Exception("disconnected"))
        service._clients = {good_ws, bad_ws}

        await service._broadcast_raw('{"test": true}')

        assert good_ws in service._clients
        assert bad_ws not in service._clients
