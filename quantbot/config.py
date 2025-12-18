from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: str = "dev"
    DB_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/quantbot"
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "Asia/Seoul"

    # Safety switch: live orders are sent ONLY when true
    TRADING_ENABLED: bool = False

    MAX_POSITION_PER_SYMBOL: float = 0.10
    MAX_DAILY_LOSS: float = 0.03
    DEFAULT_SLIPPAGE_BPS: int = 5
    DEFAULT_FEE_BPS: int = 10

    UPBIT_ACCESS_KEY: str | None = None
    UPBIT_SECRET_KEY: str | None = None

    BINANCE_API_KEY: str | None = None
    BINANCE_API_SECRET: str | None = None
    BINANCE_FUTURES: bool = False
    BINANCE_BASE_URL: str = "https://api.binance.com"

    KIS_APP_KEY: str | None = None
    KIS_APP_SECRET: str | None = None
    KIS_ACCOUNT_NO: str | None = None
    KIS_PRODUCT_CODE: str | None = None
    KIS_BASE_URL: str = "https://openapi.koreainvestment.com:9443"

    # 나무증권(QV OpenAPI 기반) 실행은 Windows(32-bit DLL) 브릿지 프로세스가 필요합니다.
    NAMOO_BRIDGE_URL: str = "http://127.0.0.1:8700"

    NEWS_POSITIVE: str = "수주,공급계약,계약,임상,승인,인수,합병,흑자전환"
    NEWS_NEGATIVE: str = "횡령,배임,유상증자,상장폐지,상폐,해킹,제재,조사"

def get_settings() -> Settings:
    return Settings()
