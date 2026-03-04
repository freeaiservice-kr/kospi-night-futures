import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_market_data():
    svc = MagicMock()
    svc.is_connected = False
    svc.add_client = AsyncMock()
    svc.remove_client = MagicMock()
    svc.start = AsyncMock()
    svc.stop = AsyncMock()
    return svc
