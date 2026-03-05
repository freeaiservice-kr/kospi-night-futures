import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Korean domain 야선.com in punycode for HTTP header compatibility
# xn--o39az20c.com is the punycode encoding of 야선.com
CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-eval' "
    "https://unpkg.com https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "connect-src 'self' wss://xn--o39az20c.com ws://localhost:* https://xn--o39az20c.com; "
    "img-src 'self' data:; "
    "frame-ancestors 'none';"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, environment: str = "dev") -> None:
        super().__init__(app)
        self.environment = environment

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = CSP_POLICY
        if self.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class BotBlockingMiddleware(BaseHTTPMiddleware):
    """Block common bot/user-agent patterns on protected endpoints."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        blocked_user_agents: list[str] | None = None,
        allowlist_user_agents: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.blocked_user_agents = [ua.lower() for ua in (blocked_user_agents or [])]
        self.allowlist_user_agents = [ua.lower() for ua in (allowlist_user_agents or [])]

    def _is_blocked(self, user_agent: str | None) -> bool:
        if not self.enabled:
            return False
        if not user_agent:
            return False
        lowered = user_agent.lower()
        if any(allow in lowered for allow in self.allowlist_user_agents):
            return False
        return any(block in lowered for block in self.blocked_user_agents)

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path.startswith("/api/") or path.startswith("/ws/"):
            user_agent = request.headers.get("user-agent", "")
            if self._is_blocked(user_agent):
                logger.warning("Blocked bot-like User-Agent for %s: %s", path, user_agent)
                return PlainTextResponse("Forbidden", status_code=403)

        return await call_next(request)
