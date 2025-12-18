from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any
import pandas as pd

from quantbot.common.types import Signal, Venue
from quantbot.features.indicators import fibonacci_levels, inverse_alignment

@dataclass
class BlenderWeights:
    trend: float = 1.0
    rsi: float = 1.0
    volume: float = 1.0
    news: float = 1.0
    orderbook: float = 1.0  # hook
    threshold_buy: float = 3.0
    threshold_sell: float = 2.5

def _trend_score(last_d: pd.Series) -> float:
    score = 0.0
    #  MA(30/120/200/864) 역배열: 장기 하락추세 + 바닥권 후보
    if inverse_alignment(last_d):
        score += 1.5

    # 단기 반등(30일선 회복) 가산점
    if not pd.isna(last_d.get("SMA_30")) and last_d["close"] > last_d["SMA_30"]:
        score += 0.5

    # 볼린저 하단 밴드 근처(과매도) 가산점
    bbl = last_d.get("BBL_20_2.0")
    if bbl is not None and not pd.isna(bbl) and float(bbl) > 0:
        if (float(last_d["close"]) - float(bbl)) / float(bbl) <= 0.01:
            score += 0.5
    return score

def _rsi_score(last_tf: pd.Series) -> float:
    rsi = last_tf.get("RSI_14")
    if pd.isna(rsi):
        return 0.0
    rsi = float(rsi)
    if rsi <= 30:
        return 0.5
    if 30 < rsi < 45:
        return 1.0
    if rsi >= 70:
        return -1.0
    return 0.0

def _volume_score(last_d: pd.Series) -> float:
    vs = last_d.get("VOL_SURGE")
    if pd.isna(vs):
        return 0.0
    vs = float(vs)
    if vs >= 2.0:
        return 1.0
    if vs >= 1.5:
        return 0.5
    return 0.0

def _fib_score(df_d: pd.DataFrame) -> float:
    fib = fibonacci_levels(df_d, lookback=60)
    px = float(df_d["close"].iloc[-1])
    lvl = fib["0.618"]
    if lvl <= 0:
        return 0.0
    if abs(px - lvl) / lvl <= 0.01:
        return 1.0
    return 0.0

def generate_signal(
    venue: Venue,
    symbol: str,
    ts,
    df_daily: pd.DataFrame,
    df_entry: pd.DataFrame,
    news_score: float = 0.0,
    orderbook_score: float = 0.0,
    w: BlenderWeights = BlenderWeights(),
    tf_context: dict[str, pd.DataFrame] | None = None,
) -> Signal:
    '''
    df_daily: daily timeframe with indicators
    df_entry: entry timeframe (e.g. 15m) with RSI etc
    '''
    last_d = df_daily.iloc[-1]
    last_e = df_entry.iloc[-1]

    s_trend = _trend_score(last_d)
    s_rsi = _rsi_score(last_e)
    s_vol = _volume_score(last_d)
    s_fib = _fib_score(df_daily)

    # Multi-timeframe confirmation (5m/10m/240m/1w/1M ...)
    mtf = 0.0
    tf_context = tf_context or {}
    for tf, d in tf_context.items():
        if d is None or d.empty:
            continue
        last = d.iloc[-1]
        # Light-touch confirmation: oversold on lower TF, trend on higher TF
        rsi = float(last.get("RSI_14", 50.0) or 50.0)
        if rsi <= 30:
            mtf += 0.25
        elif rsi >= 70:
            mtf -= 0.25
        if inverse_alignment(last):
            mtf += 0.25
        if not pd.isna(last.get("SMA_30")) and last.get("close") > last.get("SMA_30"):
            mtf += 0.10

    score = (w.trend*s_trend) + (w.rsi*s_rsi) + (w.volume*s_vol) + (w.news*news_score) + (w.orderbook*orderbook_score) + s_fib + mtf

    side = "HOLD"
    if score >= w.threshold_buy:
        side = "BUY"
    elif score <= -w.threshold_sell:
        side = "SELL"

    meta: Dict[str, Any] = {
        "score_breakdown": {
            "trend": s_trend,
            "rsi": s_rsi,
            "volume": s_vol,
            "news": news_score,
            "orderbook": orderbook_score,
            "fib": s_fib,
            "mtf": mtf,
        }
    }
    return Signal(ts=ts, venue=venue, symbol=symbol, side=side, score=float(score), meta=meta)
