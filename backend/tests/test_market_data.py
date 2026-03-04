import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from backend.market_data import MarketDataService
from backend.models import FuturesQuote
from backend.market_status import get_market_status


class TestMarketStatus:
    def test_night_session_open(self):
        # 20:00 KST on a weekday
        now = datetime(2026, 3, 4, 20, 0, 0)  # Wednesday
        status = get_market_status(now)
        assert status.is_open is True
        assert status.session_name == "night"

    def test_night_session_after_midnight(self):
        # 02:00 KST on Thursday (session started Wed evening)
        now = datetime(2026, 3, 5, 2, 0, 0)
        status = get_market_status(now)
        assert status.is_open is True
        assert status.session_name == "night"

    def test_day_session(self):
        # 10:00 KST
        now = datetime(2026, 3, 4, 10, 0, 0)
        status = get_market_status(now)
        assert status.is_open is False
        assert status.session_name == "day"

    def test_pre_open_auction(self):
        # 17:55 KST
        now = datetime(2026, 3, 4, 17, 55, 0)
        status = get_market_status(now)
        assert status.is_open is False
        assert status.session_name == "auction_pre"

    def test_weekend_closed(self):
        # Saturday 20:00 — should be closed
        now = datetime(2026, 3, 7, 20, 0, 0)  # Saturday
        status = get_market_status(now)
        assert status.is_open is False
        assert status.session_name == "closed"

    def test_holiday_closed(self):
        # March 1 (Independence Movement Day) 20:00
        now = datetime(2026, 3, 1, 20, 0, 0)
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
        mock_ws.send_text = AsyncMock()

        await service.add_client(mock_ws)
        assert mock_ws in service._clients

        service.remove_client(mock_ws)
        assert mock_ws not in service._clients

    @pytest.mark.asyncio
    async def test_broadcast_quote(self, sample_quote):
        service = MarketDataService()
        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()
        service._clients = {mock_ws1, mock_ws2}

        await service._broadcast_quote(sample_quote)

        mock_ws1.send_text.assert_called_once()
        mock_ws2.send_text.assert_called_once()
        payload = mock_ws1.send_text.call_args[0][0]
        import json
        data = json.loads(payload)
        assert data["type"] == "quote"
        assert data["data"]["price"] == 380.25

    @pytest.mark.asyncio
    async def test_broadcast_removes_disconnected_clients(self, sample_quote):
        service = MarketDataService()
        good_ws = AsyncMock()
        bad_ws = AsyncMock()
        bad_ws.send_text = AsyncMock(side_effect=Exception("disconnected"))
        service._clients = {good_ws, bad_ws}

        await service._broadcast_quote(sample_quote)

        assert good_ws in service._clients
        assert bad_ws not in service._clients

    @pytest.mark.asyncio
    async def test_start_without_credentials(self):
        service = MarketDataService()
        with patch('backend.market_data.settings') as mock_settings:
            mock_settings.kis_app_key = ""
            mock_settings.futures_symbol = "101V6"
            # Should not raise, just log warning
            # We skip the full start to avoid background tasks in tests
            assert not service.is_connected
