from __future__ import annotations

import argparse
import asyncio
from rich.console import Console
from rich.table import Table

from quantbot.config import get_settings
from quantbot.bar_builder.resampler import resample_ohlcv, RULE_MAP
from quantbot.features.indicators import add_indicators, inverse_alignment
from quantbot.news.keyword import KeywordScorer
from quantbot.strategy.blender import generate_signal, BlenderWeights
from quantbot.risk.risk_manager import RiskManager, PortfolioState
from quantbot.execution.executor import OrderExecutor
from quantbot.execution.adapters.demo_adapter import DemoAdapter
from quantbot.collectors.demo_market import generate_1m_series
from quantbot.collectors.store import upsert_candles, load_candles_df, insert_news
from quantbot.utils.time import utc_now
from quantbot.common.types import NewsItem

console = Console()
settings = get_settings()
DEFAULT_SYMBOLS = ["DEMOCOIN-KRW"]

def _print_signal(sig):
    t = Table(title="Signal")
    t.add_column("ts"); t.add_column("symbol"); t.add_column("side"); t.add_column("score")
    t.add_row(sig.ts.isoformat(), sig.symbol, sig.side, f"{sig.score:.2f}")
    console.print(t)
    console.print(sig.meta)

async def run_demo():
    venue = "demo"
    symbol = DEFAULT_SYMBOLS[0]

    # 1) Generate demo 1m candles and persist
    df_1m = generate_1m_series(symbol, minutes=60*24*30)
    upsert_candles(venue, symbol, "1m", df_1m)

    # 2) Resample to required TFs and persist
    for tf in ["5m","10m","15m","240m","1d","1w","1M"]:
        df_tf = resample_ohlcv(df_1m, RULE_MAP[tf])
        upsert_candles(venue, symbol, tf, df_tf)

    # 3) Load daily + 15m for analysis
    df_d = load_candles_df(venue, symbol, "1d", limit=2500)
    df_15 = load_candles_df(venue, symbol, "15m", limit=2500)

    df_d = add_indicators(df_d)
    df_15 = add_indicators(df_15)

    if not df_d.empty and not inverse_alignment(df_d.iloc[-1]):
        console.print("[yellow]Inverse alignment not met in demo data (OK).[/yellow]")

    # 4) News scoring (demo)
    scorer = KeywordScorer(
        positive=[k.strip() for k in settings.NEWS_POSITIVE.split(",") if k.strip()],
        negative=[k.strip() for k in settings.NEWS_NEGATIVE.split(",") if k.strip()],
    )
    demo_news_text = "공급계약 수주 공시"
    ns, hits = scorer.score(demo_news_text)
    insert_news(NewsItem(ts=utc_now(), source="demo", title=demo_news_text, score=ns, hits=hits))

    # 5) Signal
    sig = generate_signal(
        venue=venue,
        symbol=symbol,
        ts=utc_now(),
        df_daily=df_d,
        df_entry=df_15,
        news_score=ns,
        orderbook_score=0.0,
        w=BlenderWeights(),
    )
    _print_signal(sig)

    # 6) Risk + execute
    adapter = DemoAdapter()
    adapter.set_price(symbol, float(df_15["close"].iloc[-1]))
    executor = OrderExecutor(adapter, venue=venue, trading_enabled=True)
    rm = RiskManager()

    equity = await adapter.get_equity()
    positions = await adapter.get_positions()
    prices = {symbol: await adapter.get_last_price(symbol)}
    pf = PortfolioState(equity=equity, day_start_equity=equity, positions=positions, prices=prices)

    intended_notional = 100000.0
    ok, why = rm.approve(pf, sig, intended_notional=intended_notional)
    console.print(f"[cyan]Risk approve:[/cyan] {ok} {why}")

    if ok and sig.side == "BUY":
        qty = intended_notional / prices[symbol]
        res = await executor.execute_from_signal(sig, qty=qty, order_type="MARKET")
        console.print(f"[green]Executed[/green]: {res.ok} {res.reason} {res.meta}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["demo","live"], default="demo")
    parser.add_argument("--venue", choices=["demo","upbit","binance","namoo","kis"], default="demo")
    parser.add_argument("--symbols", default="", help="comma-separated symbols (e.g. KRW-BTC or BTCUSDT or 005930)")
    parser.add_argument("--entry-tf", default="15m", help="entry timeframe for RSI (default: 15m)")
    parser.add_argument("--poll-sec", type=int, default=30)
    parser.add_argument("--news-feeds", default="", help="comma-separated RSS feed URLs")
    parser.add_argument("--notional", type=float, default=100000.0)
    args = parser.parse_args()

    if args.mode == "demo":
        asyncio.run(run_demo())
    else:
        from quantbot.live import run_live, LiveConfig
        symbols = [s.strip() for s in (args.symbols or "").split(",") if s.strip()]
        if not symbols:
            console.print("[red]--symbols is required in live mode[/red]")
            return
        feeds = [s.strip() for s in (args.news_feeds or "").split(",") if s.strip()]
        cfg = LiveConfig(
            venue=args.venue,
            symbols=symbols,
            entry_tf=args.entry_tf,
            poll_sec=int(args.poll_sec),
            news_feeds=feeds,
            intended_notional=float(args.notional),
        )
        asyncio.run(run_live(cfg))

if __name__ == "__main__":
    main()
