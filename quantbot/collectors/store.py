from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from quantbot.storage.db import get_session
from quantbot.storage.models import CandleModel, NewsModel
from quantbot.common.types import NewsItem

def upsert_candles(venue: str, symbol: str, timeframe: str, df: pd.DataFrame):
    rows = []
    for ts, r in df.iterrows():
        rows.append(CandleModel(
            venue=venue, symbol=symbol, timeframe=timeframe, ts=ts.to_pydatetime(),
            open=float(r["open"]), high=float(r["high"]), low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]),
        ))
    with get_session() as s:
        for m in rows:
            s.merge(m)
        s.commit()

def load_candles_df(venue: str, symbol: str, timeframe: str, limit: int = 2000) -> pd.DataFrame:
    with get_session() as s:
        q = (select(CandleModel)
             .where(CandleModel.venue==venue, CandleModel.symbol==symbol, CandleModel.timeframe==timeframe)
             .order_by(CandleModel.ts.desc())
             .limit(limit))
        rows = list(s.execute(q).scalars())
    if not rows:
        return pd.DataFrame()
    rows = list(reversed(rows))
    df = pd.DataFrame([{
        "ts": r.ts, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume
    } for r in rows])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").sort_index()
    return df

def insert_news(item: NewsItem):
    with get_session() as s:
        m = NewsModel(ts=item.ts, source=item.source, title=item.title, body=item.body, url=item.url,
                      score=item.score, hits={"hits": item.hits})
        s.add(m)
        s.commit()
