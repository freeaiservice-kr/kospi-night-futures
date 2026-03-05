from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_market_data():
    svc = MagicMock()
    svc.is_connected = False
    svc.add_client = AsyncMock()
    svc.remove_client = MagicMock()
    svc.start = AsyncMock()
    svc.stop = AsyncMock()
    svc.get_latest_snapshot = MagicMock(return_value=None)
    return svc


@pytest.fixture
def mock_options_data():
    svc = MagicMock()
    svc.add_client = AsyncMock()
    svc.remove_client = MagicMock()
    svc.start = AsyncMock()
    svc.stop = AsyncMock()
    svc.get_latest_snapshot = MagicMock(return_value=None)
    svc.futures_store = MagicMock()
    svc.futures_store.get_history = AsyncMock(return_value=[])
    svc.investor_store = MagicMock()
    svc.investor_store.get_history = AsyncMock(return_value=[])
    svc.get_latest_snapshot.return_value = None
    return svc
