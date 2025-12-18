from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from rich.console import Console

from quantbot.config import get_settings
from quantbot.news.keyword import KeywordScorer
from quantbot.news.rss_listener import RSSNewsListener
from quantbot.collectors.store import upsert_candles, load_candles_df, insert_news
from quantbot.bar_builder.resampler import resample_ohlcv, RULE_MAP
from quantbot.features.indicators import add_indicators
from quantbot.features.orderbook import orderbook_imbalance_score
from quantbot.strategy.blender import generate_signal, BlenderWeights
from quantbot.risk.risk_manager import RiskManager, PortfolioState
from quantbot.execution.executor import OrderExecutor
from quantbot.execution.adapters.upbit_adapter import UpbitAdapter
from quantbot.execution.adapters.binance_adapter import BinanceAdapter
from quantbot.execution.adapters.binance_futures_adapter import BinanceFuturesAdapter
from quantbot.execution.adapters.namoo_adapter import NamooAdapter
from quantbot.execution.adapters.kis_adapter import KISAdapter
from quantbot.utils.time import utc_now
from quantbot.collectors.upbit_rest import fetch_upbit_candles, fetch_upbit_orderbook
from quantbot.collectors.binance_rest import fetch_binance_klines, fetch_binance_orderbook


console = Console()
settings = get_settings()


def _parse_symbol_base_quote(venue: str, symbol: str) -> tuple[str, str]:
    """Return (base, quote). Best-effort parser for spot symbols."""
    if venue == "upbit":
        # e.g. KRW-BTC
        parts = symbol.split("-")
        if len(parts) == 2:
            return parts[1], parts[0]
        return symbol, "KRW"

    if venue == "binance":
        # e.g. BTCUSDT, ETHBTC, XRPUSDT
        # Try common quote assets (longest first).
        quotes = ["USDT", "USDC", "BUSD", "FDUSD", "TUSD", "BTC", "ETH", "BNB", "TRY", "EUR", "GBP", "BRL", "AUD", "KRW", "JPY"]
        for q in sorted(quotes, key=len, reverse=True):
            if symbol.endswith(q) and len(symbol) > len(q):
                return symbol[:-len(q)], q
        # fallback
        return symbol, ""

    # stocks / others
    return symbol, ""


def _compute_equity_from_positions(venue: str, positions: dict[str, float], prices_by_symbol: dict[str, float], symbols: list[str]) -> float:
    """Approx equity in quote currency for tracked symbols only (conservative)."""
    if venue not in {"upbit", "binance"}:
        # For KIS/namoo adapters equity methods may be TODO; keep existing value.
        return 0.0

    equity = 0.0
    # Assume a single quote currency across tracked symbols for equity view
    base0, quote0 = _parse_symbol_base_quote(venue, symbols[0])
    if quote0:
        equity += float(positions.get(quote0, 0.0))

    for sym in symbols:
        base, quote = _parse_symbol_base_quote(venue, sym)
        px = prices_by_symbol.get(sym)
        if px is None or not quote:
            continue
        qty = float(positions.get(base, 0.0))
        # value in quote currency
        if quote == quote0:
            equity += qty * float(px)
    return float(equity)


@dataclass
class LiveConfig:
    venue: str
    symbols: list[str]
    entry_tf: str = "15m"  # RSI 계산용
    poll_sec: int = 30
    news_feeds: list[str] | None = None
    intended_notional: float = 100000.0


