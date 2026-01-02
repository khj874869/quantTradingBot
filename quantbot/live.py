from __future__ import annotations

import asyncio
import json
import math
import time
import inspect
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from rich.console import Console

from quantbot.config import get_settings
from quantbot.utils.time import utc_now
from quantbot.collectors.upbit_rest import fetch_upbit_candles, fetch_upbit_orderbook
from quantbot.collectors.binance_rest import fetch_binance_klines, fetch_binance_orderbook
from quantbot.collectors.store import upsert_candles, load_candles_df
from quantbot.bar_builder.resampler import resample_ohlcv, RULE_MAP
from quantbot.features.indicators import add_indicators
from quantbot.features.orderbook import orderbook_imbalance_score
from quantbot.strategy.blender import generate_signal, BlenderWeights
from quantbot.strategy.scalping import generate_scalp_signal, ScalpingParams
from quantbot.streams.pressure import TradePressureBook
from quantbot.streams.flow import TradeFlowBook
from quantbot.streams.orderbook_delta import OrderbookDeltaBook
from quantbot.streams.liquidations import LiquidationClusterBook
from quantbot.streams.ws_liquidations import run_binance_futures_liquidation_stream
from quantbot.streams.ws_trades import start_trade_stream, StreamConfig
from quantbot.risk.position_tracker import PositionTracker
from quantbot.risk.exits import ExitManager, ExitConfig
from quantbot.risk.risk_manager import RiskManager, PortfolioState
from quantbot.risk.global_exposure import GlobalExposureStore
from quantbot.execution.executor import OrderExecutor
from quantbot.common.types import OrderRequest, Signal, ExecutionResult
from quantbot.journal import append_event, append_equity_snapshot

from quantbot.execution.adapters.paper_adapter import PaperAdapter, PaperConfig
from quantbot.execution.adapters.upbit_adapter import UpbitAdapter
from quantbot.execution.adapters.binance_adapter import BinanceAdapter
from quantbot.execution.adapters.binance_futures_adapter import BinanceFuturesAdapter
from quantbot.execution.adapters.kis_adapter import KISAdapter
from quantbot.execution.adapters.namoo_stock_adapter import NamooStockAdapter
from quantbot.execution.adapters.kiwoom_rest_adapter import KiwoomRestAdapter


console = Console()
settings = get_settings()


class SimpleMinuteBarBuilder:
    """Lightweight in-memory 1m bar builder (for venues without candle endpoints)."""

    def __init__(self, max_bars: int = 2000):
        import pandas as pd  # local import to keep startup fast for non-stock venues

        self._pd = pd
        self.max_bars = int(max_bars)
        self.cur_ts = None  # pandas Timestamp (minute)
        self.cur = None  # dict
        self.bars: list[dict] = []

    def update(self, ts, price: float, volume: float = 0.0) -> None:
        pd = self._pd
        if ts is None:
            return
        try:
            minute = pd.to_datetime(ts, utc=True).floor("min")
        except Exception:
            minute = pd.Timestamp.utcnow().floor("min")

        p = float(price)
        v = float(volume or 0.0)

        if self.cur_ts is None or minute != self.cur_ts:
            if self.cur is not None:
                self.bars.append(self.cur)
                if len(self.bars) > self.max_bars:
                    self.bars = self.bars[-self.max_bars :]
            self.cur_ts = minute
            self.cur = {"ts": minute, "open": p, "high": p, "low": p, "close": p, "volume": v}
        else:
            self.cur["high"] = max(float(self.cur["high"]), p)
            self.cur["low"] = min(float(self.cur["low"]), p)
            self.cur["close"] = p
            self.cur["volume"] = float(self.cur.get("volume", 0.0)) + v

    def dataframe(self, limit: int = 400):
        pd = self._pd
        rows = list(self.bars)
        if self.cur is not None:
            rows.append(self.cur)
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df = df.set_index("ts")
        return df.tail(int(limit))

   

