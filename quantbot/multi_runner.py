from __future__ import annotations

"""Launch multiple bots (separate processes) from a single config.

Why separate processes?
- asyncio + websockets + REST calls can contend in one event loop.
- Separate processes keep per-venue latency isolated and reduce order "밀림".

Config format (JSON):
{
  "python": "python",
  "bots": [
    {"mode":"paper","venue":"binance_futures","strategy":"scalp","symbols":["BTCUSDT"],"poll_sec":5,"intended_notional":100},
    {"mode":"paper","venue":"upbit","strategy":"scalp","symbols":["KRW-BTC"],"poll_sec":5,"intended_notional":100000}
  ]
}

Notes:
- All keys are optional; unknown keys are ignored.
- Each bot becomes: `python -m quantbot.main --...`.
"""

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _build_cmd(python: str, bot: Dict[str, Any]) -> List[str]:
    cmd = [python, "-m", "quantbot.main"]

    # Required-ish
    if bot.get("mode"):
        cmd += ["--mode", str(bot["mode"])]
    if bot.get("venue"):
        cmd += ["--venue", str(bot["venue"])]
    if bot.get("strategy"):
        cmd += ["--strategy", str(bot["strategy"])]

    if bot.get("preset"):
        cmd += ["--preset", str(bot["preset"])]

    # Symbols
    syms = bot.get("symbols")
    if isinstance(syms, list) and syms:
        cmd += ["--symbols", ",".join(map(str, syms))]

    # Common
    for k, flag in [
        ("poll_sec", "--poll-sec"),
        ("intended_notional", "--notional"),
        ("stop_loss_pct", "--stop-loss-pct"),
        ("trailing_stop_pct", "--trailing-stop-pct"),
        ("take_profit_net_pct", "--take-profit-net-pct"),
        ("leverage", "--leverage"),
        ("state_dir", "--state-dir"),
        ("paper_state_path", "--paper-state-path"),
        ("paper_initial_cash", "--paper-cash"),
        ("paper_fee_bps", "--paper-fee-bps"),
        ("paper_slippage_bps", "--paper-slippage-bps"),
        ("account_tag", "--account-tag"),
        ("global_risk_path", "--global-risk-path"),
        ("max_account_exposure_frac", "--max-account-exposure-frac"),
        ("max_total_exposure_frac", "--max-total-exposure-frac"),
        ("max_account_notional", "--max-account-notional"),
        ("max_total_notional", "--max-total-notional"),
    ]:
        if bot.get(k) is not None:
            cmd += [flag, str(bot[k])]

    # Scalping filters (subset)
    for k, flag in [
        ("scalp_min_1m_trade_value", "--scalp-min-1m-trade-value"),
        ("scalp_min_orderbook_notional", "--scalp-min-orderbook-notional"),
        ("scalp_imbalance_threshold", "--scalp-imbalance-threshold"),
        ("scalp_rsi_long_trigger", "--scalp-rsi-long-trigger"),
        ("scalp_rsi_short_min", "--scalp-rsi-short-min"),
        ("scalp_rsi_short_max", "--scalp-rsi-short-max"),
        ("scalp_use_rsi_cross", "--scalp-use-rsi-cross"),
        ("scalp_require_reversal_candle", "--scalp-require-reversal-candle"),
        ("scalp_min_vol_surge", "--scalp-min-vol-surge"),
        ("scalp_pressure_window_sec", "--scalp-pressure-window-sec"),
        ("scalp_trade_pressure_threshold", "--scalp-trade-pressure-threshold"),
        ("scalp_min_trade_pressure_notional", "--scalp-min-trade-pressure-notional"),
        ("scalp_ws_staleness_sec", "--scalp-ws-staleness-sec"),
        ("scalp_flow_window_sec", "--scalp-flow-window-sec"),
        ("scalp_min_flow_notional_rate", "--scalp-min-flow-notional-rate"),
        ("scalp_min_flow_accel", "--scalp-min-flow-accel"),
        ("scalp_large_trade_min_notional", "--scalp-large-trade-min-notional"),
        ("scalp_min_large_trade_share", "--scalp-min-large-trade-share"),
        ("scalp_min_trade_count", "--scalp-min-trade-count"),
        ("scalp_ob_delta_depth", "--scalp-ob-delta-depth"),
        ("scalp_min_ob_imb_delta", "--scalp-min-ob-imb-delta"),
        ("scalp_max_spread_bps", "--scalp-max-spread-bps"),
        ("scalp_max_1m_range_pct", "--scalp-max-1m-range-pct"),
        ("scalp_max_1m_body_pct", "--scalp-max-1m-body-pct"),
        ("scalp_news_spike_tv_mult", "--scalp-news-spike-tv-mult"),
        ("scalp_news_spike_move_pct", "--scalp-news-spike-move-pct"),
        ("scalp_news_cooldown_sec", "--scalp-news-cooldown-sec"),
        ("scalp_use_liquidation_stream", "--scalp-use-liquidation-stream"),
        ("scalp_liq_window_sec", "--scalp-liq-window-sec"),
        ("scalp_liq_bucket_bps", "--scalp-liq-bucket-bps"),
        ("entry_use_ioc", "--entry-use-ioc"),
        ("exit_use_ioc", "--exit-use-ioc"),
        ("ioc_price_pad_bps", "--ioc-price-pad-bps"),
        ("ioc_max_chase_bps", "--ioc-max-chase-bps"),
    ]:
        if bot.get(k) is not None:
            cmd += [flag, str(bot[k])]

    # booleans
    if bot.get("scalp_use_ws_trades") is not None:
        cmd += ["--scalp-use-ws-trades", "1" if _as_bool(bot.get("scalp_use_ws_trades")) else "0"]

    return cmd


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print("Usage: python -m quantbot.multi_runner <config.json>")
        return 2

    cfg = _load_json(argv[0])
    python = str(cfg.get("python") or sys.executable or "python")
    bots = cfg.get("bots") or []
    if not isinstance(bots, list) or not bots:
        print("Config must include a non-empty 'bots' list")
        return 2

    procs: List[subprocess.Popen] = []

    def _terminate_all():
        for p in procs:
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
        t0 = time.time()
        while time.time() - t0 < 5.0:
            if all((p.poll() is not None) for p in procs):
                return
            time.sleep(0.1)
        for p in procs:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass

    def _sig_handler(signum, frame):
        print(f"\nReceived signal {signum}; stopping...")
        _terminate_all()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    print(f"Launching {len(bots)} bot process(es)...")
    for i, bot in enumerate(bots, 1):
        if not isinstance(bot, dict):
            continue
        cmd = _build_cmd(python, bot)
        env = os.environ.copy()
        # per-bot extra env vars
        for k, v in (bot.get("env") or {}).items() if isinstance(bot.get("env"), dict) else []:
            env[str(k)] = str(v)
        print(f"[{i}] {' '.join(cmd)}")
        procs.append(subprocess.Popen(cmd, env=env))

    # Monitor
    try:
        while True:
            alive = 0
            for p in procs:
                if p.poll() is None:
                    alive += 1
            if alive == 0:
                return 0
            time.sleep(1.0)
    finally:
        _terminate_all()


if __name__ == "__main__":
    raise SystemExit(main())
