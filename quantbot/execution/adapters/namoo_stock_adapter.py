from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from quantbot.common.types import OrderRequest, OrderUpdate
from quantbot.execution.adapters.base import BrokerAdapter
from quantbot.utils.time import utc_now


class NamooStockAdapter(BrokerAdapter):
    """REST adapter for a local 나무증권(OpenAPI) bridge service."""

    def __init__(self, base_url: str, account_no: str, timeout: float = 5.0):
        self.base_url = (base_url or "").rstrip("/")
        self.account_no = account_no
        self.client = httpx.AsyncClient(timeout=timeout)

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        r = await self.client.get(f"{self.base_url}{path}", params=params)
        r.raise_for_status()
        return r.json()

    async def _post(self, path: str, body: Dict[str, Any]) -> Any:
        r = await self.client.post(f"{self.base_url}{path}", json=body)
        r.raise_for_status()
        return r.json()

    async def _put(self, path: str, body: Dict[str, Any]) -> Any:
        r = await self.client.put(f"{self.base_url}{path}", json=body)
        r.raise_for_status()
        return r.json()

    async def _delete(self, path: str, body: Dict[str, Any]) -> Any:
        r = await self.client.request("DELETE", f"{self.base_url}{path}", json=body)
        r.raise_for_status()
        return r.json()

    def _normalize_orderbook(self, data: Any) -> Dict[str, Any]:
        """Best-effort normalize to {'bids':[[p,q],...],'asks':[[p,q],...]}.

        If the bridge already returns this shape, it is forwarded.
        """
        try:
            if isinstance(data, dict):
                if isinstance(data.get("bids"), list) and isinstance(data.get("asks"), list):
                    return {"bids": data.get("bids") or [], "asks": data.get("asks") or [], "raw": data}
                if isinstance(data.get("orderbook_units"), list):
                    bids, asks = [], []
                    for u in (data.get("orderbook_units") or [])[:10]:
                        try:
                            asks.append([float(u.get("ask_price") or 0), float(u.get("ask_size") or 0)])
                            bids.append([float(u.get("bid_price") or 0), float(u.get("bid_size") or 0)])
                        except Exception:
                            continue
                    return {"bids": bids, "asks": asks, "raw": data}
                bid = data.get("bid") or data.get("best_bid") or data.get("bestBid")
                ask = data.get("ask") or data.get("best_ask") or data.get("bestAsk")
                bids = [[float(bid), float(data.get("bid_size") or data.get("bidQty") or 0.0)]] if bid is not None else []
                asks = [[float(ask), float(data.get("ask_size") or data.get("askQty") or 0.0)]] if ask is not None else []
                return {"bids": bids, "asks": asks, "raw": data}
            if isinstance(data, list) and data and isinstance(data[0], dict):
                # sometimes list wrapper
                return self._normalize_orderbook(data[0])
        except Exception:
            pass
        return {"bids": [], "asks": [], "raw": data}

    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        meta = req.meta or {}
        action = (meta.get("action") or "NEW").upper()  # NEW|MODIFY|CANCEL
        acc_no = meta.get("acc_no") or self.account_no

        try:
            if action == "MODIFY":
                data = await self._put(
                    "/order",
                    {"org_ord_no": meta.get("org_ord_no"), "qty": req.qty, "price": req.price},
                )
            elif action == "CANCEL":
                data = await self._delete(
                    "/order",
                    {"org_ord_no": meta.get("org_ord_no"), "qty": req.qty},
                )
            else:
                data = await self._post(
                    "/order",
                    {
                        "acc_no": acc_no,
                        "code": req.symbol,
                        "qty": req.qty,
                        "price": req.price or 0,
                        "type": "매수" if req.side.upper() == "BUY" else "매도",
                    },
                )

            status = str(data.get("status") or data.get("result") or "NEW")
            order_id = str(data.get("ord_no") or data.get("order_id") or data.get("orderNo") or "")
            filled_qty = float(data.get("filled_qty") or data.get("filledQty") or 0.0)
            avg_px = data.get("avg_fill_price") or data.get("avgFillPrice")

            return OrderUpdate(
                venue=req.venue,
                symbol=req.symbol,
                order_id=order_id,
                client_order_id=req.client_order_id,
                status=status,
                filled_qty=filled_qty,
                avg_fill_price=float(avg_px) if avg_px is not None else None,
                fee=data.get("fee"),
                ts=utc_now(),
                raw=data,
            )
        except Exception as e:
            return OrderUpdate(
                venue=req.venue,
                symbol=req.symbol,
                order_id="",
                client_order_id=req.client_order_id,
                status="REJECTED",
                filled_qty=0.0,
                avg_fill_price=None,
                fee=None,
                ts=utc_now(),
                raw={"error": str(e)},
            )

    async def get_last_price(self, symbol: str) -> float:
        data = await self._get("/quote", params={"code": symbol})
        for k in ("price", "cur_prc", "last", "trade_price", "now", "close"):
            if k in data and data[k] is not None:
                return float(data[k])
        # fallback: mid from orderbook
        ob = await self.get_orderbook(symbol)
        bids = ob.get("bids") or []
        asks = ob.get("asks") or []
        bid = float(bids[0][0]) if bids else 0.0
        ask = float(asks[0][0]) if asks else 0.0
        return (bid + ask) / 2 if bid and ask else max(bid, ask)

    async def get_orderbook(self, symbol: str) -> Dict[str, Any]:
        data = await self._get("/orderbook", params={"code": symbol})
        return self._normalize_orderbook(data)

    async def get_equity(self) -> float:
        data = await self._get("/balance", params={"acc_no": self.account_no})
        for k in ("cash", "balance", "ord_cash", "available", "dbst_bal"):
            if k in data and data[k] is not None:
                try:
                    return float(data[k])
                except Exception:
                    continue
        return 0.0

    async def get_positions(self) -> Dict[str, float]:
        data = await self._get("/positions", params={"acc_no": self.account_no})
        out: Dict[str, float] = {}
        items = data.get("positions") or data.get("items") or data
        if isinstance(items, list):
            for it in items:
                try:
                    code = str(it.get("code") or it.get("stk_cd") or it.get("symbol"))
                    qty = float(it.get("qty") or it.get("rmnd_qty") or it.get("quantity") or 0.0)
                    if code and qty:
                        out[code] = qty
                except Exception:
                    continue
        return out

    async def close(self) -> None:
        await self.client.aclose()
