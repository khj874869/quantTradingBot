from __future__ import annotations

from typing import Any, Iterable, Tuple


def _lvl_to_pq(lvl: Any, *, side: str = "") -> Tuple[float, float] | None:
    """Convert a single orderbook level to (price, qty)."""
    try:
        if isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
            return float(lvl[0]), float(lvl[1])
        if isinstance(lvl, dict):
            # Common keys
            px_keys = ["price", "px", "p", "bid_price", "ask_price", "bid", "ask"]
            q_keys = ["qty", "q", "size", "volume", "bid_size", "ask_size", "bid_qty", "ask_qty"]
            px = None
            qty = None

            # Prefer side-specific keys first
            if side:
                if side == "bid":
                    for k in ["bid_price", "bid", "bid_px"]:
                        if k in lvl:
                            px = lvl.get(k)
                            break
                    for k in ["bid_size", "bid_qty", "bid_volume"]:
                        if k in lvl:
                            qty = lvl.get(k)
                            break
                elif side == "ask":
                    for k in ["ask_price", "ask", "ask_px"]:
                        if k in lvl:
                            px = lvl.get(k)
                            break
                    for k in ["ask_size", "ask_qty", "ask_volume"]:
                        if k in lvl:
                            qty = lvl.get(k)
                            break

            if px is None:
                for k in px_keys:
                    if k in lvl:
                        px = lvl.get(k)
                        break
            if qty is None:
                for k in q_keys:
                    if k in lvl:
                        qty = lvl.get(k)
                        break
            if px is None or qty is None:
                return None
            return float(px), float(qty)
        return None
    except Exception:
        return None


def _iter_levels(orderbook: Any, *, side: str, depth: int) -> Iterable[Tuple[float, float]]:
    """Yield (price, qty) for a given side."""
    if side not in {"bid", "ask"}:
        return []

    # Upbit
    if isinstance(orderbook, list) and orderbook:
        units = orderbook[0].get("orderbook_units") or []
        out = []
        for u in units[:depth]:
            pq = _lvl_to_pq(u, side=side)
            if pq:
                out.append(pq)
        return out

    # Dict-style
    if isinstance(orderbook, dict):
        # Binance style
        if "bids" in orderbook and "asks" in orderbook:
            levels = orderbook.get("bids") if side == "bid" else orderbook.get("asks")
            out = []
            for lvl in (levels or [])[:depth]:
                pq = _lvl_to_pq(lvl, side=side)
                if pq:
                    out.append(pq)
            return out

        # Some stock REST APIs use 'bid'/'ask' as lists
        if "bid" in orderbook and "ask" in orderbook and isinstance(orderbook.get("bid"), list):
            levels = orderbook.get("bid") if side == "bid" else orderbook.get("ask")
            out = []
            for lvl in (levels or [])[:depth]:
                pq = _lvl_to_pq(lvl, side=side)
                if pq:
                    out.append(pq)
            return out

        # Nested orderbook_units inside a dict
        if "orderbook_units" in orderbook and isinstance(orderbook.get("orderbook_units"), list):
            levels = orderbook.get("orderbook_units")
            out = []
            for u in (levels or [])[:depth]:
                pq = _lvl_to_pq(u, side=side)
                if pq:
                    out.append(pq)
            return out

    return []


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

        for p, q in _iter_levels(orderbook, side="bid", depth=depth):
            bid_notional += float(p) * float(q)
        for p, q in _iter_levels(orderbook, side="ask", depth=depth):
            ask_notional += float(p) * float(q)

        denom = bid_notional + ask_notional
        if denom <= 0:
            return 0.0
        return max(-1.0, min(1.0, (bid_notional - ask_notional) / denom))
    except Exception:
        return 0.0



def best_bid_ask(orderbook: Any) -> tuple[float, float]:
    """Return (best_bid, best_ask). If not available returns (0,0)."""
    try:
        if isinstance(orderbook, list) and orderbook:
            units = (orderbook[0].get('orderbook_units') or [])
            if not units:
                return 0.0, 0.0
            best_bid = float(units[0].get('bid_price') or 0.0)
            best_ask = float(units[0].get('ask_price') or 0.0)
            return best_bid, best_ask
        if isinstance(orderbook, dict):
            # Binance-style
            bids = orderbook.get('bids') or []
            asks = orderbook.get('asks') or []
            if bids and asks:
                return float(bids[0][0]), float(asks[0][0])

            # Stock REST style may provide best bid/ask directly
            bid = orderbook.get('bid')
            ask = orderbook.get('ask')
            if isinstance(bid, (int, float, str)) and isinstance(ask, (int, float, str)):
                return float(bid), float(ask)

            # Or as level lists
            if isinstance(bid, list) and bid and isinstance(ask, list) and ask:
                try:
                    return float(bid[0][0]), float(ask[0][0])
                except Exception:
                    return 0.0, 0.0
        return 0.0, 0.0
    except Exception:
        return 0.0, 0.0


def spread_bps(orderbook: Any) -> float:
    """Compute spread in basis points (bps) from best bid/ask."""
    try:
        bid, ask = best_bid_ask(orderbook)
        if bid <= 0 or ask <= 0:
            return 0.0
        mid = (bid + ask) / 2.0
        if mid <= 0:
            return 0.0
        return float((ask - bid) / mid * 10000.0)
    except Exception:
        return 0.0
