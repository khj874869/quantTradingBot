from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone

def generate_1m_series(symbol: str, minutes: int = 60*24*30, start: datetime | None = None):
    start = start or (datetime.now(timezone.utc) - timedelta(minutes=minutes))
    ts = pd.date_range(start=start, periods=minutes, freq="1min", tz="UTC")
    px = 100.0 + np.cumsum(np.random.normal(0, 0.05, size=len(ts)))
    vol = np.random.lognormal(mean=2.0, sigma=0.4, size=len(ts))
    df = pd.DataFrame(index=ts)
    df["open"] = px
    df["high"] = px + np.abs(np.random.normal(0, 0.07, size=len(ts)))
    df["low"] = px - np.abs(np.random.normal(0, 0.07, size=len(ts)))
    df["close"] = px + np.random.normal(0, 0.03, size=len(ts))
    df["volume"] = vol
    df["high"] = df[["open","close","high"]].max(axis=1)
    df["low"] = df[["open","close","low"]].min(axis=1)
    return df
