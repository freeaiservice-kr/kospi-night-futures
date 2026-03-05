import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from backend.config import settings
from backend.kis_models import (
    KISApprovalKeyResponse,
    KISTokenResponse,
)
from backend.models import FuturesQuote, SymbolInfo

logger = logging.getLogger(__name__)

TOKEN_CACHE_FILE = Path(__file__).parent / "kis_token_cache.json"
RATE_LIMIT_ERROR = "EGW00201"
EXPIRY_WARNING_DAYS = 7


class KISAuthError(Exception):
    pass


class KISRateLimitError(Exception):
    pass


class KISAPIError(Exception):
    pass


class KISClient:
    """KIS Open API REST client with token management and rate limit handling."""

    def __init__(self):
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._approval_key: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def _ensure_client(self):
        if not self._client:
            self._client = httpx.AsyncClient(timeout=30.0)

    async def _get_token(self) -> str:
        """Get valid access token, refreshing if needed."""
        async with self._lock:
            # Refresh 30 minutes before expiry
            if self._token and time.time() < self._token_expires_at - 1800:
                return self._token

            # Try loading from cache
            if TOKEN_CACHE_FILE.exists():
                try:
                    data = json.loads(TOKEN_CACHE_FILE.read_text())
                    if time.time() < data.get("expires_at", 0) - 1800:
                        self._token = data["access_token"]
                        self._token_expires_at = data["expires_at"]
                        logger.debug("Loaded token from cache")
                        return self._token
                except Exception:
                    pass

            await self._refresh_token()
            return self._token  # type: ignore

    async def _refresh_token(self):
        """Acquire a new OAuth token from KIS."""
        if not settings.kis_app_key or not settings.kis_app_secret:
            raise KISAuthError("KIS_APP_KEY and KIS_APP_SECRET must be set in environment")

        await self._ensure_client()
        url = f"{settings.kis_base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
        }

        resp = await self._client.post(url, json=payload)  # type: ignore
        resp.raise_for_status()
        data = resp.json()

        if "access_token" not in data:
            raise KISAuthError(f"Token response missing access_token: {data}")

        token_resp = KISTokenResponse(**data)
        expires_at = time.time() + token_resp.expires_in

        self._token = token_resp.access_token
        self._token_expires_at = expires_at

        # Cache to disk
        try:
            TOKEN_CACHE_FILE.write_text(json.dumps({
                "access_token": self._token,
                "expires_at": expires_at,
            }))
        except Exception as e:
            logger.warning(f"Could not cache token: {e}")

        logger.info("KIS token acquired, expires in %d seconds", token_resp.expires_in)

    async def get_approval_key(self) -> str:
        """Get WebSocket approval key."""
        if self._approval_key:
            return self._approval_key

        await self._ensure_client()
        url = f"{settings.kis_base_url}/oauth2/Approval"
        payload = {
            "grant_type": "client_credentials",
            "appkey": settings.kis_app_key,
            "secretkey": settings.kis_app_secret,
        }
        resp = await self._client.post(url, json=payload)  # type: ignore
        resp.raise_for_status()
        data = resp.json()

        key_resp = KISApprovalKeyResponse(**data)
        self._approval_key = key_resp.approval_key
        logger.info("KIS WebSocket approval key acquired")
        return self._approval_key

    def _make_headers(self, token: str, tr_id: str) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    async def get_current_price(self, symbol: str, retries: int = 3) -> FuturesQuote:
        """Fetch current futures price via REST. Handles EGW00201 rate limit."""
        await self._ensure_client()
        token = await self._get_token()
        url = f"{settings.kis_base_url}/uapi/domestic-futureoption/v1/quotations/inquire-price"
        headers = self._make_headers(token, "FHMIF10000000")
        params = {"FID_COND_MRKT_DIV_CODE": "NF", "FID_INPUT_ISCD": symbol}

        for attempt in range(retries):
            resp = await self._client.get(url, headers=headers, params=params)  # type: ignore
            resp.raise_for_status()
            data = resp.json()

            rt_cd = data.get("rt_cd", "1")
            msg_cd = data.get("msg_cd", "")

            if msg_cd == RATE_LIMIT_ERROR:
                wait = 2 ** attempt
                logger.warning("Rate limit hit (EGW00201), waiting %ds", wait)
                await asyncio.sleep(wait)
                continue

            if rt_cd != "0":
                raise KISAPIError(f"KIS API error: {data.get('msg1', 'Unknown')} (msg_cd={msg_cd})")

            output_data = data.get("output", {})
            return self._parse_price_output(symbol, output_data)

        raise KISRateLimitError(f"Rate limit persists after {retries} retries")

    def _parse_price_output(self, symbol: str, output: dict) -> FuturesQuote:
        """Parse KIS price API output to FuturesQuote."""
        def _f(key: str) -> float:
            return float(output.get(key, "0") or "0")

        def _i(key: str) -> int:
            return int(output.get(key, "0") or "0")

        # Timestamp: today's date + HHMMSS from response
        time_str = output.get("stck_cntg_hour", "000000").zfill(6)
        now = datetime.now()
        try:
            ts = now.replace(
                hour=int(time_str[:2]),
                minute=int(time_str[2:4]),
                second=int(time_str[4:6]),
                microsecond=0,
            )
        except ValueError:
            ts = now

        return FuturesQuote(
            symbol=symbol,
            price=_f("futs_prpr"),
            change=_f("prdy_vrss"),
            change_pct=_f("prdy_ctrt"),
            volume=_i("acml_vol"),
            open_price=_f("futs_oprc"),
            high_price=_f("futs_hgpr"),
            low_price=_f("futs_lwpr"),
            timestamp=ts,
            provider="kis",
        )

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Get symbol information with expiry warning if within 7 days."""
        # KIS KOSPI200 futures symbol format: 101 + expiry code
        # Expiry is the 2nd Thursday of the expiry month
        # Symbol example: "101V12" — need to parse expiry from symbol or lookup via API
        # For now, parse expiry from symbol code (simplified approach)
        expiry = _parse_symbol_expiry(symbol)
        days_to_expiry = None
        expiry_warning = False

        if expiry:
            now = datetime.now()
            delta = (expiry - now).days
            days_to_expiry = max(0, delta)
            expiry_warning = days_to_expiry < EXPIRY_WARNING_DAYS
            if expiry_warning:
                logger.warning(
                    "Futures symbol %s expires in %d days! Update FUTURES_SYMBOL.",
                    symbol, days_to_expiry
                )

        return SymbolInfo(
            symbol=symbol,
            expires_at=expiry,
            days_to_expiry=days_to_expiry,
            expiry_warning=expiry_warning,
        )

    async def get_day_futures_price(self, symbol: str) -> dict:
        """Fetch current KOSPI200 index price via REST (FHMIF10000000, output3)."""
        await self._ensure_client()
        token = await self._get_token()
        url = f"{settings.kis_base_url}/uapi/domestic-futureoption/v1/quotations/inquire-price"
        headers = self._make_headers(token, "FHMIF10000000")
        params = {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": symbol}
        resp = await self._client.get(url, headers=headers, params=params)  # type: ignore
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise KISAPIError(f"KOSPI200 price error: {data.get('msg1')} (symbol={symbol})")
        output3 = data.get("output3", {})
        output1 = data.get("output1", {})

        def _f(d, *keys):
            for k in keys:
                v = d.get(k)
                if v and v not in ('', '0', '0.00'):
                    try:
                        return float(v)
                    except (ValueError, TypeError):
                        pass
            return None

        def _fz(d, *keys):
            v = _f(d, *keys)
            return v if v is not None else 0.0

        return {
            "symbol": "KOSPI200",
            "price": _fz(output3, "bstp_nmix_prpr"),
            "change": _fz(output3, "bstp_nmix_prdy_vrss"),
            "change_pct": _fz(output3, "bstp_nmix_prdy_ctrt"),
            "volume": 0,
            "open": _f(output3, "bstp_nmix_oprc") or _f(output1, "stck_oprc"),
            "high": _f(output3, "bstp_nmix_hgpr") or _f(output1, "stck_hgpr"),
            "low": _f(output3, "bstp_nmix_lwpr") or _f(output1, "stck_lwpr"),
        }

    async def get_options_board(self, product_code: str, expiry_code: str) -> tuple[list, list]:
        """Fetch options board (call/put) from KIS FHPIF05030100."""
        await self._ensure_client()
        token = await self._get_token()
        url = (
            f"{settings.kis_base_url}/uapi/"
            "domestic-futureoption/v1/quotations/display-board-callput"
        )
        headers = self._make_headers(token, "FHPIF05030100")
        params = {
            "FID_COND_MRKT_DIV_CODE": "O",
            "FID_COND_SCR_DIV_CODE": "20503",
            "FID_MRKT_CLS_CODE": "CO",
            "FID_MTRT_CNT": expiry_code,
            "FID_COND_MRKT_CLS_CODE": product_code,
            "FID_MRKT_CLS_CODE1": "PO",
        }
        resp = await self._client.get(url, headers=headers, params=params)  # type: ignore
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise KISAPIError(
                f"Options board error: {data.get('msg1')} "
                f"(product={product_code}, expiry={expiry_code})"
            )
        return data.get("output1", []), data.get("output2", [])

    async def get_options_investor(self, market_iscd: str, call_iscd2: str, put_iscd2: str) -> dict:
        """Fetch investor trading trend for call+put from KIS FHPTJ04030000."""
        await self._ensure_client()
        token = await self._get_token()
        url = (
            f"{settings.kis_base_url}/uapi/"
            "domestic-stock/v1/quotations/inquire-investor-time-by-market"
        )
        headers = self._make_headers(token, "FHPTJ04030000")

        async def _fetch(iscd2: str) -> dict:
            r = await self._client.get(
                url,
                headers=headers,
                params={"fid_input_iscd": market_iscd, "fid_input_iscd_2": iscd2},
            )  # type: ignore
            r.raise_for_status()
            d = r.json()
            return (d.get("output") or [{}])[0]

        call_data, put_data = await asyncio.gather(_fetch(call_iscd2), _fetch(put_iscd2))
        return {"call": call_data, "put": put_data}

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


def _parse_symbol_expiry(symbol: str) -> Optional[datetime]:
    """
    Parse expiry date from KIS CME-linked night futures symbol.
    Format: A01[year_digit][month_2digit]  e.g. "A01603" = March 2026
    (Source: fo_cme_code.mst master file from KIS)
    """
    if len(symbol) != 6 or not symbol.startswith("A01"):
        return None

    try:
        year_digit = int(symbol[3])
        month = int(symbol[4:6])
        if month not in (3, 6, 9, 12):
            return None
        base_year = (datetime.now().year // 10) * 10 + year_digit
        return _second_thursday(base_year, month)
    except (ValueError, IndexError):
        return None


def _second_thursday(year: int, month: int) -> datetime:
    """Return the 2nd Thursday of given year/month."""
    from calendar import THURSDAY, monthcalendar
    weeks = monthcalendar(year, month)
    thursdays = [week[THURSDAY] for week in weeks if week[THURSDAY] != 0]
    day = thursdays[1] if len(thursdays) >= 2 else thursdays[0]
    return datetime(year, month, day, 15, 45, 0)  # KRX close time
