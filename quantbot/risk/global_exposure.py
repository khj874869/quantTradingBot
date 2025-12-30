from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple


def _safe_read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(path))


@dataclass
class ExposureRow:
    account_tag: str
    equity: float
    abs_notional: float
    ts_ms: int


class GlobalExposureStore:
    """Shared exposure store used by multiple bot processes.

    The store is a single JSON file.
    Each bot updates its own key frequently (cheap). Readers aggregate.
    """

    def __init__(self, path: str = "state/global_risk.json"):
        self.path = Path(path)

    def update(self, *, key: str, account_tag: str, equity: float, abs_notional: float) -> None:
        now_ms = int(time.time() * 1000)
        doc = _safe_read_json(self.path)
        bots = doc.get("bots")
        if not isinstance(bots, dict):
            bots = {}

        bots[str(key)] = {
            "account_tag": str(account_tag or "default"),
            "equity": float(equity or 0.0),
            "abs_notional": float(abs_notional or 0.0),
            "ts_ms": now_ms,
        }
        doc["bots"] = bots
        doc["updated_at_ms"] = now_ms
        _atomic_write_json(self.path, doc)

    def summary(self, *, max_age_sec: int = 30) -> Tuple[Dict[str, ExposureRow], ExposureRow]:
        """Return (per-account rows, total row).

        - per-account equity uses MAX(equity) across bots in that account (avoids double-counting
          when you run multiple symbols on the same account).
        - per-account abs_notional uses SUM(abs_notional) across bots in that account.
        - total equity is SUM(per-account equity)
        - total abs_notional is SUM(per-account abs_notional)
        """
        now_ms = int(time.time() * 1000)
        doc = _safe_read_json(self.path)
        bots = doc.get("bots")
        if not isinstance(bots, dict):
            bots = {}

        cutoff_ms = now_ms - max_age_sec * 1000

        per: Dict[str, ExposureRow] = {}
        for _, v in bots.items():
            if not isinstance(v, dict):
                continue
            ts_ms = int(v.get("ts_ms") or 0)
            if ts_ms < cutoff_ms:
                continue
            tag = str(v.get("account_tag") or "default")
            eq = float(v.get("equity") or 0.0)
            an = float(v.get("abs_notional") or 0.0)

            if tag not in per:
                per[tag] = ExposureRow(account_tag=tag, equity=eq, abs_notional=an, ts_ms=ts_ms)
            else:
                # equity: take max (same account), not sum
                per[tag].equity = max(per[tag].equity, eq)
                per[tag].abs_notional += an
                per[tag].ts_ms = max(per[tag].ts_ms, ts_ms)

        total_eq = sum(r.equity for r in per.values())
        total_an = sum(r.abs_notional for r in per.values())
        total = ExposureRow(account_tag="TOTAL", equity=total_eq, abs_notional=total_an, ts_ms=now_ms)
        return per, total

    def get_account(self, account_tag: str, *, max_age_sec: int = 30) -> ExposureRow:
        per, _ = self.summary(max_age_sec=max_age_sec)
        return per.get(str(account_tag or "default"), ExposureRow(account_tag=str(account_tag or "default"), equity=0.0, abs_notional=0.0, ts_ms=0))
