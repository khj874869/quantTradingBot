from __future__ import annotations

from typing import Any


def orderbook_imbalance_score(orderbook: Any, depth: int = 10) -> float:
    """Compute a simple orderbook imbalance score in [-1, 1].

    Supported formats:
      - Upbit: list[{'orderbook_units': [{'bid_size','ask_size', ...}, ...]}]
      - Binance: {'bids': [[price,qty],...], 'asks': [[price,qty],...]}

    The score is (bid_notional - ask_notional) / (bid_notional + ask_notional).
    """
    try:
        bid_notional = 0.0
        ask_notional = 0.0

        if isinstance(orderbook, list) and orderbook:
            # Upbit
            units = orderbook[0].get("orderbook_units") or []
            for u in units[:depth]:
                bid_sz = float(u.get("bid_size") or 0.0)
                ask_sz = float(u.get("ask_size") or 0.0)
                bid_px = float(u.get("bid_price") or 0.0)
                ask_px = float(u.get("ask_price") or 0.0)
                bid_notional += bid_sz * bid_px
                ask_notional += ask_sz * ask_px
        elif isinstance(orderbook, dict) and ("bids" in orderbook and "asks" in orderbook):
            # Binance
            for p, q in (orderbook.get("bids") or [])[:depth]:
                bid_notional += float(p) * float(q)
            for p, q in (orderbook.get("asks") or [])[:depth]:
                ask_notional += float(p) * float(q)
        else:
            return 0.0

        denom = bid_notional + ask_notional
        if denom <= 0:
            return 0.0
        return max(-1.0, min(1.0, (bid_notional - ask_notional) / denom))
    except Exception:
        return 0.0
