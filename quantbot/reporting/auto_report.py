from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None  # pragma: no cover


def _parse_iso(s: str) -> Optional[datetime]:
    s = s.strip()
    if not s:
        return None
    # Handle common 'Z' suffix.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_ts(obj: Dict[str, Any]) -> datetime:
    """Best-effort timestamp extraction. Returns UTC datetime."""
    ts_ms = obj.get("ts_ms") or obj.get("timestamp_ms") or obj.get("time_ms")
    if ts_ms is not None:
        try:
            ms = int(ts_ms)
            return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        except Exception:
            pass
    ts = obj.get("ts") or obj.get("timestamp") or obj.get("time") or obj.get("created_at")
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except Exception:
            pass
    if isinstance(ts, str):
        dt = _parse_iso(ts)
        if dt:
            return dt
    # fallback: now
    return datetime.now(tz=timezone.utc)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                # tolerate non-json lines
                continue
    return out


@dataclass
class FillRow:
    ts: datetime
    venue: str
    symbol: str
    side: str
    qty: float
    px: float
    fee: float
    fee_ccy: str
    order_id: str
    meta: Dict[str, Any]

    @property
    def notional(self) -> float:
        return abs(self.qty * self.px)


def _get_str(d: Dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def _get_float(d: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except Exception:
            continue
    return default


def _normalize_side(s: str) -> str:
    s = (s or "").upper().strip()
    if s in ("BUY", "B"):
        return "BUY"
    if s in ("SELL", "S"):
        return "SELL"
    return s or "UNKNOWN"


def parse_fills(raw: List[Dict[str, Any]], *, account_tag: Optional[str] = None,
                venue: Optional[str] = None, symbol: Optional[str] = None,
                since: Optional[datetime] = None) -> List[FillRow]:
    fills: List[FillRow] = []
    for r in raw:
        ts = parse_ts(r)
        if since and ts < since:
            continue

        # Optional filters
        if account_tag is not None:
            tag = r.get("account_tag") or r.get("acct") or r.get("account")
            if tag != account_tag:
                continue
        v = _get_str(r, "venue", default="")
        if venue and v != venue:
            continue
        sym = _get_str(r, "symbol", "sym", default="")
        if symbol and sym != symbol:
            continue

        side = _normalize_side(_get_str(r, "side", "direction", default="UNKNOWN"))
        qty = _get_float(r, "qty", "filled_qty", "executed_qty", default=0.0)
        px = _get_float(r, "avg_fill_price", "fill_price", "px", "price", default=0.0)
        fee = _get_float(r, "fee", "commission", "fees", default=0.0)
        fee_ccy = _get_str(r, "fee_ccy", "commissionAsset", default="")
        order_id = _get_str(r, "client_order_id", "order_id", "id", default="")
        fills.append(FillRow(ts=ts, venue=v, symbol=sym, side=side, qty=qty, px=px,
                             fee=fee, fee_ccy=fee_ccy, order_id=order_id, meta=r))
    fills.sort(key=lambda x: x.ts)
    return fills


@dataclass
class PositionState:
    qty: float = 0.0   # positive=long, negative=short
    avg: float = 0.0   # average entry price (for current position)


@dataclass
class RealizedTrade:
    venue: str
    symbol: str
    close_ts: datetime
    pnl_gross: float
    fee: float
    pnl_net: float
    close_qty: float
    side: str  # "LONG_CLOSE" or "SHORT_CLOSE"
    holding_sec: float


def build_realized_trades(fills: List[FillRow]) -> List[RealizedTrade]:
    """Conservative ledger that supports long+short.
    Assumes avg-price accounting for remaining position.
    """
    states: Dict[Tuple[str, str], PositionState] = {}
    open_ts: Dict[Tuple[str, str], Optional[datetime]] = {}
    out: List[RealizedTrade] = []

    for f in fills:
        key = (f.venue, f.symbol)
        st = states.setdefault(key, PositionState())
        ot = open_ts.get(key)

        # Fee attribution: we attach fees to realized trades proportionally when closing.
        fee = f.fee

        if f.side == "BUY":
            if st.qty >= 0:
                # add/increase long
                new_qty = st.qty + f.qty
                if new_qty != 0:
                    st.avg = (st.avg * st.qty + f.px * f.qty) / new_qty if st.qty != 0 else f.px
                st.qty = new_qty
                if ot is None and st.qty != 0:
                    open_ts[key] = f.ts
            else:
                # closing short partially/fully
                close_qty = min(f.qty, abs(st.qty))
                pnl_gross = (st.avg - f.px) * close_qty  # short profit if price down
                # proportional fee allocation (best-effort)
                fee_alloc = fee * (close_qty / f.qty) if f.qty else fee
                pnl_net = pnl_gross - fee_alloc
                hold = (f.ts - (ot or f.ts)).total_seconds()
                out.append(RealizedTrade(
                    venue=f.venue, symbol=f.symbol, close_ts=f.ts,
                    pnl_gross=pnl_gross, fee=fee_alloc, pnl_net=pnl_net,
                    close_qty=close_qty, side="SHORT_CLOSE", holding_sec=hold
                ))
                # update remaining buy qty beyond closing becomes new long
                remaining = f.qty - close_qty
                st.qty = st.qty + close_qty  # st.qty is negative
                if st.qty == 0:
                    st.avg = 0.0
                    open_ts[key] = None
                if remaining > 0:
                    st.qty = remaining  # now long
                    st.avg = f.px
                    open_ts[key] = f.ts
        elif f.side == "SELL":
            if st.qty <= 0:
                # add/increase short
                new_qty = st.qty - f.qty  # more negative
                if new_qty != 0:
                    st.avg = (st.avg * abs(st.qty) + f.px * f.qty) / abs(new_qty) if st.qty != 0 else f.px
                st.qty = new_qty
                if ot is None and st.qty != 0:
                    open_ts[key] = f.ts
            else:
                # closing long partially/fully
                close_qty = min(f.qty, st.qty)
                pnl_gross = (f.px - st.avg) * close_qty
                fee_alloc = fee * (close_qty / f.qty) if f.qty else fee
                pnl_net = pnl_gross - fee_alloc
                hold = (f.ts - (ot or f.ts)).total_seconds()
                out.append(RealizedTrade(
                    venue=f.venue, symbol=f.symbol, close_ts=f.ts,
                    pnl_gross=pnl_gross, fee=fee_alloc, pnl_net=pnl_net,
                    close_qty=close_qty, side="LONG_CLOSE", holding_sec=hold
                ))
                remaining = f.qty - close_qty
                st.qty = st.qty - close_qty
                if st.qty == 0:
                    st.avg = 0.0
                    open_ts[key] = None
                if remaining > 0:
                    st.qty = -remaining  # now short
                    st.avg = f.px
                    open_ts[key] = f.ts
        else:
            continue

    return out


def max_consecutive_losses(trades: List[RealizedTrade]) -> int:
    m = 0
    cur = 0
    for t in trades:
        if t.pnl_net < 0:
            cur += 1
            m = max(m, cur)
        else:
            cur = 0
    return m


def profit_factor(trades: List[RealizedTrade]) -> float:
    pos = sum(t.pnl_net for t in trades if t.pnl_net > 0)
    neg = -sum(t.pnl_net for t in trades if t.pnl_net < 0)
    if neg <= 0:
        return float("inf") if pos > 0 else 0.0
    return pos / neg


def compute_equity_series(equity_rows: List[Dict[str, Any]], *, since: Optional[datetime] = None,
                          account_tag: Optional[str] = None) -> List[Tuple[datetime, float]]:
    out: List[Tuple[datetime, float]] = []
    for r in equity_rows:
        ts = parse_ts(r)
        if since and ts < since:
            continue
        if account_tag is not None:
            tag = r.get("account_tag") or r.get("acct") or r.get("account")
            if tag != account_tag:
                continue
        eq = r.get("equity") or r.get("eq") or r.get("balance")
        if eq is None:
            continue
        try:
            out.append((ts, float(eq)))
        except Exception:
            pass
    out.sort(key=lambda x: x[0])
    return out


def max_drawdown(equity: List[Tuple[datetime, float]]) -> Tuple[float, Optional[datetime], Optional[datetime]]:
    """Returns (mdd_frac, peak_ts, trough_ts)."""
    if not equity:
        return 0.0, None, None
    peak = equity[0][1]
    peak_ts = equity[0][0]
    mdd = 0.0
    trough_ts = None
    cur_peak_ts = peak_ts
    for ts, val in equity:
        if val > peak:
            peak = val
            cur_peak_ts = ts
        dd = (peak - val) / peak if peak != 0 else 0.0
        if dd > mdd:
            mdd = dd
            peak_ts = cur_peak_ts
            trough_ts = ts
    return mdd, peak_ts, trough_ts


def daily_pnl(trades: List[RealizedTrade]) -> Dict[str, float]:
    d: Dict[str, float] = {}
    for t in trades:
        day = t.close_ts.date().isoformat()
        d[day] = d.get(day, 0.0) + t.pnl_net
    return dict(sorted(d.items()))


def write_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    cols = sorted({k for r in rows for k in r.keys()})
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})


