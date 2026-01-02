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

    # --- Multi-bot / multi-portfolio risk (optional) ---
    # If you run multiple bot processes (binance futures + upbit + stocks, etc.),
    # you may want a *shared* exposure cap to avoid over-sizing across processes.
    #
    # - *_EXPOSURE_FRAC: 0~1 (fraction of equity). Set < 1.0 to enable.
    # - *_NOTIONAL: absolute quote-currency cap. Set > 0 to enable.
    MAX_ACCOUNT_EXPOSURE_FRAC: float = 1.0
    MAX_TOTAL_EXPOSURE_FRAC: float = 1.0
    MAX_ACCOUNT_NOTIONAL: float = 0.0
    MAX_TOTAL_NOTIONAL: float = 0.0
    GLOBAL_RISK_STATE_PATH: str = "state/global_risk.json"
    DEFAULT_SLIPPAGE_BPS: int = 5
    DEFAULT_FEE_BPS: int = 10

    # Paper trading defaults
    PAPER_INITIAL_CASH: float = 1_000_000.0
    PAPER_FEE_BPS: int = 10
    PAPER_SLIPPAGE_BPS: int = 5

    # Exit rules (applied in live/paper loops)
    STOP_LOSS_PCT: float = 0.01
    TRAILING_STOP_PCT: float = 0.005

    # Scalping defaults (strategy='scalp')
    SCALP_TP_NET_PCT: float = 0.0039  # 0.39% (net of fees) at 1x; at 10x -> ~3.9% equity return
    SCALP_LEVERAGE: float = 1.0
    # Order sizing
    # - fixed: use LiveConfig.intended_notional
    # - equity_pct: use (equity * SCALP_TRADE_EQUITY_FRAC) as margin budget; notional = margin*leverage on futures
    SCALP_ORDER_SIZING_MODE: str = "fixed"  # fixed | equity_pct
    SCALP_TRADE_EQUITY_FRAC: float = 0.2  # fraction of wallet balance to allocate as margin per entry
    SCALP_MIN_NOTIONAL_POLICY: str = "auto"  # skip | bump | auto
    SCALP_MIN_NOTIONAL_BUFFER: float = 1.01  # bump target = min_notional * buffer
    # When SCALP_MIN_NOTIONAL_POLICY="auto": allow bump only if required notional is within (1 + frac) of intended notional
    SCALP_AUTO_BUMP_MAX_OVER_NOTIONAL_FRAC: float = 0.25

    # (AUTO v2) Additional safety caps for bump decisions
    # - If bumping to min_notional would require using more than this fraction of equity as margin, skip.
    SCALP_AUTO_BUMP_MAX_EQUITY_FRAC: float = 0.30
    # - If bumping would require more than (1 + frac) of the intended margin budget, skip.
    SCALP_AUTO_BUMP_MAX_OVER_MARGIN_FRAC: float = 0.50

    SCALP_MIN_1M_TRADE_VALUE: float = 0.0
    SCALP_MIN_ORDERBOOK_NOTIONAL: float = 0.0
    SCALP_IMBALANCE_THRESHOLD: float = 0.15

    # scalping mean-reversion entry params
    SCALP_RSI_LONG_TRIGGER: float = 40.0
    SCALP_RSI_SHORT_MIN: float = 65.0
    SCALP_RSI_SHORT_MAX: float = 70.0
    SCALP_USE_RSI_CROSS: bool = True
    SCALP_REQUIRE_REVERSAL_CANDLE: bool = True
    SCALP_MIN_VOL_SURGE: float = 0.0

    # scalp executed-trade pressure (recent ticks)
    SCALP_PRESSURE_WINDOW_SEC: int = 15
    SCALP_TRADE_PRESSURE_THRESHOLD: float = 0.20
    SCALP_MIN_TRADE_PRESSURE_NOTIONAL: float = 0.0

    # WebSocket trade stream for executed-trade pressure (fallback to REST if stale)
    SCALP_USE_WS_TRADES: bool = True
    SCALP_WS_STALENESS_SEC: int = 30

    # Trade-flow refinement for 'money flow spike' detection
    SCALP_FLOW_WINDOW_SEC: int = 5
    SCALP_MIN_FLOW_NOTIONAL_RATE: float = 0.0  # quote notional per sec
    SCALP_MIN_FLOW_ACCEL: float = 0.0  # quote notional per sec^2
    SCALP_LARGE_TRADE_MIN_NOTIONAL: float = 0.0  # quote notional threshold
    SCALP_MIN_LARGE_TRADE_SHARE: float = 0.0  # 0~1
    SCALP_MIN_TRADE_COUNT: int = 0

    # Orderbook delta refinement
    SCALP_OB_DELTA_DEPTH: int = 10
    SCALP_MIN_OB_IMB_DELTA: float = 0.0

    # Liquidation clustering (Binance Futures forceOrder stream)
    SCALP_USE_LIQUIDATION_STREAM: bool = True
    SCALP_LIQ_WINDOW_SEC: int = 30
    SCALP_LIQ_BUCKET_BPS: float = 10.0

    # Aggressive limit/IOC execution (slippage-aware)
    SCALP_ENTRY_USE_IOC: bool = True
    SCALP_EXIT_USE_IOC: bool = True
    SCALP_IOC_PRICE_PAD_BPS: float = 2.0
    SCALP_IOC_MAX_CHASE_BPS: float = 12.0

    # Microstructure / volatility filters for scalp
    SCALP_MAX_SPREAD_BPS: float = 8.0
    SCALP_MAX_1M_RANGE_PCT: float = 0.012
    SCALP_MAX_1M_BODY_PCT: float = 0.010

    # 'News candle' lockout
    SCALP_NEWS_SPIKE_TV_MULT: float = 5.0
    SCALP_NEWS_SPIKE_MOVE_PCT: float = 0.007
    SCALP_NEWS_COOLDOWN_SEC: int = 300

    # --- Exchange credentials ---
    UPBIT_ACCESS_KEY: str | None = None
    UPBIT_SECRET_KEY: str | None = None

    BINANCE_API_KEY: str | None = None
    BINANCE_API_SECRET: str | None = None
    BINANCE_FUTURES: bool = False
    BINANCE_BASE_URL: str = "https://api.binance.com"
    BINANCE_FUTURES_BASE_URL: str | None = None  # e.g. https://fapi.binance.com

    # Korea Investment & Securities (optional)
    KIS_APP_KEY: str | None = None
    KIS_APP_SECRET: str | None = None
    KIS_ACCOUNT_NO: str | None = None
    KIS_PRODUCT_CODE: str | None = None
    KIS_BASE_URL: str = "https://openapi.koreainvestment.com:9443"

    # Namoo (QV OpenAPI 기반) — requires local Windows bridge process.
    NAMOO_BRIDGE_URL: str = "http://127.0.0.1:8700"
    NAMOO_ACCOUNT_NO: str | None = None

    # Kiwoom REST API (stocks)
    KIWOOM_APPKEY: str | None = None
    KIWOOM_SECRETKEY: str | None = None
    KIWOOM_ACCOUNT_NO: str | None = None
    KIWOOM_BASE_URL: str = "https://api.kiwoom.com"

    # Keyword-based news scoring (toy)
    NEWS_POSITIVE: str = "수주,공급계약,계약,임상,승인,인수,합병,흑자전환"
    NEWS_NEGATIVE: str = "횡령,배임,유상증자,상장폐지,상폐,해킹,제재,조사"


def get_settings() -> Settings:
    return Settings()
