# QuantBot (Multi-TF + News + Orderbook) — 나무 / Upbit / Binance (+KIS optional)

> ⚠️ Educational/reference implementation. Running a live trading bot involves financial risk.
> Always start with paper trading / sandbox, include strict risk limits, and validate against exchange rules.

## Features
- Multi-timeframe bars: 5m / 10m / 15m / 240m / 1D / 1W / 1M
- Indicators: SMA(30/120/200/864), RSI(14), Bollinger Bands(20,2), volume surge, Fibonacci levels
- News listener: keyword scoring with positive/negative lists
- Strategy: "Blender score" that blends trend + RSI + volume + news (+ orderbook hooks)
- WebSocket executed-trade pressure stream for scalp (Upbit/Binance) with REST fallback when stale

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



## Paper trading

실제 주문 없이(모의 체결) **실시간 시세로** 전략/리스크/체결 로직을 검증합니다.

### Upbit (paper)
```bash
python -m quantbot.main --mode paper --venue upbit   --strategy blender   --symbols KRW-BTC   --entry-tf 15m --poll-sec 30   --paper-cash 1000000 --paper-fee-bps 10 --paper-slippage-bps 5
```

## Scalping mode (5초 루프 + 거래대금/호가잔량 필터 + 즉시 익절)

- 5초마다 신호/포지션을 체크합니다.
- **거래량(1분 캔들 거래대금)** 및 **호가잔량(상위 depth 기준 notional)** 로 유동성 필터를 걸 수 있습니다.
- 익절: 수수료(왕복) 반영 후 **0.39%** 수익이면 즉시 청산 (`--take-profit-net-pct 0.0039`)
  - 레버리지 10배 가정 시: 계좌 수익률 ~3.9% (가격 변동은 동일)

### Upbit (paper scalp)
```bash
python -m quantbot.main --mode paper --venue upbit   --strategy scalp   --symbols KRW-BTC   --entry-tf 1m --poll-sec 5   --take-profit-net-pct 0.0039 --leverage 10   --scalp-min-1m-trade-value 50000000   --scalp-min-orderbook-notional 100000000   --paper-cash 1000000 --paper-fee-bps 10 --paper-slippage-bps 5
```
### binance(paper scalp)
```bash
py -m quantbot.main --mode paper --venue binance   --strategy scalp   --symbols BTC   --entry-tf 1m --poll-sec 5   --take-profit-net-pct 0.0039 --leverage 10   --scalp-min-1m-trade-value 50000000   --scalp-min-orderbook-notional 100000000   --paper-cash 1000000 --paper-fee-bps 10 --paper-slippage-bps 5 
###선물
py -m quantbot.main --mode paper --venue binance_futures --strategy scalp `
  --symbols BTCUSDT --entry-tf 1m --poll-sec 5 `
  --take-profit-net-pct 0.0039 --leverage 10 `
  --scalp-min-1m-trade-value 50000000 --scalp-min-orderbook-notional 100000000 `
  --paper-cash 1000000 --paper-fee-bps 10 --paper-slippage-bps 5
  py -m quantbot.main --mode live --venue binance_futures --strategy scalp --symbols BTCUSDT --entry-tf 1m --poll-sec 5

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


## Scalp mode (RSI + executed-trade pressure)

Example (Upbit):
```bash
quantbot --mode paper --venue upbit --strategy scalp --symbols KRW-BTC \
  --entry-tf 1m --poll-sec 5 --notional 100000 \
  --take-profit-net-pct 0.0039 --stop-loss-pct 0.01 --trailing-stop-pct 0.005 \
  --scalp-pressure-window-sec 15 --scalp-trade-pressure-threshold 0.20 \
  --scalp-use-ws-trades 1 --scalp-ws-staleness-sec 30 \
  --scalp-max-spread-bps 8 --scalp-max-1m-range-pct 0.012 --scalp-max-1m-body-pct 0.010 \
  --scalp-news-spike-tv-mult 5 --scalp-news-spike-move-pct 0.007 --scalp-news-cooldown-sec 300
```

Notes:
- `--scalp-use-ws-trades 1` uses real-time trade stream to compute pressure; if no ticks arrive for `--scalp-ws-staleness-sec`, it falls back to REST.
- Spread/volatility/news-candle filters are optional; set them to 0 to disable.


## 4) 투자 전략 실행 json 
# v1 공격형
python -m quantbot.multi_runner multi_run_binance_demo_v1_aggressive.json

# v2 보수형
python -m quantbot.multi_runner multi_run_binance_demo_v2_conservative.json
