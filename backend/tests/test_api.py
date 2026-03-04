import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from datetime import datetime

from backend.main import create_app
from backend.models import FuturesQuote


@pytest.fixture
def mock_market_data(mock_kis_client, sample_quote):
    """Reusable mock market data service."""
    svc = MagicMock()
    svc.is_connected = True
    svc._symbol = "101V6"
    svc._last_quote = sample_quote
    svc._kis_client = mock_kis_client
    svc.add_client = AsyncMock()
    svc.remove_client = MagicMock()
    svc.start = AsyncMock()
    svc.stop = AsyncMock()
    return svc


@pytest.fixture
def client(mock_market_data):
    """TestClient with lifespan bypassed via patched MarketDataService."""
    with patch("backend.main.MarketDataService", return_value=mock_market_data):
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as c:
            # Override after lifespan sets app.state.market_data
            c.app.state.market_data = mock_market_data
            yield c


class TestHealthEndpoint:
    def test_health_check(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "provider_connected" in data


class TestFuturesEndpoints:
    def test_get_futures_price(self, client):
        resp = client.get("/api/v1/futures/price")
        assert resp.status_code == 200
        data = resp.json()
        assert data["price"] == 380.25
        assert data["symbol"] == "101V6"
        assert data["provider"] == "kis"

    def test_get_market_status(self, client):
        resp = client.get("/api/v1/futures/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_open" in data
        assert "session_name" in data

    def test_get_symbol_info(self, client):
        resp = client.get("/api/v1/futures/symbol")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "101V6"
