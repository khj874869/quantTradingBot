from __future__ import annotations

import asyncio

from quantbot.common.types import OrderRequest, OrderUpdate
from quantbot.utils.time import utc_now
from quantbot.execution.adapters.base import BrokerAdapter


class DemoAdapter(BrokerAdapter):
    def __init__(self):
        self._equity = 10_000_000.0
        self._pos: dict[str, float] = {}
        self._prices: dict[str, float] = {}

    def set_price(self, symbol: str, px: float) -> None:
        self._prices[symbol] = px

    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        await asyncio.sleep(0.02)
        px = float(req.price or self._prices.get(req.symbol, 0.0))
        if px <= 0:
            return OrderUpdate(
                venue=req.venue,
                order_id="DEMO-REJECT",
                symbol=req.symbol,
                status="REJECTED",
                filled_qty=0.0,
                avg_fill_price=None,
                fee=0.0,
                client_order_id=req.client_order_id,
                ts=utc_now(),
                raw={"error": "no price"},
            )

        signed = float(req.qty) if req.side.upper() == "BUY" else -float(req.qty)
        self._pos[req.symbol] = float(self._pos.get(req.symbol, 0.0)) + signed

        coid = req.client_order_id or "DEMO"
        return OrderUpdate(
            venue=req.venue,
            order_id="DEMO-" + coid[-10:],
            symbol=req.symbol,
            status="FILLED",
            filled_qty=float(req.qty),
            avg_fill_price=px,
            fee=0.0,
            client_order_id=req.client_order_id,
            ts=utc_now(),
            raw={"demo": True},
        )

    async def get_last_price(self, symbol: str) -> float:
        return float(self._prices.get(symbol, 0.0))

    async def get_equity(self) -> float:
        return float(self._equity)

    async def get_positions(self) -> dict[str, float]:
        return dict(self._pos)
