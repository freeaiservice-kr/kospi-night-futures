# WebSocket-to-Polling Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace browser-facing real-time WebSocket stream with short-interval REST polling while keeping backend data collection and history stores active.

**Architecture:** Keep a single FastAPI service that continuously collects KIS data (WebSocket + REST fallback internally), persist latest snapshots in memory/state, and expose deterministic REST endpoints. Frontend periodically fetches only what it needs and renders charts/status from returned payloads. CORS and domain policy remain explicit.

**Tech Stack:** FastAPI + python-3.11, vanilla JS (Alpine) + 기존 light chart stack, pytest, ruff.

---

### Task 1: backend 최신 스냅샷 API 제공용 서비스 단위 API 추가 (MarketDataService)

**Files:**
- Modify: `/Users/server/workspace/kospi-night-futures/backend/market_data.py`
- Modify: `/Users/server/workspace/kospi-night-futures/backend/tests/test_market_data.py`

**Step 1: Write failing test**
`/Users/server/workspace/kospi-night-futures/backend/tests/test_market_data.py`

```python
def test_get_latest_returns_quote_fields_when_present():
    service = MarketDataService()
    service._last_quote = FuturesQuote(
        symbol="101V2612",
        price=100.0,
        change=1.2,
        change_pct=1.2,
        volume=10,
        open_price=98.0,
        high_price=101.0,
        low_price=97.5,
        timestamp=datetime(2026, 3, 5, 9, 0, 0, tzinfo=timezone.utc),
        provider="kis",
        cttr=55.1,
        basis=0.5,
        open_interest=1234,
        oi_change=12,
    )
    service._last_trade_price = 100.0
    latest = service.get_latest_snapshot()
    assert latest["type"] == "quote"
    assert latest["data"]["symbol"] == "101V2612"
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_market_data.py::test_get_latest_returns_quote_fields_when_present -q`
Expected: FAIL (method `get_latest_snapshot` missing).

**Step 3: Write minimal implementation**

`/Users/server/workspace/kospi-night-futures/backend/market_data.py`
- Add method `get_latest_snapshot()` returning `None` or dictionary with fields:
  - `type: "quote"`
  - `data: {symbol, price, change, change_pct, volume, open_price, high_price, low_price, timestamp, provider, cttr, basis, open_interest, oi_change}`
  - `last_trade_price`
  - `state` (connected/disconnected).
- Ensure timestamp is ISO string and no crash when no quote.

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_market_data.py::test_get_latest_returns_quote_fields_when_present -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/market_data.py backend/tests/test_market_data.py
git commit -m "feat: expose market data latest snapshot accessor"
```

### Task 2: backend 옵션 스냅샷 API 제공용 서비스 단위 API 추가 (OptionsDataService)

**Files:**
- Modify: `/Users/server/workspace/kospi-night-futures/backend/options_data.py`
- Modify: `/Users/server/workspace/kospi-night-futures/backend/tests/test_api.py`

**Step 1: Write failing test**

`/Users/server/workspace/kospi-night-futures/backend/tests/test_api.py`

```python
def test_get_latest_options_data_returns_structured_payload(client):
    with patch("backend.api.OptionsDataService", autospec=True) as m:
        svc = m.return_value
        svc.get_latest_snapshot.return_value = {
            "type": "options_latest",
            "product": "WKI",
            "board": {"updated_at": "08:00:00", "expiry": "26123", "calls": [], "puts": []},
            "investor": {"product": "WKI", "rows": []},
            "futures": {"price": 100.0},
        }
        resp = client.get("/api/v1/options/latest?product=WKI")
        assert resp.status_code == 200
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_api.py::test_get_latest_options_data_returns_structured_payload -q`
Expected: FAIL (endpoint method missing).

**Step 3: Write minimal implementation**

- Add `get_latest_snapshot(product)` in `/Users/server/workspace/kospi-night-futures/backend/options_data.py`.
- Return latest board/investor/futures cache for product with default fallback to WKI.
- Keep missing data as safe empty placeholders (`{}`/`[]`).

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_api.py::test_get_latest_options_data_returns_structured_payload -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/options_data.py backend/tests/test_api.py
git commit -m "feat: expose latest options snapshot accessor"
```

### Task 3: 백엔드 REST 엔드포인트 추가

**Files:**
- Modify: `/Users/server/workspace/kospi-night-futures/backend/api.py`
- Modify: `/Users/server/workspace/kospi-night-futures/backend/tests/test_api.py`

**Step 1: Write failing test**

`/Users/server/workspace/kospi-night-futures/backend/tests/test_api.py`

```python
def test_futures_latest_endpoint_shape(client):
    resp = client.get("/api/v1/futures/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "quote"
    assert data["data"]["symbol"]
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_api.py::test_futures_latest_endpoint_shape -q`
Expected: FAIL (route missing).

