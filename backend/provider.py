from typing import Protocol, Callable
from backend.models import FuturesQuote


class FuturesDataProvider(Protocol):
    """Lightweight protocol for futures data providers. Enables future extensibility (e.g. LS증권, KRX)."""

    async def connect(self) -> None:
        """Establish connection to the data source."""
        ...

    async def disconnect(self) -> None:
        """Cleanly disconnect from the data source."""
        ...

    async def get_current_price(self, symbol: str) -> FuturesQuote:
        """Fetch the latest price snapshot via REST."""
        ...

    async def subscribe_realtime(self, symbol: str, callback: Callable[[FuturesQuote], None]) -> None:
        """Subscribe to real-time tick stream. Calls callback on each tick."""
        ...

    @property
    def is_connected(self) -> bool:
        """Returns True if the provider has an active connection."""
        ...
