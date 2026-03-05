import asyncio
import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from backend.api import router
from backend.config import settings
from backend.market_data import MarketDataService
from backend.middleware import BotBlockingMiddleware, SecurityHeadersMiddleware
from backend.options_data import OptionsDataService

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# Rate limiter (in-memory, single-server)
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting KOSPI Night Futures service...")
    market_data = MarketDataService()
    options_data = OptionsDataService()
    app.state.market_data = market_data
    app.state.options_data = options_data
    await asyncio.gather(market_data.start(), options_data.start())
    logger.info("Service started.")
    yield
    await asyncio.gather(market_data.stop(), options_data.stop())
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    is_prod = settings.environment == "production"

    app = FastAPI(
        title="KOSPI Night Futures",
        description="KOSPI 200 야간선물 정보 서비스",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )

    # Attach limiter state
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # Security headers
    app.add_middleware(SecurityHeadersMiddleware, environment=settings.environment)
    app.add_middleware(
        BotBlockingMiddleware,
        enabled=settings.bot_block_enabled,
        blocked_user_agents=settings.bot_block_user_agents_list,
        allowlist_user_agents=settings.bot_allowlist_user_agents_list,
    )

    # CORS (hardened: explicit methods/headers)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "X-API-Token",
        ],
    )

    # Global error handler (hide stack traces in production)
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        if settings.environment != "production":
            return JSONResponse(
                status_code=500,
                content={"detail": str(exc)},
            )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    app.include_router(router)

    frontend_path = pathlib.Path(__file__).parent.parent / "frontend"
    if frontend_path.exists():
        app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="static")

    return app


app = create_app()