async def _ensure_history(venue: str, symbol: str) -> None:
    """Fetch enough history to compute SMA_864 on daily bars.

    For crypto venues we pull daily candles from the exchange.
    For namoo(한국주식)는 데이터 소스가 계정/버전에 따라 달라서 현재는
    별도 데이터 소스(KIS/공공데이터/CSV)를 사용하도록 권장합니다.
    """
    if venue == "upbit":
        df_d = await fetch_upbit_candles(symbol, "1d", total=1200)
        upsert_candles(venue, symbol, "1d", df_d)
        df_entry = await fetch_upbit_candles(symbol, "1m", total=60 * 24 * 30)
        upsert_candles(venue, symbol, "1m", df_entry)
        for tf in ["5m", "10m", "15m", "240m", "1w", "1M"]:
            upsert_candles(venue, symbol, tf, resample_ohlcv(df_entry, RULE_MAP[tf]))
    elif venue == "binance":
        # Binance: pull 1m then resample, and pull daily separately (cheaper)
        df_d = await fetch_binance_klines(symbol, "1d", total=1200)
        upsert_candles(venue, symbol, "1d", df_d)
        df_1m = await fetch_binance_klines(symbol, "1m", total=60 * 24 * 30)
        upsert_candles(venue, symbol, "1m", df_1m)
        for tf in ["5m", "10m", "15m", "240m", "1w", "1M"]:
            upsert_candles(venue, symbol, tf, resample_ohlcv(df_1m, RULE_MAP[tf]))
    else:
        # namoo/kis: left to user depending on data source
        return


async def _orderbook_score(venue: str, symbol: str) -> float:
    try:
        if venue == "upbit":
            ob = await fetch_upbit_orderbook(symbol)
        elif venue == "binance":
            ob = await fetch_binance_orderbook(symbol)
        else:
            return 0.0
        return orderbook_imbalance_score(ob)
    except Exception:
        return 0.0

def _make_adapter(venue: str):
    if venue == "upbit":
        if not settings.UPBIT_ACCESS_KEY or not settings.UPBIT_SECRET_KEY:
            raise RuntimeError("Set UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY in .env")
        return UpbitAdapter(settings.UPBIT_ACCESS_KEY, settings.UPBIT_SECRET_KEY)

    if venue == "binance":
        if not settings.BINANCE_API_KEY or not settings.BINANCE_API_SECRET:
            raise RuntimeError("Set BINANCE_API_KEY/BINANCE_API_SECRET in .env")

        base_url = settings.BINANCE_BASE_URL  # config.py에 추가 필요

        if settings.BINANCE_FUTURES:
            return BinanceFuturesAdapter(
                api_key=settings.BINANCE_API_KEY,
                api_secret=settings.BINANCE_API_SECRET,
                base_url=base_url,
            )

        return BinanceAdapter(
            api_key=settings.BINANCE_API_KEY,
            api_secret=settings.BINANCE_API_SECRET,
            base_url=base_url,
        )

    if venue == "namoo":
        return NamooAdapter(settings.NAMOO_BRIDGE_URL)

    if venue == "kis":
        if not (settings.KIS_APP_KEY and settings.KIS_APP_SECRET and settings.KIS_ACCOUNT_NO and settings.KIS_PRODUCT_CODE):
            raise RuntimeError("Set KIS_* in .env")
        return KISAdapter(settings.KIS_APP_KEY, settings.KIS_APP_SECRET, settings.KIS_ACCOUNT_NO, settings.KIS_PRODUCT_CODE, settings.KIS_BASE_URL)

    raise ValueError(f"Unsupported venue: {venue}")


