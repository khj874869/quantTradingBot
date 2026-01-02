from __future__ import annotations

import hashlib
import hmac
import time
import math
from typing import Any, Dict, Optional

import httpx

from quantbot.common.types import OrderRequest, OrderUpdate
from quantbot.execution.adapters.base import BrokerAdapter

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    qty_step: float
    min_qty: float
    max_qty: float
    min_notional: float | None = None
    qty_precision: int | None = None


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _find_filter(filters: list[dict[str, Any]], filter_type: str) -> dict[str, Any] | None:
    for f in filters or []:
        try:
            if str(f.get("filterType") or "").upper() == filter_type.upper():
                return f
        except Exception:
            continue
    return None


def _extract_min_notional(filters: list[dict[str, Any]]) -> float | None:
    """Best-effort: Binance futures exchangeInfo can expose min notional under different filter types/keys."""
    for ft in ("MIN_NOTIONAL", "NOTIONAL"):
        f = _find_filter(filters, ft)
        if not f:
            continue
        for k in ("notional", "minNotional", "minNotionalValue", "minNotional", "minNotionalValue"):
            if k in f:
                v = _safe_float(f.get(k), 0.0)
                if v > 0:
                    return v
    return None


def floor_to_step(x: float, step: float) -> float:
    if step <= 0:
        return float(x)
    return math.floor(float(x) / float(step)) * float(step)


def ceil_to_step(x: float, step: float) -> float:
    if step <= 0:
        return float(x)
    return math.ceil(float(x) / float(step)) * float(step)



