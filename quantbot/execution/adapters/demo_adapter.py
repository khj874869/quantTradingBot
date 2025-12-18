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

    def set_price(self, symbol: str, px: float):
        self._prices[symbol] = px

    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        await asyncio.sleep(0.02)
        px = req.price or self._prices.get(req.symbol, 0.0)
        if px <= 0:
            return OrderUpdate(req.venue, "DEMO-REJECT", req.client_order_id, req.symbol,
                               "REJECTED", 0.0, None, 0.0, utc_now(), {"error":"no price"})
        signed = req.qty if req.side == "BUY" else -req.qty
        self._pos[req.symbol] = self._pos.get(req.symbol, 0.0) + signed
        return OrderUpdate(req.venue, "DEMO-"+req.client_order_id[-10:], req.client_order_id, req.symbol,
                           "FILLED", req.qty, float(px), 0.0, utc_now(), {"demo": True})

    async def get_last_price(self, symbol: str) -> float:
        return float(self._prices.get(symbol, 0.0))

    async def get_equity(self) -> float:
        return float(self._equity)

    async def get_positions(self) -> dict[str, float]:
        return dict(self._pos)
