from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple, Dict

import math
import pandas as pd

from quantbot.common.types import Signal
from quantbot.features.orderbook import spread_bps
from quantbot.utils.time import utc_now


@dataclass
class ScalpingParams:
    # Liquidity filters
    min_1m_trade_value: float = 0.0          # quote currency (KRW/USDT) ~ volume*close
    min_orderbook_notional: float = 0.0      # (bid+ask) notional within depth
    min_vol_surge: float = 0.0              # VOL_SURGE >= this (volume / VOL_SMA_5)

    # Market microstructure filters
    max_spread_bps: float = 0.0               # spread in bps; 0 disables
    max_1m_range_pct: float = 0.0             # last 1m (high-low)/close; 0 disables
    max_1m_body_pct: float = 0.0              # last 1m abs(close-open)/open; 0 disables

    # Pressure filters (executed trades + orderbook)
    ob_imbalance_threshold: float = 0.15     # abs(orderbook_imbalance) >= threshold
    trade_pressure_threshold: float = 0.20   # abs(trade_pressure) >= threshold (executed trades)
    min_trade_pressure_notional: float = 0.0 # total notional within pressure window must exceed this

    # Refined 'money flow spike' (가속도/증분/체결 수/큰 체결)
    min_flow_notional_rate: float = 0.0      # quote notional/sec in flow window (0 disables)
    min_flow_accel: float = 0.0              # quote notional/sec^2 (0 disables)
    large_trade_min_notional: float = 0.0    # only used for meta; threshold applied by stream
    min_large_trade_share: float = 0.0       # large_notional / total_notional
    min_trade_count: int = 0

    # Orderbook change (증분)
    min_ob_imb_delta: float = 0.0            # requires signed delta aligned with side

    # Mean-reversion RSI entry (default: use "cross back" to avoid catching a falling knife)
    rsi_long_trigger: float = 40.0
    rsi_short_min: float = 65.0
    rsi_short_max: float = 70.0
    use_rsi_cross: bool = True

    # Optional: require reversal candle direction
    require_reversal_candle: bool = True


def _orderbook_notional(orderbook: Any, depth: int = 10) -> float:
    try:
        bid = 0.0
        ask = 0.0
        if isinstance(orderbook, list) and orderbook and isinstance(orderbook[0], dict) and "orderbook_units" in orderbook[0]:
            # Upbit
            units = (orderbook[0].get("orderbook_units") or [])[:depth]
            for u in units:
                p = float(u.get("ask_price") or u.get("bid_price") or 0.0)
                bid += p * float(u.get("bid_size") or 0.0)
                ask += p * float(u.get("ask_size") or 0.0)
        elif isinstance(orderbook, dict) and ("bids" in orderbook or "asks" in orderbook):
            # Binance
            for p, q in (orderbook.get("bids") or [])[:depth]:
                bid += float(p) * float(q)
            for p, q in (orderbook.get("asks") or [])[:depth]:
                ask += float(p) * float(q)
        else:
            return 0.0
        return float(bid + ask)
    except Exception:
        return 0.0


def _last_1m_trade_value(df_1m: pd.DataFrame) -> float:
    try:
        if df_1m is None or df_1m.empty:
            return 0.0
        last = df_1m.iloc[-1]
        return float(last.get("volume", 0.0)) * float(last.get("close", 0.0))
    except Exception:
        return 0.0


def _get_float(last: pd.Series, key: str) -> Optional[float]:
    try:
        v = last.get(key)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except Exception:
        return None

def _clamp01(x: float) -> float:
    try:
        return float(max(0.0, min(1.0, float(x))))
    except Exception:
        return 0.0


def _norm_thr(value: float, thr: float, *, soft_scale: float = 1.0) -> float:
    """Normalize a signed value to [0,1] using threshold if provided.

    - If thr>0: score=|v|/thr clipped to 1.
    - If thr==0: soft_scale used with tanh(|v|/soft_scale) to avoid overfitting to units.
    """
    v = abs(float(value))
    if thr and thr > 0:
        return _clamp01(v / max(float(thr), 1e-12))
    # soft scaling (scale-free-ish)
    s = max(float(soft_scale), 1e-9)
    return _clamp01(math.tanh(v / s))


