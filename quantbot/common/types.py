from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Dict, Any, List
from datetime import datetime

Venue = Literal["upbit", "binance", "kis", "namoo", "demo"]

@dataclass(frozen=True)
class Candle:
    venue: Venue
    symbol: str
    timeframe: str  # "1m","5m","10m","15m","240m","1d","1w","1M"
    ts: datetime    # candle open time (UTC recommended)
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass(frozen=True)
class NewsItem:
    ts: datetime
    source: str
    title: str
    body: str = ""
    url: str = ""
    score: float = 0.0
    hits: List[str] = field(default_factory=list)

@dataclass(frozen=True)
class Signal:
    ts: datetime
    venue: Venue
    symbol: str
    side: Literal["BUY","SELL","HOLD"]
    score: float
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    venue: Venue
    symbol: str
    side: Literal["BUY","SELL"]
    order_type: Literal["MARKET","LIMIT"]
    qty: float
    price: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class OrderUpdate:
    venue: Venue
    order_id: str
    client_order_id: str
    symbol: str
    status: Literal["NEW","PARTIALLY_FILLED","FILLED","CANCELED","REJECTED"]
    filled_qty: float
    avg_fill_price: float | None
    fee: float | None
    ts: datetime
    raw: Dict[str, Any] = field(default_factory=dict)
