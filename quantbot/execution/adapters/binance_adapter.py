from __future__ import annotations

import hmac
import hashlib
import time
import urllib.parse
from typing import Any

import httpx

from quantbot.execution.adapters.base import BrokerAdapter
from quantbot.common.types import OrderRequest, OrderUpdate
from quantbot.utils.time import utc_now


class BinanceAdapter(BrokerAdapter):
    """Binance Spot REST adapter (HMAC-SHA256 signed endpoints).

    References (request signing / request security):
      - https://developers.binance.com/docs/binance-spot-api-docs/rest-api/request-security
    """

    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.binance.com"):
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=15)

    @staticmethod
    def _norm_symbol(symbol: str) -> str:
        return symbol.replace("-", "").replace("/", "").upper()

    def _sign(self, params: dict[str, Any]) -> str:
        # Binance expects query string signing; params should include timestamp.
        qs = urllib.parse.urlencode(params, doseq=True)
        sig = hmac.new(self.api_secret, qs.encode("utf-8"), hashlib.sha256).hexdigest()
        return qs + f"&signature={sig}"

    async def _public_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        r = await self.client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    async def _signed(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        params.setdefault("timestamp", int(time.time() * 1000))
        params.setdefault("recvWindow", 5000)
        url = f"{self.base_url}{path}?{self._sign(params)}"
        headers = {"X-MBX-APIKEY": self.api_key}
        r = await self.client.request(method, url, headers=headers)
        r.raise_for_status()
        return r.json()

    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        symbol = self._norm_symbol(req.symbol)
        side = req.side.upper()
        order_type = (req.order_type or "MARKET").upper()
        meta = req.meta or {}

        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
        }
        if meta.get("newOrderRespType"):
            params["newOrderRespType"] = str(meta["newOrderRespType"])
        elif meta.get("timeInForce") in {"IOC", "FOK"}:
            params["newOrderRespType"] = "RESULT"

        if order_type == "LIMIT":
            if req.price is None:
                raise ValueError("LIMIT order requires req.price")
            params.update({
                "timeInForce": str(meta.get("timeInForce") or "GTC"),
                "quantity": self._fmt_qty(req.qty),
                "price": self._fmt_price(req.price),
            })
        else:
            # MARKET
            # For BUY we can also support quoteOrderQty if user passes meta.
            if side == "BUY" and req.meta and "quoteOrderQty" in req.meta:
                params["quoteOrderQty"] = self._fmt_qty(float(req.meta["quoteOrderQty"]))
            else:
                params["quantity"] = self._fmt_qty(req.qty)

        try:
            data = await self._signed("POST", "/api/v3/order", params=params)
            status = (data.get("status") or "NEW").upper()
            filled_qty = float(data.get("executedQty") or 0.0)
            avg_price = None
            cumm_quote = data.get("cummulativeQuoteQty")
            if filled_qty > 0 and cumm_quote is not None:
                try:
                    avg_price = float(cumm_quote) / filled_qty
                except Exception:
                    avg_price = None

            return OrderUpdate(
                venue=req.venue,
                order_id=str(data.get("orderId") or ""),
                client_order_id=req.client_order_id,
                symbol=req.symbol,
                status="FILLED" if status == "FILLED" else ("NEW" if status in {"NEW", "PARTIALLY_FILLED"} else status),
                filled_qty=filled_qty,
                avg_fill_price=avg_price,
                fee=None,
                ts=utc_now(),
                raw=data,
            )
        except Exception as e:
            return OrderUpdate(
                venue=req.venue,
                order_id="",
                client_order_id=req.client_order_id,
                symbol=req.symbol,
                status="REJECTED",
                filled_qty=0.0,
                avg_fill_price=None,
                fee=None,
                ts=utc_now(),
                raw={"error": str(e)},
            )

    async def get_last_price(self, symbol: str) -> float:
        s = self._norm_symbol(symbol)
        data = await self._public_get("/api/v3/ticker/price", params={"symbol": s})
        return float(data["price"])

    async def get_equity(self) -> float:
        # Spot account doesn't expose a single "equity" number; return USDT free + locked if present.
        data = await self._signed("GET", "/api/v3/account")
        balances = data.get("balances") or []
        for b in balances:
            if b.get("asset") == "USDT":
                return float(b.get("free") or 0.0) + float(b.get("locked") or 0.0)
        return 0.0

    async def get_positions(self) -> dict[str, float]:
        data = await self._signed("GET", "/api/v3/account")
        out: dict[str, float] = {}
        for b in (data.get("balances") or []):
            asset = b.get("asset")
            if not asset:
                continue
            qty = float(b.get("free") or 0.0) + float(b.get("locked") or 0.0)
            if qty != 0.0:
                out[asset] = qty
        return out

    async def get_order_update(self, symbol: str, order_id: str) -> OrderUpdate:
        """Fetch order status from Binance spot and convert to OrderUpdate.

        Used as a post-trade confirmation when place_order returns an ACK/NEW response.
        """
        s = self._norm_symbol(symbol)
        data = await self._signed("GET", "/api/v3/order", params={"symbol": s, "orderId": order_id})

        status = (data.get("status") or "NEW").upper()
        filled_qty = float(data.get("executedQty") or 0.0)
        avg_price = None
        cumm_quote = data.get("cummulativeQuoteQty")
        if filled_qty > 0 and cumm_quote is not None:
            try:
                avg_price = float(cumm_quote) / filled_qty
            except Exception:
                avg_price = None

        mapped_status = {
            "NEW": "NEW",
            "PARTIALLY_FILLED": "PARTIALLY_FILLED",
            "FILLED": "FILLED",
            "CANCELED": "CANCELED",
            "REJECTED": "REJECTED",
            "EXPIRED": "CANCELED",
        }.get(status, status)

        return OrderUpdate(
            venue="binance",
            order_id=str(data.get("orderId") or order_id),
            client_order_id=str(data.get("clientOrderId") or "") or None,
            symbol=symbol,
            status=mapped_status,
            filled_qty=filled_qty,
            avg_fill_price=avg_price,
            fee=None,
            ts=utc_now(),
            raw=data,
        )

    @staticmethod
    def _fmt_qty(x: float) -> str:
        # Binance expects decimal string; avoid scientific notation.
        return f"{float(x):.10f}".rstrip("0").rstrip(".")

    @staticmethod
    def _fmt_price(x: float) -> str:
        return f"{float(x):.10f}".rstrip("0").rstrip(".")
