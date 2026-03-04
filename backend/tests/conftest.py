import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from backend.models import FuturesQuote


@pytest.fixture
def sample_quote():
    return FuturesQuote(
        symbol="101V6",
        price=380.25,
        change=2.75,
        change_pct=0.73,
        volume=15234,
        open_price=377.50,
        high_price=381.00,
        low_price=376.80,
        timestamp=datetime(2026, 3, 4, 20, 30, 0),
        provider="kis",
    )


@pytest.fixture
def mock_kis_client(sample_quote):
    client = AsyncMock()
    client.get_current_price = AsyncMock(return_value=sample_quote)
    client.get_approval_key = AsyncMock(return_value="test-approval-key-12345")
    client.get_symbol_info = AsyncMock(return_value=MagicMock(
        symbol="101V6",
        expires_at=None,
        days_to_expiry=None,
        expiry_warning=False,
    ))
    return client
