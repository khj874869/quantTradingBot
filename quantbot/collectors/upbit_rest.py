from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx
import pandas as pd


BASE_URL = "https://api.upbit.com"


def _to_param_from_ts(ts: pd.Timestamp) -> str:
    # Upbit accepts `to` as ISO8601 (UTC) like 2025-12-18T00:00:00Z
    t = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


async def fetch_upbit_candles(market: str, tf: str, total: int = 200) -> pd.DataFrame:
    """Fetch Upbit candles for tf.

    tf: 1m,5m,10m,15m,240m,1d,1w,1M
    total: number of bars to fetch (loops internally; Upbit max 200 per request)
    """
    tf = tf.strip()
    if tf.endswith("m"):
        unit = int(tf[:-1])
        path = f"/v1/candles/minutes/{unit}"
    elif tf == "1d":
        path = "/v1/candles/days"
    elif tf == "1w":
        path = "/v1/candles/weeks"
    elif tf == "1M":
        path = "/v1/candles/months"
    else:
        raise ValueError(f"Unsupported tf for Upbit: {tf}")

    client = httpx.AsyncClient(timeout=20)
    out: list[dict[str, Any]] = []
    to: str | None = None

    try:
        remaining = int(total)
        while remaining > 0:
            count = min(200, remaining)
            params: dict[str, Any] = {"market": market, "count": count}
            if to:
                params["to"] = to
            r = await client.get(BASE_URL + path, params=params, headers={"accept": "application/json"})
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            out.extend(data)

            # next page: oldest candle time
            oldest = data[-1].get("candle_date_time_utc")
            if not oldest:
                break
            # subtract 1s to avoid duplication
            dt = pd.to_datetime(oldest).tz_localize("UTC")
            to = _to_param_from_ts(dt - pd.Timedelta(seconds=1))
            remaining -= len(data)
            await asyncio.sleep(0.05)
    finally:
        await client.aclose()

    # Upbit returns newest first; convert to ascending
    rows = []
    for d in out:
        ts = pd.to_datetime(d["candle_date_time_utc"]).tz_localize("UTC")
        rows.append({
            "ts": ts,
            "open": float(d["opening_price"]),
            "high": float(d["high_price"]),
            "low": float(d["low_price"]),
            "close": float(d["trade_price"]),
            "volume": float(d["candle_acc_trade_volume"]),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("ts").drop_duplicates("ts").set_index("ts")
    return df


async def fetch_upbit_orderbook(market: str) -> Any:
    client = httpx.AsyncClient(timeout=10)
    try:
        r = await client.get(BASE_URL + "/v1/orderbook", params={"markets": market})
        r.raise_for_status()
        return r.json()
    finally:
        await client.aclose()
