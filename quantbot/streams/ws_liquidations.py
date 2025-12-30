from __future__ import annotations

import asyncio
import json
from typing import Optional

import websockets

from quantbot.streams.liquidations import LiquidationClusterBook


async def run_binance_futures_liquidation_stream(
    symbol: str,
    book: LiquidationClusterBook,
    stop_event: asyncio.Event,
    ws_url: str = "wss://fstream.binance.com/ws/!forceOrder@arr",
) -> None:
    """Listen to Binance Futures forced liquidation stream and feed into LiquidationClusterBook."""

    sym = symbol.lower()
    while not stop_event.is_set():
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                while not stop_event.is_set():
                    msg = await ws.recv()
                    data = json.loads(msg)
                    # expected: {"e":"forceOrder", ... , "o": {"s":"BTCUSDT", "S":"SELL|BUY", "p":"...", "q":"..."}}
                    if isinstance(data, dict) and data.get("e") == "forceOrder":
                        o = data.get("o") or {}
                        s = str(o.get("s") or "").lower()
                        if s != sym:
                            continue
                        side = str(o.get("S") or "").upper()
                        price = float(o.get("p") or 0.0)
                        qty = float(o.get("q") or 0.0)
                        ts = int(data.get("E") or 0)
                        if price > 0 and qty > 0:
                            book.add_event(symbol, ts, side, price, qty)
                    elif isinstance(data, dict) and isinstance(data.get("data"), list):
                        # some gateways may wrap array
                        for ev in data["data"]:
                            if not isinstance(ev, dict) or ev.get("e") != "forceOrder":
                                continue
                            o = ev.get("o") or {}
                            s = str(o.get("s") or "").lower()
                            if s != sym:
                                continue
                            side = str(o.get("S") or "").upper()
                            price = float(o.get("p") or 0.0)
                            qty = float(o.get("q") or 0.0)
                            ts = int(ev.get("E") or 0)
                            if price > 0 and qty > 0:
                                book.add_event(symbol, ts, side, price, qty)
        except Exception:
            await asyncio.sleep(2.0)
