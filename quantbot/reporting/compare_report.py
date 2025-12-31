from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .auto_report import (
    build_realized_trades,
    compute_equity_series,
    daily_pnl,
    max_consecutive_losses,
    max_drawdown,
    parse_fills,
    profit_factor,
    read_jsonl,
    parse_ts,
)

def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return f"{x*100:.3f}%"

def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)

def _load_summary_from_report_dir(d: Path) -> Optional[Dict[str, Any]]:
    p = d / "summary.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def _calc_from_state(
    *,
    state_dir: Path,
    days: int,
    label: str,
    account_tag: Optional[str],
    venue: Optional[str],
    symbol: Optional[str],
) -> Dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(days=int(days))

    fills_raw = read_jsonl(state_dir / "fills.jsonl")
    fills = parse_fills(fills_raw, account_tag=account_tag, venue=venue, symbol=symbol, since=since)
    trades = build_realized_trades(fills)

    equity_raw = read_jsonl(state_dir / "equity_history.jsonl")
    equity = compute_equity_series(equity_raw, since=since, account_tag=account_tag)

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

    mdd, peak_ts, trough_ts = max_drawdown(equity)

    ret = None
    if len(equity) >= 2 and equity[0][1] != 0:
        ret = (equity[-1][1] - equity[0][1]) / equity[0][1]

    return {
        "label": label,
        "window_days": days,
        "account_tag": account_tag or "",
        "venue": venue or "",
        "symbol": symbol or "",
        "fills": len(fills),
        "trades": len(trades),
        "pnl_net": net,
        "pnl_gross": gross,
        "fees": fees,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": pf,
        "max_consecutive_losses": max_ls,
        "avg_holding_sec": hold_avg,
        "max_drawdown_frac": mdd,
        "return_frac": ret,
        "mdd_peak_utc": peak_ts.isoformat() if peak_ts else "",
        "mdd_trough_utc": trough_ts.isoformat() if trough_ts else "",
    }