def _composite_setup_score(
    *,
    side: str,
    trade_pressure: float,
    orderbook_imbalance: float,
    orderbook_imbalance_delta: float,
    params: ScalpingParams,
    flow: Optional[dict],
    liq: Optional[dict],
) -> Tuple[float, Dict[str, float]]:
    """Composite score for 'money flow spike' + microstructure alignment.

    Returns (score, components). Score is positive and roughly 0~3.
    """
    side_u = (side or "").upper()
    want_long = side_u == "BUY"

    # Core alignment (always meaningful)
    tp_s = _norm_thr(trade_pressure, max(params.trade_pressure_threshold, 1e-6), soft_scale=0.25)
    ob_s = _norm_thr(orderbook_imbalance, max(params.ob_imbalance_threshold, 1e-6), soft_scale=0.20)

    # Delta alignment (optional)
    ob_d_s = 0.0
    if params.min_ob_imb_delta > 0:
        ob_d_s = _norm_thr(orderbook_imbalance_delta, params.min_ob_imb_delta, soft_scale=0.05)

    # Flow refinement
    flow_rate_s = 0.0
    flow_acc_s = 0.0
    trade_cnt_s = 0.0
    large_share_s = 0.0
    rate_z_s = 0.0
    accel_z_s = 0.0
    if flow:
        rate = float(flow.get("notional_rate", 0.0))
        acc = float(flow.get("notional_accel", 0.0))
        cnt = float(flow.get("trade_count", 0.0))
        lshare = float(flow.get("large_trade_share", 0.0))

        flow_rate_s = _norm_thr(rate, params.min_flow_notional_rate, soft_scale=max(float(flow.get("rate_ema", 0.0)), 1.0))
        flow_acc_s = _norm_thr(acc, params.min_flow_accel, soft_scale=max(abs(float(flow.get("accel_ema", 0.0))), 1.0))
        if params.min_trade_count > 0:
            trade_cnt_s = _clamp01(cnt / max(float(params.min_trade_count), 1.0))
        if params.min_large_trade_share > 0:
            large_share_s = _clamp01(lshare / max(float(params.min_large_trade_share), 1e-9))

        # EMA-normalized z (scale-free-ish)
        rate_z = float(flow.get("rate_z", 0.0))
        accel_z = float(flow.get("accel_z", 0.0))
        # Only reward z in the intended direction
        rz_dir = rate_z if want_long else -rate_z
        az_dir = accel_z if want_long else -accel_z
        rate_z_s = _clamp01(math.tanh(max(0.0, rz_dir) / 3.0))  # z~3 => strong
        accel_z_s = _clamp01(math.tanh(max(0.0, az_dir) / 3.0))

    # Liquidation bias (futures)
    liq_s = 0.0
    if liq:
        buy_liq = float(liq.get("buy_liq_notional", 0.0))
        sell_liq = float(liq.get("sell_liq_notional", 0.0))
        tot = max(buy_liq + sell_liq, 1e-9)
        bias = (buy_liq - sell_liq) / tot  # + => forced buys
        dir_bias = bias if want_long else -bias
        liq_s = _clamp01(max(0.0, dir_bias) / 0.6)  # 0.6 bias => max score

    # Weighted sum (keep simple + interpretable)
    score = (
        0.80 * tp_s
        + 0.80 * ob_s
        + 0.35 * ob_d_s
        + 0.35 * flow_rate_s
        + 0.35 * flow_acc_s
        + 0.25 * trade_cnt_s
        + 0.20 * large_share_s
        + 0.30 * rate_z_s
        + 0.30 * accel_z_s
        + 0.25 * liq_s
    )

    comps = {
        "tp": float(tp_s),
        "ob": float(ob_s),
        "ob_delta": float(ob_d_s),
        "flow_rate": float(flow_rate_s),
        "flow_accel": float(flow_acc_s),
        "trade_count": float(trade_cnt_s),
        "large_share": float(large_share_s),
        "rate_z": float(rate_z_s),
        "accel_z": float(accel_z_s),
        "liq": float(liq_s),
        "score": float(score),
    }
    return float(score), comps



