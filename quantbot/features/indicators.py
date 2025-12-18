from __future__ import annotations

import pandas as pd
import numpy as np


def _sma(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length, min_periods=length).mean()


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder RSI.

    Uses exponential smoothing (RMA) to match common charting platforms.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    # Wilder smoothing (RMA) == EMA with alpha=1/length
    alpha = 1.0 / float(length)
    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=length).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["SMA_30"] = _sma(df["close"], 30)
    df["SMA_120"] = _sma(df["close"], 120)
    df["SMA_200"] = _sma(df["close"], 200)
    # 864d ~= 3.4y trading days (calendar ~ 4y). Needs long history.
    df["SMA_864"] = _sma(df["close"], 864)
    df["RSI_14"] = _rsi(df["close"], 14)

    # Bollinger Bands (20, 2)
    bb_mid = _sma(df["close"], 20)
    bb_std = df["close"].rolling(20, min_periods=20).std()
    df["BBM_20_2"] = bb_mid
    df["BBU_20_2"] = bb_mid + 2 * bb_std
    df["BBL_20_2"] = bb_mid - 2 * bb_std

    df["VOL_SMA_5"] = _sma(df["volume"], 5)
    df["VOL_SMA_20"] = _sma(df["volume"], 20)
    df["VOL_SURGE"] = (df["volume"] / df["VOL_SMA_5"]).replace([float("inf")], pd.NA)
    return df

def inverse_alignment(last: pd.Series) -> bool:
    keys = ["SMA_30","SMA_120","SMA_200","SMA_864"]
    if any(pd.isna(last.get(k)) for k in keys):
        return False
    return (last["close"] < last["SMA_30"] < last["SMA_120"] < last["SMA_200"] < last["SMA_864"])

def fibonacci_levels(df: pd.DataFrame, lookback: int = 60) -> dict:
    sub = df.tail(lookback)
    recent_high = float(sub["high"].max())
    recent_low = float(sub["low"].min())
    diff = recent_high - recent_low
    return {
        "low": recent_low,
        "high": recent_high,
        "0.382": recent_low + diff * 0.382,
        "0.5": recent_low + diff * 0.5,
        "0.618": recent_low + diff * 0.618,
    }
