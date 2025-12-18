from __future__ import annotations

from sqlalchemy import String, Float, DateTime, Integer, JSON, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from quantbot.storage.db import Base

class CandleModel(Base):
    __tablename__ = "candles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    ts: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("venue","symbol","timeframe","ts", name="uq_candle"),
        Index("ix_candle_lookup", "venue","symbol","timeframe","ts"),
    )

class NewsModel(Base):
    __tablename__ = "news"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(String(4000), default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    hits: Mapped[dict] = mapped_column(JSON, default=dict)

class OrderModel(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    client_order_id: Mapped[str] = mapped_column(String(80), unique=True)
    order_id: Mapped[str] = mapped_column(String(120), index=True)
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    ts: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
