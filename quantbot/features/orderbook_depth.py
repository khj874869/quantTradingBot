from __future__ import annotations

from typing import Any

# Reuse the robust parsers from orderbook.py
from quantbot.features.orderbook import _iter_levels


def orderbook_depth_notional(orderbook: Any, depth: int = 10) -> float:
    """Sum bid+ask notional across top `depth` levels.

    Returns quote-currency notional for both sides combined.
    """
    try:
        tot = 0.0
        for p, q in _iter_levels(orderbook, side="bid", depth=depth):
            tot += float(p) * float(q)
        for p, q in _iter_levels(orderbook, side="ask", depth=depth):
            tot += float(p) * float(q)
        return float(tot)
    except Exception:
        return 0.0
