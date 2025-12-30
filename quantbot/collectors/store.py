from __future__ import annotations

"""Lightweight local persistence using sqlite3.

The earlier version depended on SQLAlchemy/Postgres. This repository is
intended to run locally with minimal dependencies, so we use SQLite.

Tables
------
- candles(venue, symbol, tf, ts_ms, open, high, low, close, volume)
- news(ts_ms, ts_iso, source, title, score, hits_json)
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

from quantbot.utils.time import utc_now
from quantbot.common.types import NewsItem


DEFAULT_DB_PATH = Path("state") / "quantbot.sqlite"


def _db_path() -> Path:
    p = Path(os.environ.get("QUANTBOT_DB_PATH", str(DEFAULT_DB_PATH)))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candles(
                venue TEXT NOT NULL,
                symbol TEXT NOT NULL,
                tf TEXT NOT NULL,
                ts_ms INTEGER NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY(venue, symbol, tf, ts_ms)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news(
                ts_ms INTEGER NOT NULL,
                ts_iso TEXT NOT NULL,
                source TEXT,
                title TEXT,
                score REAL,
                hits_json TEXT
            )
            """
        )


def upsert_candles(venue: str, symbol: str, tf: str, df: pd.DataFrame) -> None:
    """Upsert OHLCV candles.

    `df` must contain columns: open, high, low, close, volume. Index can be a
    DatetimeIndex or a column named 'ts'/'timestamp'.
    """
    if df is None or df.empty:
        return
    _init()

    dfx = df.copy()
    if isinstance(dfx.index, pd.DatetimeIndex):
        ts_ms = (dfx.index.astype("int64") // 1_000_000).astype("int64")
    else:
        # try a timestamp column
        tcol = None
        for c in ["ts", "timestamp", "time"]:
            if c in dfx.columns:
                tcol = c
                break
        if tcol is None:
            raise ValueError("DataFrame must have DatetimeIndex or a ts/timestamp/time column")
        ts_ms = (pd.to_datetime(dfx[tcol]).astype("int64") // 1_000_000).astype("int64")

    rows = []
    for i, (_, row) in enumerate(dfx.iterrows()):
        rows.append(
            (
                venue,
                symbol,
                tf,
                int(ts_ms[i]),
                float(row.get("open")),
                float(row.get("high")),
                float(row.get("low")),
                float(row.get("close")),
                float(row.get("volume")),
            )
        )

    with _connect() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO candles(venue, symbol, tf, ts_ms, open, high, low, close, volume)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )


def load_candles_df(venue: str, symbol: str, tf: str, limit: int = 500) -> pd.DataFrame:
    """Load last N candles as a DataFrame indexed by UTC datetime."""
    _init()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT ts_ms, open, high, low, close, volume
            FROM candles
            WHERE venue=? AND symbol=? AND tf=?
            ORDER BY ts_ms DESC
            LIMIT ?
            """,
            (venue, symbol, tf, int(limit)),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    rows = list(reversed(rows))
    ts = pd.to_datetime([r[0] for r in rows], unit="ms", utc=True)
    out = pd.DataFrame(
        {
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
            "volume": [r[5] for r in rows],
        },
        index=ts,
    )
    return out


def insert_news(item: NewsItem) -> None:
    _init()
    ts = item.ts or utc_now()
    ts_ms = int(ts.timestamp() * 1000)
    hits_json = json.dumps(item.hits or [], ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO news(ts_ms, ts_iso, source, title, score, hits_json) VALUES(?,?,?,?,?,?)",
            (ts_ms, ts.isoformat(), item.source, item.title, float(item.score), hits_json),
        )
