"""IP ban manager for automatic blocking of abusive clients."""
from __future__ import annotations

import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

BAN_DURATION_SECONDS = 86400
VIOLATION_WINDOW_SECONDS = 60
RATE_LIMIT_STRIKES = 3
WS_SPAM_STRIKES = 5
SCAN_STRIKES = 10
HTTP_FLOOD_THRESHOLD = 600
CLEANUP_INTERVAL_SECONDS = 300
STALE_COUNTER_SECONDS = 60
HEALTH_PATH = "/health"
API_PREFIX = "/api/"

_TRIGGER_THRESHOLDS: dict[str, int] = {
    "rate_limit": RATE_LIMIT_STRIKES,
    "ws_spam": WS_SPAM_STRIKES,
    "scan_404": SCAN_STRIKES,
}


class BanManager:
    """In-memory IP ban manager with TTL-based expiry and sliding window violation tracking."""

    def __init__(self) -> None:
        self._bans: dict[str, float] = {}
        self._violations: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        self._http_counter: dict[str, list[float]] = defaultdict(list)

    def is_banned(self, ip: str) -> bool:
        expires_at = self._bans.get(ip)
        if expires_at is None:
            return False
        if time.time() < expires_at:
            return True
        del self._bans[ip]
        return False

    def ban(self, ip: str) -> None:
        expires_at = time.time() + BAN_DURATION_SECONDS
        self._bans[ip] = expires_at
        logger.warning("IP banned: %s, expires_at=%.0f", ip, expires_at)

    def record_violation(self, ip: str, trigger: str) -> bool:
        now = time.time()
        cutoff = now - VIOLATION_WINDOW_SECONDS
        timestamps = self._violations[ip][trigger]
        # Prune stale entries
        pruned = [t for t in timestamps if t >= cutoff]
        pruned.append(now)
        self._violations[ip][trigger] = pruned

        threshold = _TRIGGER_THRESHOLDS.get(trigger, 0)
        if threshold and len(pruned) >= threshold:
            self.ban(ip)
            return True
        return False

    def record_http_request(self, ip: str, path: str) -> bool:
        if path == HEALTH_PATH:
            return False
        now = time.time()
        cutoff = now - VIOLATION_WINDOW_SECONDS
        timestamps = self._http_counter[ip]
        pruned = [t for t in timestamps if t >= cutoff]
        pruned.append(now)
        self._http_counter[ip] = pruned

        if len(pruned) >= HTTP_FLOOD_THRESHOLD:
            self.ban(ip)
            return True
        return False

    def cleanup_expired(self) -> None:
        now = time.time()
        cutoff = now - STALE_COUNTER_SECONDS

        # Remove expired bans
        expired_ips = [ip for ip, exp in self._bans.items() if exp <= now]
        for ip in expired_ips:
            del self._bans[ip]

        # Remove stale http counter entries (no recent requests)
        stale_http = [ip for ip, ts in self._http_counter.items() if not ts or max(ts) < cutoff]
        for ip in stale_http:
            del self._http_counter[ip]

        # Remove stale violation entries
        stale_violations = []
        for ip, triggers in self._violations.items():
            all_stale = all(not ts or max(ts) < cutoff for ts in triggers.values())
            if all_stale:
                stale_violations.append(ip)
        for ip in stale_violations:
            del self._violations[ip]

    def get_stats(self) -> dict:
        return {
            "banned_count": len(self._bans),
            "violation_ips": len(self._violations),
        }