**Step 3: Write minimal implementation**

- Add endpoint `GET /api/v1/futures/latest` calling `market_data = request.app.state.market_data.get_latest_snapshot()`.
- Add endpoint `GET /api/v1/options/latest?product=WKI` calling options service accessor.
- Both endpoints return 503 with `detail` when no data available.

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_api.py::test_futures_latest_endpoint_shape -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/api.py backend/tests/test_api.py
git commit -m "feat: add polling-friendly latest data endpoints"
```

### Task 4: 프런트 futures 스토어를 polling으로 전환

**Files:**
- Modify: `/Users/server/workspace/kospi-night-futures/frontend/js/futuresStore.js`
- Modify: `/Users/server/workspace/kospi-night-futures/frontend/js/app.js` (if needed)

**Step 1: Write failing test**

No JS unit framework currently in repo. Add JS test can be delayed to next task.

**Step 2: Run test to verify it fails**

N/A for this repo.

**Step 3: Write minimal implementation**

- Remove `_connect`, WebSocket state/ping/reconnect code from `init()` path.
- Add `FETCH_POLL_INTERVAL_MS = 2000` and `_pollFutures()` timer loop.
- On success, parse `/api/v1/futures/latest` and map payload through existing `_handleMessage`-equivalent logic (new helper `_applyLatestPayload`).
- Keep stale logic: if no update in N polls, set `isStale=true`.
- Preserve chart update using `chart_tick` updates from `/api/v1/options/futures-history` or latest snapshot.

**Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests -q`
Expected: backend tests unchanged pass.

**Step 5: Commit**

```bash
git add frontend/js/futuresStore.js frontend/js/app.js
git commit -m "refactor(frontend): switch futures panel from WS to polling"
```

### Task 5: 프런트 options 스토어 polling 전환

**Files:**
- Modify: `/Users/server/workspace/kospi-night-futures/frontend/js/optionsStore.js`

**Step 1: Write failing test**

No JS unit framework currently in repo. Add behavior later in separate QA pass.

**Step 2: Run test to verify it fails**

N/A for this repo.

**Step 3: Write minimal implementation**

- Remove options WebSocket connect/reconnect code from `init()` path.
- Add polling loop at `POLL_MS = 3000` for `/api/v1/options/latest?product=${activeProduct}`.
- Apply payload updates directly to `rawCalls/rawPuts/callInvestor/putInvestor/futures*`.
- Keep current product switching by restarting polling timer to keep server load controlled.

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests -q`
Expected: all backend tests pass (frontend unaffected for backend test suite).

**Step 5: Commit**

```bash
git add frontend/js/optionsStore.js
git commit -m "refactor(frontend): switch options panel from WS to polling"
```

### Task 6: Cloudflare/도메인 운영 플래그 연동 (선택)

**Files:**
- Modify: `/Users/server/workspace/kospi-night-futures/backend/config.py`
- Modify: `/Users/server/workspace/kospi-night-futures/backend/api.py`

**Step 1: Write failing test**

`/Users/server/workspace/kospi-night-futures/backend/tests/test_api.py`

```python
def test_polling_feature_flag_toggle_smoke():
    pass
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_api.py -q`
Expected: fail or todo if added as placeholder.

**Step 3: Write minimal implementation**

- Add env flag `POLLING_ONLY=true/false`.
- In endpoints/UI responses, keep both data paths for rollback:
  - if true: frontend uses `/api/v1/*/latest`
  - if false: keep existing WS path.

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_api.py -q`
Expected: no regression.

**Step 5: Commit**

```bash
git add backend/config.py backend/api.py backend/tests/test_api.py
git commit -m "chore: add optional polling-only switch for phased rollout"
```

### Task 7: 운영 문서 업데이트

**Files:**
- Modify: `/Users/server/workspace/kospi-night-futures/README.md`

**Step 1: Write/update docs**
- Add section: WebSocket 제거/복구 시나리오, polling 간격, 예상 호출량 계산법, 비용 제약.

**Step 2: Review**
- Confirm domain/CORS/Cloudflare 설정 links remain valid.

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add polling migration runbook and operation notes"
```

## 실행 후 QA 체크

1. Backend: `make test` pass
2. Local run: `make dev`, 브라우저에서 페이지 로드 후 1분 동안 값 갱신 확인
3. Render 배포: 서비스 부하 예측(동시접속자 × 호출빈도) 점검
4. CORS 설정값에 최종 도메인(`https://xn--6i4bt3f.com`, `https://www.xn--6i4bt3f.com`) 반영

**Plan complete and saved to `docs/plans/2026-03-05-websocket-polling-refactor.md`. Two execution options:**

1. **Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration
2. **Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
