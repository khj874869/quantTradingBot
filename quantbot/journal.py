from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable

from quantbot.utils.time import utc_now


def append_event(event: Dict[str, Any], path: str = "state/fills.jsonl") -> None:
    """Append a JSONL event.

    This is deliberately simple (no DB requirement) so you can compute daily/monthly
    PnL without slowing the trading loop.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    e = {"ts": utc_now().isoformat(), **event}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


def iter_events(path: str = "state/fills.jsonl") -> Iterable[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def append_equity_snapshot(snapshot: Dict[str, Any], path: str = "state/equity_history.jsonl") -> None:
    """Append an equity snapshot JSONL (for dashboard equity curve).

    Keep this lightweight: writer can throttle (e.g. once per minute) in the live loop.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    e = {"ts": utc_now().isoformat(), **snapshot}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


def append_sizing_snapshot(snapshot: Dict[str, Any], path: str = "state/sizing_history.jsonl") -> None:
    """Append an order-sizing decision JSONL.

    This is a low-overhead debug tape that explains why a trade was sized,
    bumped up to meet minNotional, or skipped.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    e = {"ts": utc_now().isoformat(), **snapshot}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


def append_cooldown_snapshot(snapshot: Dict[str, Any], path: str = "state/cooldown_history.jsonl") -> None:
    """Append a cooldown decision JSONL.

    This helps debug why the bot skipped entries (cooldown active), and what event
    (EXIT fill / ENTRY reject / rate-limit) triggered the cooldown.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    e = {"ts": utc_now().isoformat(), **snapshot}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