async def run_live(cfg: LiveConfig):
    adapter = _make_adapter(cfg.venue)
    executor = OrderExecutor(adapter, venue=cfg.venue, trading_enabled=settings.TRADING_ENABLED)
    rm = RiskManager()

    scorer = KeywordScorer(
        positive=[k.strip() for k in settings.NEWS_POSITIVE.split(",") if k.strip()],
        negative=[k.strip() for k in settings.NEWS_NEGATIVE.split(",") if k.strip()],
    )

    listener = RSSNewsListener(cfg.news_feeds or [], scorer=scorer, poll_sec=cfg.poll_sec)

    # Load/seed history
    for s in cfg.symbols:
        await _ensure_history(cfg.venue, s)

    # Simple polling loop
    while True:
        # 1) News update
        news_score_by_symbol: dict[str, float] = {s: 0.0 for s in cfg.symbols}
        for n in listener.poll_once():
            insert_news(n)
            # naive matching: if symbol substring in title, apply score
            for s in cfg.symbols:
                if s in n.title:
                    news_score_by_symbol[s] += n.score

        # 2) For each symbol: compute signal
        for symbol in cfg.symbols:
            df_d = load_candles_df(cfg.venue, symbol, "1d", limit=2000)
            df_entry = load_candles_df(cfg.venue, symbol, cfg.entry_tf, limit=5000)
            # optional context timeframes
            ctx = {
                "5m": load_candles_df(cfg.venue, symbol, "5m", limit=2000),
                "10m": load_candles_df(cfg.venue, symbol, "10m", limit=2000),
                "240m": load_candles_df(cfg.venue, symbol, "240m", limit=2000),
                "1w": load_candles_df(cfg.venue, symbol, "1w", limit=2000),
                "1M": load_candles_df(cfg.venue, symbol, "1M", limit=2000),
            }
            if df_d.empty or df_entry.empty:
                continue

            df_d = add_indicators(df_d)
            df_entry = add_indicators(df_entry)
            for k, v in list(ctx.items()):
                ctx[k] = add_indicators(v) if v is not None and not v.empty else v

            ob_score = await _orderbook_score(cfg.venue, symbol)
            ns = news_score_by_symbol.get(symbol, 0.0)

            sig = generate_signal(
                venue=cfg.venue,
                symbol=symbol,
                ts=utc_now(),
                df_daily=df_d,
                df_entry=df_entry,
                news_score=ns,
                orderbook_score=ob_score,
                w=BlenderWeights(),
                tf_context=ctx,
            )

            console.print(f"[{cfg.venue}] {symbol} => {sig.side} score={sig.score:.2f} news={ns:.2f} ob={ob_score:.2f}")

            # 3) Risk check + execute
            try:
                positions = await adapter.get_positions()
                # Fetch prices for all tracked symbols once per tick for consistent risk checks
                prices: dict[str, float] = {}
                for s in cfg.symbols:
                    try:
                        prices[s] = await adapter.get_last_price(s)
                    except Exception:
                        continue
                last_price = prices.get(symbol) or await adapter.get_last_price(symbol)

                # Approx equity (quote currency) using tracked symbols only (conservative)
                equity = await adapter.get_equity()
                approx = _compute_equity_from_positions(cfg.venue, positions, prices, cfg.symbols)
                if approx > 0:
                    equity = approx

                if not hasattr(run_live, "_day_start_equity"):
                    run_live._day_start_equity = equity  # type: ignore[attr-defined]

                pf = PortfolioState(
                    equity=equity,
                    day_start_equity=getattr(run_live, "_day_start_equity"),
                    positions=positions,
                    prices={symbol: float(last_price)},
                )
                # api demo 연계 됐는지 추 후 확인할 로그
                # if cfg.venue == "binance" and getattr(settings, "BINANCE_FUTURES", False):
                #     await adapter.set_leverage("BTCUSDT", 2)
                #     px = await adapter.get_last_price("BTCUSDT")
                #     qty = 0.001  # 최소수량이 더 크면 0.002 같은 값으로
                #     sig = Signal(ts=utc_now(), venue="binance", symbol="BTCUSDT", side="BUY", score=999)
                #     res = await executor.execute_from_signal(sig, qty=qty, order_type="MARKET")
                #     console.print(res)
                # # --- end smoke test ---
                ok, why = rm.approve(pf, sig, intended_notional=cfg.intended_notional)
                if not ok:
                    continue

                if sig.side in {"BUY", "SELL"}:
                    # Spot-safe sizing:
                    #  - BUY: use intended notional
                    #  - SELL: sell existing base position (no shorting)
                    base, quote = _parse_symbol_base_quote(cfg.venue, symbol)
                    if sig.side == "BUY":
                        qty = float(cfg.intended_notional) / float(last_price)
                        meta = {}
                        # Venue-specific: allow "quote notional" for market-buy when supported
                        if cfg.venue == "upbit":
                            meta["quote_amount"] = float(cfg.intended_notional)
                        if cfg.venue == "binance":
                            meta["quoteOrderQty"] = float(cfg.intended_notional)
                    else:
                        pos_qty = float(positions.get(base, 0.0)) if base else float(positions.get(symbol, 0.0))
                        if pos_qty <= 0:
                            continue
                        qty = pos_qty
                        meta = {}

                    await executor.execute_from_signal(sig, qty=qty, order_type="MARKET", meta=meta)
            except Exception as e:
                console.print(f"[red]live error[/red] {symbol}: {e}")

        await asyncio.sleep(cfg.poll_sec)
