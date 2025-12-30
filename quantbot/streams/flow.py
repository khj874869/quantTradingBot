from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from typing import Deque, Dict, Optional, Tuple, List, Any


@dataclass
class FlowSnapshot:
    """Trade-flow features for a rolling time window."""

    window_sec: float
    trade_count: int
    buy_notional: float
    sell_notional: float
    total_notional: float

    # Derived
    notional_rate: float          # quote notional / sec
    notional_accel: float         # (rate_now - rate_prev) / dt

    # Normalized (EMA baseline) - helps when symbol notional scale differs
    rate_ema: float
    rate_z: float
    accel_ema: float
    accel_z: float
    large_trade_count: int
    large_buy_notional: float
    large_sell_notional: float
    large_total_notional: float
    large_trade_share: float      # large_total_notional / total_notional


class TradeFlowBook:
    """Maintains rolling-window trade-flow stats per symbol.

    - You call `add_trade(symbol, ts_ms, price, qty, is_buy)` from a trade stream.
    - Then call `snapshot(symbol, now_ms)` to get FlowSnapshot.

    Additionally, this keeps a small recent-trade tape for UI/debugging.
    """

    def __init__(
        self,
        window_sec: float = 5.0,
        large_trade_min_notional: float = 0.0,
        tape_maxlen: int = 200,
    ):
        self.window_sec = float(window_sec)
        self.large_trade_min_notional = float(large_trade_min_notional)

        # Rolling window (trimmed to window_sec)
        self._trades: Dict[str, Deque[Tuple[int, float, float, bool]]] = {}
        self._last_rate: Dict[str, Tuple[int, float]] = {}

        # EMA baselines for scale-free scoring
        self._ema_rate: Dict[str, float] = {}
        self._ema_rate_dev: Dict[str, float] = {}
        self._ema_accel: Dict[str, float] = {}
        self._ema_accel_dev: Dict[str, float] = {}

        # Recent tape (NOT trimmed by window, only maxlen)
        self._tape: Dict[str, Deque[Tuple[int, float, float, bool]]] = {}
        self._tape_maxlen = int(max(50, tape_maxlen))

    def add_trade(self, symbol: str, ts_ms: int, price: float, qty: float, is_buy: bool) -> None:
        ts_ms = int(ts_ms or 0)
        if not symbol or ts_ms <= 0:
            return
        px = float(price)
        q = float(qty)
        if px <= 0 or q <= 0:
            return

        dq = self._trades.setdefault(symbol, deque())
        dq.append((ts_ms, px, q, bool(is_buy)))

        tape = self._tape.setdefault(symbol, deque(maxlen=self._tape_maxlen))
        tape.append((ts_ms, px, q, bool(is_buy)))

    def _trim(self, dq: Deque[Tuple[int, float, float, bool]], now_ms: int) -> None:
        cutoff = int(now_ms - self.window_sec * 1000.0)
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def snapshot(self, symbol: str, now_ms: int) -> FlowSnapshot:
        dq = self._trades.get(symbol) or deque()
        self._trim(dq, now_ms)

        buy_notional = 0.0
        sell_notional = 0.0
        large_buy = 0.0
        large_sell = 0.0
        large_count = 0
        for ts, px, q, is_buy in dq:
            notional = px * q
            if is_buy:
                buy_notional += notional
            else:
                sell_notional += notional
            if self.large_trade_min_notional > 0.0 and notional >= self.large_trade_min_notional:
                large_count += 1
                if is_buy:
                    large_buy += notional
                else:
                    large_sell += notional

        total = buy_notional + sell_notional
        rate = total / max(self.window_sec, 1e-9)
        last = self._last_rate.get(symbol)
        accel = 0.0
        if last:
            last_ts, last_rate = last
            dt = max((now_ms - last_ts) / 1000.0, 1e-6)
            accel = (rate - last_rate) / dt
        self._last_rate[symbol] = (int(now_ms), rate)

        # Update EMA baselines (robust-ish using EMA of absolute deviation)
        alpha = 0.08
        r0 = self._ema_rate.get(symbol, rate)
        r_ema = (1 - alpha) * r0 + alpha * rate
        rdev0 = self._ema_rate_dev.get(symbol, 0.0)
        r_dev = (1 - alpha) * rdev0 + alpha * abs(rate - r_ema)

        a0 = self._ema_accel.get(symbol, accel)
        a_ema = (1 - alpha) * a0 + alpha * accel
        adev0 = self._ema_accel_dev.get(symbol, 0.0)
        a_dev = (1 - alpha) * adev0 + alpha * abs(accel - a_ema)

        self._ema_rate[symbol] = r_ema
        self._ema_rate_dev[symbol] = r_dev
        self._ema_accel[symbol] = a_ema
        self._ema_accel_dev[symbol] = a_dev

        eps = 1e-9
        rate_z = (rate - r_ema) / max(r_dev, eps)
        accel_z = (accel - a_ema) / max(a_dev, eps)

        large_total = large_buy + large_sell
        share = (large_total / total) if total > 0 else 0.0

        return FlowSnapshot(
            window_sec=self.window_sec,
            trade_count=len(dq),
            buy_notional=buy_notional,
            sell_notional=sell_notional,
            total_notional=total,
            notional_rate=rate,
            notional_accel=accel,
            rate_ema=r_ema,
            rate_z=float(rate_z),
            accel_ema=a_ema,
            accel_z=float(accel_z),
            large_trade_count=large_count,
            large_buy_notional=large_buy,
            large_sell_notional=large_sell,
            large_total_notional=large_total,
            large_trade_share=share,
        )

    def recent_trades(
        self,
        symbol: str,
        limit: int = 60,
        *,
        now_ms: Optional[int] = None,
        max_age_sec: Optional[float] = 120.0,
    ) -> List[Dict[str, Any]]:
        """Return recent trades for UI.

        Output: [{ts_ms, side, price, qty, notional}, ...] newest-first.
        """
        tape = self._tape.get(symbol)
        if not tape:
            return []
        lim = int(max(1, min(500, limit)))
        now = int(now_ms or 0)
        out: List[Dict[str, Any]] = []
        # iterate reversed to get newest first
        for ts, px, q, is_buy in reversed(tape):
            if max_age_sec is not None and now > 0:
                if (now - int(ts)) > int(max_age_sec * 1000.0):
                    break
            out.append(
                {
                    "ts_ms": int(ts),
                    "side": "BUY" if is_buy else "SELL",
                    "price": float(px),
                    "qty": float(q),
                    "notional": float(px) * float(q),
                }
            )
            if len(out) >= lim:
                break
        return out
