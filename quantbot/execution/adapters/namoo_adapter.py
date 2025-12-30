from __future__ import annotations

from typing import Any

import httpx

from quantbot.execution.adapters.base import BrokerAdapter
from quantbot.common.types import OrderRequest, OrderUpdate
from quantbot.utils.time import utc_now


class NamooAdapter(BrokerAdapter):
    """나무증권 자동매매 브릿지 어댑터.

    왜 브릿지가 필요한가?
      - 나무/QV OpenAPI는 Windows 전용 32-bit DLL(wmca.dll) 기반이며 이벤트(윈도우 메시지) 방식입니다.
      - 따라서 본 프로젝트는 별도 프로세스(예: FastAPI)로 DLL을 감싼 뒤 HTTP로 호출하는 구조를 제공합니다.

    브릿지 구현은 repo의 `namoo_bridge/` 참고.
    """

    def __init__(self, bridge_url: str = "http://127.0.0.1:8700"):
        self.bridge_url = bridge_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=20)

    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        payload: dict[str, Any] = {
            "symbol": req.symbol,
            "side": req.side.upper(),
            "order_type": (req.order_type or "MARKET").upper(),
            "qty": float(req.qty),
            "price": float(req.price) if req.price is not None else None,
            "client_order_id": req.client_order_id,
            "meta": req.meta or {},
        }
        try:
            r = await self.client.post(f"{self.bridge_url}/order", json=payload)
            r.raise_for_status()
            data = r.json()
            return OrderUpdate(
                venue=req.venue,
                order_id=str(data.get("order_id") or ""),
                client_order_id=req.client_order_id,
                symbol=req.symbol,
                status=str(data.get("status") or "NEW"),
                filled_qty=float(data.get("filled_qty") or 0.0),
                avg_fill_price=(float(data["avg_fill_price"]) if data.get("avg_fill_price") is not None else None),
                fee=(float(data["fee"]) if data.get("fee") is not None else None),
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
        r = await self.client.get(f"{self.bridge_url}/quote", params={"symbol": symbol})
        r.raise_for_status()
        data = r.json()
        return float(data["last_price"])

    async def get_equity(self) -> float:
        r = await self.client.get(f"{self.bridge_url}/equity")
        r.raise_for_status()
        data = r.json()
        return float(data.get("equity") or 0.0)

    async def get_positions(self) -> dict[str, float]:
        r = await self.client.get(f"{self.bridge_url}/positions")
        r.raise_for_status()
        data = r.json()
        out: dict[str, float] = {}
        for k, v in (data.get("positions") or {}).items():
            out[str(k)] = float(v)
        return out
