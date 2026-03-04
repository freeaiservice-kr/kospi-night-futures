import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import pathlib

from backend.config import settings
from backend.api import router
from backend.market_data import MarketDataService

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

market_data_service: MarketDataService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global market_data_service
    logger.info("Starting KOSPI Night Futures service...")
    market_data_service = MarketDataService()
    app.state.market_data = market_data_service
    await market_data_service.start()
    logger.info("MarketDataService started.")
    yield
    logger.info("Shutting down...")
    await market_data_service.stop()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    app = FastAPI(
        title="KOSPI Night Futures API",
        description="Real-time KOSPI 200 night futures data service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    # Serve frontend static files
    frontend_path = pathlib.Path(__file__).parent.parent / "frontend"
    if frontend_path.exists():
        app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="static")

    return app


app = create_app()
