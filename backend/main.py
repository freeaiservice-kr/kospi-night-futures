import asyncio
import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from backend.api import router
from backend.ban_manager import BanManager
from backend.config import settings
from backend.kis_client import KISClient
from backend.leader_scorer import LeaderScorer
from backend.leader_store import LeaderStore
from backend.market_data import MarketDataService
from backend.middleware import BotBlockingMiddleware, IPBanMiddleware, SecurityHeadersMiddleware
from backend.options_data import OptionsDataService
from backend.sector_service import SectorAnalysisService
from backend.watchlist_poller import WatchlistPoller

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# Rate limiter (in-memory, single-server)
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


async def _cleanup_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(300)
        try:
            app.state.ban_manager.cleanup_expired()
        except Exception as e:
            logger.error("BanManager cleanup error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting KOSPI Night Futures service...")
    app.state.ban_manager = BanManager()
    kis_client = KISClient()
    market_data = MarketDataService()
    options_data = OptionsDataService(kis_client=kis_client)
    sector_data = SectorAnalysisService(kis_client=kis_client)
    leader_store = LeaderStore()
    leader_scorer = LeaderScorer(leader_store)
    watchlist_poller = WatchlistPoller(kis_client, leader_store, leader_scorer)
    app.state.market_data = market_data
    app.state.options_data = options_data
    app.state.sector_data = sector_data
    app.state.watchlist_poller = watchlist_poller
    app.state.leader_store = leader_store
    cleanup_task = asyncio.create_task(_cleanup_loop(app))
    await asyncio.gather(
        market_data.start(),
        options_data.start(),
        sector_data.start(),
        watchlist_poller.start(),
    )
    logger.info("Service started.")
    yield
    cleanup_task.cancel()
    await asyncio.gather(
        market_data.stop(),
        options_data.stop(),
        sector_data.stop(),
        watchlist_poller.stop(),
    )
    await leader_store.close()
    await kis_client.close()
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

    async def _custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
        ip = request.client.host if request.client else "unknown"
        try:
            request.app.state.ban_manager.record_violation(ip, "rate_limit")
        except Exception:
            pass
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
            headers={"Retry-After": "60"},
        )

    app.add_exception_handler(RateLimitExceeded, _custom_rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)

    # Security headers then IP ban (FastAPI middleware executes in reverse registration order,
    # so IPBanMiddleware runs first on incoming requests)
    app.add_middleware(SecurityHeadersMiddleware, environment=settings.environment)
    app.add_middleware(IPBanMiddleware)
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
