from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any

from quantbot.utils.time import utc_now
from quantbot.common.types import OrderUpdate


@dataclass
class PositionInfo:
    qty: float = 0.0
    avg_cost: float = 0.0
    high_water: float = 0.0
    low_water: float = 0.0

    # Cumulative realized PnL (quote currency), estimated from fills.
    realized_pnl: float = 0.0
    realized_pnl_net: float = 0.0
    fee_paid: float = 0.0

    updated_at: str | None = None


def _parse_symbol_base_quote(venue: str, symbol: str) -> tuple[str, str]:
    if venue == "upbit":
        if "-" in symbol:
            q, b = symbol.split("-", 1)
            return b, q
        return symbol, "KRW"

    if venue in {"binance", "binance_futures"}:
        quotes = [
            "USDT",
            "USDC",
            "BUSD",
            "FDUSD",
            "TUSD",
            "BTC",
            "ETH",
            "BNB",
            "TRY",
            "EUR",
            "GBP",
            "BRL",
            "AUD",
            "KRW",
            "JPY",
        ]
        for q in sorted(quotes, key=len, reverse=True):
            if symbol.endswith(q) and len(symbol) > len(q):
                return symbol[: -len(q)], q
        return symbol, ""

    # Stocks
    return symbol, "KRW"


class PositionTracker:
    """Tracks per-symbol positions with avg cost + watermarks for trailing stop.

    Persisted to a JSON file so stop/trailing works after restarts.
    """

    def __init__(self, venue: str, path: str = "state/positions.json"):
        self.venue = venue
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.positions: Dict[str, PositionInfo] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.positions = {k: PositionInfo(**v) for k, v in (raw or {}).items()}
        except Exception:
            self.positions = {}

    def save(self) -> None:
        raw = {k: asdict(v) for k, v in self.positions.items()}
        self.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, symbol: str) -> PositionInfo:
        return self.positions.get(symbol) or PositionInfo()

    def has_position(self, symbol: str) -> bool:
        return self.get(symbol).qty != 0

    def update_mark(self, symbol: str, last_price: float) -> None:
        p = self.positions.get(symbol) or PositionInfo()
        lp = float(last_price)
        if p.qty > 0:
            p.high_water = max(float(p.high_water), lp) if float(p.high_water) > 0 else lp
        elif p.qty < 0:
            p.low_water = min(float(p.low_water), lp) if float(p.low_water) > 0 else lp
        else:
            return
        p.updated_at = utc_now().isoformat()
        self.positions[symbol] = p
        self.save()

    def on_fill(self, upd: OrderUpdate) -> None:
        """Update tracker from a FILLED order update.

        Most live adapters don't put 'side' into OrderUpdate, so this is mainly
        useful for PaperAdapter which embeds position state in raw.
        """
        if upd.status != "FILLED" or not upd.avg_fill_price:
            return
        side = (upd.raw or {}).get("side")
        if side not in {"BUY", "SELL"}:
            return
        self.apply_fill(upd.symbol, side, float(upd.filled_qty), float(upd.avg_fill_price), fee=float(upd.fee or 0.0))

    def apply_fill(self, symbol: str, side: str, filled_qty: float, avg_fill_price: float, *, fee: float = 0.0) -> Dict[str, Any]:
        """Apply a fill and update realized PnL.

        Returns a dict containing realized deltas, useful for journaling.

        Conventions:
        - LONG: qty > 0
        - SHORT: qty < 0
        """
        p = self.positions.get(symbol) or PositionInfo()
        q = float(filled_qty)
        px = float(avg_fill_price)

        realized_delta = 0.0

        if side == "BUY":
            if p.qty < 0:
                # Cover short: realize PnL on the closed portion.
                close_qty = min(q, abs(p.qty))
                realized_delta += close_qty * (p.avg_cost - px)
                remaining_buy = q - close_qty

                # Update qty after covering
                p.qty = p.qty + close_qty  # less negative

                if p.qty == 0:
                    p.avg_cost = 0.0
                    p.high_water = 0.0
                    p.low_water = 0.0

                # If we bought more than needed to cover, open long for remainder
                if remaining_buy > 0:
                    p.qty = remaining_buy
                    p.avg_cost = px
                    p.high_water = px
                    p.low_water = px
            else:
                # Open/add long
                new_qty = p.qty + q
                if new_qty > 0:
                    p.avg_cost = (p.qty * p.avg_cost + q * px) / new_qty if p.qty > 0 else px
                p.qty = new_qty
                p.high_water = max(float(p.high_water), px) if float(p.high_water) > 0 else px
                if p.low_water == 0:
                    p.low_water = px

        elif side == "SELL":
            if p.qty > 0:
                # Reduce/close long: realize PnL on the closed portion.
                close_qty = min(q, p.qty)
                realized_delta += close_qty * (px - p.avg_cost)
                remaining_sell = q - close_qty

                p.qty = p.qty - close_qty

                if p.qty == 0:
                    p.avg_cost = 0.0
                    p.high_water = 0.0
                    p.low_water = 0.0

                # If we sold more than held, open short for remainder
                if remaining_sell > 0:
                    p.qty = -remaining_sell
                    p.avg_cost = px
                    p.high_water = px
                    p.low_water = px
            else:
                # Open/add short
                abs_old = abs(float(p.qty))
                abs_new = abs_old + q
                p.avg_cost = (abs_old * p.avg_cost + q * px) / abs_new if abs_old > 0 else px
                p.qty = float(p.qty) - q
                p.low_water = min(float(p.low_water), px) if float(p.low_water) > 0 else px
                if p.high_water == 0:
                    p.high_water = px

        # Fees
        fee_f = float(fee or 0.0)
        p.fee_paid = float(p.fee_paid) + fee_f
        p.realized_pnl = float(p.realized_pnl) + realized_delta
        p.realized_pnl_net = float(p.realized_pnl_net) + (realized_delta - fee_f)

        p.updated_at = utc_now().isoformat()
        self.positions[symbol] = p
        self.save()

        return {
            "realized_pnl_delta": realized_delta,
            "realized_pnl_net_delta": realized_delta - fee_f,
            "fee": fee_f,
            "pos": asdict(p),
        }
