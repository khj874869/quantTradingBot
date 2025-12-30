from __future__ import annotations

import time
from typing import Any

import httpx

from quantbot.execution.adapters.base import BrokerAdapter
from quantbot.common.types import OrderRequest, OrderUpdate
from quantbot.utils.time import utc_now


class KISAdapter(BrokerAdapter):
    """Korea Investment & Securities (KIS Developers) REST adapter.

    Official sample repo: https://github.com/koreainvestment/open-trading-api
    Portal endpoints include:
      - /oauth2/tokenP for access tokens
      - /uapi/domestic-stock/v1/quotations/inquire-price (TR: FHKST01010100)
      - /uapi/domestic-stock/v1/trading/order-cash (TR: TTTC0802U buy / TTTC0801U sell)

    NOTE: KIS API parameters can vary by account type and environment (prod/vps). If your
    account rejects the request, check the portal for required fields and TR IDs.
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        product_code: str,
        base_url: str = "https://openapi.koreainvestment.com:9443",
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no
        self.product_code = product_code
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=20)
        self._access_token: str | None = None
        self._access_token_exp: float = 0.0

    async def ensure_token(self) -> None:
        if self._access_token and time.time() < (self._access_token_exp - 60):
            return
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        r = await self.client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        self._access_token = data.get("access_token")
        expires_in = float(data.get("expires_in") or 0)
        # KIS docs typically state 24h validity, but trust response.
        self._access_token_exp = time.time() + max(0.0, expires_in)
        if not self._access_token:
            raise RuntimeError(f"Failed to obtain KIS token: {data}")

    async def _hashkey(self, body: dict[str, Any]) -> str:
        """Generate hashkey for POST bodies (recommended/required for some endpoints)."""
        await self.ensure_token()
        url = f"{self.base_url}/uapi/hashkey"
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self._access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        r = await self.client.post(url, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        hk = data.get("HASH") or data.get("hash") or data.get("hashkey")
        if not hk:
            raise RuntimeError(f"Failed to obtain hashkey: {data}")
        return hk

    def _headers(self, tr_id: str, hashkey: str | None = None) -> dict[str, str]:
        h = {
            "content-type": "application/json",
            "authorization": f"Bearer {self._access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }
        if hashkey:
            h["hashkey"] = hashkey
        return h

    async def get_last_price(self, symbol: str) -> float:
        """symbol: 6-digit KRX code (e.g., 005930)."""
        await self.ensure_token()
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {
            "fid_cond_mrkt_div_code": "J",  # 주식
            "fid_input_iscd": symbol,
        }
        headers = self._headers("FHKST01010100")
        r = await self.client.get(url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()
        # official response contains output -> stck_prpr (현재가)
        out = data.get("output") or {}
        px = out.get("stck_prpr") or out.get("stck_prpr1") or out.get("stck_prpr2")
        if px is None:
            raise RuntimeError(f"Unexpected inquire-price response: {data}")
        return float(px)

    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        await self.ensure_token()

        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        side = req.side.upper()
        order_type = (req.order_type or "MARKET").upper()

        # KIS order division codes (commonly used):
        #   00: 지정가, 01: 시장가
        ord_dvsn = "00" if order_type == "LIMIT" else "01"
        ord_unpr = "0" if order_type != "LIMIT" else str(req.price if req.price is not None else 0)

        body = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.product_code,
            "PDNO": req.symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(req.qty),
            "ORD_UNPR": ord_unpr,
        }

        tr_id = "TTTC0802U" if side == "BUY" else "TTTC0801U"

        try:
            hk = await self._hashkey(body)
            headers = self._headers(tr_id, hashkey=hk)
            r = await self.client.post(url, headers=headers, json=body)
            r.raise_for_status()
            data = r.json()

            out = data.get("output") or {}
            order_id = out.get("ODNO") or out.get("odno") or ""

            # KIS returns 성공여부 in rt_cd, msg_cd, msg1
            rt_cd = data.get("rt_cd")
            status = "NEW" if str(rt_cd) == "0" else "REJECTED"

            return OrderUpdate(
                venue=req.venue,
                order_id=str(order_id),
                client_order_id=req.client_order_id,
                symbol=req.symbol,
                status=status,
                filled_qty=0.0,
                avg_fill_price=None,
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

    async def get_equity(self) -> float:
        # TODO: Implement via '주식잔고조회' endpoint.
        return 0.0

    async def get_positions(self) -> dict[str, float]:
        # TODO: Implement via '주식잔고조회' endpoint.
        return {}
