from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from quantbot.common.types import OrderRequest, OrderUpdate, Venue
from quantbot.execution.adapters.base import BrokerAdapter
from quantbot.utils.time import utc_now


def _state_default() -> dict[str, Any]:
    return {
        "cash": 0.0,
        "positions": {},      # base_asset -> {"qty": float, "avg_cost": float, "high_water": float}
        "realized_pnl": 0.0,
        "realized_pnl_net": 0.0,
        "updated_at": None,
    }


def _parse_symbol_base_quote(venue: str, symbol: str) -> tuple[str, str]:
    # Keep compatible with live._parse_symbol_base_quote without importing to avoid cycles.
    if venue == "upbit":
        if "-" in symbol:
            q, b = symbol.split("-", 1)
            return b, q
        return symbol, "KRW"

    if venue == "binance":
        quotes = ["USDT", "USDC", "BUSD", "FDUSD", "TUSD", "BTC", "ETH", "BNB", "TRY", "EUR", "GBP", "BRL", "AUD", "KRW", "JPY"]
        for q in sorted(quotes, key=len, reverse=True):
            if symbol.endswith(q) and len(symbol) > len(q):
                return symbol[:-len(q)], q
        return symbol, ""
    return symbol, ""


async def _public_last_price(venue: str, symbol: str) -> float:
    """Fetch last price via public endpoints (no auth). Supports upbit/binance."""
    if venue == "upbit":
        url = "https://api.upbit.com/v1/ticker"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params={"markets": symbol})
            r.raise_for_status()
            data = r.json()
            if not data:
                raise RuntimeError(f"upbit ticker empty for {symbol}")
            return float(data[0]["trade_price"])

    if venue == "binance":
        url = "https://api.binance.com/api/v3/ticker/price"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params={"symbol": symbol})
            r.raise_for_status()
            data = r.json()
            return float(data["price"])

    if venue == "binance_futures":
        url = "https://fapi.binance.com/fapi/v1/ticker/price"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params={"symbol": symbol})
            r.raise_for_status()
            data = r.json()
            return float(data["price"])

    raise RuntimeError(f"public last price not supported for venue={venue}")


@dataclass
class PaperConfig:
    initial_cash: float = 1_000_000.0
    fee_bps: int = 10          # 10bps = 0.10% per side
    slippage_bps: int = 5      # 5bps slippage for market orders
    state_path: str = "state/paper_state.json"


