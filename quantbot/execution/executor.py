from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Dict, Any

from quantbot.common.types import OrderRequest, OrderUpdate, Signal, Venue
from quantbot.execution.adapters.base import BrokerAdapter
from quantbot.storage.db import get_session
from quantbot.storage.models import OrderModel

def make_client_order_id(venue: str, symbol: str, side: str, ts_iso: str) -> str:
    raw = f"{venue}|{symbol}|{side}|{ts_iso}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{venue}-{symbol.replace('/','_')}-{side}-{h}"

@dataclass
class ExecutionResult:
    ok: bool
    update: Optional[OrderUpdate] = None
    reason: str = "OK"
    meta: Dict[str, Any] | None = None

class OrderExecutor:
    def __init__(self, adapter: BrokerAdapter, venue: Venue, trading_enabled: bool = True):
        self.adapter = adapter
        self.venue = venue
        self.trading_enabled = trading_enabled

    async def execute_from_signal(self, sig: Signal, qty: float, order_type: str = "MARKET", price: float | None = None, meta: Optional[Dict[str, Any]] = None) -> ExecutionResult:
        if sig.side not in ("BUY","SELL"):
            return ExecutionResult(ok=False, reason="NO_ACTION", meta={"signal": sig.side})

        coid = make_client_order_id(self.venue, sig.symbol, sig.side, sig.ts.isoformat())
        req = OrderRequest(
            client_order_id=coid,
            venue=self.venue,
            symbol=sig.symbol,
            side=sig.side,
            order_type=order_type,
            qty=qty,
            price=price,
            meta={"score": sig.score, "signal_meta": sig.meta},
        )
        upd = await self.adapter.place_order(req)
        self._persist_order(req, upd)
        return ExecutionResult(ok=(upd.status in ("NEW","PARTIALLY_FILLED","FILLED")), update=upd, reason=upd.status, meta={"client_order_id": coid})

    def _persist_order(self, req: OrderRequest, upd: OrderUpdate) -> None:
        with get_session() as s:
            m = OrderModel(
                venue=req.venue,
                symbol=req.symbol,
                client_order_id=req.client_order_id,
                order_id=upd.order_id,
                side=req.side,
                order_type=req.order_type,
                qty=req.qty,
                price=req.price,
                status=upd.status,
                filled_qty=upd.filled_qty,
                avg_fill_price=upd.avg_fill_price,
                fee=upd.fee,
                ts=upd.ts,
                raw=upd.raw,
            )
            s.add(m)
            s.commit()
