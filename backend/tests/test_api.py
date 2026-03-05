from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import create_app


@pytest.fixture
def client(mock_market_data, mock_options_data):
    with patch("backend.main.MarketDataService", return_value=mock_market_data), patch(
        "backend.main.OptionsDataService", return_value=mock_options_data
    ):
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as c:
            c.app.state.market_data = mock_market_data
            c.app.state.options_data = mock_options_data
            yield c


@pytest.fixture
def auth_client(mock_market_data, mock_options_data, monkeypatch):
    monkeypatch.setattr(settings, "api_require_auth", True)
    monkeypatch.setattr(settings, "api_tokens", "token123")

    with patch("backend.main.MarketDataService", return_value=mock_market_data), patch(
        "backend.main.OptionsDataService", return_value=mock_options_data
    ):
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as c:
            c.app.state.market_data = mock_market_data
            c.app.state.options_data = mock_options_data
            yield c


@pytest.fixture
def polling_client(mock_market_data, mock_options_data):
    with patch("backend.main.MarketDataService", return_value=mock_market_data), patch(
        "backend.main.OptionsDataService", return_value=mock_options_data
    ):
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as c:
            c.app.state.market_data = mock_market_data
            c.app.state.options_data = mock_options_data
            yield c


class TestHealthEndpoint:
    def test_health_check(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestFuturesEndpoints:
    def test_get_market_status(self, client):
        resp = client.get("/api/v1/futures/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_open" in data
        assert "session_name" in data

    def test_market_status_blocked_for_bot_user_agent(self, client):
        resp = client.get(
            "/api/v1/futures/status",
            headers={"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"},
        )
        assert resp.status_code == 403

    def test_market_status_allowed_for_browser_user_agent(self, client):
        resp = client.get(
            "/api/v1/futures/status",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "is_open" in data
        assert "session_name" in data

    def test_market_status_requires_token_when_enabled(self, auth_client):
        resp = auth_client.get("/api/v1/futures/status")
        assert resp.status_code == 401

    def test_market_status_accepts_valid_token_when_enabled(self, auth_client):
        resp = auth_client.get(
            "/api/v1/futures/status",
            headers={"X-API-Token": "token123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "is_open" in data
        assert "session_name" in data

    def test_futures_latest_endpoint_shape(self, polling_client, mock_market_data):
        mock_market_data.get_latest_snapshot.return_value = {
            "type": "quote",
            "state": "disconnected",
            "last_trade_price": 100.0,
            "data": {
                "symbol": "101V2612",
                "price": 100.0,
                "change": 1.2,
                "change_pct": 1.2,
                "volume": 10,
                "open_price": 98.0,
                "high_price": 101.0,
                "low_price": 97.5,
                "timestamp": datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc).isoformat(),
                "provider": "kis",
                "cttr": 55.1,
                "basis": 0.5,
                "open_interest": 1234,
                "oi_change": 12,
            },
        }
        resp = polling_client.get("/api/v1/futures/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "quote"
        assert data["data"]["symbol"] == "101V2612"
        assert data["state"] == "disconnected"

    def test_futures_latest_returns_service_unavailable_when_empty(
        self,
        polling_client,
        mock_market_data,
    ):
        mock_market_data.get_latest_snapshot.return_value = None
        resp = polling_client.get("/api/v1/futures/latest")
        assert resp.status_code == 503


def test_options_latest_payload_shape(polling_client, mock_options_data):
    mock_options_data.get_latest_snapshot.return_value = {
        "type": "options_latest",
        "product": "WKI",
        "board": {
            "type": "options_board",
            "product": "WKI",
            "expiry": "26123",
            "expiry_date": "2026-03-01",
            "updated_at": "08:00:00",
            "calls": [],
            "puts": [],
        },
        "investor": {
            "type": "investor_flow",
            "product": "WKI",
            "call_investor": {},
            "put_investor": {},
            "delta": [],
        },
        "futures": {
            "type": "futures_price",
            "symbol": "101V2612",
            "price": 100.0,
            "change": 1.0,
            "change_pct": 0.2,
            "high": 100.5,
            "low": 99.2,
            "open": 98.8,
        },
    }
    resp = polling_client.get("/api/v1/options/latest?product=WKI")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "options_latest"
    assert data["board"]["product"] == "WKI"
    assert data["futures"]["price"] == 100.0
    assert data["investor"]["product"] == "WKI"


def test_options_latest_returns_service_unavailable_when_empty(polling_client, mock_options_data):
    mock_options_data.get_latest_snapshot.return_value = {}
    resp = polling_client.get("/api/v1/options/latest?product=WKI")
    assert resp.status_code == 503