class PaperAdapter(BrokerAdapter):
    """Paper trading broker adapter.

    - Uses public last-price endpoints for upbit/binance by default.
    - Simulates immediate fills for MARKET orders.
    - Tracks cash + positions (base asset qty + average cost).
    - Persists state to a json file for restart safety.
    """

    def __init__(self, venue: Venue, config: PaperConfig, market_adapter: BrokerAdapter | None = None):
        self.venue: Venue = venue
        self.cfg = config
        self.market_adapter = market_adapter
        self._state_file = Path(self.cfg.state_path)
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state = _state_default()
        self._load()

        # Initialize cash if this is a fresh state
        if (self._state.get("cash") or 0.0) <= 0.0:
            self._state["cash"] = float(self.cfg.initial_cash)
            self._save()

    # ---- persistence ----
    def _load(self) -> None:
        try:
            if self._state_file.exists():
                self._state = json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception:
            self._state = _state_default()

    def _save(self) -> None:
        self._state["updated_at"] = utc_now().isoformat()
        self._state_file.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- interface ----
    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        order_type = (req.order_type or "MARKET").upper()
        meta = req.meta or {}

        # Determine fill price (market reference)
        px = await self.get_last_price(req.symbol)
        market_fill = float(px) * (1.0 + (self.cfg.slippage_bps / 10_000.0) * (1 if req.side == "BUY" else -1))

        # LIMIT simulation:
        # - If timeInForce=IOC and price does NOT cross, we return CANCELED immediately.
        # - If it crosses, we fill at market_fill (optimistic; avoids overstating edge on worse limit).
        if order_type == "LIMIT":
            if req.price is None:
                raise ValueError("LIMIT order requires req.price")
            tif = str(meta.get("timeInForce") or "GTC").upper()
            limit_px = float(req.price)
            crosses = (req.side == "BUY" and limit_px >= market_fill) or (req.side == "SELL" and limit_px <= market_fill)
            if not crosses:
                if tif == "IOC":
                    return OrderUpdate(
                        venue=self.venue,
                        order_id="PAPER-" + hashlib.sha256(f"{req.client_order_id}|{utc_now().isoformat()}".encode("utf-8")).hexdigest()[:12],
                        client_order_id=req.client_order_id,
                        symbol=req.symbol,
                        status="CANCELED",
                        filled_qty=0.0,
                        avg_fill_price=None,
                        ts=utc_now(),
                        raw={"reason": "IOC_NOT_CROSSING", "limit": limit_px, "mkt": market_fill},
                    )
                # GTC pending orders are not simulated in this lightweight paper adapter
                return OrderUpdate(
                    venue=self.venue,
                    order_id="PAPER-" + hashlib.sha256(f"{req.client_order_id}|{utc_now().isoformat()}".encode("utf-8")).hexdigest()[:12],
                    client_order_id=req.client_order_id,
                    symbol=req.symbol,
                    status="NEW",
                    filled_qty=0.0,
                    avg_fill_price=None,
                    ts=utc_now(),
                    raw={"reason": "LIMIT_PENDING_NOT_SUPPORTED", "limit": limit_px, "mkt": market_fill},
                )

        fill_price = market_fill

        # Support "quote notional market buy" if provided
        qty = float(req.qty)
        q_notional = None
        if req.meta:
            q_notional = req.meta.get("quoteOrderQty") or req.meta.get("quote_amount")
        if req.side == "BUY" and q_notional:
            qty = float(q_notional) / float(fill_price)

        base, quote = _parse_symbol_base_quote(self.venue, req.symbol)
        fee_rate = self.cfg.fee_bps / 10_000.0

        order_id = "PAPER-" + hashlib.sha256(f"{req.client_order_id}|{utc_now().isoformat()}".encode("utf-8")).hexdigest()[:12]

        # Update state
        positions: dict[str, Any] = self._state.setdefault("positions", {})
        pos = positions.get(base) or {"qty": 0.0, "avg_cost": 0.0, "high_water": 0.0, "low_water": 0.0}

        notional = qty * fill_price
        fee = notional * fee_rate

        cash = float(self._state.get("cash", 0.0))

        if req.side == "BUY":
            have = float(pos.get("qty", 0.0))
            remaining = float(qty)

            # 1) Cover short first (have < 0)
            if have < 0 and remaining > 0:
                cover_qty = min(remaining, abs(have))
                cover_cost = cover_qty * fill_price
                fee_cover = cover_cost * fee_rate
                total_cost = cover_cost + fee_cover
                if cash < total_cost - 1e-12:
                    return OrderUpdate(
                        venue=self.venue,
                        order_id=order_id,
                        client_order_id=req.client_order_id,
                        symbol=req.symbol,
                        status="REJECTED",
                        filled_qty=0.0,
                        avg_fill_price=None,
                        ts=utc_now(),
                        raw={"reason": "INSUFFICIENT_CASH", "cash": cash, "need": total_cost},
                    )

                cash -= total_cost

                # realized pnl for covered portion
                avg_cost = float(pos.get("avg_cost", 0.0))
                realized = (avg_cost - fill_price) * cover_qty  # short: profit if price falls
                realized_net = realized - fee_cover - (cover_qty * avg_cost * fee_rate)
                self._state["realized_pnl"] = float(self._state.get("realized_pnl", 0.0)) + float(realized)
                self._state["realized_pnl_net"] = float(self._state.get("realized_pnl_net", 0.0)) + float(realized_net)

                new_qty = have + cover_qty  # less negative
                pos["qty"] = new_qty
                # keep avg_cost for remaining short; if fully covered reset
                if abs(new_qty) < 1e-12:
                    pos["qty"] = 0.0
                    pos["avg_cost"] = 0.0
                    pos["high_water"] = 0.0
                    pos["low_water"] = 0.0
                remaining -= cover_qty

            # 2) If still BUY qty remains, open/add long
            if remaining > 0:
                buy_cost = remaining * fill_price
                fee_buy = buy_cost * fee_rate
                total_cost = buy_cost + fee_buy
                if cash < total_cost - 1e-12:
                    return OrderUpdate(
                        venue=self.venue,
                        order_id=order_id,
                        client_order_id=req.client_order_id,
                        symbol=req.symbol,
                        status="REJECTED",
                        filled_qty=0.0,
                        avg_fill_price=None,
                        ts=utc_now(),
                        raw={"reason": "INSUFFICIENT_CASH", "cash": cash, "need": total_cost},
                    )

                have2 = float(pos.get("qty", 0.0))
                if have2 < 0:
                    # should not happen (we covered first), but guard anyway
                    have2 = 0.0
                    pos["qty"] = 0.0
                    pos["avg_cost"] = 0.0

                new_qty = have2 + remaining
                if new_qty > 0:
                    new_avg = (have2 * float(pos.get("avg_cost", 0.0)) + remaining * fill_price) / new_qty
                else:
                    new_avg = 0.0
                pos["qty"] = new_qty
                pos["avg_cost"] = new_avg
                hw = float(pos.get("high_water", 0.0))
                lw = float(pos.get("low_water", 0.0))
                pos["high_water"] = max(hw, fill_price) if hw > 0 else fill_price
                pos["low_water"] = min(lw, fill_price) if lw > 0 else fill_price

                cash -= total_cost

        else:  # SELL
            have = float(pos.get("qty", 0.0))
            remaining = float(qty)

            # 1) Reduce/close long first (have > 0)
            if have > 0 and remaining > 0:
                sell_qty = min(remaining, have)
                proceeds = sell_qty * fill_price
                fee_sell = proceeds * fee_rate
                cash += proceeds - fee_sell

                avg_cost = float(pos.get("avg_cost", 0.0))
                realized = (fill_price - avg_cost) * sell_qty
                realized_net = realized - fee_sell - (sell_qty * avg_cost * fee_rate)
                self._state["realized_pnl"] = float(self._state.get("realized_pnl", 0.0)) + float(realized)
                self._state["realized_pnl_net"] = float(self._state.get("realized_pnl_net", 0.0)) + float(realized_net)

                new_qty = have - sell_qty
                if new_qty <= 1e-12:
                    pos["qty"] = 0.0
                    pos["avg_cost"] = 0.0
                    pos["high_water"] = 0.0
                    pos["low_water"] = 0.0
                else:
                    pos["qty"] = new_qty
                remaining -= sell_qty

            # 2) If still SELL qty remains, open/add short
            if remaining > 0:
                proceeds = remaining * fill_price
                fee_short = proceeds * fee_rate
                cash += proceeds - fee_short

                have2 = float(pos.get("qty", 0.0))
                abs_old = abs(have2)
                abs_new = abs_old + remaining
                if abs_new > 0:
                    pos["avg_cost"] = (abs_old * float(pos.get("avg_cost", 0.0)) + remaining * fill_price) / abs_new
                pos["qty"] = have2 - remaining  # more negative

                # watermarks for short
                hw = float(pos.get("high_water", 0.0))
                lw = float(pos.get("low_water", 0.0))
                pos["low_water"] = min(lw, fill_price) if lw > 0 else fill_price
                pos["high_water"] = max(hw, fill_price) if hw > 0 else fill_price

        positions[base] = pos
        self._state["cash"] = cash
        self._save()

        return OrderUpdate(
            venue=self.venue,
            order_id=order_id,
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            status="FILLED",
            filled_qty=float(qty),
            avg_fill_price=float(fill_price),
            fee=float(fee),
            ts=utc_now(),
            raw={"paper": True, "base": base, "quote": quote, "cash": cash, "pos": pos, "meta": (req.meta or {})},
        )

    async def get_last_price(self, symbol: str) -> float:
        if self.market_adapter is not None:
            return float(await self.market_adapter.get_last_price(symbol))
        return float(await _public_last_price(self.venue, symbol))

    async def get_equity(self) -> float:
        cash = float(self._state.get("cash", 0.0))
        equity = cash
        positions: dict[str, Any] = self._state.get("positions", {}) or {}
        # mark-to-market for tracked positions
        for base, info in positions.items():
            qty = float(info.get("qty", 0.0))
            if qty == 0:
                continue
            # Need a symbol to price; best-effort: assume single quote currency "KRW" for upbit, "USDT" for binance
            quote = "KRW" if self.venue == "upbit" else "USDT"
            sym = f"{quote}-{base}" if self.venue == "upbit" else f"{base}{quote}"
            try:
                px = await self.get_last_price(sym)
                equity += qty * float(px)
            except Exception:
                continue
        return float(equity)

    async def get_positions(self) -> dict[str, float]:
        out: dict[str, float] = {}
        positions: dict[str, Any] = self._state.get("positions", {}) or {}
        for base, info in positions.items():
            out[base] = float(info.get("qty", 0.0))
        # include cash as quote "position"
        quote = "KRW" if self.venue == "upbit" else "USDT"
        out[quote] = float(self._state.get("cash", 0.0))
        return out

    # ---- helpers for stop/trailing ----
    def get_position_info(self, base_asset: str) -> dict[str, float]:
        positions: dict[str, Any] = self._state.get("positions", {}) or {}
        info = positions.get(base_asset) or {"qty": 0.0, "avg_cost": 0.0, "high_water": 0.0}
        return {"qty": float(info.get("qty", 0.0)), "avg_cost": float(info.get("avg_cost", 0.0)), "high_water": float(info.get("high_water", 0.0))}
