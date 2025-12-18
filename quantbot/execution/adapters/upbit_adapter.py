from __future__ import annotations

import hashlib
import uuid
import urllib.parse
from typing import Any

import httpx
import jwt

from quantbot.execution.adapters.base import BrokerAdapter
from quantbot.common.types import OrderRequest, OrderUpdate
from quantbot.utils.time import utc_now


class UpbitAdapter(BrokerAdapter):
    """Upbit Exchange REST adapter.

    Notes:
      - Upbit uses JWT auth with query_hash (SHA512) for signed endpoints.
      - Market BUY requires total *quote* amount (KRW/USDT/etc) via `ord_type=price`.
        This adapter supports:
          * req.meta['quote_amount'] (preferred)
          * else (req.qty * last_price) (qty interpreted as base size)
      - Market SELL uses `ord_type=market` + volume.

    Docs (auth): https://docs.upbit.com/kr/reference/auth
    """

    def __init__(self, access_key: str, secret_key: str, base_url: str = "https://api.upbit.com"):
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=15)

    def _make_jwt(self, params: dict[str, Any] | None = None) -> str:
        payload: dict[str, Any] = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
        }
        if params:
            query = urllib.parse.urlencode(params, doseq=True).encode("utf-8")
            qh = hashlib.sha512(query).hexdigest()
            payload["query_hash"] = qh
            payload["query_hash_alg"] = "SHA512"

        token = jwt.encode(payload, self.secret_key, algorithm="HS256")
        # PyJWT may return str or bytes depending on version
        return token.decode() if isinstance(token, bytes) else token

    async def _get(self, path: str, params: dict[str, Any] | None = None, auth: bool = False) -> Any:
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"accept": "application/json"}
        if auth:
            token = self._make_jwt(params or {})
            headers["Authorization"] = f"Bearer {token}"
        r = await self.client.get(url, params=params, headers=headers)
        r.raise_for_status()
        return r.json()

    async def _post(self, path: str, params: dict[str, Any] | None = None, auth: bool = True) -> Any:
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"accept": "application/json"}
        if auth:
            token = self._make_jwt(params or {})
            headers["Authorization"] = f"Bearer {token}"
        r = await self.client.post(url, params=params, headers=headers)
        r.raise_for_status()
        return r.json()

    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        # Upbit expects:
        #   market (e.g., KRW-BTC), side: bid/ask, ord_type: limit/price/market
        #   limit: price + volume
        #   market buy(price): price
        #   market sell(market): volume
        market = req.symbol
        side = "bid" if req.side.upper() == "BUY" else "ask"

        order_type = (req.order_type or "MARKET").upper()
        params: dict[str, Any] = {
            "market": market,
            "side": side,
        }

        if order_type == "LIMIT":
            if req.price is None:
                raise ValueError("LIMIT order requires req.price")
            params.update({
                "ord_type": "limit",
                "price": str(req.price),
                "volume": str(req.qty),
            })
        else:
            # MARKET
            if side == "bid":
                quote_amount = None
                if req.meta and "quote_amount" in req.meta:
                    quote_amount = float(req.meta["quote_amount"])
                if quote_amount is None:
                    last_px = await self.get_last_price(market)
                    quote_amount = float(req.qty) * float(last_px)
                params.update({
                    "ord_type": "price",
                    "price": str(quote_amount),
                })
            else:
                params.update({
                    "ord_type": "market",
                    "volume": str(req.qty),
                })

        try:
            data = await self._post("/v1/orders", params=params, auth=True)
            # Upbit returns uuid, state(wait/done/cancel), etc.
            order_id = data.get("uuid") or data.get("id") or ""
            state = (data.get("state") or "wait").lower()
            status = "NEW" if state in {"wait", "watch"} else ("FILLED" if state == "done" else "CANCELED")
            filled_qty = float(data.get("volume") or 0.0)  # not always filled
            avg_price = None
            if data.get("price") is not None:
                try:
                    avg_price = float(data.get("price"))
                except Exception:
                    avg_price = None

            return OrderUpdate(
                venue=req.venue,
                order_id=order_id,
                client_order_id=req.client_order_id,
                symbol=req.symbol,
                status=status,
                filled_qty=filled_qty,
                avg_fill_price=avg_price,
                fee=None,
                ts=utc_now(),
                meta={"raw": data},
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
                meta={"error": str(e)},
            )

    async def get_last_price(self, symbol: str) -> float:
        # GET /v1/ticker?markets=KRW-BTC
        data = await self._get("/v1/ticker", params={"markets": symbol}, auth=False)
        if isinstance(data, list) and data:
            return float(data[0]["trade_price"])
        raise ValueError(f"Unexpected ticker response for {symbol}: {data}")

    async def get_equity(self) -> float:
        # GET /v1/accounts (auth)
        data = await self._get("/v1/accounts", params={}, auth=True)
        # return KRW balance if present, else sum of all balances as float (not valued)
        if isinstance(data, list):
            for row in data:
                if row.get("currency") == "KRW":
                    return float(row.get("balance") or 0.0)
            return float(sum(float(r.get("balance") or 0.0) for r in data))
        raise ValueError(f"Unexpected accounts response: {data}")

    async def get_positions(self) -> dict[str, float]:
        data = await self._get("/v1/accounts", params={}, auth=True)
        out: dict[str, float] = {}
        if isinstance(data, list):
            for row in data:
                cur = row.get("currency")
                if not cur:
                    continue
                out[cur] = float(row.get("balance") or 0.0)
            return out
        return out
