from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pandas as pd


BASE_URL = "https://api.binance.com"


def _norm_symbol(symbol: str) -> str:
    return symbol.replace("-", "").replace("/", "").upper()


async def fetch_binance_klines(symbol: str, interval: str, total: int = 1000) -> pd.DataFrame:
    """Fetch Binance klines.

    interval: 1m,5m,15m,4h,1d,1w,1M (Binance native). For 10m use 1m then resample.
    total: number of bars to fetch (loops internally; max 1000 per request)
    """
    sym = _norm_symbol(symbol)
    client = httpx.AsyncClient(timeout=20)
    out: list[list[Any]] = []

    try:
        remaining = int(total)
        end_time: int | None = None
        while remaining > 0:
            limit = min(1000, remaining)
            params: dict[str, Any] = {
                "symbol": sym,
                "interval": interval,
                "limit": limit,
            }
            if end_time is not None:
                params["endTime"] = end_time
            r = await client.get(BASE_URL + "/api/v3/klines", params=params)
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            out.extend(data)

            # next page: earliest open time - 1ms
            oldest_open = int(data[0][0])
            end_time = oldest_open - 1
            remaining -= len(data)
            await asyncio.sleep(0.05)

    finally:
        await client.aclose()

    if not out:
        return pd.DataFrame()

    # Binance returns oldest->newest for each call; our pagination pushes older data too.
    rows = []
    for k in out:
        # kline format: [ openTime, open, high, low, close, volume, closeTime, quoteVolume, trades, ...]
        ts = pd.to_datetime(int(k[0]), unit="ms", utc=True)
        rows.append({
            "ts": ts,
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("ts").drop_duplicates("ts").set_index("ts")
    return df


async def fetch_binance_orderbook(symbol: str, limit: int = 50) -> Any:
    sym = _norm_symbol(symbol)
    client = httpx.AsyncClient(timeout=10)
    try:
        r = await client.get(BASE_URL + "/api/v3/depth", params={"symbol": sym, "limit": limit})
        r.raise_for_status()
        return r.json()
    finally:
        await client.aclose()
