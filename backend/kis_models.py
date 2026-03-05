
from pydantic import BaseModel


class KISTokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int  # seconds
    access_token_token_expired: str  # datetime string "YYYY-MM-DD HH:MM:SS"


class KISApprovalKeyResponse(BaseModel):
    approval_key: str


class KISPriceOutput(BaseModel):
    """Raw KIS futures price API output field"""
    stck_cntg_hour: str = ""    # 체결시각 HHMMSS
    futs_prpr: str = "0"         # 현재가 (futures present price)
    futs_bspr: str = "0"         # 기준가 (base price / 전일종가)
    prdy_vrss: str = "0"         # 전일대비
    prdy_ctrt: str = "0.00"      # 전일대비율
    acml_vol: str = "0"          # 누적거래량
    futs_oprc: str = "0"         # 시가
    futs_hgpr: str = "0"         # 고가
    futs_lwpr: str = "0"         # 저가
    futs_shrn_iscd: str = ""     # 선물 단축 종목코드


class KISPriceResponse(BaseModel):
    rt_cd: str          # "0" = success
    msg_cd: str
    msg1: str
    output: KISPriceOutput | None = None
