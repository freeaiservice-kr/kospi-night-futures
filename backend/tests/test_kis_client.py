import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from backend.kis_client import KISClient, KISAPIError, KISRateLimitError, _parse_symbol_expiry, _second_thursday
from backend.kis_websocket import KISWebSocketClient


class TestKISWebSocketParser:
    """Test H0MFCNT0 pipe-delimited frame parsing (official KIS field layout)."""

    def _make_client(self):
        return KISWebSocketClient("ws://test", "key", "101V6", lambda q: None)

    def _build_frame(self, fields: list) -> str:
        """Build a KIS WS frame: enc|tr_id|count|f0^f1^f2^...  (data is ^-delimited)"""
        return "0|H0MFCNT0|1|" + "^".join(fields)

    def test_parse_normal_tick(self):
        # H0MFCNT0 fields: symbol, time, change, sign, change_pct, price, open, high, low, tick_vol, acml_vol
        fields = ["101V6", "203000", "2.75", "2", "0.73", "380.25", "377.50", "381.00", "376.80", "5", "15234"]
        frame = self._build_frame(fields)
        client = self._make_client()
        quote = client._parse_pipe_frame(frame)
        assert quote is not None
        assert quote.symbol == "101V6"
        assert quote.price == 380.25
        assert quote.change == 2.75      # sign=2 (상승) → positive
        assert quote.change_pct == 0.73
        assert quote.volume == 15234
        assert quote.open_price == 377.50
        assert quote.high_price == 381.00
        assert quote.low_price == 376.80
        assert quote.provider == "kis"

    def test_parse_negative_change(self):
        # sign=5 (하락) → change should be negative
        fields = ["101V6", "220000", "1.50", "5", "0.40", "378.50", "377.50", "381.00", "376.80", "3", "20000"]
        frame = self._build_frame(fields)
        client = self._make_client()
        quote = client._parse_pipe_frame(frame)
        assert quote is not None
        assert quote.change == -1.50
        assert quote.change_pct == -0.40

    def test_parse_wrong_tr_id(self):
        frame = "0|H0MFASP0|1|101V6|203000"
        client = self._make_client()
        quote = client._parse_pipe_frame(frame)
        assert quote is None

    def test_parse_too_short_frame(self):
        frame = "0|H0MFCNT0|1|101V6|203000"
        client = self._make_client()
        quote = client._parse_pipe_frame(frame)
        assert quote is None

    def test_parse_json_frame_returns_none(self):
        frame = '{"header": {}, "body": {"rt_cd": "0"}}'
        client = self._make_client()
        # JSON frames handled by _handle_message, not _parse_pipe_frame
        quote = client._parse_pipe_frame(frame)
        assert quote is None  # no pipe-delimited data


class TestSymbolParsing:
    def test_parse_valid_symbol(self):
        # Symbol "101V6" — V=Oct, 6=last digit of year
        expiry = _parse_symbol_expiry("101V6")
        assert expiry is not None
        assert expiry.month == 10

    def test_parse_invalid_symbol_too_short(self):
        expiry = _parse_symbol_expiry("101")
        assert expiry is None

    def test_parse_unknown_month_code(self):
        expiry = _parse_symbol_expiry("101A6")
        assert expiry is None

    def test_second_thursday(self):
        # Known: 2nd Thursday of March 2026 is March 12
        result = _second_thursday(2026, 3)
        assert result.day == 12
        assert result.month == 3
        assert result.year == 2026


class TestKISClientPriceOutput:
    def test_parse_price_output_normal(self):
        client = KISClient()
        output = {
            "stck_cntg_hour": "203000",
            "futs_prpr": "380.25",
            "prdy_vrss": "2.75",
            "prdy_ctrt": "0.73",
            "acml_vol": "15234",
            "futs_oprc": "377.50",
            "futs_hgpr": "381.00",
            "futs_lwpr": "376.80",
        }
        quote = client._parse_price_output("101V6", output)
        assert quote.symbol == "101V6"
        assert quote.price == 380.25
        assert quote.change == 2.75
        assert quote.change_pct == 0.73
        assert quote.volume == 15234
        assert quote.provider == "kis"

    def test_parse_price_output_empty(self):
        client = KISClient()
        quote = client._parse_price_output("101V6", {})
        assert quote.price == 0.0
        assert quote.volume == 0
        assert quote.provider == "kis"


@pytest.mark.asyncio
class TestKISClientIntegration:
    async def test_get_current_price_success(self):
        client = KISClient()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "rt_cd": "0",
            "msg_cd": "KIOK0000",
            "msg1": "정상처리",
            "output": {
                "stck_cntg_hour": "203000",
                "futs_prpr": "380.25",
                "prdy_vrss": "2.75",
                "prdy_ctrt": "0.73",
                "acml_vol": "15234",
                "futs_oprc": "377.50",
                "futs_hgpr": "381.00",
                "futs_lwpr": "376.80",
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        with patch.object(client, '_get_token', return_value="test-token"):
            quote = await client.get_current_price("101V6")

        assert quote.price == 380.25
        assert quote.symbol == "101V6"

    async def test_get_current_price_rate_limit_then_success(self):
        client = KISClient()

        rate_limit_response = MagicMock()
        rate_limit_response.json.return_value = {
            "rt_cd": "1",
            "msg_cd": "EGW00201",
            "msg1": "초당 거래건수를 초과하였습니다.",
        }
        rate_limit_response.raise_for_status = MagicMock()

        success_response = MagicMock()
        success_response.json.return_value = {
            "rt_cd": "0",
            "msg_cd": "KIOK0000",
            "msg1": "정상처리",
            "output": {"futs_prpr": "380.00"},
        }
        success_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(side_effect=[rate_limit_response, success_response])
        client._client = mock_http_client

        with patch.object(client, '_get_token', return_value="test-token"), \
             patch('asyncio.sleep', return_value=None):
            quote = await client.get_current_price("101V6")

        assert quote.price == 380.0

    async def test_get_current_price_api_error(self):
        client = KISClient()

        error_response = MagicMock()
        error_response.json.return_value = {
            "rt_cd": "1",
            "msg_cd": "ERROR001",
            "msg1": "종목코드 오류",
        }
        error_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=error_response)
        client._client = mock_http_client

        with patch.object(client, '_get_token', return_value="test-token"), \
             pytest.raises(KISAPIError):
            await client.get_current_price("INVALID")
