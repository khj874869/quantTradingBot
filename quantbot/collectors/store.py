from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from quantbot.storage.db import get_session
from quantbot.storage.models import CandleModel, NewsModel
from quantbot.common.types import NewsItem


def upsert_candles(venue: str, symbol: str, timeframe: str, df: pd.DataFrame):
    if df is None or df.empty:
        return

    df = df.copy()

    # 1) ts가 컬럼이면 인덱스로 올리고, 아니면 인덱스를 ts로 사용
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"]).set_index("ts")
    else:
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True, errors="coerce")
        df = df[~df.index.isna()]
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

    # 2) 필수 컬럼 체크
    need = {"open", "high", "low", "close", "volume"}
    missing = need - set(df.columns)
    if missing:
        raise KeyError(f"upsert_candles: missing columns {missing}, got={list(df.columns)}")

    rows = []
    for ts, r in df.iterrows():
        rows.append({
            "venue": venue,
            "symbol": symbol,
            "timeframe": timeframe,
            "ts": ts.to_pydatetime(),          # ✅ 인덱스에서 ts 사용
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["volume"]),
        })

    stmt = pg_insert(CandleModel.__table__).values(rows)

    stmt = stmt.on_conflict_do_update(
    index_elements=["venue", "symbol", "timeframe", "ts"],
    set_={
        "open": stmt.excluded.open,
        "high": stmt.excluded.high,
        "low": stmt.excluded.low,
        "close": stmt.excluded.close,
        "volume": stmt.excluded.volume,
        },
    )

    with get_session() as s:
        s.execute(stmt)
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
