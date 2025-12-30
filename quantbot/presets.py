from __future__ import annotations

"""Built-in parameter presets.

Goal
----
Give you sane scalping defaults per-venue / per-symbol without constantly
tuning dozens of CLI flags.

Usage
-----
CLI:
  python -m quantbot.main --mode paper --venue binance_futures --strategy scalp \
    --symbols BTCUSDT --preset binance_futures_btc_scalp

Multi-runner JSON:
  {"preset": "upbit_btc_scalp", "symbols": ["KRW-BTC"], ...}

You can also pass a JSON file path as --preset, e.g. --preset presets/my.json
The JSON should be a dict of LiveConfig field overrides.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional


# NOTE: These are *starting points*, not guarantees.
BUILTIN: Dict[str, Dict[str, Any]] = {
    # Binance Futures: high-liquidity BTC scalp, 1m features, 5s loop.
    "binance_futures_btc_scalp": {
        "poll_sec": 5,
        "entry_tf": "1m",
        "leverage": 10.0,
        "take_profit_net_pct": 0.0039,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": True,
        "scalp_flow_window_sec": 5,
        "scalp_pressure_window_sec": 15,
        "scalp_rsi_long_trigger": 40.0,
        "scalp_rsi_short_min": 65.0,
        "scalp_rsi_short_max": 70.0,
        "scalp_imbalance_threshold": 0.15,
        "scalp_trade_pressure_threshold": 0.20,
        "scalp_max_spread_bps": 8.0,
        "entry_use_ioc": True,
        "exit_use_ioc": True,
        "ioc_price_pad_bps": 2.0,
        "ioc_max_chase_bps": 12.0,
        # global risk defaults (safe-ish)
        "max_account_exposure_frac": 0.35,
        "max_total_exposure_frac": 0.60,
    },

    # Upbit spot: no short; lower aggressiveness; KRW quote.
    "upbit_btc_scalp": {
        "poll_sec": 5,
        "entry_tf": "1m",
        "leverage": 1.0,
        "take_profit_net_pct": 0.0039,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": False,
        "entry_use_ioc": False,  # upbit IOC is not supported in this bot
        "exit_use_ioc": False,
        "scalp_max_spread_bps": 12.0,
        "scalp_imbalance_threshold": 0.12,
        "max_account_exposure_frac": 0.25,
        "max_total_exposure_frac": 0.60,
    },

    # Korea stocks via Namoo/Kiwoom: no short; slower market; wider spreads.
    "kr_stock_scalp": {
        "poll_sec": 5,
        "entry_tf": "1m",
        "leverage": 1.0,
        "take_profit_net_pct": 0.0039,
        "scalp_use_ws_trades": False,
        "scalp_use_liquidation_stream": False,
        "entry_use_ioc": False,
        "exit_use_ioc": False,
        "scalp_max_spread_bps": 25.0,
        "scalp_imbalance_threshold": 0.18,
        "max_account_exposure_frac": 0.20,
        "max_total_exposure_frac": 0.60,
    },
}


def load_preset(name_or_path: str) -> Dict[str, Any]:
    """Return a dict of LiveConfig overrides.

    - If name_or_path matches a BUILTIN key, returns it.
    - If it is a file path to JSON, reads and returns it.
    """
    if not name_or_path:
        return {}
    if name_or_path in BUILTIN:
        return dict(BUILTIN[name_or_path])

    p = Path(name_or_path)
    if p.exists() and p.is_file() and p.suffix.lower() in {".json"}:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def list_presets() -> list[str]:
    return sorted(BUILTIN.keys())
