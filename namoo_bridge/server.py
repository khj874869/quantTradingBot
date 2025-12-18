from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from namoo_bridge.wmca_client import WmcaClient


app = FastAPI(title="namoo_bridge", version="0.1.0")


class OrderIn(BaseModel):
    symbol: str
    side: str  # BUY/SELL
    order_type: str = "MARKET"  # MARKET/LIMIT
    qty: float
    price: float | None = None
    client_order_id: str
    meta: dict[str, Any] = {}


_client: WmcaClient | None = None


def get_client() -> WmcaClient:
    global _client
    if _client is None:
        dll_path = os.environ.get("WMCA_DLL_PATH")
        if not dll_path:
            raise RuntimeError("Set WMCA_DLL_PATH env var to the absolute path of wmca.dll")
        _client = WmcaClient(dll_path)
    return _client


@app.get("/ping")
def ping():
    return {"ok": True}


@app.post("/connect")
def connect():
    try:
        c = get_client()
        c.connect()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/quote")
def quote(symbol: str):
    """Return last price and orderbook snapshot.

    This endpoint is intentionally minimal so the main bot can call `get_last_price`.
    """
    try:
        c = get_client()
        q = c.get_quote(symbol)
        return q
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/order")
def order(inp: OrderIn):
    try:
        c = get_client()
        return c.place_order(inp.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/equity")
def equity():
    try:
        c = get_client()
        return {"equity": c.get_equity()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/positions")
def positions():
    try:
        c = get_client()
        return {"positions": c.get_positions()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
