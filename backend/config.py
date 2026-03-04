from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List


class Settings(BaseSettings):
    # KIS API credentials
    kis_app_key: str = Field(default="", description="KIS Open API App Key")
    kis_app_secret: str = Field(default="", description="KIS Open API App Secret")

    # KIS API endpoints
    kis_base_url: str = Field(default="https://openapi.koreainvestment.com:9443")
    kis_ws_url: str = Field(default="ws://ops.koreainvestment.com:21000")

    # Night session hours (KST, configurable)
    night_session_open: str = Field(default="18:00", description="Night session open time HH:MM KST")
    night_session_close: str = Field(default="05:00", description="Night session close time HH:MM KST")

    # Futures symbol
    futures_symbol: str = Field(default="auto", description="KOSPI200 futures symbol or 'auto' for auto-detect")

    # Application
    environment: str = Field(default="dev")
    log_level: str = Field(default="INFO")
    cors_origins: str = Field(default="http://localhost:3000,http://localhost:8000")

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
