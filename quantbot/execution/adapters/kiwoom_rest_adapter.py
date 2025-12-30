from __future__ import annotations

import time
from typing import Any, Dict, Optional, List

import httpx

from quantbot.common.types import OrderRequest, OrderUpdate
from quantbot.execution.adapters.base import BrokerAdapter
from quantbot.utils.time import utc_now


class KiwoomRestAdapter(BrokerAdapter):
    """Kiwoom REST API adapter (stocks)."""

    def __init__(
        self,
        appkey: str,
        secretkey: str,
        account_no: str,
        base_url: str = "https://api.kiwoom.com",
        timeout: float = 5.0,
    ):
        self.appkey = appkey
        self.secretkey = secretkey
        self.account_no = account_no
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout)

        self._token: Optional[str] = None
        self._token_expiry_ts: float = 0.0

    async def _get_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expiry_ts - 10:
            return self._token

        r = await self.client.post(
            f"{self.base_url}/oauth2/token",
            json={"grant_type": "client_credentials", "appkey": self.appkey, "secretkey": self.secretkey},
            headers={"Content-Type": "application/json;charset=UTF-8"},
        )
        r.raise_for_status()
        data = r.json()
        tok = data.get("token")
        if not tok:
            raise RuntimeError(f"Kiwoom token response missing token: {data}")

        expires_dt = str(data.get("expires_dt") or "")
        self._token = tok
        try:
            if len(expires_dt) >= 14:
                import datetime as _dt

                exp = _dt.datetime.strptime(expires_dt[:14], "%Y%m%d%H%M%S")
                self._token_expiry_ts = exp.timestamp()
            else:
                self._token_expiry_ts = now + 3600
        except Exception:
            self._token_expiry_ts = now + 3600

        return self._token

    async def _post_tr(self, path: str, api_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        tok = await self._get_token()
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {tok}",
            "api-id": api_id,
        }
        r = await self.client.post(f"{self.base_url}{path}", json=body, headers=headers)
        r.raise_for_status()
        return r.json()

    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        side = req.side.upper()
        order_type = req.order_type.upper()
        meta = req.meta or {}

        try:
            # Kiwoom order uses trde_tp: 0 limit, 3 market, 2 IOC(limit)
            tif = (meta.get("timeInForce") or "GTC").upper()
            trde_tp = "0"
            if order_type == "MARKET":
                trde_tp = "3"
            elif tif == "IOC":
                trde_tp = "2"

            api_id = "kt10000" if side == "BUY" else "kt10001"
            body = {
                "dmst_stex_tp": meta.get("dmst_stex_tp", "KRX"),
                "accno": meta.get("accno") or self.account_no,
                "passwd": meta.get("passwd", ""),
                "input_pw": meta.get("input_pw", "00"),
                "stk_cd": req.symbol,
                "ord_qty": str(req.qty),
                "ord_uv": str(req.price or 0),
                "trde_tp": trde_tp,
                "cond_uv": meta.get("cond_uv", ""),
            }

            data = await self._post_tr("/api/dostk/ordr", api_id, body)

            order_no = str(data.get("ord_no") or data.get("order_no") or data.get("ordNo") or "")
            status = str(data.get("status") or data.get("result") or "NEW")
            filled_qty = float(data.get("filled_qty") or 0.0)
            avg_px = data.get("avg_fill_price")

            return OrderUpdate(
                venue=req.venue,
                symbol=req.symbol,
                order_id=order_no,
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

    def _normalize_orderbook(self, data: Dict[str, Any]) -> Dict[str, Any]:
        bids: List[List[float]] = []
        asks: List[List[float]] = []
        try:
            for i in range(1, 11):
                bp = data.get(f"buy_{i}th_pre_bid") or data.get(f"bid{i}") or data.get(f"buy{i}_price")
                bq = data.get(f"buy_{i}th_pre_bid_rsqn") or data.get(f"bid{i}_qty") or data.get(f"buy{i}_qty") or 0
                ap = data.get(f"sel_{i}th_pre_bid") or data.get(f"ask{i}") or data.get(f"sel{i}_price")
                aq = data.get(f"sel_{i}th_pre_bid_rsqn") or data.get(f"ask{i}_qty") or data.get(f"sel{i}_qty") or 0
                if bp is not None and float(bp) > 0:
                    bids.append([float(bp), float(bq or 0.0)])
                if ap is not None and float(ap) > 0:
                    asks.append([float(ap), float(aq or 0.0)])
        except Exception:
            pass
        return {"bids": bids, "asks": asks, "raw": data}

    async def get_orderbook(self, symbol: str) -> Dict[str, Any]:
        data = await self._post_tr("/api/dostk/mrkcond", "ka10004", {"stk_cd": symbol})
        if isinstance(data, dict):
            return self._normalize_orderbook(data)
        return {"bids": [], "asks": [], "raw": data}

    async def get_last_price(self, symbol: str) -> float:
        ob = await self.get_orderbook(symbol)
        bids = ob.get("bids") or []
        asks = ob.get("asks") or []
        bid = float(bids[0][0]) if bids else 0.0
        ask = float(asks[0][0]) if asks else 0.0
        if bid and ask:
            return (bid + ask) / 2
        return bid or ask or 0.0

    async def get_equity(self) -> float:
        import datetime as _dt

        ymd = _dt.datetime.now().strftime("%Y%m%d")
        data = await self._post_tr("/api/dostk/acnt", "kt00017", {"qry_dt": ymd})
        for k in ("day_stk_asst", "tot_evlt_amt", "dbst_bal"):
            if k in data:
                try:
                    return float(data[k])
                except Exception:
                    continue
        return 0.0

    async def get_positions(self) -> Dict[str, float]:
        import datetime as _dt

        ymd = _dt.datetime.now().strftime("%Y%m%d")
        data = await self._post_tr("/api/dostk/acnt", "kt00017", {"qry_dt": ymd})
        items = data.get("day_bal_rt") or data.get("positions") or data.get("items")
        out: Dict[str, float] = {}
        if isinstance(items, list):
            for it in items:
                try:
                    code = str(it.get("stk_cd") or it.get("code") or "")
                    qty = float(it.get("rmnd_qty") or it.get("qty") or 0.0)
                    if code and qty:
                        out[code] = qty
                except Exception:
                    continue
        return out

    async def close(self) -> None:
        await self.client.aclose()
