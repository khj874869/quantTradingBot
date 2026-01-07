from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx
import pandas as pd

# Spot REST base (api/v3)
BASE_URL_SPOT = "https://api.binance.com"
# USDⓈ-M Futures REST base (fapi)
BASE_URL_FUTURES = "https://fapi.binance.com"

# Reuse clients (connection pooling) to reduce latency and avoid churn.
# NOTE: We keep these as module-level singletons; the process is long-lived.
_client_spot: Optional[httpx.AsyncClient] = None
_client_futures: Optional[httpx.AsyncClient] = None

# AsyncClient instances are bound to the event loop they were created in.
# If the bot restarts its asyncio loop (e.g. after a crash + auto-restart),
# a previously created client can throw "Event loop is closed" on Windows.
# We track the creating loop id and recreate the client when the loop changes.
_client_spot_loop_id: Optional[int] = None
_client_futures_loop_id: Optional[int] = None


def _norm_symbol(symbol: str) -> str:
    return symbol.replace("-", "").replace("/", "").upper()


def _get_client(*, futures: bool, timeout: float) -> httpx.AsyncClient:
    global _client_spot, _client_futures, _client_spot_loop_id, _client_futures_loop_id

    loop_id: Optional[int]
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        # No running loop (shouldn't happen in our async usage); don't pin.
        loop_id = None

    if futures:
        if (
            _client_futures is None
            or _client_futures.is_closed
            or (_client_futures_loop_id is not None and loop_id is not None and _client_futures_loop_id != loop_id)
        ):
            if _client_futures is not None and not _client_futures.is_closed:
                try:
                    asyncio.create_task(_client_futures.aclose())
                except Exception:
                    pass
            _client_futures = httpx.AsyncClient(timeout=timeout)
            _client_futures_loop_id = loop_id
        return _client_futures
    if (
        _client_spot is None
        or _client_spot.is_closed
        or (_client_spot_loop_id is not None and loop_id is not None and _client_spot_loop_id != loop_id)
    ):
        if _client_spot is not None and not _client_spot.is_closed:
            try:
                asyncio.create_task(_client_spot.aclose())
            except Exception:
                pass
        _client_spot = httpx.AsyncClient(timeout=timeout)
        _client_spot_loop_id = loop_id
    return _client_spot


def _base_url(*, futures: bool, base_url: Optional[str]) -> str:
    if base_url:
        return str(base_url).rstrip("/")
    return BASE_URL_FUTURES if futures else BASE_URL_SPOT


async def fetch_binance_klines(
    symbol: str,
    interval: str,
    total: int = 1000,
    *,
    futures: bool = False,
    base_url: Optional[str] = None,
    timeout: float = 20.0,
) -> pd.DataFrame:
    """Fetch Binance klines with backwards pagination.

    - Spot:    GET /api/v3/klines
    - Futures: GET /fapi/v1/klines

    interval: 1m,5m,15m,4h,1d,1w,1M (Binance native). For 10m use 1m then resample.
    total: number of bars to fetch (loops internally; max 1000 per request)
    """
    sym = _norm_symbol(symbol)
    client = _get_client(futures=futures, timeout=timeout)
    out: list[list[Any]] = []

    remaining = int(total)
    end_time: int | None = None
    path = "/fapi/v1/klines" if futures else "/api/v3/klines"
    url = _base_url(futures=futures, base_url=base_url) + path

    while remaining > 0:
        limit = min(1000, remaining)
        params: dict[str, Any] = {
            "symbol": sym,
            "interval": interval,
            "limit": limit,
        }
        if end_time is not None:
            params["endTime"] = end_time

        r = await client.get(url, params=params)
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

    if not out:
        return pd.DataFrame()

    rows = []
    for k in out:
        # kline format: [ openTime, open, high, low, close, volume, closeTime, quoteVolume, trades, ...]
        ts = pd.to_datetime(int(k[0]), unit="ms", utc=True)
        rows.append(
            {
                "ts": ts,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        )

    df = pd.DataFrame(rows)
    df = df.sort_values("ts").drop_duplicates("ts").set_index("ts")
    return df


async def fetch_binance_klines_latest(
    symbol: str,
    interval: str,
    limit: int = 500,
    *,
    futures: bool = False,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> pd.DataFrame:
    """Fetch the latest N klines in a single request.

    This is used by the live loop for incremental updates to avoid repeatedly
    downloading hundreds of bars every few seconds.
    """
    sym = _norm_symbol(symbol)
    client = _get_client(futures=futures, timeout=timeout)
    path = "/fapi/v1/klines" if futures else "/api/v3/klines"
    url = _base_url(futures=futures, base_url=base_url) + path

    r = await client.get(url, params={"symbol": sym, "interval": interval, "limit": int(limit)})
    r.raise_for_status()
    data = r.json()
    if not data:
        return pd.DataFrame()

    rows = []
    for k in data:
        ts = pd.to_datetime(int(k[0]), unit="ms", utc=True)
        rows.append(
            {
                "ts": ts,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        )
    df = pd.DataFrame(rows)
    df = df.sort_values("ts").drop_duplicates("ts").set_index("ts")
    return df


async def fetch_binance_orderbook(
    symbol: str,
    limit: int = 50,
    *,
    futures: bool = False,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> Any:
    sym = _norm_symbol(symbol)
    client = _get_client(futures=futures, timeout=timeout)
    path = "/fapi/v1/depth" if futures else "/api/v3/depth"
    url = _base_url(futures=futures, base_url=base_url) + path

    r = await client.get(url, params={"symbol": sym, "limit": int(limit)})
    r.raise_for_status()
    return r.json()


async def fetch_binance_recent_trades(
    symbol: str,
    limit: int = 500,
    *,
    futures: bool = False,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> list[dict]:
    """Public recent trades.

    Spot:   GET /api/v3/trades
    Futures: GET /fapi/v1/trades (different payload but includes 'time' and 'isBuyerMaker')
    """
    s = _norm_symbol(symbol)
    client = _get_client(futures=futures, timeout=timeout)
    path = "/fapi/v1/trades" if futures else "/api/v3/trades"
    url = _base_url(futures=futures, base_url=base_url) + path

    r = await client.get(url, params={"symbol": s, "limit": int(limit)})
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []
