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

        self._time_offset_ms: int = 0
        self._dual_side_cache: Optional[bool] = None
        self._dual_side_cache_ts_ms: int = 0

    def _ts(self) -> int:
        return int(time.time() * 1000) + int(self._time_offset_ms or 0)

    async def sync_time(self) -> int:
        """Sync local clock offset against Binance server time.

        Returns the computed offset (server_ms - local_ms).
        """
        try:
            data = await self._public_get("/fapi/v1/time", {})
            server_ms = int(data.get("serverTime") or 0)
            local_ms = int(time.time() * 1000)
            if server_ms > 0:
                self._time_offset_ms = int(server_ms - local_ms)
            return int(self._time_offset_ms or 0)
        except Exception:
            return int(self._time_offset_ms or 0)

    async def get_dual_side_position(self, *, max_age_sec: int = 300) -> Optional[bool]:
        """Return True if account is Hedge Mode (dualSidePosition), False if One-way, None on failure."""
        try:
            now_ms = int(time.time() * 1000)
            if self._dual_side_cache is not None and (now_ms - int(self._dual_side_cache_ts_ms or 0)) < int(max_age_sec * 1000):
                return bool(self._dual_side_cache)
            data = await self._signed_request("GET", "/fapi/v1/positionSide/dual", {})
            # response: {"dualSidePosition": true}
            dual = bool(data.get("dualSidePosition"))
            self._dual_side_cache = dual
            self._dual_side_cache_ts_ms = now_ms
            return dual
        except Exception:
            return None

    def invalidate_exchange_info(self) -> None:
        self._exchange_info_cache = None

    def invalidate_symbol_rules(self, symbol: Optional[str] = None) -> None:
        if symbol is None:
            self._symbol_rules_cache = {}
        else:
            s = self._normalize_symbol(symbol)
            if s in self._symbol_rules_cache:
                del self._symbol_rules_cache[s]

    async def refresh_symbol_rules(self, symbol: str, *, order_type: str = "MARKET") -> Optional[SymbolRules]:
        """Force-refresh exchangeInfo + symbol rules cache."""
        try:
            self.invalidate_exchange_info()
            self.invalidate_symbol_rules(symbol)
            return await self.get_symbol_rules(symbol, order_type=str(order_type).upper())
        except Exception:
            return None

    async def list_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        sym = self._normalize_symbol(symbol)
        data = await self._signed_request("GET", "/fapi/v1/openOrders", {"symbol": sym})
        if isinstance(data, list):
            return data
        return []

    async def cancel_order(self, symbol: str, *, order_id: Optional[str] = None, orig_client_order_id: Optional[str] = None) -> bool:
        sym = self._normalize_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}
        if order_id:
            params["orderId"] = order_id
        if orig_client_order_id:
            params["origClientOrderId"] = orig_client_order_id
        if not (order_id or orig_client_order_id):
            return False
        try:
            await self._signed_request("DELETE", "/fapi/v1/order", params)
            return True
        except Exception:
            return False

    async def cancel_own_open_orders(self, symbol: str, *, min_age_sec: int = 60) -> Dict[str, Any]:
        """Cancel bot-owned open orders (best-effort) to recover from max-open-orders.

        Cancels only orders whose clientOrderId looks like QuantBot generated:
          - contains '-ENTRY-' or '-EXIT-'
          - or startswith '{SYMBOL}-'
        and older than min_age_sec.
        """
        out = {"canceled": 0, "scanned": 0}
        try:
            sym = self._normalize_symbol(symbol)
            now_ms = int(time.time() * 1000)
            min_age_ms = int(max(0, min_age_sec) * 1000)
            orders = await self.list_open_orders(sym)
            out["scanned"] = len(orders)
            for o in orders:
                try:
                    coid = str(o.get("clientOrderId") or "")
                    oid = str(o.get("orderId") or "")
                    t = int(o.get("time") or o.get("updateTime") or 0)
                    age = now_ms - t if t else 0
                    looks_like_bot = ("-ENTRY-" in coid) or ("-EXIT-" in coid) or coid.startswith(f"{sym}-")
                    if not looks_like_bot:
                        continue
                    if min_age_ms and age and age < min_age_ms:
                        continue
                    ok = await self.cancel_order(sym, order_id=oid or None, orig_client_order_id=coid or None)
                    if ok:
                        out["canceled"] += 1
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _sign(self, query: str) -> str:
        return hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    async def _signed_request(self, method: str, path: str, params: Dict[str, Any]) -> Any:
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

    async def _public_get(self, path: str, params: Dict[str, Any]) -> Any:
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

        # positionSide handling (hedge vs one-way)
        desired_ps = meta.get("positionSide")
        ps_param: Optional[str] = None
        try:
            desired_str = str(desired_ps).upper() if desired_ps is not None else ""
        except Exception:
            desired_str = ""
        dual = None
        try:
            dual = await self.get_dual_side_position()
        except Exception:
            dual = None

        if dual is True:
            # Hedge mode requires LONG/SHORT.
            if desired_str in {"LONG", "SHORT"}:
                ps_param = desired_str
            else:
                ps_param = "LONG" if side == "BUY" else "SHORT"
        elif dual is False:
            # One-way mode: omit positionSide (or BOTH if explicitly requested).
            if desired_str == "BOTH":
                ps_param = "BOTH"
            else:
                ps_param = None
        else:
            # Unknown; be conservative (omit) unless explicitly provided.
            if desired_str in {"LONG", "SHORT", "BOTH"}:
                ps_param = desired_str

        if ps_param is not None:
            params["positionSide"] = ps_param

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
