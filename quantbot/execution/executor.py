from __future__ import annotations

from dataclasses import asdict
from typing import Optional, Sequence

from quantbot.common.types import ExecutionResult, OrderRequest, OrderUpdate
from quantbot.execution.adapters.base import BrokerAdapter
from quantbot.utils.time import utc_now


class OrderExecutor:
    """Executes orders via a BrokerAdapter.

    Latency-sensitive logic (IOC/limit chase, market fallback) lives here so
    strategies can stay venue-agnostic.
    """

    def __init__(self, adapter: BrokerAdapter, *, persist_orders: bool = False):
        self.adapter = adapter
        self.persist_orders = persist_orders

    async def execute(self, req: OrderRequest) -> ExecutionResult:
        """Execute a single order request, converting exceptions into REJECTED."""
        try:
            upd = await self.adapter.place_order(req)
            return ExecutionResult(req=req, update=upd)
        except Exception as e:
            upd = OrderUpdate(
                venue=req.venue,
                order_id="",
                symbol=req.symbol,
                status="REJECTED",
                filled_qty=0.0,
                avg_fill_price=None,
                fee=None,
                client_order_id=req.client_order_id,
                ts=utc_now(),
                raw={"error": str(e)},
            )
            return ExecutionResult(req=req, update=upd)

    async def execute_ioc_limit_prices_then_market(
        self,
        req: OrderRequest,
        prices: Sequence[float],
        *,
        fallback_market: bool = True,
    ) -> ExecutionResult:
        """Place one or more LIMIT-IOC orders (in order), then market-fill remainder.

        The caller supplies a list of candidate limit prices (already including any
        pads / hint prices). We will try them sequentially for the remaining qty.

        Returns
        - ExecutionResult with a synthetic OrderUpdate where filled_qty/avg_fill_price/fee
          are aggregated across all legs.
        """
        if req.order_type != "LIMIT":
            return await self.execute(req)

        total_qty = float(req.qty)
        filled_total = 0.0
        fee_total = 0.0
        wsum = 0.0
        leg_ids: list[str] = []
        raw_legs: list[dict] = []

        remaining = total_qty
        for px in prices:
            if remaining <= 0:
                break

            ioc_req = OrderRequest(
                venue=req.venue,
                symbol=req.symbol,
                side=req.side,
                order_type="LIMIT",
                qty=remaining,
                price=float(px),
                client_order_id=req.client_order_id,
                meta={**(req.meta or {}), "timeInForce": "IOC"},
            )
            ioc_res = await self.execute(ioc_req)
            leg_ids.append(str(ioc_res.update.order_id or ""))
            raw_legs.append(ioc_res.update.raw or {})

            f = float(ioc_res.update.filled_qty or 0.0)
            if f > 0:
                filled_total += f
                px_f = float(ioc_res.update.avg_fill_price) if ioc_res.update.avg_fill_price is not None else float(px)
                wsum += px_f * f

            fee_total += float(ioc_res.update.fee or 0.0)
            remaining = max(0.0, total_qty - filled_total)

        mkt_raw = None
        if remaining > 0 and fallback_market:
            mkt_req = OrderRequest(
                venue=req.venue,
                symbol=req.symbol,
                side=req.side,
                order_type="MARKET",
                qty=remaining,
                price=None,
                client_order_id=f"{req.client_order_id}-MKT" if req.client_order_id else None,
                meta=req.meta or {},
            )
            mkt_res = await self.execute(mkt_req)
            mkt_raw = mkt_res.update.raw or {}
            leg_ids.append(str(mkt_res.update.order_id or ""))
            f2 = float(mkt_res.update.filled_qty or 0.0)
            if f2 > 0:
                filled_total += f2
                px2 = float(mkt_res.update.avg_fill_price or 0.0) or 0.0
                if px2 <= 0:
                    # fallback to last IOC price if we have it, else 0
                    px2 = float(prices[-1]) if prices else 0.0
                wsum += px2 * f2
            fee_total += float(mkt_res.update.fee or 0.0)
            remaining = max(0.0, total_qty - filled_total)

        avg_px = (wsum / filled_total) if filled_total > 0 else None
        status = "FILLED" if filled_total >= total_qty - 1e-12 else ("PARTIALLY_FILLED" if filled_total > 0 else "REJECTED")

        synth = OrderUpdate(
            venue=req.venue,
            symbol=req.symbol,
            order_id="+".join([i for i in leg_ids if i]) or "",
            client_order_id=req.client_order_id,
            status=status,
            filled_qty=filled_total,
            avg_fill_price=avg_px,
            fee=fee_total if fee_total != 0.0 else None,
            ts=utc_now(),
            raw={"ioc_legs": raw_legs, "market": mkt_raw},
        )
        return ExecutionResult(req=req, update=synth)

    async def execute_ioc_limit_then_market(
        self,
        req: OrderRequest,
        *,
        fallback_market: bool = True,
    ) -> ExecutionResult:
        """Compatibility wrapper: single IOC price then market fallback."""
        if req.order_type != "LIMIT":
            return await self.execute(req)
        if req.price is None:
            return await self.execute(req)
        return await self.execute_ioc_limit_prices_then_market(req, [float(req.price)], fallback_market=fallback_market)
