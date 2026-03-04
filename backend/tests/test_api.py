import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend.main import create_app


@pytest.fixture
def client(mock_market_data):
    with patch("backend.main.MarketDataService", return_value=mock_market_data):
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as c:
            c.app.state.market_data = mock_market_data
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

