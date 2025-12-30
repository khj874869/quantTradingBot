from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from typing import Deque, Dict, Optional, Tuple


@dataclass
class LiquidationSnapshot:
    window_sec: float
    buy_liq_notional: float    # forced BUY orders (often short liquidations)
    sell_liq_notional: float   # forced SELL orders (often long liquidations)
    top_buy_price: Optional[float]
    top_sell_price: Optional[float]
    top_buy_bucket_notional: float
    top_sell_bucket_notional: float


class LiquidationClusterBook:
    """Keeps a rolling window of liquidation events and clusters by price bucket.

    Designed for Binance Futures forceOrder stream:
    - side == BUY  => forced BUY (typically short liquidation)
    - side == SELL => forced SELL (typically long liquidation)
    """

    def __init__(self, window_sec: float = 30.0, bucket_bps: float = 10.0):
        self.window_sec = float(window_sec)
        self.bucket_bps = float(bucket_bps)
        self._events: Dict[str, Deque[Tuple[int, str, float, float]]] = {}
        self._buckets: Dict[str, Dict[str, Dict[float, float]]] = {}  # symbol -> side -> bucket_price -> notional

    def _bucket(self, price: float) -> float:
        # bucket size as price * bucket_bps
        step = max(price * (self.bucket_bps / 10_000.0), 1e-9)
        return round(price / step) * step

    def add_event(self, symbol: str, ts_ms: int, side: str, price: float, qty: float) -> None:
        side = side.upper()
        dq = self._events.setdefault(symbol, deque())
        dq.append((int(ts_ms), side, float(price), float(qty)))

    def _trim(self, symbol: str, now_ms: int) -> None:
        dq = self._events.get(symbol)
        if not dq:
            return
        cutoff = int(now_ms - self.window_sec * 1000.0)
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def snapshot(self, symbol: str, now_ms: int) -> LiquidationSnapshot:
        dq = self._events.get(symbol) or deque()
        self._trim(symbol, now_ms)

        buckets_buy: Dict[float, float] = {}
        buckets_sell: Dict[float, float] = {}
        buy_total = 0.0
        sell_total = 0.0

        for ts, side, px, q in dq:
            notional = px * q
            b = self._bucket(px)
            if side == "BUY":
                buy_total += notional
                buckets_buy[b] = buckets_buy.get(b, 0.0) + notional
            elif side == "SELL":
                sell_total += notional
                buckets_sell[b] = buckets_sell.get(b, 0.0) + notional

        top_buy_price = max(buckets_buy, key=buckets_buy.get) if buckets_buy else None
        top_sell_price = max(buckets_sell, key=buckets_sell.get) if buckets_sell else None
        top_buy_notional = buckets_buy.get(top_buy_price, 0.0) if top_buy_price is not None else 0.0
        top_sell_notional = buckets_sell.get(top_sell_price, 0.0) if top_sell_price is not None else 0.0

        return LiquidationSnapshot(
            window_sec=self.window_sec,
            buy_liq_notional=buy_total,
            sell_liq_notional=sell_total,
            top_buy_price=float(top_buy_price) if top_buy_price is not None else None,
            top_sell_price=float(top_sell_price) if top_sell_price is not None else None,
            top_buy_bucket_notional=top_buy_notional,
            top_sell_bucket_notional=top_sell_notional,
        )

    def hint_price_for_side(self, symbol: str, side: str, now_ms: int) -> Optional[float]:
        snap = self.snapshot(symbol, now_ms)
        side = side.upper()
        if side == "BUY":
            return snap.top_buy_price
        return snap.top_sell_price