def try_plot(out_dir: Path, equity: List[Tuple[datetime, float]], daily: Dict[str, float]) -> None:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        return

    if equity:
        xs = [t for t, _ in equity]
        ys = [v for _, v in equity]
        plt.figure()
        plt.plot(xs, ys)
        plt.title("Equity (raw)")
        plt.xlabel("Time (UTC)")
        plt.ylabel("Equity")
        plt.tight_layout()
        plt.savefig(out_dir / "equity.png", dpi=150)
        plt.close()

    if daily:
        xs = list(daily.keys())
        ys = [daily[k] for k in xs]
        plt.figure()
        plt.bar(xs, ys)
        plt.title("Daily Realized PnL (net)")
        plt.xlabel("Day")
        plt.ylabel("PnL")
        plt.xticks(rotation=60, ha="right")
        plt.tight_layout()
        plt.savefig(out_dir / "daily_pnl.png", dpi=150)
        plt.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Quantbot auto report (fills.jsonl + equity_history.jsonl).")
    ap.add_argument("--state-dir", default="state", help="Directory containing fills.jsonl and equity_history.jsonl")
    ap.add_argument("--days", type=int, default=30, help="Lookback window in days")
    ap.add_argument("--account-tag", default=None, help="Filter by account_tag if present in records")
    ap.add_argument("--venue", default=None, help="Filter by venue")
    ap.add_argument("--symbol", default=None, help="Filter by symbol")
    ap.add_argument("--out-dir", default=None, help="Output directory (default: reports/<timestamp>)")
    ap.add_argument("--no-plots", action="store_true", help="Disable matplotlib plot output")
    args = ap.parse_args()

    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(days=int(args.days))

    state_dir = Path(args.state_dir)
    fills_path = state_dir / "fills.jsonl"
    equity_path = state_dir / "equity_history.jsonl"

    fills_raw = read_jsonl(fills_path)
    fills = parse_fills(
        fills_raw,
        account_tag=args.account_tag,
        venue=args.venue,
        symbol=args.symbol,
        since=since,
    )
    trades = build_realized_trades(fills)

    equity_raw = read_jsonl(equity_path)
    equity = compute_equity_series(equity_raw, since=since, account_tag=args.account_tag)

    out_dir = Path(args.out_dir) if args.out_dir else Path("reports") / now.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Summary metrics
    net = sum(t.pnl_net for t in trades)
    gross = sum(t.pnl_gross for t in trades)
    fees = sum(t.fee for t in trades)

    wins = [t for t in trades if t.pnl_net > 0]
    losses = [t for t in trades if t.pnl_net < 0]
    win_rate = (len(wins) / len(trades)) if trades else 0.0
    avg_win = (sum(t.pnl_net for t in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(t.pnl_net for t in losses) / len(losses)) if losses else 0.0
    pf = profit_factor(trades)
    max_ls = max_consecutive_losses(trades)
    hold_avg = (sum(t.holding_sec for t in trades) / len(trades)) if trades else 0.0

    mdd, mdd_peak, mdd_trough = max_drawdown(equity)

    # 30d return (best-effort) from equity series
    ret_30d = None
    if len(equity) >= 2:
        first = equity[0][1]
        last = equity[-1][1]
        if first != 0:
            ret_30d = (last - first) / first

    daily = daily_pnl(trades)

    summary = {
        "window_days": args.days,
        "since_utc": since.isoformat(),
        "until_utc": now.isoformat(),
        "filters": {"account_tag": args.account_tag, "venue": args.venue, "symbol": args.symbol},
        "fills_count": len(fills),
        "realized_trades_count": len(trades),
        "pnl_net": net,
        "pnl_gross": gross,
        "fees_total": fees,
        "win_rate": win_rate,
        "avg_win_net": avg_win,
        "avg_loss_net": avg_loss,
        "profit_factor": pf,
        "max_consecutive_losses": max_ls,
        "avg_holding_sec": hold_avg,
        "max_drawdown_frac": mdd,
        "mdd_peak_utc": mdd_peak.isoformat() if mdd_peak else None,
        "mdd_trough_utc": mdd_trough.isoformat() if mdd_trough else None,
        "return_frac_from_equity": ret_30d,
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # Write detail CSVs
    write_csv(out_dir / "realized_trades.csv", [
        {
            "close_ts_utc": t.close_ts.isoformat(),
            "venue": t.venue,
            "symbol": t.symbol,
            "side": t.side,
            "close_qty": t.close_qty,
            "pnl_gross": t.pnl_gross,
            "fee": t.fee,
            "pnl_net": t.pnl_net,
            "holding_sec": round(t.holding_sec, 3),
        } for t in trades
    ])

    write_csv(out_dir / "daily_pnl.csv", [{"day": k, "pnl_net": v} for k, v in daily.items()])

    write_csv(out_dir / "equity_series.csv", [
        {"ts_utc": ts.isoformat(), "equity": eq} for ts, eq in equity
    ])

    # Human-friendly report.md
    def fmt_pct(x: Optional[float]) -> str:
        if x is None:
            return "n/a"
        return f"{x*100:.3f}%"

    md = []
    md.append(f"# Quantbot Auto Report")
    md.append("")
    md.append(f"- Window: last **{args.days}** days")
    md.append(f"- Since (UTC): `{since.isoformat()}`")
    md.append(f"- Until (UTC): `{now.isoformat()}`")
    md.append(f"- Filters: account_tag={args.account_tag}, venue={args.venue}, symbol={args.symbol}")
    md.append("")
    md.append("## Summary")
    md.append(f"- Fills: **{len(fills)}**")
    md.append(f"- Realized trades: **{len(trades)}** (derived via conservative ledger)")
    md.append(f"- Net PnL: **{net:.6f}**")
    md.append(f"- Gross PnL: **{gross:.6f}**")
    md.append(f"- Fees (allocated): **{fees:.6f}**")
    md.append(f"- Win rate: **{fmt_pct(win_rate)}** (wins={len(wins)}, losses={len(losses)})")
    md.append(f"- Avg win (net): **{avg_win:.6f}**")
    md.append(f"- Avg loss (net): **{avg_loss:.6f}**")
    md.append(f"- Profit factor: **{pf:.4f}**")
    md.append(f"- Max consecutive losses: **{max_ls}**")
    md.append(f"- Avg holding time: **{hold_avg:.1f}s**")
    md.append(f"- Max drawdown (equity): **{fmt_pct(mdd)}**")
    md.append(f"- 30d return (from equity series): **{fmt_pct(ret_30d)}**")
    md.append("")
    md.append("## Outputs")
    md.append("- `summary.json`")
    md.append("- `realized_trades.csv`")
    md.append("- `daily_pnl.csv`")
    md.append("- `equity_series.csv`")
    md.append("- `equity.png`, `daily_pnl.png` (if matplotlib installed and plots enabled)")
    md.append("")
    (out_dir / "report.md").write_text("\n".join(md), encoding="utf-8")

    if not args.no_plots:
        try_plot(out_dir, equity, daily)

    print(f"[OK] Wrote report to: {out_dir.resolve()}")
    print(f"  - report.md")
    print(f"  - summary.json")
    print(f"  - realized_trades.csv / daily_pnl.csv / equity_series.csv")
    if not args.no_plots:
        print(f"  - equity.png / daily_pnl.png (if matplotlib installed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
