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

    # Binance Futures: BTC scalp (relaxed entry) - more frequent trades for testing / tuning.
    # - Lower TP/OB thresholds
    # - RSI cross & reversal candle are optional (disabled here)
    "binance_futures_btc_scalp_relaxed": {
        "poll_sec": 5,
        "entry_tf": "1m",
        "leverage": 10.0,
        "take_profit_net_pct": 0.0030,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": True,
        "scalp_flow_window_sec": 5,
        "scalp_pressure_window_sec": 15,
        "scalp_rsi_long_trigger": 45.0,
        "scalp_rsi_short_min": 60.0,
        "scalp_rsi_short_max": 72.0,
        "scalp_use_rsi_cross": False,
        "scalp_require_reversal_candle": False,
        "scalp_imbalance_threshold": 0.10,
        "scalp_trade_pressure_threshold": 0.12,
        "scalp_max_spread_bps": 10.0,
        "entry_use_ioc": True,
        "exit_use_ioc": True,
        "ioc_price_pad_bps": 2.0,
        "ioc_max_chase_bps": 15.0,
        "max_account_exposure_frac": 0.25,
        "max_total_exposure_frac": 0.50,
    },

    # --- Trade-frequency tuned presets (TARGET: ~5 / ~10 / ~20 entries per day) ---
    # NOTE: These are starting points; realized frequency depends on regime/volatility and your symbol list.
    # You can run multiple symbols to hit the target range more easily.

    # BTC (tight spread, high liquidity)
    "binance_futures_btc_scalp_t5": {
        "poll_sec": 5,
        "entry_tf": "1m",
        "leverage": 10.0,
        "take_profit_net_pct": 0.0036,
        "stop_loss_pct": 0.010,
        "trailing_stop_pct": 0.005,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": True,
        "scalp_pressure_window_sec": 15,
        "scalp_flow_window_sec": 5,
        "scalp_use_rsi_cross": True,
        "scalp_require_reversal_candle": True,
        "scalp_rsi_long_trigger": 40.0,
        "scalp_rsi_short_min": 65.0,
        "scalp_rsi_short_max": 70.0,
        "scalp_trade_pressure_threshold": 0.16,
        "scalp_imbalance_threshold": 0.12,
        "scalp_max_spread_bps": 8.0,
        "scalp_max_1m_range_pct": 0.011,
        "scalp_max_1m_body_pct": 0.009,
        "scalp_news_cooldown_sec": 300,
        "max_account_exposure_frac": 0.25,
        "max_total_exposure_frac": 0.45,
    },
    "binance_futures_btc_scalp_t10": {
        "poll_sec": 5,
        "entry_tf": "1m",
        "leverage": 10.0,
        "take_profit_net_pct": 0.0032,
        "stop_loss_pct": 0.010,
        "trailing_stop_pct": 0.005,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": True,
        "scalp_pressure_window_sec": 15,
        "scalp_flow_window_sec": 5,
        "scalp_use_rsi_cross": False,
        "scalp_require_reversal_candle": False,
        "scalp_rsi_long_trigger": 45.0,
        "scalp_rsi_short_min": 60.0,
        "scalp_rsi_short_max": 72.0,
        "scalp_trade_pressure_threshold": 0.13,
        "scalp_imbalance_threshold": 0.10,
        "scalp_max_spread_bps": 10.0,
        "scalp_max_1m_range_pct": 0.013,
        "scalp_max_1m_body_pct": 0.010,
        "scalp_news_cooldown_sec": 240,
        "max_account_exposure_frac": 0.25,
        "max_total_exposure_frac": 0.50,
    },
    "binance_futures_btc_scalp_t20": {
        "poll_sec": 3,
        "entry_tf": "1m",
        "leverage": 10.0,
        "take_profit_net_pct": 0.0026,
        "stop_loss_pct": 0.011,
        "trailing_stop_pct": 0.006,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": True,
        "scalp_pressure_window_sec": 12,
        "scalp_flow_window_sec": 5,
        "scalp_use_rsi_cross": False,
        "scalp_require_reversal_candle": False,
        "scalp_trade_pressure_threshold": 0.10,
        "scalp_imbalance_threshold": 0.07,
        "scalp_max_spread_bps": 12.0,
        "scalp_max_1m_range_pct": 0.016,
        "scalp_max_1m_body_pct": 0.013,
        "scalp_news_cooldown_sec": 180,
        "max_account_exposure_frac": 0.22,
        "max_total_exposure_frac": 0.45,
    },

    # ETH (slightly wider spread + more 1m volatility)
    "binance_futures_eth_scalp_t5": {
        "poll_sec": 5,
        "entry_tf": "1m",
        "leverage": 10.0,
        "order_sizing_mode": "equity_pct",
        "trade_equity_frac": 0.20,
        "min_notional_policy": "auto",
        "min_notional_buffer": 1.02,
        "take_profit_net_pct": 0.0038,
        "stop_loss_pct": 0.011,
        "trailing_stop_pct": 0.006,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": True,
        "scalp_use_rsi_cross": True,
        "scalp_require_reversal_candle": True,
        "scalp_trade_pressure_threshold": 0.15,
        "scalp_imbalance_threshold": 0.12,
        "scalp_max_spread_bps": 10.0,
        "scalp_max_1m_range_pct": 0.015,
        "scalp_max_1m_body_pct": 0.012,
        "scalp_news_cooldown_sec": 300,
        "max_account_exposure_frac": 0.22,
        "max_total_exposure_frac": 0.40,
    },
    "binance_futures_eth_scalp_t10": {
        "poll_sec": 5,
        "entry_tf": "1m",
        "leverage": 10.0,
        "order_sizing_mode": "equity_pct",
        "trade_equity_frac": 0.20,
        "min_notional_policy": "auto",
        "min_notional_buffer": 1.02,
        "take_profit_net_pct": 0.0032,
        "stop_loss_pct": 0.011,
        "trailing_stop_pct": 0.006,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": True,
        "scalp_use_rsi_cross": False,
        "scalp_require_reversal_candle": False,
        "scalp_trade_pressure_threshold": 0.12,
        "scalp_imbalance_threshold": 0.10,
        "scalp_max_spread_bps": 12.0,
        "scalp_max_1m_range_pct": 0.018,
        "scalp_max_1m_body_pct": 0.014,
        "scalp_news_cooldown_sec": 240,
        "max_account_exposure_frac": 0.22,
        "max_total_exposure_frac": 0.45,
    },
    "binance_futures_eth_scalp_t20": {
        "poll_sec": 3,
        "entry_tf": "1m",
        "leverage": 10.0,
        "order_sizing_mode": "equity_pct",
        "trade_equity_frac": 0.18,
        "min_notional_policy": "auto",
        "min_notional_buffer": 1.02,
        "take_profit_net_pct": 0.0027,
        "stop_loss_pct": 0.012,
        "trailing_stop_pct": 0.007,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": True,
        "scalp_use_rsi_cross": False,
        "scalp_require_reversal_candle": False,
        "scalp_trade_pressure_threshold": 0.10,
        "scalp_imbalance_threshold": 0.08,
        "scalp_max_spread_bps": 14.0,
        "scalp_max_1m_range_pct": 0.022,
        "scalp_max_1m_body_pct": 0.017,
        "scalp_news_cooldown_sec": 180,
        "max_account_exposure_frac": 0.20,
        "max_total_exposure_frac": 0.40,
    },

    # ALT (only for *high-liquidity* large-cap alts: SOL/XRP/DOGE/ADA/BNB, etc.)
    "binance_futures_alt_scalp_t5": {
        "poll_sec": 5,
        "entry_tf": "1m",
        "leverage": 5.0,
        "take_profit_net_pct": 0.0045,
        "stop_loss_pct": 0.013,
        "trailing_stop_pct": 0.007,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": True,
        "scalp_use_rsi_cross": True,
        "scalp_require_reversal_candle": True,
        "scalp_trade_pressure_threshold": 0.14,
        "scalp_imbalance_threshold": 0.12,
        "scalp_max_spread_bps": 16.0,
        "scalp_max_1m_range_pct": 0.024,
        "scalp_max_1m_body_pct": 0.020,
        "scalp_news_cooldown_sec": 300,
        "max_account_exposure_frac": 0.18,
        "max_total_exposure_frac": 0.35,
    },
    "binance_futures_alt_scalp_t10": {
        "poll_sec": 5,
        "entry_tf": "1m",
        "leverage": 5.0,
        "take_profit_net_pct": 0.0040,
        "stop_loss_pct": 0.014,
        "trailing_stop_pct": 0.008,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": True,
        "scalp_use_rsi_cross": False,
        "scalp_require_reversal_candle": False,
        "scalp_trade_pressure_threshold": 0.11,
        "scalp_imbalance_threshold": 0.10,
        "scalp_max_spread_bps": 20.0,
        "scalp_max_1m_range_pct": 0.028,
        "scalp_max_1m_body_pct": 0.022,
        "scalp_news_cooldown_sec": 240,
        "max_account_exposure_frac": 0.18,
        "max_total_exposure_frac": 0.35,
    },
    "binance_futures_alt_scalp_t20": {
        "poll_sec": 3,
        "entry_tf": "1m",
        "leverage": 5.0,
        "take_profit_net_pct": 0.0032,
        "stop_loss_pct": 0.015,
        "trailing_stop_pct": 0.009,
        "scalp_use_ws_trades": True,
        "scalp_use_liquidation_stream": True,
        "scalp_use_rsi_cross": False,
        "scalp_require_reversal_candle": False,
        "scalp_trade_pressure_threshold": 0.09,
        "scalp_imbalance_threshold": 0.07,
        "scalp_max_spread_bps": 24.0,
        "scalp_max_1m_range_pct": 0.033,
        "scalp_max_1m_body_pct": 0.026,
        "scalp_news_cooldown_sec": 180,
        "max_account_exposure_frac": 0.16,
        "max_total_exposure_frac": 0.32,
    },

# Binance Futures: small-account ETH scalp (lower min notional vs BTC on many accounts).
"binance_futures_eth_scalp_small": {
    "poll_sec": 5,
    "entry_tf": "1m",
    "leverage": 10.0,
    "order_sizing_mode": "equity_pct",
    "trade_equity_frac": 0.2,
    "min_notional_policy": "auto",
    "min_notional_buffer": 1.02,
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
    "scalp_max_spread_bps": 10.0,
    "entry_use_ioc": True,
    "exit_use_ioc": True,
    "ioc_price_pad_bps": 2.0,
    "ioc_max_chase_bps": 12.0,
    "max_account_exposure_frac": 0.25,
    "max_total_exposure_frac": 0.45,
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
