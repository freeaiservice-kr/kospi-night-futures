#!/usr/bin/env python3
"""
KIS API connectivity test script.
Tests: authentication, price query, WebSocket protocol detection.

Usage:
    python3 scripts/check_api.py

Requires KIS_APP_KEY and KIS_APP_SECRET in .env or environment.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Try manual .env loading
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def check_env():
    app_key = os.environ.get("KIS_APP_KEY", "")
    app_secret = os.environ.get("KIS_APP_SECRET", "")
    if not app_key or app_key == "your_app_key_here":
        print("❌ KIS_APP_KEY not set. Copy .env.example → .env and fill in your credentials.")
        return False
    if not app_secret or app_secret == "your_app_secret_here":
        print("❌ KIS_APP_SECRET not set.")
        return False
    print(f"✅ KIS_APP_KEY set: {app_key[:8]}...")
    return True


async def test_auth():
    import httpx
    base_url = os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")

    print(f"\n[1] Testing KIS OAuth2 authentication → {base_url}/oauth2/tokenP")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{base_url}/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret,
        })
        if resp.status_code != 200:
            print(f"❌ Auth failed: HTTP {resp.status_code} — {resp.text[:200]}")
            return None
        data = resp.json()
        if "access_token" not in data:
            print(f"❌ No access_token in response: {data}")
            return None
        token = data["access_token"]
        expires_in = data.get("expires_in", 0)
        print(f"✅ Token acquired! Expires in {expires_in}s ({expires_in//3600}h)")
        return token


async def test_price_query(token: str):
    import httpx
    base_url = os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    symbol = os.environ.get("FUTURES_SYMBOL", "101V6")
    if symbol == "auto":
        symbol = "101V6"
        print(f"  (Using placeholder symbol {symbol} — set FUTURES_SYMBOL in .env for real symbol)")

    print(f"\n[2] Testing price query for symbol: {symbol}")
    headers = {
        "Authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "FHMIF10000000",
        "Content-Type": "application/json",
        "custtype": "P",
    }
    params = {"FID_COND_MRKT_DIV_CODE": "NF", "FID_INPUT_ISCD": symbol}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{base_url}/uapi/domestic-futureoption/v1/quotations/inquire-price",
            headers=headers,
            params=params,
        )
        data = resp.json()
        rt_cd = data.get("rt_cd", "?")
        msg = data.get("msg1", "")
        if rt_cd == "0":
            output = data.get("output", {})
            price = output.get("futs_prpr", "N/A")
            print(f"✅ Price query OK! 현재가: {price}, msg: {msg}")
        else:
            print(f"⚠️  Price query returned rt_cd={rt_cd}: {msg}")
            print(f"   (This may be OK if market is closed or symbol is wrong)")
            print(f"   Full response: {json.dumps(data, ensure_ascii=False, indent=2)}")


async def test_websocket_protocol():
    ws_url_ws = os.environ.get("KIS_WS_URL", "ws://ops.koreainvestment.com:21000")
    ws_url_wss = ws_url_ws.replace("ws://", "wss://")

    print(f"\n[3] Testing WebSocket protocol availability")

    # Test ws://
    try:
        import websockets
        print(f"  Trying ws:// → {ws_url_ws}")
        async with websockets.connect(ws_url_ws, open_timeout=5) as ws:
            print(f"✅ ws:// works! Use KIS_WS_URL=ws://...")
            return "ws"
    except Exception as e:
        print(f"  ws:// failed: {e}")

    # Try wss://
    try:
        print(f"  Trying wss:// → {ws_url_wss}")
        async with websockets.connect(ws_url_wss, open_timeout=5) as ws:
            print(f"✅ wss:// works! Update KIS_WS_URL=wss://...")
            return "wss"
    except Exception as e:
        print(f"  wss:// failed: {e}")

    print("⚠️  Both ws:// and wss:// failed. Check network connectivity.")
    return None


async def main():
    print("=" * 60)
    print("KIS API Connectivity Check")
    print("=" * 60)

    if not check_env():
        sys.exit(1)

    token = await test_auth()
    if not token:
        sys.exit(1)

    await test_price_query(token)
    await test_websocket_protocol()

    print("\n" + "=" * 60)
    print("✅ Connectivity check complete!")
    print("   If all checks passed, run: make dev")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