def generate_scalp_signal(
    venue: str,
    symbol: str,
    last_price: float,
    df_1m: pd.DataFrame,
    orderbook: Any,
    orderbook_imbalance: float,
    orderbook_imbalance_delta: float,
    trade_pressure: float,
    trade_pressure_notional: float,
    params: ScalpingParams,
    in_position: bool,
    flow: Optional[dict] = None,
    liq: Optional[dict] = None,
) -> Signal:
    """Mean-reversion scalping entry (역추세 추종) with refined 'money flow spike' filters.

    User intent:
      - 시장이 '붙은' 상태: 거래대금/거래량/호가 유동성 필터 통과
      - 롱: RSI 40 근처 반등 + 매수 체결/호가 압력 + (선택) 거래대금 가속도/큰 체결/호가 증분
      - 숏: RSI 65~70 구간 + 매도 체결/호가 압력 + (선택) 거래대금 가속도/큰 체결/호가 증분
    """
    if in_position:
        return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "IN_POSITION"})

    meta: dict = {}

    # Liquidity: 1m trade value
    tv = _last_1m_trade_value(df_1m)
    meta["tv_1m"] = tv
    if params.min_1m_trade_value > 0 and tv < params.min_1m_trade_value:
        return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "LOW_TRADE_VALUE", **meta})

    # Liquidity: orderbook depth notional
    ob_notional = _orderbook_notional(orderbook, depth=10)
    meta["ob_notional"] = ob_notional
    if params.min_orderbook_notional > 0 and ob_notional < params.min_orderbook_notional:
        return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "LOW_ORDERBOOK", **meta})

    # Liquidity: volume surge (requires add_indicators() upstream)
    if df_1m is not None and not df_1m.empty:
        last = df_1m.iloc[-1]
        vol_surge = _get_float(last, "VOL_SURGE")
        if vol_surge is not None:
            meta["vol_surge"] = vol_surge
        if params.min_vol_surge > 0 and vol_surge is not None and vol_surge < params.min_vol_surge:
            return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "LOW_VOL_SURGE", **meta})

    # Market microstructure: spread / volatility filters
    spr = spread_bps(orderbook)
    meta["spread_bps"] = spr
    if params.max_spread_bps > 0 and spr > params.max_spread_bps:
        return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "WIDE_SPREAD", **meta})

    if df_1m is not None and not df_1m.empty:
        last = df_1m.iloc[-1]
        opx = _get_float(last, "open")
        hpx = _get_float(last, "high")
        lpx = _get_float(last, "low")
        cpx = _get_float(last, "close")
        if params.max_1m_range_pct > 0 and cpx and hpx is not None and lpx is not None:
            range_pct = float(hpx - lpx) / float(cpx) if float(cpx) != 0 else 0.0
            meta["range_pct"] = range_pct
            if range_pct > params.max_1m_range_pct:
                return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "HIGH_1M_RANGE", **meta})
        if params.max_1m_body_pct > 0 and opx and cpx:
            body_pct = abs(float(cpx - opx)) / float(opx) if float(opx) != 0 else 0.0
            meta["body_pct"] = body_pct
            if body_pct > params.max_1m_body_pct:
                return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "HIGH_1M_BODY", **meta})

    # Pressure: executed trades (momentary money flow)
    meta["tp"] = float(trade_pressure)
    meta["tp_notional"] = float(trade_pressure_notional)

    if params.min_trade_pressure_notional > 0 and trade_pressure_notional < params.min_trade_pressure_notional:
        return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "LOW_TRADE_PRESSURE_NOTIONAL", **meta})

    if params.trade_pressure_threshold > 0 and abs(float(trade_pressure)) < params.trade_pressure_threshold:
        return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "LOW_TRADE_PRESSURE", **meta})

    # Pressure: orderbook (snapshot)
    meta["ob_imb"] = float(orderbook_imbalance)
    if params.ob_imbalance_threshold > 0 and abs(float(orderbook_imbalance)) < params.ob_imbalance_threshold:
        return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "LOW_OB_IMBALANCE", **meta})

    # Orderbook delta
    meta["ob_imb_delta"] = float(orderbook_imbalance_delta)
    if params.min_ob_imb_delta > 0 and abs(float(orderbook_imbalance_delta)) < params.min_ob_imb_delta:
        return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "LOW_OB_DELTA", **meta})

    # Refined flow (optional)
    if flow:
        # Expected keys from TradeFlowBook snapshot
        meta.update({
            "flow_total": float(flow.get("total_notional", 0.0)),
            "flow_rate": float(flow.get("notional_rate", 0.0)),
            "flow_accel": float(flow.get("notional_accel", 0.0)),
            "flow_trades": int(flow.get("trade_count", 0)),
            "large_share": float(flow.get("large_trade_share", 0.0)),
            "large_trades": int(flow.get("large_trade_count", 0)),
        })

        if params.min_flow_notional_rate > 0 and meta["flow_rate"] < params.min_flow_notional_rate:
            return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "LOW_FLOW_RATE", **meta})

        if params.min_trade_count > 0 and meta["flow_trades"] < params.min_trade_count:
            return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "LOW_TRADE_COUNT", **meta})

        if params.min_large_trade_share > 0 and meta["large_share"] < params.min_large_trade_share:
            return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "LOW_LARGE_SHARE", **meta})

    # Liquidation clustering (optional; mainly futures)
    if liq:
        meta.update({
            "liq_buy_notional": float(liq.get("buy_liq_notional", 0.0)),
            "liq_sell_notional": float(liq.get("sell_liq_notional", 0.0)),
            "liq_top_buy_price": liq.get("top_buy_price"),
            "liq_top_sell_price": liq.get("top_sell_price"),
        })

    # RSI rules
    rsi = None
    rsi_prev = None
    open_px = None
    close_px = None
    if df_1m is not None and len(df_1m) >= 2:
        last = df_1m.iloc[-1]
        prev = df_1m.iloc[-2]
        rsi = _get_float(last, "RSI_14")
        rsi_prev = _get_float(prev, "RSI_14")
        open_px = _get_float(last, "open")
        close_px = _get_float(last, "close")

    if rsi is None:
        return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "NO_RSI", **meta})

    meta["rsi"] = float(rsi)
    if rsi_prev is not None:
        meta["rsi_prev"] = float(rsi_prev)

    # Decide intended direction from pressure alignment (both should agree)
    long_pressure_ok = (trade_pressure >= params.trade_pressure_threshold) and (orderbook_imbalance >= params.ob_imbalance_threshold)
    short_pressure_ok = (trade_pressure <= -params.trade_pressure_threshold) and (orderbook_imbalance <= -params.ob_imbalance_threshold)

    # Orderbook delta should also align if enabled
    if params.min_ob_imb_delta > 0:
        long_pressure_ok = long_pressure_ok and (orderbook_imbalance_delta >= params.min_ob_imb_delta)
        short_pressure_ok = short_pressure_ok and (orderbook_imbalance_delta <= -params.min_ob_imb_delta)

    # Flow accel should align if enabled
    if flow and params.min_flow_accel > 0:
        accel = float(flow.get("notional_accel", 0.0))
        long_pressure_ok = long_pressure_ok and (accel >= params.min_flow_accel)
        short_pressure_ok = short_pressure_ok and (accel <= -params.min_flow_accel)

    # Candle direction requirement (optional)
    long_candle_ok = True
    short_candle_ok = True
    if params.require_reversal_candle and open_px is not None and close_px is not None:
        long_candle_ok = close_px >= open_px
        short_candle_ok = close_px <= open_px

    # Long entry: RSI cross back above 40 (default) or RSI <= 40 (if use_rsi_cross=False)
    long_rsi_ok = False
    if params.use_rsi_cross:
        if rsi_prev is not None:
            long_rsi_ok = (rsi_prev < params.rsi_long_trigger) and (rsi >= params.rsi_long_trigger)
        else:
            long_rsi_ok = (rsi >= params.rsi_long_trigger)
    else:
        long_rsi_ok = (rsi <= params.rsi_long_trigger)

    # Short entry: RSI falls from above short_max into [short_min, short_max]
    short_rsi_ok = False
    if params.use_rsi_cross:
        if rsi_prev is not None:
            short_rsi_ok = (rsi_prev > params.rsi_short_max) and (params.rsi_short_min <= rsi <= params.rsi_short_max)
        else:
            short_rsi_ok = (params.rsi_short_min <= rsi <= params.rsi_short_max)
    else:
        short_rsi_ok = (params.rsi_short_min <= rsi <= params.rsi_short_max)

    # Final decision (composite scoring)
    if long_pressure_ok and long_rsi_ok and long_candle_ok:
        score, comps = _composite_setup_score(
            side="BUY",
            trade_pressure=float(trade_pressure),
            orderbook_imbalance=float(orderbook_imbalance),
            orderbook_imbalance_delta=float(orderbook_imbalance_delta),
            params=params,
            flow=flow,
            liq=liq,
        )
        meta["score_components"] = comps
        meta.update({"intent": "OPEN_LONG"})
        # liquidation hint: when shorts are liquidated, forced BUY often appears
        if liq and liq.get("top_buy_price") is not None:
            meta["liq_hint_price"] = float(liq["top_buy_price"])
        return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="BUY", score=float(score), meta=meta)

    if short_pressure_ok and short_rsi_ok and short_candle_ok:
        score, comps = _composite_setup_score(
            side="SELL",
            trade_pressure=float(trade_pressure),
            orderbook_imbalance=float(orderbook_imbalance),
            orderbook_imbalance_delta=float(orderbook_imbalance_delta),
            params=params,
            flow=flow,
            liq=liq,
        )
        meta["score_components"] = comps
        meta.update({"intent": "OPEN_SHORT"})
        # liquidation hint: when longs are liquidated, forced SELL often appears
        if liq and liq.get("top_sell_price") is not None:
            meta["liq_hint_price"] = float(liq["top_sell_price"])
        return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="SELL", score=float(score), meta=meta)

    return Signal(ts=utc_now(), venue=venue, symbol=symbol, side="HOLD", score=0.0, meta={"reason": "NO_SETUP", **meta})