def _rank(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    # Create a simple heuristic ranking:
    # Prefer higher net pnl, then lower drawdown, then higher profit factor.
    def key(r: Dict[str, Any]):
        pnl = float(r.get("pnl_net") or 0.0)
        mdd = float(r.get("max_drawdown_frac") or 0.0)
        pf = float(r.get("profit_factor") or 0.0)
        return (pnl, -mdd, pf)
    best = max(rows, key=key) if rows else None
    conservative = min(rows, key=lambda r: float(r.get("max_drawdown_frac") or 0.0)) if rows else None
    return {
        "best_overall": best["label"] if best else "n/a",
        "lowest_drawdown": conservative["label"] if conservative else "n/a",
    }

def main() -> int:
    ap = argparse.ArgumentParser(description="Compare multiple experiments (auto_report summaries).")
    ap.add_argument("--out-dir", default=None, help="Output directory. Default: reports/compare_<timestamp>")
    ap.add_argument("--report-dirs", nargs="*", default=None, help="Compare existing report output directories (each containing summary.json).")
    ap.add_argument("--state-dir", default="state", help="State directory for live computation (fills/equity_history). Used when --report-dirs not provided.")
    ap.add_argument("--days", type=int, default=7, help="Lookback window for state-based compare.")
    ap.add_argument("--exp", action="append", default=[], help="Experiment spec: label,account_tag,venue,symbol (comma-separated). Example: v0,binance_demo_v0,binance_futures,BTCUSDT")
    args = ap.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else Path("reports") / ("compare_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []

    if args.report_dirs:
        for rd in args.report_dirs:
            d = Path(rd)
            s = _load_summary_from_report_dir(d)
            if not s:
                continue
            label = s.get("filters", {}).get("account_tag") or d.name
            # Normalize fields where possible
            rows.append({
                "label": label,
                "window_days": s.get("window_days"),
                "account_tag": (s.get("filters", {}) or {}).get("account_tag") or "",
                "venue": (s.get("filters", {}) or {}).get("venue") or "",
                "symbol": (s.get("filters", {}) or {}).get("symbol") or "",
                "fills": s.get("fills_count"),
                "trades": s.get("realized_trades_count"),
                "pnl_net": s.get("pnl_net"),
                "pnl_gross": s.get("pnl_gross"),
                "fees": s.get("fees_total"),
                "win_rate": s.get("win_rate"),
                "avg_win": s.get("avg_win_net"),
                "avg_loss": s.get("avg_loss_net"),
                "profit_factor": s.get("profit_factor"),
                "max_consecutive_losses": s.get("max_consecutive_losses"),
                "avg_holding_sec": s.get("avg_holding_sec"),
                "max_drawdown_frac": s.get("max_drawdown_frac"),
                "return_frac": s.get("return_frac_from_equity"),
                "mdd_peak_utc": s.get("mdd_peak_utc") or "",
                "mdd_trough_utc": s.get("mdd_trough_utc") or "",
            })
    else:
        if not args.exp:
            raise SystemExit("Provide --exp at least once, or use --report-dirs.")
        state_dir = Path(args.state_dir)
        for spec in args.exp:
            parts = [p.strip() for p in spec.split(",")]
            if len(parts) < 1:
                continue
            label = parts[0]
            account_tag = parts[1] if len(parts) > 1 and parts[1] else None
            venue = parts[2] if len(parts) > 2 and parts[2] else None
            symbol = parts[3] if len(parts) > 3 and parts[3] else None
            rows.append(_calc_from_state(
                state_dir=state_dir,
                days=args.days,
                label=label,
                account_tag=account_tag,
                venue=venue,
                symbol=symbol
            ))

    if not rows:
        raise SystemExit("No comparable rows found. Check inputs.")

    # Sort for display
    rows_sorted = sorted(rows, key=lambda r: float(r.get("pnl_net") or 0.0), reverse=True)

    _write_csv(out_dir / "compare.csv", rows_sorted)

    ranks = _rank(rows_sorted)

    # Markdown summary
    md = []
    md.append("# Quantbot Compare Report")
    md.append("")
    md.append(f"- Created (UTC): `{datetime.now(tz=timezone.utc).isoformat()}`")
    md.append(f"- Best overall: **{ranks['best_overall']}**")
    md.append(f"- Lowest drawdown: **{ranks['lowest_drawdown']}**")
    md.append("")
    md.append("## Table")
    md.append("")
    md.append("| label | pnl_net | win_rate | PF | MDD | trades | avg_hold(s) | fees | return | account_tag | venue | symbol |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|")
    for r in rows_sorted:
        md.append(
            f"| {r['label']} | {float(r.get('pnl_net') or 0.0):.6f} | "
            f"{_fmt_pct(float(r.get('win_rate') or 0.0))} | "
            f"{float(r.get('profit_factor') or 0.0):.3f} | "
            f"{_fmt_pct(float(r.get('max_drawdown_frac') or 0.0))} | "
            f"{int(r.get('trades') or 0)} | {float(r.get('avg_holding_sec') or 0.0):.1f} | "
            f"{float(r.get('fees') or 0.0):.6f} | {_fmt_pct(r.get('return_frac'))} | "
            f"{r.get('account_tag','')} | {r.get('venue','')} | {r.get('symbol','')} |"
        )
    md.append("")
    md.append("## How to interpret")
    md.append("- Prefer **higher pnl_net** and **higher profit factor** with **lower drawdown**.")
    md.append("- If two configs have similar pnl_net, choose the one with lower MDD / lower consecutive losses.")
    md.append("")
    md.append("## Files")
    md.append("- `compare.csv` (all metrics)")
    md.append("- `compare.md` (this file)")
    (out_dir / "compare.md").write_text("\n".join(md), encoding="utf-8")

    print(f"[OK] Wrote compare report to: {out_dir.resolve()}")
    print("  - compare.md")
    print("  - compare.csv")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