@dataclass
class LiveConfig:
    venue: str
    symbols: list[str]

    # optional: news feeds (currently unused in scalping loop; reserved for future)
    news_feeds: list[str] | None = None

    # mode: 'live' sends real orders only if settings.TRADING_ENABLED == True
    mode: str = "paper"  # live | paper
    strategy: str = "scalp"  # blender | scalp

    entry_tf: str = "1m"  # blender uses entry_tf; scalp always uses 1m features
    poll_sec: int = 5

    # order sizing
    intended_notional: float = 100_000.0  # used when order_sizing_mode='fixed'
    order_sizing_mode: str = settings.SCALP_ORDER_SIZING_MODE  # fixed | equity_pct
    trade_equity_frac: float = settings.SCALP_TRADE_EQUITY_FRAC
    min_notional_policy: str = settings.SCALP_MIN_NOTIONAL_POLICY  # skip | bump | auto
    min_notional_buffer: float = settings.SCALP_MIN_NOTIONAL_BUFFER
    auto_bump_max_over_notional_frac: float = settings.SCALP_AUTO_BUMP_MAX_OVER_NOTIONAL_FRAC
    auto_bump_max_equity_frac: float = settings.SCALP_AUTO_BUMP_MAX_EQUITY_FRAC
    auto_bump_max_over_margin_frac: float = settings.SCALP_AUTO_BUMP_MAX_OVER_MARGIN_FRAC

    # exits
    stop_loss_pct: float = settings.STOP_LOSS_PCT
    trailing_stop_pct: float = settings.TRAILING_STOP_PCT
    take_profit_net_pct: float = settings.SCALP_TP_NET_PCT
    leverage: float = settings.SCALP_LEVERAGE

    # paper
    paper_initial_cash: float = settings.PAPER_INITIAL_CASH
    paper_fee_bps: int = settings.PAPER_FEE_BPS
    paper_slippage_bps: int = settings.PAPER_SLIPPAGE_BPS
    paper_state_path: str = "state/paper_state.json"

    # scalping params
    scalp_min_1m_trade_value: float = settings.SCALP_MIN_1M_TRADE_VALUE
    scalp_min_orderbook_notional: float = settings.SCALP_MIN_ORDERBOOK_NOTIONAL
    scalp_imbalance_threshold: float = settings.SCALP_IMBALANCE_THRESHOLD

    scalp_rsi_long_trigger: float = settings.SCALP_RSI_LONG_TRIGGER
    scalp_rsi_short_min: float = settings.SCALP_RSI_SHORT_MIN
    scalp_rsi_short_max: float = settings.SCALP_RSI_SHORT_MAX
    scalp_use_rsi_cross: bool = settings.SCALP_USE_RSI_CROSS
    scalp_require_reversal_candle: bool = settings.SCALP_REQUIRE_REVERSAL_CANDLE
    scalp_min_vol_surge: float = settings.SCALP_MIN_VOL_SURGE

    scalp_pressure_window_sec: int = settings.SCALP_PRESSURE_WINDOW_SEC
    scalp_trade_pressure_threshold: float = settings.SCALP_TRADE_PRESSURE_THRESHOLD
    scalp_min_trade_pressure_notional: float = settings.SCALP_MIN_TRADE_PRESSURE_NOTIONAL

    scalp_use_ws_trades: bool = settings.SCALP_USE_WS_TRADES
    scalp_ws_staleness_sec: int = settings.SCALP_WS_STALENESS_SEC

    # flow refinement
    scalp_flow_window_sec: int = settings.SCALP_FLOW_WINDOW_SEC
    scalp_min_flow_notional_rate: float = settings.SCALP_MIN_FLOW_NOTIONAL_RATE
    scalp_min_flow_accel: float = settings.SCALP_MIN_FLOW_ACCEL
    scalp_large_trade_min_notional: float = settings.SCALP_LARGE_TRADE_MIN_NOTIONAL
    scalp_min_large_trade_share: float = settings.SCALP_MIN_LARGE_TRADE_SHARE
    scalp_min_trade_count: int = settings.SCALP_MIN_TRADE_COUNT

    # news spike lockout (reserved)
    scalp_news_spike_tv_mult: float = settings.SCALP_NEWS_SPIKE_TV_MULT
    scalp_news_spike_move_pct: float = settings.SCALP_NEWS_SPIKE_MOVE_PCT
    scalp_news_cooldown_sec: int = settings.SCALP_NEWS_COOLDOWN_SEC

    # orderbook delta refinement
    scalp_ob_delta_depth: int = settings.SCALP_OB_DELTA_DEPTH
    scalp_min_ob_imb_delta: float = settings.SCALP_MIN_OB_IMB_DELTA

    # liquidation clustering
    scalp_use_liquidation_stream: bool = settings.SCALP_USE_LIQUIDATION_STREAM
    scalp_liq_window_sec: int = settings.SCALP_LIQ_WINDOW_SEC
    scalp_liq_bucket_bps: float = settings.SCALP_LIQ_BUCKET_BPS

    # execution aggressiveness
    entry_use_ioc: bool = settings.SCALP_ENTRY_USE_IOC
    exit_use_ioc: bool = settings.SCALP_EXIT_USE_IOC
    ioc_price_pad_bps: float = settings.SCALP_IOC_PRICE_PAD_BPS
    ioc_max_chase_bps: float = settings.SCALP_IOC_MAX_CHASE_BPS

    # microstructure filters
    scalp_max_spread_bps: float = settings.SCALP_MAX_SPREAD_BPS
    scalp_max_1m_range_pct: float = settings.SCALP_MAX_1M_RANGE_PCT
    scalp_max_1m_body_pct: float = settings.SCALP_MAX_1M_BODY_PCT

    # state
    state_dir: str = "state/bots"

    # Multi-bot risk / grouping
    account_tag: str = ""  # group bots that share the same account (e.g. "binance_futures_main")
    global_risk_path: str = settings.GLOBAL_RISK_STATE_PATH
    max_account_exposure_frac: float = settings.MAX_ACCOUNT_EXPOSURE_FRAC
    max_total_exposure_frac: float = settings.MAX_TOTAL_EXPOSURE_FRAC
    max_account_notional: float = settings.MAX_ACCOUNT_NOTIONAL
    max_total_notional: float = settings.MAX_TOTAL_NOTIONAL


def _best_bid_ask(venue: str, ob_raw: Any) -> Tuple[Optional[float], Optional[float]]:
    try:
        if venue == "upbit" and isinstance(ob_raw, list) and ob_raw:
            units = ob_raw[0].get("orderbook_units") or []
            if not units:
                return None, None
            best_ask = float(units[0].get("ask_price"))
            best_bid = float(units[0].get("bid_price"))
            return best_bid, best_ask
        if isinstance(ob_raw, dict):
            bids = ob_raw.get("bids") or []
            asks = ob_raw.get("asks") or []
            best_bid = float(bids[0][0]) if bids else None
            best_ask = float(asks[0][0]) if asks else None
            return best_bid, best_ask
    except Exception:
        return None, None
    return None, None



def _compute_intended_notional(
    *,
    venue: str,
    equity: float,
    last_price: float,
    cfg: LiveConfig,
) -> float:
    """Compute intended notional (quote currency) for an entry."""
    mode = str(getattr(cfg, "order_sizing_mode", "fixed") or "fixed").lower()
    if mode == "equity_pct":
        frac = float(getattr(cfg, "trade_equity_frac", 0.0) or 0.0)
        frac = max(0.0, min(1.0, frac))
        margin_budget = max(0.0, equity) * frac
        if str(venue).lower() in {"binance_futures"}:
            return margin_budget * float(getattr(cfg, "leverage", 1.0) or 1.0)
        return margin_budget
    return float(getattr(cfg, "intended_notional", 0.0) or 0.0)


