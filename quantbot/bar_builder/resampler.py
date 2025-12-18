from __future__ import annotations
import pandas as pd

def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = pd.DataFrame(index=df.resample(rule).first().index)
    out["open"] = df["open"].resample(rule).first()
    out["high"] = df["high"].resample(rule).max()
    out["low"] = df["low"].resample(rule).min()
    out["close"] = df["close"].resample(rule).last()
    out["volume"] = df["volume"].resample(rule).sum()
    out = out.dropna(subset=["open","high","low","close"])
    return out

RULE_MAP = {
    "5m": "5min",
    "10m": "10min",
    "15m": "15min",
    "240m": "240min",
    "1d": "1D",
    "1w": "1W",
    "1M": "1M",
}
