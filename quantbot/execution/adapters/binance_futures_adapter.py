from __future__ import annotations

import time
import hmac
import hashlib
from urllib.parse import urlencode

import httpx

from quantbot.execution.adapters.base import BrokerAdapter
from quantbot.common.types import OrderRequest, OrderUpdate
from quantbot.utils.time import utc_now


class BinanceFuturesAdapter(BrokerAdapter):
    """
    USD-M Futures (FAPI) adapter.
    Testnet REST base: https://demo-fapi.binance.com  :contentReference[oaicite:3]{index=3}
    """

    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://fapi.binance.com"):
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=10)

    # ---------- helpers ----------
    def _sign(self, params: dict) -> dict:
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        qs = urlencode(params, doseq=True)
        sig = hmac.new(self.api_secret, qs.encode("utf-8"), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    async def _signed(self, method: str, path: str, params: dict | None = None) -> dict:
        params = params or {}
        signed = self._sign(params)
        headers = {"X-MBX-APIKEY": self.api_key}
        url = f"{self.base_url}{path}"
        # Binance는 signed params를 querystring 또는 form으로 받을 수 있음(일반적으로 form 사용)
        if method.upper() == "GET":
            r = await self.client.request(method, url, params=signed, headers=headers)
        else:
            r = await self.client.request(method, url, data=signed, headers=headers)
        r.raise_for_status()
        return r.json()

    async def _public(self, method: str, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        r = await self.client.request(method, url, params=params or {})
        r.raise_for_status()
        return r.json()

    # ---------- futures extra ----------
    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        # POST /fapi/v1/leverage :contentReference[oaicite:4]{index=4}
        return await self._signed("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})

    # ---------- BrokerAdapter ----------
    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        """
        Futures new order: POST /fapi/v1/order :contentReference[oaicite:5]{index=5}
        """
        side = "BUY" if req.side == "BUY" else "SELL"

        payload: dict = {
            "symbol": req.symbol,
            "side": side,
            "newClientOrderId": req.client_order_id,
        }

        if req.order_type == "MARKET":
            payload["type"] = "MARKET"
            payload["quantity"] = self._fmt_qty(req.qty)
        else:
            payload["type"] = "LIMIT"
            payload["timeInForce"] = "GTC"
            payload["quantity"] = self._fmt_qty(req.qty)
            if req.price is None:
                raise ValueError("LIMIT order requires price")
            payload["price"] = self._fmt_price(req.price)

        try:
            data = await self._signed("POST", "/fapi/v1/order", payload)
            # 체결 즉시 응답이 아니어도 orderId 등은 내려옴
            return OrderUpdate(
                venue=req.venue,
                order_id=str(data.get("orderId", "")),
                client_order_id=req.client_order_id,
                symbol=req.symbol,
                status="NEW",
                filled_qty=float(data.get("executedQty", 0.0) or 0.0),
                avg_fill_price=float(data.get("avgPrice", 0.0) or 0.0) if "avgPrice" in data else None,
                fee=None,
                ts=utc_now(),
                raw=data,
            )
        except httpx.HTTPStatusError as e:
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
                raw={"error": str(e), "response": getattr(e.response, "text", "")},
            )

    async def get_last_price(self, symbol: str) -> float:
        # GET /fapi/v1/ticker/price :contentReference[oaicite:6]{index=6}
        data = await self._public("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        return float(data["price"])

    async def get_equity(self) -> float:
        # GET /fapi/v2/balance :contentReference[oaicite:7]{index=7}
        data = await self._signed("GET", "/fapi/v2/balance", {})
        # USDT margin 기준으로 “총 잔고(balance)” 합산(단순)
        total = 0.0
        for a in data:
            if a.get("asset") == "USDT":
                total = float(a.get("balance", 0.0) or 0.0)
                break
        return total

    async def get_positions(self) -> dict[str, float]:
        # GET /fapi/v2/positionRisk :contentReference[oaicite:8]{index=8}
        data = await self._signed("GET", "/fapi/v2/positionRisk", {})
        out: dict[str, float] = {}
        for p in data:
            sym = p.get("symbol")
            amt = float(p.get("positionAmt", 0.0) or 0.0)
            if sym:
                out[sym] = amt
        return out

    @staticmethod
    def _fmt_qty(q: float) -> str:
        # 심볼별 stepSize는 exchangeInfo로 맞추는 게 정석이지만, 일단 소수 6자리로
        return f"{float(q):.6f}".rstrip("0").rstrip(".")

    @staticmethod
    def _fmt_price(p: float) -> str:
        return f"{float(p):.2f}".rstrip("0").rstrip(".")