async def _adjust_qty_by_rules(
    *,
    adapter: Any,
    venue: str,
    symbol: str,
    order_type: str,
    last_price: float,
    qty: float,
    equity: float,
    intended_notional: float,
    cfg: LiveConfig,
    console: Console,
) -> tuple[float, float, str | None]:
    """Return (qty_adj, notional_adj, reason_if_skipped).

    Uses adapter.get_symbol_rules when available (Binance futures) to:
      - floor to stepSize
      - enforce minQty
      - enforce minNotional (skip or bump)
    """
    qty = float(qty or 0.0)
    if qty <= 0 or last_price <= 0:
        return 0.0, 0.0, "BAD_QTY_OR_PRICE"

    # Default: no rules
    rules = None
    if hasattr(adapter, "get_symbol_rules"):
        try:
            rules = await adapter.get_symbol_rules(symbol, order_type=str(order_type).upper())
        except Exception:
            rules = None

    qty_adj = qty
    if rules is not None and getattr(rules, "qty_step", 0.0):
        step = float(getattr(rules, "qty_step", 0.0) or 0.0)
        if step > 0:
            qty_adj = math.floor(qty_adj / step) * step

    if rules is not None:
        min_qty = float(getattr(rules, "min_qty", 0.0) or 0.0)
        if min_qty > 0 and qty_adj < min_qty:
            qty_adj = min_qty

    notional_adj = qty_adj * last_price

    # Enforce min notional if known
    if rules is not None and getattr(rules, "min_notional", None):
        min_notional = float(getattr(rules, "min_notional") or 0.0)
        if min_notional > 0 and notional_adj < min_notional:
            policy = str(getattr(cfg, "min_notional_policy", "skip") or "skip").lower()
            if policy in ("bump", "auto"):
                buf = float(getattr(cfg, "min_notional_buffer", 1.0) or 1.0)
                target = min_notional * max(1.0, buf)
                if policy == "auto":
                    # AUTO decision: bump only if doing so stays within risk-safe margin caps.
                    # - Cap A: required margin <= intended_margin * (1 + auto_bump_max_over_margin_frac)
                    # - Cap B: required margin <= equity * auto_bump_max_equity_frac
                    lev = float(getattr(cfg, "leverage", 1.0) or 1.0)
                    lev = max(1e-9, lev)
                    intended_margin = float(intended_notional) / lev
                    req_margin = target / lev
                    max_over_margin = float(getattr(cfg, "auto_bump_max_over_margin_frac", 0.5) or 0.5)
                    max_equity_frac = float(getattr(cfg, "auto_bump_max_equity_frac", 0.30) or 0.30)
                    cap_margin = intended_margin * (1.0 + max(0.0, max_over_margin))
                    cap_equity = (float(equity) * max(0.0, max_equity_frac)) if max_equity_frac > 0 else float("inf")
                    if intended_margin <= 0:
                        return 0.0, 0.0, f"MIN_NOTIONAL<{min_notional:g}_AUTO_SKIP(no_margin_budget)"
                    if req_margin > cap_margin or req_margin > cap_equity:
                        return 0.0, 0.0, (
                            f"MIN_NOTIONAL<{min_notional:g}_AUTO_SKIP("
                            f"req_margin={req_margin:.4g} cap_margin={cap_margin:.4g} cap_equity={cap_equity:.4g}"
                            f")"
                        )
                step = float(getattr(rules, "qty_step", 0.0) or 0.0)
                qty_needed = target / max(1e-12, last_price)
                if step > 0:
                    qty_adj = math.ceil(qty_needed / step) * step
                else:
                    qty_adj = qty_needed
                # enforce min qty again
                min_qty = float(getattr(rules, "min_qty", 0.0) or 0.0)
                if min_qty > 0 and qty_adj < min_qty:
                    qty_adj = min_qty
                notional_adj = qty_adj * last_price
                console.print(f"[dim]MIN_NOTIONAL bump qty -> {qty_adj:g} (target_notional≈{target:g}, policy={policy})[/dim]")
            else:
                return 0.0, 0.0, f"MIN_NOTIONAL<{min_notional:g}"

    if rules is not None:
        max_qty = float(getattr(rules, "max_qty", 0.0) or 0.0)
        if max_qty > 0 and qty_adj > max_qty:
            return 0.0, 0.0, "QTY_ABOVE_MAX"

    if qty_adj <= 0:
        return 0.0, 0.0, "QTY_ADJ_LE0"

    return qty_adj, notional_adj, None


def _orderbook_l2(venue: str, ob_raw: Any, depth: int = 10) -> Optional[dict]:
    """Normalize orderbook to {bids:[[p,q]], asks:[[p,q]]} for UI."""
    try:
        d = int(max(1, depth))
        if venue == "upbit" and isinstance(ob_raw, list) and ob_raw:
            units = (ob_raw[0].get("orderbook_units") or [])[:d]
            bids = []
            asks = []
            for u in units:
                bid_p = float(u.get("bid_price") or 0.0)
                bid_q = float(u.get("bid_size") or 0.0)
                ask_p = float(u.get("ask_price") or 0.0)
                ask_q = float(u.get("ask_size") or 0.0)
                if bid_p > 0 and bid_q > 0:
                    bids.append([bid_p, bid_q])
                if ask_p > 0 and ask_q > 0:
                    asks.append([ask_p, ask_q])
            return {"bids": bids, "asks": asks}
        if isinstance(ob_raw, dict):
            bids = [[float(p), float(q)] for p, q in (ob_raw.get("bids") or [])[:d]]
            asks = [[float(p), float(q)] for p, q in (ob_raw.get("asks") or [])[:d]]
            return {"bids": bids, "asks": asks}
    except Exception:
        return None
    return None



def _bps(x: float) -> float:
    return float(x) / 10000.0


def _limit_price_for_ioc(
    *,
    side: str,
    best_bid: Optional[float],
    best_ask: Optional[float],
    pad_bps: float,
    hint_price: Optional[float] = None,
) -> Optional[float]:
    """Compute an aggressive limit price that is intended to fill immediately with IOC."""
    if best_bid is None or best_ask is None:
        return hint_price

    pad = _bps(pad_bps)
    if side.upper() == "BUY":
        px = best_ask * (1.0 + pad)
        if hint_price is not None:
            px = max(px, float(hint_price))
        return px
    else:
        px = best_bid * (1.0 - pad)
        if hint_price is not None:
            px = min(px, float(hint_price))
        return px



