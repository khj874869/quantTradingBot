# QuantBot (Multi-TF + News + Orderbook) — 나무 / Upbit / Binance (+KIS optional)

> ⚠️ Educational/reference implementation. Running a live trading bot involves financial risk.
> Always start with paper trading / sandbox, include strict risk limits, and validate against exchange rules.

## Features
- Multi-timeframe bars: 5m / 10m / 15m / 240m / 1D / 1W / 1M
- Indicators: SMA(30/120/200/864), RSI(14), Bollinger Bands(20,2), volume surge, Fibonacci levels
- News listener: keyword scoring with positive/negative lists
- Strategy: "Blender score" that blends trend + RSI + volume + news (+ orderbook hooks)
- Execution adapters:
  - 나무증권: `NamooAdapter` (HTTP bridge; see `namoo_bridge/`)
  - Upbit (spot): REST trading + account + price
  - Binance (spot): REST trading + account + price
  - KIS (optional): REST trading + price (positions/equity TODO)
- Storage: PostgreSQL/TimescaleDB via SQLAlchemy
- Reporting: order log CSV + FIFO PnL ledger

## Quick Start
### 1) Create venv and install
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e .
```

### 2) Start DB (optional but recommended)
```bash
docker compose up -d
python -m quantbot.cli.init_db
```

### 3) Configure secrets
Copy `.env.example` to `.env` and fill values.

### 4) Run (demo mode)
Runs with mock market data (so you can test the pipeline end-to-end):
```bash
python -m quantbot.main --mode demo
```

## Live usage examples

### Upbit
```bash
python -m quantbot.main --mode live --venue upbit \
  --symbols KRW-BTC \
  --entry-tf 15m \
  --notional 100000 \
  --news-feeds "https://.../rss"
```

### Binance (spot)
```bash
python -m quantbot.main --mode live --venue binance --symbols BTCUSDT --entry-tf 15m --notional 50
  --symbols BTCUSDT,ETHUSDT --entry-tf 15m --notional 100
```

### 나무증권
1) Windows에서 `namoo_bridge` 실행 (see `namoo_bridge/README.md`)
2) 봇 실행:
```bash
python -m quantbot.main --mode live --venue namoo --symbols 005930 --entry-tf 15m
```

## Reporting (수익률 표)

```bash
python -c "from quantbot.reporting.performance import compute_trade_ledger, performance_summary; import pandas as pd; \
ledger = compute_trade_ledger(); print(performance_summary(ledger)); ledger.to_csv('ledger.csv', index=False)"
```

## Project layout
- `quantbot/collectors/`   : market data collectors + DB store helpers
- `quantbot/bar_builder/`  : resampling / bar generation
- `quantbot/features/`     : indicators
- `quantbot/news/`         : 뉴스 수집/키워드 점수화
- `quantbot/strategy/`     : screener + signal blender
- `quantbot/risk/`         : risk controls
- `quantbot/execution/`    : executor + venue adapters
- `quantbot/storage/`      : DB models
- `quantbot/reporting/`    : CSV exports


## Remote/VPS deployment (개인 계정 API키로 원격 자동매매)

### 핵심 개념
- 봇은 **VPS/Linux 서버**에서 24/7로 실행 (Docker 권장)
- API 키/시크릿은 **코드에 절대 하드코딩하지 않고**, `.env` 또는 서버의 환경변수로만 주입
- 거래소/증권사 측에서 제공하는 **IP 고정(Allowlist)** 기능이 있으면 반드시 사용 (고정 IP가 있는 VPS 권장)
- 안전장치: `TRADING_ENABLED=true`일 때만 실제 주문 전송

### 1) VPS 준비
- 고정 IP(Elastic IP 등) 가능한 서버 권장
- 방화벽: DB(5432)는 외부 공개하지 말고, SSH(22)만 최소 공개 권장

### 2) 서버에서 실행 (Docker Compose)
```bash
# 서버에서
git clone <your repo>
cd <repo>
cp .env.example .env
# .env에 API키/계좌정보/심볼/노셔널 설정
docker compose up -d --build
docker compose logs -f bot
```

### 3) 실주문 켜기
`.env`에서 아래를 설정해야만 실제 주문이 나갑니다.
```env
TRADING_ENABLED=true
BOT_MODE=live
BOT_VENUE=upbit   # or binance / namoo / kis
BOT_SYMBOLS=KRW-BTC
BOT_NOTIONAL=100000
```

### 4) 나무증권(나무/QV OpenAPI) 원격 연결 방법
- 나무/QV OpenAPI는 Windows(32-bit DLL) 제약이 있어, `namoo_bridge/`를 **Windows PC**에서 실행하고
  봇은 해당 브릿지를 HTTP로 호출하는 형태를 권장합니다.
- 포트(기본 8700)를 인터넷에 그대로 노출하지 말고, **VPN(Tailscale 등)** 또는 **SSH 터널링**을 권장합니다.
