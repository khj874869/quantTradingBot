from __future__ import annotations

import json
import os
import urllib.parse
import datetime as _dt
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_jsonl(path: Path, max_lines: int = 20000) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        # Prefer the most recent tail to keep the dashboard responsive
        lines = path.read_text(encoding="utf-8").splitlines()
        if max_lines and len(lines) > max_lines:
            lines = lines[-max_lines:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return out


def _event_ts_ms(e: Dict[str, Any]) -> int:
    ts_ms = e.get("ts_ms")
    if ts_ms is not None:
        try:
            return int(ts_ms)
        except Exception:
            pass
    ts = e.get("ts")
    if isinstance(ts, str) and ts:
        try:
            s = ts.replace("Z", "+00:00")
            dt = _dt.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            return 0
    return 0


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str, state_dir: str, **kwargs):
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/snapshot"):
            self._handle_snapshot(parsed)
            return
        if parsed.path.startswith("/api/bot"):
            self._handle_bot(parsed)
            return
        if parsed.path.startswith("/api/pnl_series"):
            self._handle_pnl_series(parsed)
            return
        if parsed.path.startswith("/api/equity_series"):
            self._handle_equity_series(parsed)
            return
        if parsed.path.startswith("/api/pnl"):
            self._handle_pnl(parsed)
            return
        if parsed.path.startswith("/api/fills"):
            self._handle_fills(parsed)
            return
        if parsed.path.startswith("/api/global_risk"):
            self._handle_global_risk(parsed)
            return
        return super().do_GET()

    def _json(self, payload: Any, status: int = 200):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle_snapshot(self, parsed: urllib.parse.ParseResult):
        bots = []
        for p in sorted(self._state_dir.glob("*.json")):
            d = _read_json(p)
            if d:
                d.setdefault("id", p.stem)
                bots.append(d)
        self._json({"bots": bots})

    def _handle_bot(self, parsed: urllib.parse.ParseResult):
        q = urllib.parse.parse_qs(parsed.query)
        bot_id = (q.get("id") or [""])[0]
        p = (self._state_dir / f"{bot_id}.json")
        self._json(_read_json(p) if p.exists() else {}, status=(200 if p.exists() else 404))

    def _handle_pnl(self, parsed: urllib.parse.ParseResult):
        # aggregate realized_net over last 30 days from fills.jsonl
        q = urllib.parse.parse_qs(parsed.query)
        days = int((q.get("days") or ["30"])[0])
        from_ts_ms = int((q.get("from_ts_ms") or ["0"])[0])
        account_tag = (q.get("account_tag") or [""])[0].strip()

        fills_path = Path("state/fills.jsonl")
        events = _read_jsonl(fills_path)

        # If from_ts_ms isn't provided, compute from 'days'
        if from_ts_ms <= 0 and days > 0:
            import time

            from_ts_ms = int((time.time() - days * 86400) * 1000)

        realized = 0.0
        fees = 0.0
        n = 0
        for e in events:
            if account_tag and str(e.get("account_tag") or "") != account_tag:
                continue
            ts = _event_ts_ms(e)
            if ts and ts < from_ts_ms:
                continue
            realized += float(e.get("realized_net_delta") or 0.0)
            fees += float(e.get("fee") or 0.0)
            n += 1

        self._json({"days": days, "from_ts_ms": from_ts_ms, "fills": n, "realized_net": realized, "fees": fees, "account_tag": account_tag})

    def _handle_pnl_series(self, parsed: urllib.parse.ParseResult):
        q = urllib.parse.parse_qs(parsed.query)
        days = int((q.get("days") or ["30"])[0])
        from_ts_ms = int((q.get("from_ts_ms") or ["0"])[0])
        account_tag = (q.get("account_tag") or [""])[0].strip()

        if from_ts_ms <= 0 and days > 0:
            import time
            from_ts_ms = int((time.time() - days * 86400) * 1000)

        tz = ZoneInfo("Asia/Seoul") if ZoneInfo else _dt.timezone.utc

        events = _read_jsonl(Path("state/fills.jsonl"), max_lines=200000)
        buckets: Dict[str, Dict[str, float]] = {}
        for e in events:
            if account_tag and str(e.get("account_tag") or "") != account_tag:
                continue
            ts = _event_ts_ms(e)
            if ts and ts < from_ts_ms:
                continue
            rn = float(e.get("realized_net_delta") or 0.0)
            fee = float(e.get("fee") or 0.0)
            if rn == 0.0 and fee == 0.0:
                continue
            dt = _dt.datetime.fromtimestamp(ts / 1000.0, tz=tz)
            key = dt.date().isoformat()
            b = buckets.setdefault(key, {"realized_net": 0.0, "fees": 0.0, "fills": 0.0})
            b["realized_net"] += rn
            b["fees"] += fee
            b["fills"] += 1.0

        daily = [{"date": d, **buckets[d]} for d in sorted(buckets.keys())]
        cum = 0.0
        cumulative = []
        for row in daily:
            cum += float(row.get("realized_net") or 0.0)
            cumulative.append({"date": row["date"], "cum_realized_net": cum})

        self._json({
            "days": days,
            "from_ts_ms": from_ts_ms,
            "account_tag": account_tag,
            "daily": daily,
            "cumulative": cumulative,
        })

    def _handle_equity_series(self, parsed: urllib.parse.ParseResult):
        q = urllib.parse.parse_qs(parsed.query)
        days = int((q.get("days") or ["30"])[0])
        from_ts_ms = int((q.get("from_ts_ms") or ["0"])[0])
        account_tag = (q.get("account_tag") or [""])[0].strip()

        if from_ts_ms <= 0 and days > 0:
            import time
            from_ts_ms = int((time.time() - days * 86400) * 1000)

        tz = ZoneInfo("Asia/Seoul") if ZoneInfo else _dt.timezone.utc

        path = Path("state/equity_history.jsonl")
        rows = _read_jsonl(path, max_lines=200000)
        series = []
        for e in rows:
            if account_tag and str(e.get("account_tag") or "") != account_tag:
                continue
            ts = _event_ts_ms(e)
            if ts and ts < from_ts_ms:
                continue
            eq = e.get("equity")
            if eq is None:
                continue
            try:
                series.append({"ts_ms": int(ts), "equity": float(eq)})
            except Exception:
                continue

        series.sort(key=lambda x: x["ts_ms"])

        # daily close (last point per local date)
        daily_last: Dict[str, Dict[str, float]] = {}
        for pt in series:
            dt = _dt.datetime.fromtimestamp(pt["ts_ms"] / 1000.0, tz=tz)
            key = dt.date().isoformat()
            daily_last[key] = {"date": key, "equity": float(pt["equity"]), "ts_ms": int(pt["ts_ms"])}
        daily = [daily_last[d] for d in sorted(daily_last.keys())]

        self._json({
            "days": days,
            "from_ts_ms": from_ts_ms,
            "account_tag": account_tag,
            "series": series,
            "daily": daily,
        })

    def _handle_fills(self, parsed: urllib.parse.ParseResult):
        """Return recent fill/order events from state/fills.jsonl.

        Query params:
          - limit: max events (default 200)
          - days / from_ts_ms: time filter (same as /api/pnl)
          - account_tag: filter
          - symbol: filter
          - venue: filter
        """
        q = urllib.parse.parse_qs(parsed.query)
        limit = int((q.get("limit") or ["200"])[0])
        days = int((q.get("days") or ["30"])[0])
        from_ts_ms = int((q.get("from_ts_ms") or ["0"])[0])
        account_tag = (q.get("account_tag") or [""])[0].strip()
        symbol = (q.get("symbol") or [""])[0].strip()
        venue = (q.get("venue") or [""])[0].strip()

        if from_ts_ms <= 0 and days > 0:
            import time
            from_ts_ms = int((time.time() - days * 86400) * 1000)

        events = _read_jsonl(Path("state/fills.jsonl"), max_lines=200000)
        out: List[Dict[str, Any]] = []
        for e in reversed(events):
            if account_tag and str(e.get("account_tag") or "") != account_tag:
                continue
            if symbol and str(e.get("symbol") or "") != symbol:
                continue
            if venue and str(e.get("venue") or "") != venue:
                continue
            ts = _event_ts_ms(e)
            if ts and ts < from_ts_ms:
                continue
            e2 = dict(e)
            e2["ts_ms"] = ts
            out.append(e2)
            if limit and len(out) >= limit:
                break

        self._json({
            "limit": limit,
            "days": days,
            "from_ts_ms": from_ts_ms,
            "account_tag": account_tag,
            "symbol": symbol,
            "venue": venue,
            "events": out,
        })

    def _handle_global_risk(self, parsed: urllib.parse.ParseResult):
        """Return aggregated exposure snapshot from state/global_risk.json."""
        q = urllib.parse.parse_qs(parsed.query)
        max_age_sec = int((q.get("max_age_sec") or ["60"])[0])
        account_tag = (q.get("account_tag") or [""])[0].strip()

        path = Path("state/global_risk.json")
        doc = _read_json(path) if path.exists() else {}
        bots = doc.get("bots")
        if not isinstance(bots, dict):
            bots = {}

        import time
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - max_age_sec * 1000

        per: Dict[str, Dict[str, Any]] = {}
        for _, v in bots.items():
            if not isinstance(v, dict):
                continue
            ts_ms = int(v.get("ts_ms") or 0)
            if ts_ms < cutoff_ms:
                continue
            tag = str(v.get("account_tag") or "default")
            eq = float(v.get("equity") or 0.0)
            an = float(v.get("abs_notional") or 0.0)
            row = per.get(tag)
            if row is None:
                per[tag] = {"account_tag": tag, "equity": eq, "abs_notional": an, "ts_ms": ts_ms}
            else:
                row["equity"] = max(float(row.get("equity") or 0.0), eq)
                row["abs_notional"] = float(row.get("abs_notional") or 0.0) + an
                row["ts_ms"] = max(int(row.get("ts_ms") or 0), ts_ms)

        # total
        total_eq = sum(float(r.get("equity") or 0.0) for r in per.values())
        total_an = sum(float(r.get("abs_notional") or 0.0) for r in per.values())
        total = {"account_tag": "TOTAL", "equity": total_eq, "abs_notional": total_an, "ts_ms": now_ms}

        # optional: filter single account
        if account_tag:
            per = {k: v for k, v in per.items() if k == account_tag}

        self._json({
            "max_age_sec": max_age_sec,
            "updated_at_ms": int(doc.get("updated_at_ms") or 0),
            "accounts": [per[k] for k in sorted(per.keys())],
            "total": total,
        })



def serve_dashboard(host: str = "127.0.0.1", port: int = 8899, *, state_dir: str = "state/bots") -> None:
    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    handler = lambda *args, **kwargs: DashboardHandler(*args, directory=str(static_dir), state_dir=state_dir, **kwargs)  # type: ignore

    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Dashboard: http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    host = os.environ.get("QBOT_DASH_HOST", "127.0.0.1")
    port = int(os.environ.get("QBOT_DASH_PORT", "8899"))
    serve_dashboard(host=host, port=port)