def _ioc_price_ladder(
    *,
    side: str,
    best_bid: Optional[float],
    best_ask: Optional[float],
    pad_bps: float,
    max_chase_bps: float,
    hint_price: Optional[float] = None,
) -> list[float]:
    """Create a small ladder of IOC prices from pad_bps up to max_chase_bps."""
    pads: list[float] = [float(pad_bps)]
    try:
        mb = float(max_chase_bps or 0.0)
        if mb > float(pad_bps) + 1e-9:
            pads += [float(pad_bps) + (mb - float(pad_bps)) * 0.5, mb]
    except Exception:
        pass

    out: list[float] = []
    for p in pads:
        px = _limit_price_for_ioc(side=side, best_bid=best_bid, best_ask=best_ask, pad_bps=p, hint_price=hint_price)
        if px is None:
            continue
        # de-dup in order
        if not out or abs(float(out[-1]) - float(px)) > 1e-12:
            out.append(float(px))
    return out


def _venue_supports_ioc(venue: str) -> bool:
    return venue in {"binance", "binance_futures", "paper", "kiwoom", "namoo_stock"}


async def _call(fn, *args, **kwargs):
    """
    Call a function that might be async or sync.
    - If async: await it.
    - If sync: run in asyncio.to_thread to avoid blocking the event loop.
    """
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _fetch_1m_candles(venue: str, symbol: str, limit: int = 400):
    # Upbit candles (REST)
    if venue == "upbit":
        return await _call(fetch_upbit_candles, symbol, minutes=1, count=min(int(limit), 200))

    # Binance spot / futures candles (REST)
    if venue in ("binance", "binance_futures"):
        base = {"symbol": symbol, "interval": "1m"}
        limit_keys = ("limit", "count", "n", "size", "max_records", "maxRows")
        flags = ("futures", "is_futures", "futures_mode") if venue == "binance_futures" else (None,)
        for lk in limit_keys:
            for fk in flags:
                kw = dict(base)
                kw[lk] = int(limit)
                try:
                    if fk:
                        return await _call(fetch_binance_klines, **kw, **{fk: True})
                    return await _call(fetch_binance_klines, **kw)
                except TypeError as e:
                    msg = str(e)
                    if "unexpected keyword argument" in msg and (f"\'{lk}\'" in msg or (fk and f"\'{fk}\'" in msg)):
                        continue
                    raise
        # Fallback: try without limit kw (collector might have a default)
        for fk in flags:
            try:
                if fk:
                    return await _call(fetch_binance_klines, symbol=symbol, interval="1m", **{fk: True})
                return await _call(fetch_binance_klines, symbol=symbol, interval="1m")
            except TypeError as e:
                msg = str(e)
                if "unexpected keyword argument" in msg and fk and f"\'{fk}\'" in msg:
                    continue
                # maybe expects positional args
                try:
                    return await _call(fetch_binance_klines, symbol, "1m", int(limit))
                except TypeError:
                    return await _call(fetch_binance_klines, symbol, "1m")

    # Other venues: candles are built elsewhere (e.g., SimpleMinuteBarBuilder for stocks)
    return None


async def _fetch_orderbook(venue: str, symbol: str, adapter: Optional[Any] = None) -> Optional[dict]:
    if venue == "upbit":
        return await _call(fetch_upbit_orderbook, symbol)

    if venue in {"binance", "binance_futures"}:
        if venue == "binance_futures":
            for flag in ("futures", "is_futures", "futures_mode"):
                try:
                    return await _call(fetch_binance_orderbook, symbol, **{flag: True})
                except TypeError as e:
                    if f"unexpected keyword argument '{flag}'" in str(e):
                        continue
                    raise
            return await _call(fetch_binance_orderbook, symbol)
        return await _call(fetch_binance_orderbook, symbol)

    # Stocks/others: try adapter-provided orderbook if available.
    if adapter is not None and hasattr(adapter, "get_orderbook"):
        try:
            return await adapter.get_orderbook(symbol)  # type: ignore[attr-defined]
        except Exception:
            return None
    return None


def _write_bot_state(cfg: LiveConfig, symbol: str, payload: Dict[str, Any]) -> None:
    d = Path(cfg.state_dir)
    d.mkdir(parents=True, exist_ok=True)
    bot_id = f"{cfg.venue}_{symbol}".replace("/", "_").replace("-", "_")
    path = d / f"{bot_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _estimate_fee(venue: str, notional: float) -> float:
    # Strategy uses fee_rate estimate; we reuse that for journaling when raw fee is missing.
    fee_rate = settings.DEFAULT_FEE_BPS / 10000.0
    if venue == "paper":
        fee_rate = settings.PAPER_FEE_BPS / 10000.0
    return abs(notional) * fee_rate

def _exec_success(res: ExecutionResult) -> bool:
    try:
        if res is None or res.update is None:
            return False
        filled = float(res.update.filled_qty or 0.0)
        if filled <= 0:
            return False
        st = str(res.update.status or "").upper()
        return st not in {"REJECTED"}
    except Exception:
        return False



