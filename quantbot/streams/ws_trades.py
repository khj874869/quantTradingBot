from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Optional

import websockets

from quantbot.streams.pressure import TradePressureBook
from quantbot.streams.flow import TradeFlowBook


@dataclass
class StreamConfig:
    venue: str
    symbols: list[str]
    ping_interval: int = 20
    reconnect_min_sec: float = 1.0
    reconnect_max_sec: float = 30.0
    # For Binance, choose spot vs futures streams
    binance_futures: bool = False


def _now_ms() -> int:
    return int(time.time() * 1000)


def _binance_ws_symbol(symbol: str) -> str:
    return symbol.lower()


async def run_upbit_trade_stream(
    cfg: StreamConfig,
    pressure: TradePressureBook,
    stop: asyncio.Event,
    flow: Optional[TradeFlowBook] = None,
) -> None:
    """Upbit trade stream."""
    url = "wss://api.upbit.com/websocket/v1"
    sub = [
        {"ticket": f"quantbot-{_now_ms()}"},
        {"type": "trade", "codes": cfg.symbols, "isOnlyRealtime": True},
    ]

    backoff = cfg.reconnect_min_sec
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=cfg.ping_interval, close_timeout=5) as ws:
                await ws.send(json.dumps(sub))
                backoff = cfg.reconnect_min_sec
                while not stop.is_set():
                    msg = await ws.recv()
                    if msg is None:
                        break
                    if isinstance(msg, (bytes, bytearray)):
                        try:
                            payload = json.loads(msg.decode("utf-8"))
                        except Exception:
                            continue
                    else:
                        try:
                            payload = json.loads(str(msg))
                        except Exception:
                            continue

                    code = payload.get("code") or payload.get("market") or ""
                    px = float(payload.get("trade_price") or 0.0)
                    qty = float(payload.get("trade_volume") or 0.0)
                    ts = int(payload.get("trade_timestamp") or payload.get("timestamp") or 0)
                    side = (payload.get("ask_bid") or "").upper()
                    is_buy = True if side == "BID" else False if side == "ASK" else True
                    if code and px > 0 and qty > 0:
                        pressure.add_trade(code, ts, px, qty, is_buy=is_buy)
                        if flow is not None:
                            flow.add_trade(code, ts, px, qty, is_buy=is_buy)
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(cfg.reconnect_max_sec, backoff * 1.8 + 0.2)


async def run_binance_trade_stream(
    cfg: StreamConfig,
    pressure: TradePressureBook,
    stop: asyncio.Event,
    flow: Optional[TradeFlowBook] = None,
) -> None:
    """Binance spot/futures trade stream."""
    streams = "/".join([f"{_binance_ws_symbol(s)}@trade" for s in cfg.symbols])

    if cfg.binance_futures:
        base = "wss://fstream.binance.com"
        url = f"{base}/stream?streams={streams}" if len(cfg.symbols) > 1 else f"{base}/ws/{streams}"
    else:
        base = "wss://stream.binance.com:9443"
        url = f"{base}/stream?streams={streams}" if len(cfg.symbols) > 1 else f"{base}/ws/{streams}"

    backoff = cfg.reconnect_min_sec
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=cfg.ping_interval, close_timeout=5) as ws:
                backoff = cfg.reconnect_min_sec
                while not stop.is_set():
                    msg = await ws.recv()
                    if msg is None:
                        break
                    try:
                        payload = json.loads(msg)
                    except Exception:
                        continue
                    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
                    if not isinstance(data, dict):
                        continue

                    symbol = data.get("s") or ""
                    px = float(data.get("p") or 0.0)
                    qty = float(data.get("q") or 0.0)
                    ts = int(data.get("T") or 0)
                    is_buyer_maker = bool(data.get("m"))
                    is_buy = False if is_buyer_maker else True  # taker buy => buy pressure
                    if symbol and px > 0 and qty > 0:
                        pressure.add_trade(symbol, ts, px, qty, is_buy=is_buy)
                        if flow is not None:
                            flow.add_trade(symbol, ts, px, qty, is_buy=is_buy)
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(cfg.reconnect_max_sec, backoff * 1.8 + 0.2)


async def start_trade_stream(
    cfg: StreamConfig,
    pressure: TradePressureBook,
    flow: Optional[TradeFlowBook] = None,
) -> asyncio.Event:
    """Start a background trade stream task."""
    stop = asyncio.Event()
    venue = (cfg.venue or "").lower()
    if venue == "upbit":
        asyncio.create_task(run_upbit_trade_stream(cfg, pressure, stop, flow))
    elif venue in {"binance", "binance_futures"}:
        if venue == "binance_futures":
            cfg.binance_futures = True
        asyncio.create_task(run_binance_trade_stream(cfg, pressure, stop, flow))
    else:
        stop.set()
    return stop
