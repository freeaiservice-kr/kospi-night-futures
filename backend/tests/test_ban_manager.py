"""Unit tests for BanManager and IPBanMiddleware."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

from backend.ban_manager import (
    BAN_DURATION_SECONDS,
    HTTP_FLOOD_THRESHOLD,
    RATE_LIMIT_STRIKES,
    SCAN_STRIKES,
    WS_SPAM_STRIKES,
    BanManager,
)
from backend.middleware import IPBanMiddleware


# ---------------------------------------------------------------------------
# BanManager unit tests
# ---------------------------------------------------------------------------


def test_ban_and_is_banned():
    bm = BanManager()
    assert not bm.is_banned("1.2.3.4")
    bm.ban("1.2.3.4")
    assert bm.is_banned("1.2.3.4")


def test_ban_expiry():
    bm = BanManager()
    bm.ban("1.2.3.4")
    # Simulate TTL expiry by backdating the entry
    bm._bans["1.2.3.4"] = time.time() - 1
    assert not bm.is_banned("1.2.3.4")
    # Entry should be cleaned up automatically
    assert "1.2.3.4" not in bm._bans


def test_rate_limit_trigger():
    bm = BanManager()
    ip = "10.0.0.1"
    for _ in range(RATE_LIMIT_STRIKES - 1):
        result = bm.record_violation(ip, "rate_limit")
        assert not result
    result = bm.record_violation(ip, "rate_limit")
    assert result
    assert bm.is_banned(ip)


def test_rate_limit_below_trigger():
    bm = BanManager()
    ip = "10.0.0.2"
    for _ in range(RATE_LIMIT_STRIKES - 1):
        bm.record_violation(ip, "rate_limit")
    assert not bm.is_banned(ip)


def test_ws_spam_trigger():
    bm = BanManager()
    ip = "10.0.0.3"
    for _ in range(WS_SPAM_STRIKES - 1):
        result = bm.record_violation(ip, "ws_spam")
        assert not result
    result = bm.record_violation(ip, "ws_spam")
    assert result
    assert bm.is_banned(ip)


def test_scan_404_trigger():
    bm = BanManager()
    ip = "10.0.0.4"
    for _ in range(SCAN_STRIKES - 1):
        result = bm.record_violation(ip, "scan_404")
        assert not result
    result = bm.record_violation(ip, "scan_404")
    assert result
    assert bm.is_banned(ip)


def test_http_flood_trigger():
    bm = BanManager()
    ip = "10.0.0.5"
    for i in range(HTTP_FLOOD_THRESHOLD - 1):
        result = bm.record_http_request(ip, "/api/v1/data")
        assert not result, f"Should not ban before threshold, iteration {i}"
    result = bm.record_http_request(ip, "/api/v1/data")
    assert result
    assert bm.is_banned(ip)


def test_http_flood_health_excluded():
    bm = BanManager()
    ip = "10.0.0.6"
    for _ in range(HTTP_FLOOD_THRESHOLD + 10):
        result = bm.record_http_request(ip, "/health")
        assert not result
    assert not bm.is_banned(ip)


def test_sliding_window():
    bm = BanManager()
    ip = "10.0.0.7"
    # Record violations with timestamps older than the window
    old_time = time.time() - 120  # 2 minutes ago
    bm._violations[ip]["rate_limit"] = [old_time] * (RATE_LIMIT_STRIKES - 1)
    # This fresh violation should not push past threshold (old ones pruned)
    result = bm.record_violation(ip, "rate_limit")
    assert not result
    assert not bm.is_banned(ip)


def test_cleanup_expired_bans():
    bm = BanManager()
    bm.ban("alive.ip")
    bm.ban("dead.ip")
    # Expire dead.ip
    bm._bans["dead.ip"] = time.time() - 1
    bm.cleanup_expired()
    assert "dead.ip" not in bm._bans
    assert "alive.ip" in bm._bans


def test_cleanup_stale_http_counter():
    bm = BanManager()
    ip = "10.0.0.8"
    # Add stale counter entry (older than STALE_COUNTER_SECONDS)
    bm._http_counter[ip] = [time.time() - 120]
    bm.cleanup_expired()
    assert ip not in bm._http_counter


def test_cleanup_stale_violations():
    bm = BanManager()
    ip = "10.0.0.9"
    bm._violations[ip]["rate_limit"] = [time.time() - 120]
    bm.cleanup_expired()
    assert ip not in bm._violations


def test_get_stats():
    bm = BanManager()
    bm.ban("1.1.1.1")
    bm.ban("2.2.2.2")
    bm.record_violation("3.3.3.3", "rate_limit")
    stats = bm.get_stats()
    assert stats["banned_count"] == 2
    assert stats["violation_ips"] >= 1


# ---------------------------------------------------------------------------
# IPBanMiddleware integration tests
# ---------------------------------------------------------------------------


def _make_app(ban_manager: BanManager | None = None) -> FastAPI:
    """Create a minimal FastAPI app with IPBanMiddleware."""
    app = FastAPI()

    @app.get("/api/v1/test")
    async def test_endpoint():
        return {"ok": True}

    @app.get("/static/file.js")
    async def static_file():
        return {"ok": True}

    app.add_middleware(IPBanMiddleware)

    if ban_manager is not None:
        app.state.ban_manager = ban_manager
    return app


def test_middleware_blocks_banned_ip():
    # TestClient uses "testclient" as the client host
    bm = BanManager()
    bm.ban("testclient")
    app = _make_app(bm)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/v1/test")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "86400"


def test_middleware_allows_clean_ip():
    bm = BanManager()
    app = _make_app(bm)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/v1/test")
    assert r.status_code == 200


def test_404_triggers_scan_violation_only_for_api():
    bm = BanManager()
    app = _make_app(bm)
    client = TestClient(app, raise_server_exceptions=False)
    # /api/ 404 should trigger scan violation
    # TestClient uses "testclient" as client host
    r = client.get("/api/v1/nonexistent")
    assert r.status_code == 404
    assert len(bm._violations.get("testclient", {}).get("scan_404", [])) == 1
    # non-/api/ 404 should NOT trigger scan violation
    r2 = client.get("/missing-static.js")
    assert r2.status_code == 404
    # scan_404 count should still be 1 (not incremented)
    assert len(bm._violations.get("testclient", {}).get("scan_404", [])) == 1


def test_health_excluded_from_flood_count():
    bm = BanManager()
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.add_middleware(IPBanMiddleware)
    app.state.ban_manager = bm

    client = TestClient(app, raise_server_exceptions=False)
    for _ in range(10):
        r = client.get("/health")
        assert r.status_code == 200
    # /health requests should not accumulate in http_counter
    assert len(bm._http_counter.get("testclient", [])) == 0


def test_middleware_fail_open():
    """When BanManager is not set on app.state, middleware should fail-open."""
    app = FastAPI()

    @app.get("/api/v1/test")
    async def test_ep():
        return {"ok": True}

    app.add_middleware(IPBanMiddleware)
    # Intentionally do NOT set app.state.ban_manager

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/v1/test")
    assert r.status_code == 200