def _positions_to_dict(tracker: PositionTracker, symbols: list[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for s in symbols:
        p = tracker.get(s)
        if p.qty:
            out[s] = {
                "qty": p.qty,
                "avg_cost": p.avg_cost,
                "realized_net": p.realized_pnl_net,
                "fee_paid": p.fee_paid,
            }
    return out


def _mk_scalp_params(cfg: LiveConfig) -> ScalpingParams:
    """Map LiveConfig -> scalping strategy parameters.

    The strategy module is intentionally pure; all real-time features (flow/pressure/liquidations)
    are computed in the live loop and passed in.
    """
    # Streams are only available for Upbit/Binance. For other venues, disable
    # stream-dependent thresholds so the bot can still operate on candles+orderbook.
    venue_l = (cfg.venue or "").lower()
    has_streams = venue_l in {"upbit", "binance", "binance_futures"} and bool(cfg.scalp_use_ws_trades)

    tp_thr = cfg.scalp_trade_pressure_threshold if has_streams else 0.0
    tp_min_notional = cfg.scalp_min_trade_pressure_notional if has_streams else 0.0

    min_flow_rate = cfg.scalp_min_flow_notional_rate if has_streams else 0.0
    min_flow_accel = cfg.scalp_min_flow_accel if has_streams else 0.0
    min_large_share = cfg.scalp_min_large_trade_share if has_streams else 0.0
    min_trade_count = cfg.scalp_min_trade_count if has_streams else 0

    is_stock = venue_l in {"kis", "namoo", "namoo_stock", "kiwoom"}
    min_1m_trade_value = 0.0 if is_stock else cfg.scalp_min_1m_trade_value
    min_vol_surge = 0.0 if is_stock else cfg.scalp_min_vol_surge

    return ScalpingParams(
        min_1m_trade_value=min_1m_trade_value,
        min_orderbook_notional=cfg.scalp_min_orderbook_notional,
        min_vol_surge=min_vol_surge,
        max_spread_bps=cfg.scalp_max_spread_bps,
        max_1m_range_pct=cfg.scalp_max_1m_range_pct,
        max_1m_body_pct=cfg.scalp_max_1m_body_pct,
        ob_imbalance_threshold=cfg.scalp_imbalance_threshold,
        trade_pressure_threshold=tp_thr,
        min_trade_pressure_notional=tp_min_notional,
        min_flow_notional_rate=min_flow_rate,
        min_flow_accel=min_flow_accel,
        large_trade_min_notional=cfg.scalp_large_trade_min_notional,
        min_large_trade_share=min_large_share,
        min_trade_count=min_trade_count,
        min_ob_imb_delta=cfg.scalp_min_ob_imb_delta,
        rsi_long_trigger=cfg.scalp_rsi_long_trigger,
        rsi_short_min=cfg.scalp_rsi_short_min,
        rsi_short_max=cfg.scalp_rsi_short_max,
        use_rsi_cross=cfg.scalp_use_rsi_cross,
        require_reversal_candle=cfg.scalp_require_reversal_candle,
    )


def _candles_for_ui(df_1m, limit: int = 240) -> list[dict]:
    """Return OHLCV list for lightweight-charts.

    Output format: [{t,o,h,l,c}, ...] where t is UNIX seconds.
    """
    try:
        if df_1m is None or len(df_1m) == 0:
            return []
        sub = df_1m.tail(limit)
        out: list[dict] = []
        for ts, r in sub.iterrows():
            try:
                t = int(getattr(ts, "timestamp")() )
            except Exception:
                # fallback: parse as pandas Timestamp
                import pandas as _pd

                t = int(_pd.to_datetime(ts, utc=True).timestamp())
            out.append({
                "t": t,
                "o": float(r["open"]),
                "h": float(r["high"]),
                "l": float(r["low"]),
                "c": float(r["close"]),
                "v": float(r.get("volume", 0.0)),
            })
        return out
    except Exception:
        return []


def _make_adapter(cfg: LiveConfig):
    venue = cfg.venue
    if cfg.mode == "paper":
        pcfg = PaperConfig(
            initial_cash=cfg.paper_initial_cash,
            fee_bps=cfg.paper_fee_bps,
            slippage_bps=cfg.paper_slippage_bps,
            state_path=cfg.paper_state_path,
        )
        market_adapter = None
        if venue == "demo":
            from quantbot.execution.adapters.demo_adapter import DemoAdapter

            market_adapter = DemoAdapter()
        return PaperAdapter(venue=venue, config=pcfg, market_adapter=market_adapter)

    # live
    if venue == "upbit":
        return UpbitAdapter(settings.UPBIT_ACCESS_KEY or "", settings.UPBIT_SECRET_KEY or "")
    if venue == "binance":
        return BinanceAdapter(settings.BINANCE_API_KEY or "", settings.BINANCE_API_SECRET or "", base_url=settings.BINANCE_BASE_URL)
    if venue == "binance_futures":
        base = settings.BINANCE_FUTURES_BASE_URL
        if not base:
            # common default
            base = "https://fapi.binance.com"
        return BinanceFuturesAdapter(settings.BINANCE_API_KEY or "", settings.BINANCE_API_SECRET or "", base_url=base)
    if venue == "kis":
        return KISAdapter(
            app_key=settings.KIS_APP_KEY or "",
            app_secret=settings.KIS_APP_SECRET or "",
            account_no=settings.KIS_ACCOUNT_NO or "",
            product_code=settings.KIS_PRODUCT_CODE or "",
            base_url=settings.KIS_BASE_URL,
        )
    if venue in {"namoo", "namoo_stock"}:
        return NamooStockAdapter(base_url=settings.NAMOO_BRIDGE_URL, account_no=settings.NAMOO_ACCOUNT_NO)
    if venue == "kiwoom":
        return KiwoomRestAdapter(
            appkey=settings.KIWOOM_APPKEY or "",
            secretkey=settings.KIWOOM_SECRETKEY or "",
            account_no=settings.KIWOOM_ACCOUNT_NO or "",
            base_url=settings.KIWOOM_BASE_URL,
        )

    raise ValueError(f"Unsupported venue: {venue}")


async def run_live(cfg: LiveConfig) -> None:
    venue = cfg.venue.lower()
    # In paper mode we always "trade". In live mode, TRADING_ENABLED acts as a kill-switch.
    trading_enabled = bool(cfg.mode != "live" or settings.TRADING_ENABLED)

    # ---- Loud runtime banner (prevents "I thought it was live" accidents) ----
    if cfg.mode != "live":
        console.print("[bold yellow]PAPER MODE[/bold yellow] - 실주문 전송 안 함 (바이낸스 앱에 체결이 안 보이는 게 정상).")
    else:
        if settings.TRADING_ENABLED:
            console.print("[bold green]LIVE MODE[/bold green] - 실주문 전송 [bold green]ON[/bold green] (TRADING_ENABLED=true)")
        else:
            console.print("[bold red]LIVE MODE[/bold red] - 실주문 전송 [bold red]OFF[/bold red] (TRADING_ENABLED=false → DRYRUN)")

    adapter = _make_adapter(cfg)
    executor = OrderExecutor(adapter)

    tracker = PositionTracker(venue, path=f"state/positions_{venue}.json")
    slip_rate = (cfg.paper_slippage_bps / 10000.0) if cfg.mode == "paper" else (settings.DEFAULT_SLIPPAGE_BPS / 10000.0)

    exit_mgr = ExitManager(
        tracker,
        ExitConfig(
            stop_loss_pct=cfg.stop_loss_pct,
            trailing_stop_pct=cfg.trailing_stop_pct,
            take_profit_net_pct=cfg.take_profit_net_pct,
            leverage=cfg.leverage,
            fee_rate=settings.DEFAULT_FEE_BPS / 10000.0,
            slippage_rate=slip_rate,
        ),
    )

    rm = RiskManager(max_position_per_symbol=settings.MAX_POSITION_PER_SYMBOL, max_daily_loss=settings.MAX_DAILY_LOSS)

    # Shared exposure caps across processes (optional)
    acct_tag = cfg.account_tag or venue
    global_store = GlobalExposureStore(cfg.global_risk_path) if cfg.global_risk_path else None
    last_equity_log_ms = 0

    pressure = TradePressureBook(window_sec=cfg.scalp_pressure_window_sec)
    flow = TradeFlowBook(window_sec=cfg.scalp_flow_window_sec, large_trade_min_notional=cfg.scalp_large_trade_min_notional)
    ob_delta = OrderbookDeltaBook()
    #depth=cfg.scalp_ob_delta_depth
    liq_cluster = LiquidationClusterBook(window_sec=cfg.scalp_liq_window_sec, bucket_bps=cfg.scalp_liq_bucket_bps)

    # Background streams (non-blocking)
    stop_ws: Optional[asyncio.Event] = None
    stop_liq: Optional[asyncio.Event] = None
    if cfg.strategy == "scalp" and cfg.scalp_use_ws_trades and venue in {"upbit", "binance", "binance_futures"}:
        stop_ws = await start_trade_stream(StreamConfig(venue=venue, symbols=cfg.symbols), pressure, flow)

    if cfg.strategy == "scalp" and cfg.scalp_use_liquidation_stream and venue == "binance_futures":
        stop_liq = asyncio.Event()
        asyncio.create_task(run_binance_futures_liquidation_stream(cfg.symbols, liq_cluster, stop_liq))

    day_start_equity: Optional[float] = None

    # UI/debug tapes (in-memory ring buffers)
    event_tape: Dict[str, Any] = {s: deque(maxlen=80) for s in cfg.symbols}
    last_signal: Dict[str, Any] = {s: {} for s in cfg.symbols}

    stock_venues = {"kiwoom", "namoo_stock", "kis", "namoo"}
    bar_builders = {s: SimpleMinuteBarBuilder() for s in cfg.symbols} if venue in stock_venues else {}

    console.print(f"[cyan]Running[/cyan] venue={venue} mode={cfg.mode} strategy={cfg.strategy} enabled={trading_enabled}")

    try:
        while True:
            t0 = time.time()
            ts = utc_now()

            # Snapshot equity/positions (best-effort). Some adapters are stubby; we fall back to tracker.
            try:
                equity = float(await adapter.get_equity())
            except Exception:
                equity = 0.0
            if day_start_equity is None and equity > 0:
                day_start_equity = equity

            # equity history log (for dashboard equity curve) - throttle to ~1/min
            now_ms = int(time.time() * 1000)
            if equity > 0 and (now_ms - last_equity_log_ms) >= 60_000:
                last_equity_log_ms = now_ms
                try:
                    append_equity_snapshot({
                        "ts": ts.isoformat(),
                        "ts_ms": now_ms,
                        "venue": venue,
                        "account_tag": acct_tag,
                        "mode": cfg.mode,
                        "strategy": cfg.strategy,
                        "simulated": bool(cfg.mode != "live"),
                        "equity": float(equity),
                    })
                except Exception:
                    pass

            for symbol in cfg.symbols:
                # Market data
                if venue in stock_venues:
                    try:
                        px = float(await adapter.get_last_price(symbol))
                    except Exception:
                        px = 0.0
                    if px > 0 and symbol in bar_builders:
                        bar_builders[symbol].update(ts, px, 0.0)
                    df_1m = bar_builders[symbol].dataframe(limit=400) if symbol in bar_builders else None
                else:
                    df_1m = await _fetch_1m_candles(venue, symbol, limit=400)
                if df_1m is None or len(df_1m) < 60:
                    continue

                # Persist/reuse for multi-tf strategies
                try:
                    if venue not in stock_venues and hasattr(df_1m, "index"):
                        upsert_candles(venue, symbol, "1m", df_1m)
                except Exception:
                    pass

                df_1m = add_indicators(df_1m)
                last_row = df_1m.iloc[-1]
                last_price = float(last_row["close"])
                tracker.update_mark(symbol, last_price)

                # Orderbook
                ob_raw = await _fetch_orderbook(venue, symbol, adapter)
                best_bid, best_ask = _best_bid_ask(venue, ob_raw)
                ob_l2 = _orderbook_l2(venue, ob_raw, depth=10)
                # Microstructure features
                ob_imb = orderbook_imbalance_score(ob_raw) if ob_raw is not None else 0.0
                ob_imb_delta = 0.0
                ob_delta_snap = None
                if ob_raw is not None and isinstance(ob_raw, dict) and ("bids" in ob_raw and "asks" in ob_raw):
                    try:
                        ob_delta_snap = ob_delta.update(symbol, ob_raw)
                        ob_imb_delta = float(ob_delta_snap.imbalance_delta)
                    except Exception:
                        ob_delta_snap = None

                now_ms = int(time.time() * 1000)
                ps = pressure.snapshot(symbol)
                trade_pressure = float(ps.pressure)
                trade_pressure_notional = float(ps.notional)
                fs = flow.snapshot(symbol, now_ms)
                flow_dict = asdict(fs)
                recent_trades = []
                try:
                    recent_trades = flow.recent_trades(symbol, limit=60, now_ms=now_ms, max_age_sec=120.0)
                except Exception:
                    recent_trades = []

                liq_dict = None
                liq_snap = None
                if cfg.strategy == "scalp" and cfg.scalp_use_liquidation_stream and venue == "binance_futures":
                    try:
                        liq_snap = liq_cluster.snapshot(symbol, now_ms)
                        liq_dict = asdict(liq_snap)
                    except Exception:
                        liq_dict = None

                # Exit check (uses tracker + last price)
                decision = exit_mgr.check(symbol, last_price)
                pos = tracker.get(symbol)

                if decision.should_exit and pos.qty != 0:
                    close_side = "SELL" if pos.qty > 0 else "BUY"
                    close_qty = abs(pos.qty)

                    use_ioc = bool(cfg.exit_use_ioc and _venue_supports_ioc(venue) and best_bid and best_ask)

                    meta = {}
                    if venue == "binance_futures":
                        meta["reduceOnly"] = True

                    res = None
                    if trading_enabled:
                        if use_ioc:
                            prices = _ioc_price_ladder(
                                side=close_side,
                                best_bid=best_bid,
                                best_ask=best_ask,
                                pad_bps=cfg.ioc_price_pad_bps,
                                max_chase_bps=cfg.ioc_max_chase_bps,
                                hint_price=None,
                            )
                        else:
                            prices = []

                        if use_ioc and prices:
                            req = OrderRequest(
                                venue=venue,
                                symbol=symbol,
                                side=close_side,
                                qty=close_qty,
                                order_type="LIMIT",
                                price=float(prices[0]),
                                meta=meta,
                            )
                            res = await executor.execute_ioc_limit_prices_then_market(req, prices, fallback_market=True)
                        else:
                            req = OrderRequest(
                                venue=venue,
                                symbol=symbol,
                                side=close_side,
                                qty=close_qty,
                                order_type="MARKET",
                                price=None,
                                meta=meta,
                            )
                            res = await executor.execute(req)

                    if _exec_success(res):
                        # fee estimate if raw missing
                        filled_qty = float(res.update.filled_qty)
                        fill_px = float(res.update.avg_fill_price or last_price)
                        notional = filled_qty * fill_px
                        fee = float(res.update.fee or _estimate_fee(venue, notional))
                        realized = tracker.apply_fill(symbol, close_side, filled_qty, fill_px, fee=fee)
                        append_event(
                            {
                                "ts": ts.isoformat(),
                                "venue": venue,
                                "account_tag": acct_tag,
                                "mode": cfg.mode,
                                "simulated": bool(cfg.mode != "live"),
                                "symbol": symbol,
                                "side": close_side,
                                "qty": filled_qty,
                                "price": fill_px,
                                "fee": fee,
                                "order_id": str(res.update.order_id or ""),
                                "client_order_id": str(res.update.client_order_id or ""),
                                "order_status": str(res.update.status or ""),
                                "reason": decision.reason,
                                "realized_gross_delta": realized.get("realized_pnl_delta", 0.0),
                                "realized_net_delta": realized.get("realized_pnl_net_delta", 0.0),
                            }
                        )
                        try:
                            event_tape[symbol].append({"ts": ts.isoformat(), "type": "EXIT", "side": close_side, "qty": filled_qty, "price": fill_px, "fee": fee, "reason": decision.reason})
                        except Exception:
                            pass

                    if res is None:
                        console.print(f"[yellow]EXIT[/yellow] {symbol} {decision.reason} DRYRUN")
                    else:
                        console.print(f"[yellow]EXIT[/yellow] {symbol} {decision.reason} {res.update.status} filled={res.update.filled_qty}")

                # Entry logic
                if pos.qty == 0:
                    sig: Optional[Signal] = None
                    if cfg.strategy == "scalp":
                        params = _mk_scalp_params(cfg)
                        sig = generate_scalp_signal(
                            venue=venue,
                            symbol=symbol,
                            last_price=last_price,
                            df_1m=df_1m,
                            orderbook=ob_raw,
                            orderbook_imbalance=float(ob_imb),
                            orderbook_imbalance_delta=float(ob_imb_delta),
                            trade_pressure=float(trade_pressure),
                            trade_pressure_notional=float(trade_pressure_notional),
                            params=params,
                            in_position=False,
                            flow=flow_dict,
                            liq=liq_dict,
                        )
                        try:
                            last_signal[symbol] = {"ts": ts.isoformat(), "side": sig.side, "score": float(sig.score), "meta": sig.meta}
                        except Exception:
                            last_signal[symbol] = {}
                    else:
                        # blender uses daily + entry_tf; load from DB (assumes ingest is running)
                        df_daily = load_candles_df(venue, symbol, "1d", limit=1500)
                        df_entry = load_candles_df(venue, symbol, cfg.entry_tf, limit=1500)
                        df_daily = add_indicators(df_daily) if len(df_daily) else df_daily
                        df_entry = add_indicators(df_entry) if len(df_entry) else df_entry
                        ob_score = orderbook_imbalance_score(ob_raw) if ob_raw is not None else 0.0
                        sig = generate_signal(venue, symbol, ts, df_daily, df_entry, 0.0, ob_score, BlenderWeights())

                    # Spot/stock venues: do not open shorts by default (shorting requires margin/borrow).
                    if sig is not None and sig.side == "SELL" and venue not in {"binance_futures"}:
                        console.print(f"[dim]SKIP[/dim] {symbol} short entry not supported on venue={venue}")
                        sig = None

                    if sig is not None and sig.side in {"BUY", "SELL"}:
                        # Sizing (compute intended notional and adjust qty to exchange rules)
                        intended_notional = _compute_intended_notional(
                            venue=venue,
                            equity=float(equity or 0.0),
                            last_price=float(last_price or 0.0),
                            cfg=cfg,
                        )
                        qty_raw = intended_notional / max(1e-12, last_price)
                        intended_notional_raw = float(intended_notional)
                        qty, intended_notional_adj, skip_reason = await _adjust_qty_by_rules(
                            adapter=adapter,
                            venue=venue,
                            symbol=symbol,
                            order_type="MARKET",
                            last_price=float(last_price),
                            qty=float(qty_raw),
                            equity=float(equity or 0.0),
                            intended_notional=float(intended_notional_raw),
                            cfg=cfg,
                            console=console,
                        )
                        if skip_reason:
                            console.print(f"[dim]SKIP[/dim] {symbol} sizing: {skip_reason}")
                            continue
                        intended_notional = float(intended_notional_adj)

                        # Risk
                        prices = {symbol: last_price}
                        pf = PortfolioState(
                            equity=equity,
                            day_start_equity=(day_start_equity or equity),
                            positions=_positions_to_dict(tracker, cfg.symbols),
                            prices=prices,
                        )
                        ok, why = rm.approve(
                            pf,
                            sig,
                            intended_notional=intended_notional,
                            venue=venue,
                            global_store=global_store,
                            account_tag=acct_tag,
                            max_account_exposure_frac=cfg.max_account_exposure_frac,
                            max_total_exposure_frac=cfg.max_total_exposure_frac,
                            max_account_notional=cfg.max_account_notional,
                            max_total_notional=cfg.max_total_notional,
                        )
                        if not ok:
                            console.print(f"[dim]SKIP[/dim] {symbol} risk: {why}")
                        else:
                            side = sig.side
                            use_ioc = bool(cfg.entry_use_ioc and _venue_supports_ioc(venue) and best_bid and best_ask)
                            hint_px = None
                            try:
                                hint_px = float((sig.meta or {}).get("liq_hint_price")) if (sig.meta or {}).get("liq_hint_price") else None
                            except Exception:
                                hint_px = None

                            res = None
                            if trading_enabled:
                                client_oid = f"{symbol}-ENTRY-{int(time.time() * 1000)}"
                                if use_ioc:
                                    prices = _ioc_price_ladder(
                                        side=side,
                                        best_bid=best_bid,
                                        best_ask=best_ask,
                                        pad_bps=cfg.ioc_price_pad_bps,
                                        max_chase_bps=cfg.ioc_max_chase_bps,
                                        hint_price=hint_px,
                                    )
                                else:
                                    prices = []

                                if use_ioc and prices:
                                    req = OrderRequest(
                                        venue=venue,
                                        symbol=symbol,
                                        side=side,
                                        qty=qty,
                                        order_type="LIMIT",
                                        price=float(prices[0]),
                                        client_order_id=client_oid,
                                        meta={},
                                    )
                                    res = await executor.execute_ioc_limit_prices_then_market(req, prices, fallback_market=True)
                                else:
                                    res = await executor.execute(OrderRequest(venue=venue, symbol=symbol, side=side, qty=qty, order_type="MARKET", client_order_id=client_oid))

                            if _exec_success(res):
                                filled_qty = float(res.update.filled_qty)
                                fill_px = float(res.update.avg_fill_price or last_price)
                                notional = filled_qty * fill_px
                                fee = float(res.update.fee or _estimate_fee(venue, notional))
                                tracker.apply_fill(symbol, side, filled_qty, fill_px, fee=fee)
                                append_event(
                                    {
                                        "ts": ts.isoformat(),
                                        "venue": venue,
                                        "account_tag": acct_tag,
                                        "mode": cfg.mode,
                                        "simulated": bool(cfg.mode != "live"),
                                        "symbol": symbol,
                                        "side": side,
                                        "qty": filled_qty,
                                        "price": fill_px,
                                        "fee": fee,
                                        "order_id": str(res.update.order_id or ""),
                                        "client_order_id": str(res.update.client_order_id or ""),
                                        "order_status": str(res.update.status or ""),
                                        "reason": "ENTRY",
                                        "signal": {"side": sig.side, "score": sig.score, "meta": sig.meta},
                                    }
                                )
                                try:
                                    event_tape[symbol].append({"ts": ts.isoformat(), "type": "ENTRY", "side": side, "qty": filled_qty, "price": fill_px, "fee": fee, "score": float(sig.score)})
                                except Exception:
                                    pass

                            if res is None:
                                console.print(f"[green]ENTRY[/green] {symbol} {sig.side} score={sig.score:.2f} DRYRUN")
                            else:
                                console.print(f"[green]ENTRY[/green] {symbol} {sig.side} score={sig.score:.2f} {res.update.status} filled={res.update.filled_qty}")

                # UI state dump (cheap)
                p = tracker.get(symbol)
                unrealized = 0.0
                try:
                    if p.qty > 0:
                        unrealized = (float(last_price) - float(p.avg_cost)) * float(p.qty)
                    elif p.qty < 0:
                        unrealized = (float(p.avg_cost) - float(last_price)) * abs(float(p.qty))
                except Exception:
                    unrealized = 0.0

                pos_notional = abs(float(p.qty) * float(last_price)) if last_price else 0.0
                pnl_total = float(getattr(p, "realized_pnl_net", 0.0) or 0.0) + float(unrealized)
                pnl_pct = (pnl_total / float(equity)) if equity and equity > 0 else 0.0

                # Update shared exposure state (best-effort). This lets RiskManager gate new entries
                # across multiple bot processes.
                if global_store is not None:
                    try:
                        global_store.update(
                            key=f"{acct_tag}:{venue}:{symbol}",
                            account_tag=acct_tag,
                            equity=float(equity or 0.0),
                            abs_notional=float(pos_notional),
                        )
                    except Exception:
                        pass

                state_payload = {
                    "ts": ts.isoformat(),
                    "venue": venue,
                    "account_tag": acct_tag,
                    "symbol": symbol,
                    "mode": cfg.mode,
                    "strategy": cfg.strategy,
                    "equity": float(equity or 0.0),
                    "day_start_equity": float(day_start_equity or equity or 0.0),
                    "last_price": last_price,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "position": {**asdict(p), "unrealized_pnl": float(unrealized), "notional": float(pos_notional), "pnl_total": float(pnl_total), "pnl_pct": float(pnl_pct)},
                    "pressure": asdict(ps) if ps else None,
                    "flow": flow_dict,
                    "liq": liq_dict,
                    "ob": {
                        "imbalance": float(ob_imb),
                        "imbalance_delta": float(ob_imb_delta),
                    },
                    "orderbook_l2": ob_l2,
                    "trades": recent_trades,
                    "events": list(event_tape.get(symbol) or []),
                    "last_signal": last_signal.get(symbol) or {},
                    "candles_1m": _candles_for_ui(df_1m, limit=240),
                }
                _write_bot_state(cfg, symbol, state_payload)

            # Sleep with drift correction
            dt = time.time() - t0
            sleep_for = max(0.0, float(cfg.poll_sec) - dt)
            await asyncio.sleep(sleep_for)

    finally:
        if stop_ws is not None:
            stop_ws.set()
        if stop_liq is not None:
            stop_liq.set()
        try:
            if hasattr(adapter, "close"):
                await adapter.close()  # type: ignore[attr-defined]
        except Exception:
            pass
