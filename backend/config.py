import logging
from typing import List, Set

from pydantic import Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # KIS API credentials
    kis_app_key: str = Field(default="", description="KIS Open API App Key")
    kis_app_secret: str = Field(default="", description="KIS Open API App Secret")

    # KIS API endpoints
    kis_base_url: str = Field(default="https://openapi.koreainvestment.com:9443")
    kis_ws_url: str = Field(default="ws://ops.koreainvestment.com:21000")

    # Night session hours (KST, configurable)
    night_session_open: str = Field(
        default="18:00", description="Night session open time HH:MM KST"
    )
    night_session_close: str = Field(
        default="05:00", description="Night session close time HH:MM KST"
    )

    # Futures symbol
    futures_symbol: str = Field(
        default="auto",
        description="KOSPI200 futures symbol or 'auto' for auto-detect",
    )

    # Application
    environment: str = Field(default="dev")
    log_level: str = Field(default="INFO")
    cors_origins: str = Field(default="http://localhost:3000,http://localhost:8000")
    api_require_auth: bool = Field(
        default=False, description="Enable API token checks for /api/* and /ws/* endpoints"
    )
    api_tokens: str = Field(
        default="", description="Comma-separated API tokens for backend API authentication"
    )
    bot_block_enabled: bool = Field(default=True, description="Enable bot User-Agent filtering")
    bot_block_user_agents: str = Field(
        default=(
            "googlebot,bingbot,slurp,yandexbot,duckduckgo,baiduspider,crawler,spider,headless," 
            "python-requests,curl,wget,httpx,axios,node-fetch,selenium,playwright,puppeteer,phantomjs"
        ),
        description="Comma-separated User-Agent substrings to block"
    )
    bot_allowlist_user_agents: str = Field(
        default="",
        description="Comma-separated User-Agent substrings to bypass bot filtering"
    )

    @property
    def cors_origins_list(self) -> List[str]:
        origins = [origin.strip() for origin in self.cors_origins.split(",")]
        if "*" in origins:
            logger.warning(
                "CORS origins contains wildcard '*' -- this allows all origins. "
                "Set CORS_ORIGINS to specific domains in production."
            )
        return origins

    @property
    def api_token_set(self) -> Set[str]:
        return {token.strip() for token in self.api_tokens.split(",") if token.strip()}

    @property
    def bot_block_user_agents_list(self) -> List[str]:
        return [s.strip().lower() for s in self.bot_block_user_agents.split(",") if s.strip()]

    @property
    def bot_allowlist_user_agents_list(self) -> List[str]:
        return [s.strip().lower() for s in self.bot_allowlist_user_agents.split(",") if s.strip()]

    @property
    def api_auth_enabled(self) -> bool:
        # require auth only when explicitly enabled and at least one token is provided
        return self.api_require_auth and bool(self.api_token_set)

    def is_api_token_valid(self, token: str | None) -> bool:
        if not self.api_auth_enabled:
            return True
        if not token:
            return False
        return token in self.api_token_set

    def validate_production(self) -> None:
        """Raise if required production settings are missing."""
        if self.environment == "production":
            if not self.kis_app_key or not self.kis_app_secret:
                raise RuntimeError(
                    "KIS_APP_KEY and KIS_APP_SECRET must be set in production environment."
                )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
settings.validate_production()
