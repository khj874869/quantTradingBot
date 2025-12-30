from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

Venue = Literal[
    "upbit",
    "binance",
    "binance_futures",
    "kis",
    "namoo",
    "namoo_stock",
    "kiwoom",
    "demo",
]


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Candle:
    venue: Venue
    symbol: str
    timeframe: str  # "1m","5m","10m","15m","240m","1d","1w","1M"
    ts: datetime  # candle open time
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
    side: Literal["BUY", "SELL", "HOLD"]
    score: float
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderRequest:
    venue: Venue
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    qty: float
    price: Optional[float] = None
    client_order_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderUpdate:
    venue: Venue
    order_id: str
    symbol: str
    status: Literal["NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED"]
    filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    fee: Optional[float] = None
    client_order_id: Optional[str] = None
    ts: datetime = field(default_factory=utc_now_dt)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    req: OrderRequest
    update: OrderUpdate