class BinanceFuturesAdapter(BrokerAdapter):
    """Binance USDⓈ-M futures (fapi) adapter.

    - Orders: POST /fapi/v1/order
    - Price: GET /fapi/v1/ticker/price
    - Account equity: GET /fapi/v2/account

    Note: You must set API key/secret with futures permission.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://fapi.binance.com",
        timeout: float = 10.0,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout)

        self._exchange_info_cache: dict[str, Any] | None = None
        self._symbol_rules_cache: dict[str, SymbolRules] = {}

    def _ts(self) -> int:
        return int(time.time() * 1000)

    def _sign(self, query: str) -> str:
        return hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    async def _signed_request(self, method: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "BINANCE_API_KEY/BINANCE_API_SECRET not set; cannot call signed endpoints (account/equity/orders)."
            )

        params = {k: v for k, v in params.items() if v is not None}
        params.setdefault("timestamp", self._ts())
        # Allow some clock drift / network jitter.
        params.setdefault("recvWindow", 5000)
        query = str(httpx.QueryParams(params))
        sig = self._sign(query)
        url = f"{self.base_url}{path}?{query}&signature={sig}"
        headers = {"X-MBX-APIKEY": self.api_key}
        r = await self.client.request(method, url, headers=headers)
        r.raise_for_status()
        return r.json()

    async def _public_get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        r = await self.client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "").replace("-", "").upper()

    @staticmethod
    def _fmt_qty(x: float) -> str:
        # Avoid scientific notation.
        return f"{float(x):.10f}".rstrip("0").rstrip(".")

    @staticmethod
    def _fmt_price(x: float) -> str:
        return f"{float(x):.10f}".rstrip("0").rstrip(".")

    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        symbol = self._normalize_symbol(req.symbol)
        side = req.side.upper()
        order_type = req.order_type.upper()

        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": self._fmt_qty(req.qty),
            "newClientOrderId": req.client_order_id,
        }

        meta = req.meta or {}
        # Futures-specific flags
        if meta.get("reduceOnly") is not None:
            params["reduceOnly"] = "true" if bool(meta.get("reduceOnly")) else "false"
        if meta.get("positionSide") is not None:
            params["positionSide"] = meta.get("positionSide")  # LONG/SHORT (hedge mode)

        if order_type == "LIMIT":
            if req.price is None:
                raise ValueError("LIMIT order requires price")
            params["price"] = self._fmt_price(req.price)
            params["timeInForce"] = meta.get("timeInForce", "GTC")

        # IMPORTANT:
        # Futures REST "newOrderRespType" defaults to ACK on some accounts, which returns
        # executedQty=0 even for MARKET orders. We prefer RESULT so the bot can reliably
        # detect fills and journal them.
        params["newOrderRespType"] = str(meta.get("newOrderRespType") or "RESULT")

        try:
            # IOC uses taker fee; for speed you may want price protection via slippage bps.
            data = await self._signed_request("POST", "/fapi/v1/order", params)
        except httpx.HTTPStatusError as e:
            # Binance returns JSON like: {"code": -2010, "msg": "..."}
            body: Any
            try:
                body = e.response.json()
            except Exception:
                body = {"text": e.response.text}
            return OrderUpdate(
                venue=req.venue,
                symbol=req.symbol,
                order_id="",
                client_order_id=req.client_order_id,
                status="REJECTED",
                filled_qty=0.0,
                avg_fill_price=None,
                fee=None,
                raw={
                    "error": "http_error",
                    "http_status": int(getattr(e.response, "status_code", 0) or 0),
                    "body": body,
                    "request": params,
                },
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
                raw={"error": str(e), "request": params},
            )

        status = str(data.get("status") or "NEW")
        executed_qty = float(data.get("executedQty") or 0.0)
        avg_price = float(data.get("avgPrice") or 0.0)
        fee = None

        # Some responses include fills only in spot; futures usually doesn't.
        if avg_price == 0.0 and executed_qty > 0 and data.get("cumQuote") is not None:
            try:
                cum_quote = float(data.get("cumQuote") or 0.0)
                avg_price = cum_quote / executed_qty if executed_qty else 0.0
            except Exception:
                pass

        mapped_status = {
            "NEW": "NEW",
            "PARTIALLY_FILLED": "PARTIALLY_FILLED",
            "FILLED": "FILLED",
            "CANCELED": "CANCELED",
            "REJECTED": "REJECTED",
            "EXPIRED": "CANCELED",
        }.get(status, status)

        return OrderUpdate(
            venue=req.venue,
            symbol=req.symbol,
            order_id=str(data.get("orderId")),
            client_order_id=req.client_order_id,
            status=mapped_status,
            filled_qty=executed_qty,
            avg_fill_price=avg_price if avg_price > 0 else None,
            fee=fee,
            raw=data,
        )

    async def get_order_update(self, symbol: str, order_id: str) -> OrderUpdate:
        """Fetch order status from Binance futures and convert to OrderUpdate.

        Used as a post-trade confirmation when place_order returns ACK/NEW with 0 executed.
        """
        symbol_n = self._normalize_symbol(symbol)
        data = await self._signed_request("GET", "/fapi/v1/order", {"symbol": symbol_n, "orderId": order_id})
        status = str(data.get("status") or "NEW")
        executed_qty = float(data.get("executedQty") or 0.0)
        avg_price = float(data.get("avgPrice") or 0.0)
        if avg_price == 0.0 and executed_qty > 0 and data.get("cumQuote") is not None:
            try:
                cum_quote = float(data.get("cumQuote") or 0.0)
                avg_price = cum_quote / executed_qty if executed_qty else 0.0
            except Exception:
                pass

        mapped_status = {
            "NEW": "NEW",
            "PARTIALLY_FILLED": "PARTIALLY_FILLED",
            "FILLED": "FILLED",
            "CANCELED": "CANCELED",
            "REJECTED": "REJECTED",
            "EXPIRED": "CANCELED",
        }.get(status, status)

        return OrderUpdate(
            venue="binance_futures",
            symbol=symbol,
            order_id=str(data.get("orderId") or order_id),
            client_order_id=str(data.get("clientOrderId") or "") or None,
            status=mapped_status,
            filled_qty=executed_qty,
            avg_fill_price=avg_price if avg_price > 0 else None,
            fee=None,
            raw=data,
        )

    async def get_last_price(self, symbol: str) -> float:
        symbol_n = self._normalize_symbol(symbol)
        data = await self._public_get("/fapi/v1/ticker/price", {"symbol": symbol_n})
        return float(data["price"])

    async def get_equity(self) -> float:
        data = await self._signed_request("GET", "/fapi/v2/account", {})
        # totalMarginBalance includes unrealized PnL (closer to what users see as "equity").
        try:
            return float(data.get("totalMarginBalance") or data.get("totalWalletBalance") or 0.0)
        except Exception:
            return 0.0

    async def get_positions(self) -> Dict[str, float]:
        data = await self._signed_request("GET", "/fapi/v2/account", {})
        out: Dict[str, float] = {}
        for p in data.get("positions", []) or []:
            try:
                sym = str(p.get("symbol"))
                amt = float(p.get("positionAmt") or 0.0)
                if amt != 0.0:
                    out[sym] = amt
            except Exception:
                continue
        return out

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        symbol_n = self._normalize_symbol(symbol)
        await self._signed_request("POST", "/fapi/v1/leverage", {"symbol": symbol_n, "leverage": int(leverage)})

    

async def get_exchange_info(self) -> Dict[str, Any]:
    """Return /fapi/v1/exchangeInfo (cached)."""
    if self._exchange_info_cache is None:
        self._exchange_info_cache = await self._public_get("/fapi/v1/exchangeInfo", {})
    return self._exchange_info_cache


async def get_symbol_rules(self, symbol: str, *, order_type: str = "MARKET") -> SymbolRules:
    """Fetch and cache per-symbol trading rules (step size, min qty, min notional)."""
    sym = self._normalize_symbol(symbol)
    if sym in self._symbol_rules_cache:
        return self._symbol_rules_cache[sym]

    info = await self.get_exchange_info()
    symbols = info.get("symbols") or []
    target = None
    for s in symbols:
        try:
            if str(s.get("symbol")) == sym:
                target = s
                break
        except Exception:
            continue
    if not target:
        raise ValueError(f"Symbol not found in exchangeInfo: {sym}")

    filters = target.get("filters") or []
    # Prefer MARKET_LOT_SIZE for market orders if present; otherwise LOT_SIZE.
    lot = None
    if str(order_type).upper() == "MARKET":
        lot = _find_filter(filters, "MARKET_LOT_SIZE") or _find_filter(filters, "LOT_SIZE")
    else:
        lot = _find_filter(filters, "LOT_SIZE") or _find_filter(filters, "MARKET_LOT_SIZE")
    if not lot:
        raise ValueError(f"LOT_SIZE filter missing for {sym}")

    step = _safe_float(lot.get("stepSize"), 0.0)
    min_qty = _safe_float(lot.get("minQty"), 0.0)
    max_qty = _safe_float(lot.get("maxQty"), 0.0) if lot.get("maxQty") is not None else float("inf")
    min_notional = _extract_min_notional(filters)
    qty_precision = None
    try:
        qty_precision = int(target.get("quantityPrecision")) if target.get("quantityPrecision") is not None else None
    except Exception:
        qty_precision = None

    rules = SymbolRules(symbol=sym, qty_step=step, min_qty=min_qty, max_qty=max_qty, min_notional=min_notional, qty_precision=qty_precision)
    self._symbol_rules_cache[sym] = rules
    return rules

async def close(self) -> None:
        await self.client.aclose()
