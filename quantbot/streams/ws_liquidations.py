from __future__ import annotations

import asyncio
import json
from typing import Iterable, Union

import websockets

from quantbot.streams.liquidations import LiquidationClusterBook


def _normalize_symbols(symbol: Union[str, Iterable[str]]) -> list[str]:
    if isinstance(symbol, str):
        symbols = [symbol]
    else:
        symbols = list(symbol)

    # 빈 값 제거 + 소문자
    symbols = [s.strip().lower() for s in symbols if isinstance(s, str) and s.strip()]
    if not symbols:
        raise ValueError("No symbols provided for liquidation stream")
    return symbols


def _iter_forceorder_events(payload):
    """
    Binance forceOrder payload can be:
    - dict for a single event: {"e":"forceOrder", "o":{...}}
    - list of events: [{...}, {...}]
    - wrapped dict: {"data":[{...}, ...]}   (some gateways)
    """
    if isinstance(payload, dict):
        # Wrapped array
        if isinstance(payload.get("data"), list):
            for ev in payload["data"]:
                if isinstance(ev, dict):
                    yield ev
            return
        # Single event dict
        yield payload
        return

    if isinstance(payload, list):
        for ev in payload:
            if isinstance(ev, dict):
                yield ev
        return


async def run_binance_futures_liquidation_stream(
    symbol: Union[str, Iterable[str]],     # ✅ str 또는 list/iterable 모두 허용
    book: LiquidationClusterBook,
    stop_event: asyncio.Event,
    ws_url: str = "wss://fstream.binance.com/ws/!forceOrder@arr",
) -> None:
    """Listen to Binance Futures forced liquidation stream and feed into LiquidationClusterBook.

    - ws_url 기본값(!forceOrder@arr)은 '전체 심볼' 강제청산 이벤트가 배열로 날아옵니다.
    - symbol이 str이면 해당 심볼만, list/iterable이면 그 목록만 필터링합니다.
    """

    allowed = set(_normalize_symbols(symbol))  # e.g. {"btcusdt", "ethusdt"}

    while not stop_event.is_set():
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                while not stop_event.is_set():
                    msg = await ws.recv()
                    payload = json.loads(msg)

                    for ev in _iter_forceorder_events(payload):
                        if ev.get("e") != "forceOrder":
                            continue

                        o = ev.get("o") or {}
                        s = str(o.get("s") or "").lower()
                        if not s:
                            continue

                        # ✅ 원하는 심볼만 통과
                        if allowed and s not in allowed:
                            continue

                        side = str(o.get("S") or "").upper()
                        try:
                            price = float(o.get("p") or 0.0)
                            qty = float(o.get("q") or 0.0)
                        except Exception:
                            continue

                        ts = int(ev.get("E") or 0)

                        if price > 0 and qty > 0:
                            # ✅ book에는 '실제 이벤트 심볼'을 넣어야 함 (입력 symbol이 list일 수 있으므로)
                            book.add_event(s.upper(), ts, side, price, qty)

        except Exception:
            # 재연결
            await asyncio.sleep(2.0)
