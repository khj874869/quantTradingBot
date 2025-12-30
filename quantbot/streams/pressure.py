from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Tuple
import time


@dataclass
class PressureSnapshot:
    pressure: float          # [-1, 1]
    notional: float          # buy+sell within window
    buy_notional: float
    sell_notional: float
    trade_count: int
    last_update_ms: int
    staleness_sec: float


class _RollingPressure:
    """Rolling executed-trade pressure over a time window.

    Stores (ts_ms, notional, is_buy) and maintains rolling sums.
    """
    def __init__(self, window_sec: int):
        self.window_ms = max(1, int(window_sec) * 1000)
        self._q: Deque[Tuple[int, float, bool]] = deque()
        self.buy = 0.0
        self.sell = 0.0
        self.count = 0
        self.last_update_ms = 0

    def set_window(self, window_sec: int) -> None:
        self.window_ms = max(1, int(window_sec) * 1000)

    def add(self, ts_ms: int, notional: float, is_buy: bool) -> None:
        if ts_ms <= 0 or notional <= 0:
            return
        self._q.append((ts_ms, float(notional), bool(is_buy)))
        if is_buy:
            self.buy += float(notional)
        else:
            self.sell += float(notional)
        self.count += 1
        self.last_update_ms = max(self.last_update_ms, int(ts_ms))
        self._evict(ts_ms)

    def _evict(self, now_ms: int) -> None:
        cutoff = int(now_ms) - self.window_ms
        while self._q and self._q[0][0] < cutoff:
            ts, notional, is_buy = self._q.popleft()
            if is_buy:
                self.buy -= notional
            else:
                self.sell -= notional
            self.count -= 1
        if self.buy < 0:
            self.buy = 0.0
        if self.sell < 0:
            self.sell = 0.0
        if self.count < 0:
            self.count = 0

    def snapshot(self, now_ms: int | None = None) -> PressureSnapshot:
        now_ms = int(now_ms or time.time() * 1000)
        self._evict(now_ms)
        total = self.buy + self.sell
        if total <= 0:
            pressure = 0.0
        else:
            pressure = (self.buy - self.sell) / total
        pressure = float(max(-1.0, min(1.0, pressure)))
        staleness = max(0.0, (now_ms - self.last_update_ms) / 1000.0) if self.last_update_ms else float("inf")
        return PressureSnapshot(
            pressure=pressure,
            notional=float(total),
            buy_notional=float(self.buy),
            sell_notional=float(self.sell),
            trade_count=int(self.count),
            last_update_ms=int(self.last_update_ms),
            staleness_sec=float(staleness),
        )


class TradePressureBook:
    """Per-symbol executed-trade pressure cache, updated by WebSocket streams."""
    def __init__(self, window_sec: int = 15):
        self._window_sec = int(window_sec)
        self._by_symbol: Dict[str, _RollingPressure] = {}

    def set_window(self, window_sec: int) -> None:
        self._window_sec = int(window_sec)
        for rp in self._by_symbol.values():
            rp.set_window(window_sec)

    def add_trade(self, symbol: str, ts_ms: int, price: float, qty: float, is_buy: bool) -> None:
        if not symbol:
            return
        rp = self._by_symbol.get(symbol)
        if rp is None:
            rp = _RollingPressure(self._window_sec)
            self._by_symbol[symbol] = rp
        notional = float(price) * float(qty)
        rp.add(int(ts_ms), float(notional), bool(is_buy))

    def snapshot(self, symbol: str) -> PressureSnapshot:
        rp = self._by_symbol.get(symbol)
        if rp is None:
            return PressureSnapshot(pressure=0.0, notional=0.0, buy_notional=0.0, sell_notional=0.0,
                                    trade_count=0, last_update_ms=0, staleness_sec=float("inf"))
        return rp.snapshot()
