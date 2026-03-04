# 야선 - KOSPI 200 야간선물 실시간 서비스

KOSPI 200 야간선물(KRX Night Futures) 실시간 시세 서비스.

**한국투자증권 Open API** 기반으로 야간 선물 체결가를 실시간으로 브라우저에 전달합니다.

## 스택

| 레이어 | 기술 |
|--------|------|
| Backend | FastAPI + Python 3.11 |
| 실시간 | KIS WebSocket (야간선물 체결) |
| Frontend | Vanilla HTML + Alpine.js + lightweight-charts |
| 배포 | Docker / fly.io |

## 빠른 시작

### 1. 환경 설정

```bash
cp .env.example .env
# .env 파일에 한국투자증권 API 키 입력:
# KIS_APP_KEY=...
# KIS_APP_SECRET=...
# KIS_ACCOUNT_NO=...
# FUTURES_SYMBOL=101V6  # 또는 실제 선물 코드
```

### 2. 의존성 설치

```bash
# Python 3.11 필요
pip install -e "backend[dev]"
```

### 3. API 연결 확인

```bash
make check-api
```

### 4. 개발 서버 실행

```bash
make dev
# → http://localhost:8000
```

## 디렉토리 구조

```
kospi-night-futures/
├── backend/          # FastAPI 서버
│   ├── main.py       # 앱 팩토리
│   ├── config.py     # 설정 (pydantic-settings)
│   ├── kis_client.py # KIS REST 클라이언트
│   ├── kis_websocket.py  # KIS WebSocket 스트리밍
│   ├── market_data.py    # 브라우저 fan-out 서비스
│   ├── market_status.py  # 야간장 세션 상태
│   └── api.py        # REST/WebSocket 엔드포인트
├── frontend/         # 정적 대시보드
│   ├── index.html    # Alpine.js 대시보드
│   └── js/app.js     # WebSocket 클라이언트
├── scripts/
│   └── check_api.py  # KIS API 연결 테스트
└── Makefile
```

## KIS API 설정

1. [한국투자증권 Open API](https://apiportal.koreainvestment.com/) 가입
2. 앱 생성 후 APP_KEY / APP_SECRET 발급
3. `.env`에 입력

### 선물 심볼 코드

KIS KOSPI200 야간선물 심볼 형식: `101` + 월 코드 + 연도 마지막 자리

| 월 | 코드 |
|----|------|
| 3월 | H |
| 6월 | M |
| 9월 | U |
| 12월 | Z |

예: `101H6` = KOSPI200 선물, 2026년 3월물

`FUTURES_SYMBOL=auto` 설정 시 자동 감지를 시도합니다.

## 테스트

```bash
make test
```

## 야간 거래 시간 (KST)

| 세션 | 시간 |
|------|------|
| 단일가 (장전) | 17:50 ~ 18:00 |
| 야간 정규 | 18:00 ~ 익일 05:00 |
| 단일가 (장마감) | 04:50 ~ 05:00 |

## 면책조항

본 서비스는 **정보 제공 목적**으로만 운영됩니다. 투자 권유가 아닙니다.
