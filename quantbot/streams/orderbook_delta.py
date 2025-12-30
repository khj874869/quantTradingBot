from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class OrderbookDeltaSnapshot:
    bid_notional: float
    ask_notional: float
    bid_notional_delta: float
    ask_notional_delta: float
    imbalance_delta: float


class OrderbookDeltaBook:
    """Track short-horizon orderbook notional deltas (bids vs asks)."""

    def __init__(self, depth_levels: int = 10):
        self.depth_levels = int(depth_levels)
        self._prev: Dict[str, Tuple[float, float]] = {}  # symbol -> (bid_notional, ask_notional)

    @staticmethod
    def _notional(levels: List[List[str | float]], n: int) -> float:
        total = 0.0
        for px, qty in levels[:n]:
            total += float(px) * float(qty)
        return total

    def update(self, symbol: str, ob_raw: dict) -> OrderbookDeltaSnapshot:
        bids = ob_raw.get("bids") or []
        asks = ob_raw.get("asks") or []
        bid_notional = self._notional(bids, self.depth_levels)
        ask_notional = self._notional(asks, self.depth_levels)

        prev = self._prev.get(symbol)
        if prev:
            bid_delta = bid_notional - prev[0]
            ask_delta = ask_notional - prev[1]
        else:
            bid_delta = 0.0
            ask_delta = 0.0

        prev_imb = (prev[0] - prev[1]) if prev else 0.0
        imb = bid_notional - ask_notional
        imb_delta = imb - prev_imb

        self._prev[symbol] = (bid_notional, ask_notional)

        return OrderbookDeltaSnapshot(
            bid_notional=bid_notional,
            ask_notional=ask_notional,
            bid_notional_delta=bid_delta,
            ask_notional_delta=ask_delta,
            imbalance_delta=imb_delta,
        )
