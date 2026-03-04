from datetime import datetime
from pydantic import BaseModel


class FuturesQuote(BaseModel):
    symbol: str               # e.g., "101V12"
    price: float              # 현재가
    change: float             # 전일대비
    change_pct: float         # 등락률 (%)
    volume: int               # 거래량
    open_price: float         # 시가
    high_price: float         # 고가
    low_price: float          # 저가
    timestamp: datetime       # 체결시각
    provider: str             # "kis"


class MarketStatus(BaseModel):
    is_open: bool
    session_name: str         # "night", "day", "auction_pre", "auction_close", "closed"
    next_open: datetime | None = None
    next_close: datetime | None = None


class SymbolInfo(BaseModel):
    symbol: str
    expires_at: datetime | None = None
    days_to_expiry: int | None = None
    expiry_warning: bool = False  # True if expires within 7 days
