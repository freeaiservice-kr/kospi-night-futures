"""stock_master.py — 종목 마스터 관리.

시드 48종목(섹터별 8개 x 6개 섹터) + sector_code/sector_category 포함.
sector_code는 KIS FHPUP02140000 응답 기준 KRX 업종코드 (sector_mapping.py와 동일).
"""
from __future__ import annotations

# 48개 시드 종목. sector_code는 sector_mapping.py의 krx_codes 형식과 동일.
SEED_STOCKS: list[dict] = [
    # 반도체 (G25: 전기·전자)
    {"code": "005930", "name": "삼성전자",         "market": "KOSPI",  "sector_code": "G25", "sector_category": "semiconductor"},
    {"code": "000660", "name": "SK하이닉스",        "market": "KOSPI",  "sector_code": "G25", "sector_category": "semiconductor"},
    {"code": "042700", "name": "한미반도체",         "market": "KOSPI",  "sector_code": "G25", "sector_category": "semiconductor"},
    {"code": "009150", "name": "삼성전기",           "market": "KOSPI",  "sector_code": "G25", "sector_category": "semiconductor"},
    {"code": "066570", "name": "LG전자",             "market": "KOSPI",  "sector_code": "G25", "sector_category": "semiconductor"},
    {"code": "058470", "name": "리노공업",           "market": "KOSPI",  "sector_code": "G25", "sector_category": "semiconductor"},
    {"code": "039030", "name": "이오테크닉스",       "market": "KOSPI",  "sector_code": "G25", "sector_category": "semiconductor"},
    {"code": "240810", "name": "원익IPS",            "market": "KOSDAQ", "sector_code": "G25", "sector_category": "semiconductor"},

    # 자동차 (G35: 운수장비)
    {"code": "005380", "name": "현대차",             "market": "KOSPI",  "sector_code": "G35", "sector_category": "auto"},
    {"code": "000270", "name": "기아",               "market": "KOSPI",  "sector_code": "G35", "sector_category": "auto"},
    {"code": "012330", "name": "현대모비스",          "market": "KOSPI",  "sector_code": "G35", "sector_category": "auto"},
    {"code": "011210", "name": "현대위아",            "market": "KOSPI",  "sector_code": "G35", "sector_category": "auto"},
    {"code": "204320", "name": "만도",               "market": "KOSPI",  "sector_code": "G35", "sector_category": "auto"},
    {"code": "018880", "name": "한온시스템",          "market": "KOSPI",  "sector_code": "G35", "sector_category": "auto"},
    {"code": "073240", "name": "금호타이어",          "market": "KOSPI",  "sector_code": "G35", "sector_category": "auto"},
    {"code": "161390", "name": "한국타이어앤테크놀로지", "market": "KOSPI", "sector_code": "G35", "sector_category": "auto"},

    # 뷰티/화학 (G30: 화학)
    {"code": "090430", "name": "아모레퍼시픽",        "market": "KOSPI",  "sector_code": "G30", "sector_category": "beauty"},
    {"code": "051900", "name": "LG생활건강",          "market": "KOSPI",  "sector_code": "G30", "sector_category": "beauty"},
    {"code": "051910", "name": "LG화학",              "market": "KOSPI",  "sector_code": "G30", "sector_category": "beauty"},
    {"code": "006400", "name": "삼성SDI",             "market": "KOSPI",  "sector_code": "G30", "sector_category": "beauty"},
    {"code": "009830", "name": "한화솔루션",           "market": "KOSPI",  "sector_code": "G30", "sector_category": "beauty"},
    {"code": "011170", "name": "롯데케미칼",           "market": "KOSPI",  "sector_code": "G30", "sector_category": "beauty"},
    {"code": "002380", "name": "KCC",                "market": "KOSPI",  "sector_code": "G30", "sector_category": "beauty"},
    {"code": "000240", "name": "한국콜마홀딩스",       "market": "KOSPI",  "sector_code": "G30", "sector_category": "beauty"},

    # 필수소비재 (G15: 음식료품)
    {"code": "097950", "name": "CJ제일제당",          "market": "KOSPI",  "sector_code": "G15", "sector_category": "consumer"},
    {"code": "271560", "name": "오리온",              "market": "KOSPI",  "sector_code": "G15", "sector_category": "consumer"},
    {"code": "280360", "name": "롯데칠성음료",         "market": "KOSPI",  "sector_code": "G15", "sector_category": "consumer"},
    {"code": "145990", "name": "삼양식품",            "market": "KOSPI",  "sector_code": "G15", "sector_category": "consumer"},
    {"code": "000080", "name": "하이트진로",           "market": "KOSPI",  "sector_code": "G15", "sector_category": "consumer"},
    {"code": "033780", "name": "KT&G",               "market": "KOSPI",  "sector_code": "G15", "sector_category": "consumer"},
    {"code": "004370", "name": "농심",               "market": "KOSPI",  "sector_code": "G15", "sector_category": "consumer"},
    {"code": "003230", "name": "삼양홀딩스",           "market": "KOSPI",  "sector_code": "G15", "sector_category": "consumer"},

    # 에너지 (G45: 전기가스업)
    {"code": "015760", "name": "한국전력",            "market": "KOSPI",  "sector_code": "G45", "sector_category": "energy"},
    {"code": "036460", "name": "한국가스공사",         "market": "KOSPI",  "sector_code": "G45", "sector_category": "energy"},
    {"code": "096770", "name": "SK이노베이션",         "market": "KOSPI",  "sector_code": "G45", "sector_category": "energy"},
    {"code": "010950", "name": "S-Oil",              "market": "KOSPI",  "sector_code": "G45", "sector_category": "energy"},
    {"code": "078930", "name": "GS",                 "market": "KOSPI",  "sector_code": "G45", "sector_category": "energy"},
    {"code": "034020", "name": "두산에너빌리티",        "market": "KOSPI",  "sector_code": "G45", "sector_category": "energy"},
    {"code": "010060", "name": "OCI홀딩스",           "market": "KOSPI",  "sector_code": "G45", "sector_category": "energy"},
    {"code": "267250", "name": "HD현대",              "market": "KOSPI",  "sector_code": "G45", "sector_category": "energy"},

    # 금융 (G55: 금융업)
    {"code": "105560", "name": "KB금융",              "market": "KOSPI",  "sector_code": "G55", "sector_category": "finance"},
    {"code": "055550", "name": "신한지주",             "market": "KOSPI",  "sector_code": "G55", "sector_category": "finance"},
    {"code": "086790", "name": "하나금융지주",          "market": "KOSPI",  "sector_code": "G55", "sector_category": "finance"},
    {"code": "138040", "name": "메리츠금융지주",        "market": "KOSPI",  "sector_code": "G55", "sector_category": "finance"},
    {"code": "032830", "name": "삼성생명",             "market": "KOSPI",  "sector_code": "G55", "sector_category": "finance"},
    {"code": "000810", "name": "삼성화재",             "market": "KOSPI",  "sector_code": "G55", "sector_category": "finance"},
    {"code": "005830", "name": "DB손해보험",           "market": "KOSPI",  "sector_code": "G55", "sector_category": "finance"},
    {"code": "316140", "name": "우리금융지주",          "market": "KOSPI",  "sector_code": "G55", "sector_category": "finance"},
]

# sector_category → 한글 표시명
SECTOR_LABELS: dict[str, str] = {
    "semiconductor": "반도체",
    "auto":          "자동차",
    "beauty":        "화학/뷰티",
    "consumer":      "필수소비재",
    "energy":        "에너지",
    "finance":       "금융",
}


def load_seed_stocks() -> list[dict]:
    """시드 종목 리스트 반환. sector_code, sector_category 포함."""
    return SEED_STOCKS
