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
from quantbot.presets import load_preset, list_presets

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
    parser.add_argument("--mode", choices=["demo", "live", "paper"], default="demo")
    parser.add_argument("--venue", choices=["demo", "upbit", "binance", "binance_futures", "namoo", "namoo_stock", "kis", "kiwoom"], default="demo")
    parser.add_argument("--strategy", choices=["blender", "scalp"], default="blender")

    # Presets
    parser.add_argument("--preset", default="", help="built-in preset name or JSON file path (use --list-presets)")
    parser.add_argument("--list-presets", action="store_true", help="print available built-in presets and exit")

    parser.add_argument("--symbols", default="", help="comma-separated symbols (e.g. KRW-BTC or BTCUSDT or 005930)")
    parser.add_argument("--entry-tf", default=None, help="entry timeframe (default: blender=15m, scalp=1m)")
    parser.add_argument("--poll-sec", type=int, default=None, help="loop interval seconds (default: blender=30, scalp=5)")
    parser.add_argument("--news-feeds", default="", help="comma-separated RSS feed URLs")
    parser.add_argument("--notional", type=float, default=None, help="intended notional in quote currency")
    parser.add_argument("--state-dir", default="state/bots", help="bot state dir for dashboard (default=state/bots)")

    # Multi-bot risk / grouping
    parser.add_argument("--account-tag", default=None, help="group bots that share the same account (for global exposure caps)")
    parser.add_argument("--global-risk-path", default=None, help="shared global risk JSON path (default=state/global_risk.json)")
    parser.add_argument("--max-account-exposure-frac", type=float, default=None, help="cap account exposure (abs_notional/equity). <1 enables")
    parser.add_argument("--max-total-exposure-frac", type=float, default=None, help="cap total exposure across all accounts. <1 enables")
    parser.add_argument("--max-account-notional", type=float, default=None, help="cap abs notional per account (quote currency). >0 enables")
    parser.add_argument("--max-total-notional", type=float, default=None, help="cap abs notional across all accounts. >0 enables")

    # exits
    parser.add_argument("--stop-loss-pct", type=float, default=None, help="e.g. 0.01 = 1%% stop-loss")
    parser.add_argument("--trailing-stop-pct", type=float, default=None, help="e.g. 0.005 = 0.5%% trailing-stop")
    parser.add_argument("--take-profit-net-pct", type=float, default=None, help="net take-profit target (after fees). scalp default=0.0039")
    parser.add_argument("--leverage", type=float, default=None, help="for futures mental model (e.g. 10 => 10x)")

    # paper trading
    parser.add_argument("--paper-cash", type=float, default=None)
    parser.add_argument("--paper-fee-bps", type=int, default=None)
    parser.add_argument("--paper-slippage-bps", type=int, default=None)
    parser.add_argument("--paper-state-path", default="state/paper_state.json")

    # scalping filters
    parser.add_argument("--scalp-min-1m-trade-value", type=float, default=None, help="min 1m candle trade value (volume*close) in quote currency")
    parser.add_argument("--scalp-min-orderbook-notional", type=float, default=None, help="min orderbook depth notional (bid+ask) in quote currency")
    parser.add_argument("--scalp-imbalance-threshold", type=float, default=None, help="orderbook imbalance threshold (0~1)")

    # scalping mean-reversion entry params
    parser.add_argument("--scalp-rsi-long-trigger", type=float, default=None, help="RSI trigger for long (default=40). use_rsi_cross=1이면 40 돌파(반등) 기준")
    parser.add_argument("--scalp-rsi-short-min", type=float, default=None, help="RSI lower bound for short zone (default=65)")
    parser.add_argument("--scalp-rsi-short-max", type=float, default=None, help="RSI upper bound for short zone (default=70)")
    parser.add_argument("--scalp-use-rsi-cross", type=int, default=None, help="1=use RSI cross-back (recommended), 0=use zone only")
    parser.add_argument("--scalp-require-reversal-candle", type=int, default=None, help="1=require candle direction (long: close>=open, short: close<=open)")
    parser.add_argument("--scalp-min-vol-surge", type=float, default=None, help="VOL_SURGE >= this (volume / VOL_SMA_5). 0이면 미사용")

    # executed-trade pressure (recent ticks)
    parser.add_argument("--scalp-pressure-window-sec", type=int, default=None, help="recent trade window seconds (default=15)")
    parser.add_argument("--scalp-trade-pressure-threshold", type=float, default=None, help="abs(trade_pressure) threshold in [-1,1] (default=0.20)")
    parser.add_argument("--scalp-min-trade-pressure-notional", type=float, default=None, help="min total notional within pressure window (default=0)")

    # executed-trade pressure via WebSocket (fallback to REST)
    parser.add_argument("--scalp-use-ws-trades", type=int, default=None, help="1=use WebSocket trades for pressure (recommended), 0=REST only")
    parser.add_argument("--scalp-ws-staleness-sec", type=int, default=None, help="if WS data older than this seconds, fallback to REST (default=30)")

    # flow refinement
    parser.add_argument("--scalp-flow-window-sec", type=int, default=None, help="trade flow window seconds (default=10)")
    parser.add_argument("--scalp-min-flow-notional-rate", type=float, default=None, help="min notional/sec (default=0)")
    parser.add_argument("--scalp-min-flow-accel", type=float, default=None, help="min accel notional/sec^2 (default=0)")
    parser.add_argument("--scalp-large-trade-min-notional", type=float, default=None, help="large trade threshold notional (default=0)")
    parser.add_argument("--scalp-min-large-trade-share", type=float, default=None, help="min share of large trades (default=0)")
    parser.add_argument("--scalp-min-trade-count", type=int, default=None, help="min trade count in window (default=0)")

    # orderbook delta refinement
    parser.add_argument("--scalp-ob-delta-depth", type=int, default=None, help="orderbook delta depth (default=10)")
    parser.add_argument("--scalp-min-ob-imb-delta", type=float, default=None, help="min imbalance delta threshold (default=0)")

    # liquidation clustering (binance_futures only)
    parser.add_argument("--scalp-use-liquidation-stream", type=int, default=None, help="1=use liquidation stream (default=1)")
    parser.add_argument("--scalp-liq-window-sec", type=float, default=None, help="liq window seconds (default=30)")
    parser.add_argument("--scalp-liq-bucket-bps", type=float, default=None, help="liq price bucket bps (default=10)")

    # execution aggressiveness
    parser.add_argument("--entry-use-ioc", type=int, default=None, help="1=entry IOC limit->market (default=1)")
    parser.add_argument("--exit-use-ioc", type=int, default=None, help="1=exit IOC limit->market (default=1)")
    parser.add_argument("--ioc-price-pad-bps", type=float, default=None, help="IOC limit pad bps (default=1.0)")
    parser.add_argument("--ioc-max-chase-bps", type=float, default=None, help="IOC max chase bps (default=3.0)")

    # additional scalp filters
    parser.add_argument("--scalp-max-spread-bps", type=float, default=None, help="skip entry if spread > this bps (default=8)")
    parser.add_argument("--scalp-max-1m-range-pct", type=float, default=None,help="skip entry if last 1m range (high-low)/close > this (e.g. 0.012 = 1.2 percent)")
    parser.add_argument("--scalp-max-1m-body-pct", type=float, default=None, help="skip entry if last 1m body abs(close-open)/open > this")

    # news candle lockout
    parser.add_argument("--scalp-news-spike-tv-mult", type=float, default=None, help="trade-value spike multiplier vs SMA20 (default=5)")
    parser.add_argument("--scalp-news-spike-move-pct", type=float, default=None, help="price move threshold for spike (default=0.007=0.7 percent)")
    parser.add_argument("--scalp-news-cooldown-sec", type=int, default=None, help="cooldown seconds after spike (default=300)")

    args = parser.parse_args()

    if args.list_presets:
        for name in list_presets():
            print(name)
        return

    if args.mode == "demo":
        asyncio.run(run_demo())
        return

    from quantbot.live import run_live, LiveConfig

    syms = [s.strip() for s in (args.symbols or "").split(",") if s.strip()]
    if not syms:
        raise SystemExit("Please provide --symbols for live/paper mode")

    # Apply preset (if any), then override with explicit CLI flags.
    preset = load_preset(args.preset or "")
    preset_has_entry_tf = "entry_tf" in preset
    preset_has_poll_sec = "poll_sec" in preset

    cfg = LiveConfig(
        venue=args.venue,
        symbols=syms,
        mode=args.mode,
        strategy=args.strategy,
        state_dir=str(args.state_dir),
    )

    for k, v in preset.items():
        if hasattr(cfg, k):
            try:
                setattr(cfg, k, v)
            except Exception:
                pass

    # Strategy defaults (only if neither preset nor CLI provides them)
    if args.strategy == "blender":
        if args.entry_tf is None and not preset_has_entry_tf:
            cfg.entry_tf = "15m"
        if args.poll_sec is None and not preset_has_poll_sec:
            cfg.poll_sec = 30
    else:
        if args.entry_tf is None and not preset_has_entry_tf:
            cfg.entry_tf = "1m"
        if args.poll_sec is None and not preset_has_poll_sec:
            cfg.poll_sec = 5

    # Core overrides
    if args.entry_tf is not None:
        cfg.entry_tf = str(args.entry_tf)
    if args.poll_sec is not None:
        cfg.poll_sec = int(args.poll_sec)
    if args.news_feeds:
        cfg.news_feeds = [s.strip() for s in (args.news_feeds or "").split(",") if s.strip()]
    if args.notional is not None:
        cfg.intended_notional = float(args.notional)

    # Multi-bot risk / grouping
    if args.account_tag is not None:
        cfg.account_tag = str(args.account_tag)
    if args.global_risk_path is not None:
        cfg.global_risk_path = str(args.global_risk_path)
    if args.max_account_exposure_frac is not None:
        cfg.max_account_exposure_frac = float(args.max_account_exposure_frac)
    if args.max_total_exposure_frac is not None:
        cfg.max_total_exposure_frac = float(args.max_total_exposure_frac)
    if args.max_account_notional is not None:
        cfg.max_account_notional = float(args.max_account_notional)
    if args.max_total_notional is not None:
        cfg.max_total_notional = float(args.max_total_notional)

    # Exits / sizing
    if args.stop_loss_pct is not None:
        cfg.stop_loss_pct = float(args.stop_loss_pct)
    if args.trailing_stop_pct is not None:
        cfg.trailing_stop_pct = float(args.trailing_stop_pct)
    if args.take_profit_net_pct is not None:
        cfg.take_profit_net_pct = float(args.take_profit_net_pct)
    else:
        if args.strategy != "scalp":
            cfg.take_profit_net_pct = 0.0
    if args.leverage is not None:
        cfg.leverage = float(args.leverage)

    # Paper
    if args.paper_cash is not None:
        cfg.paper_initial_cash = float(args.paper_cash)
    if args.paper_fee_bps is not None:
        cfg.paper_fee_bps = int(args.paper_fee_bps)
    if args.paper_slippage_bps is not None:
        cfg.paper_slippage_bps = int(args.paper_slippage_bps)
    if args.paper_state_path:
        cfg.paper_state_path = str(args.paper_state_path)

    # Scalping params overrides
    for arg_name, field_name in [
        ("scalp_min_1m_trade_value", "scalp_min_1m_trade_value"),
        ("scalp_min_orderbook_notional", "scalp_min_orderbook_notional"),
        ("scalp_imbalance_threshold", "scalp_imbalance_threshold"),
        ("scalp_rsi_long_trigger", "scalp_rsi_long_trigger"),
        ("scalp_rsi_short_min", "scalp_rsi_short_min"),
        ("scalp_rsi_short_max", "scalp_rsi_short_max"),
        ("scalp_min_vol_surge", "scalp_min_vol_surge"),
        ("scalp_pressure_window_sec", "scalp_pressure_window_sec"),
        ("scalp_trade_pressure_threshold", "scalp_trade_pressure_threshold"),
        ("scalp_min_trade_pressure_notional", "scalp_min_trade_pressure_notional"),
        ("scalp_ws_staleness_sec", "scalp_ws_staleness_sec"),
        ("scalp_flow_window_sec", "scalp_flow_window_sec"),
        ("scalp_min_flow_notional_rate", "scalp_min_flow_notional_rate"),
        ("scalp_min_flow_accel", "scalp_min_flow_accel"),
        ("scalp_large_trade_min_notional", "scalp_large_trade_min_notional"),
        ("scalp_min_large_trade_share", "scalp_min_large_trade_share"),
        ("scalp_min_trade_count", "scalp_min_trade_count"),
        ("scalp_ob_delta_depth", "scalp_ob_delta_depth"),
        ("scalp_min_ob_imb_delta", "scalp_min_ob_imb_delta"),
        ("scalp_liq_window_sec", "scalp_liq_window_sec"),
        ("scalp_liq_bucket_bps", "scalp_liq_bucket_bps"),
        ("ioc_price_pad_bps", "ioc_price_pad_bps"),
        ("ioc_max_chase_bps", "ioc_max_chase_bps"),
        ("scalp_max_spread_bps", "scalp_max_spread_bps"),
        ("scalp_max_1m_range_pct", "scalp_max_1m_range_pct"),
        ("scalp_max_1m_body_pct", "scalp_max_1m_body_pct"),
        ("scalp_news_spike_tv_mult", "scalp_news_spike_tv_mult"),
        ("scalp_news_spike_move_pct", "scalp_news_spike_move_pct"),
        ("scalp_news_cooldown_sec", "scalp_news_cooldown_sec"),
    ]:
        v = getattr(args, arg_name, None)
        if v is not None:
            try:
                setattr(cfg, field_name, v)
            except Exception:
                pass

    # bool-ish flags (0/1)
    if args.scalp_use_rsi_cross is not None:
        cfg.scalp_use_rsi_cross = bool(args.scalp_use_rsi_cross)
    if args.scalp_require_reversal_candle is not None:
        cfg.scalp_require_reversal_candle = bool(args.scalp_require_reversal_candle)
    if args.scalp_use_ws_trades is not None:
        cfg.scalp_use_ws_trades = bool(args.scalp_use_ws_trades)
    if args.scalp_use_liquidation_stream is not None:
        cfg.scalp_use_liquidation_stream = bool(args.scalp_use_liquidation_stream)
    if args.entry_use_ioc is not None:
        cfg.entry_use_ioc = bool(args.entry_use_ioc)
    if args.exit_use_ioc is not None:
        cfg.exit_use_ioc = bool(args.exit_use_ioc)
    asyncio.run(run_live(cfg))


if __name__ == "__main__":
    main()