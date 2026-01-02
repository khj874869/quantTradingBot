from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Tuple

from quantbot.journal import append_cooldown_snapshot


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class CooldownState:
    symbol: str
    until_ms: int = 0
    fail_count: int = 0
    last_fail_ms: int = 0
    last_reason: str = ""
    last_payload: Optional[Dict[str, Any]] = None

    def is_active(self, now_ms: Optional[int] = None) -> bool:
        n = _now_ms() if now_ms is None else int(now_ms)
        return n < int(self.until_ms or 0)


class CooldownManager:
    """Per-symbol cooldowns for entry logic.

    Goals
    - Prevent rapid retry loops when an entry is rejected (insufficient margin, bad params, permissions).
    - Apply exponential backoff on repeated failures.
    - Never block risk exits.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        after_exit_fill_sec: int = 2,
        after_entry_fill_sec: int = 0,
        reject_base_sec: int = 10,
        http400_sec: int = 30,
        http401_sec: int = 600,
        rate_limit_sec: int = 5,
        insufficient_margin_sec: int = 180,
        # cause-specific bases
        min_notional_sec: int = 300,
        filter_fail_sec: int = 60,
        precision_sec: int = 60,
        position_side_sec: int = 600,
        reduce_only_sec: int = 60,
        trigger_immediate_sec: int = 20,
        timestamp_sec: int = 10,
        max_open_orders_sec: int = 120,
        liquidation_sec: int = 600,
        backoff_mult: float = 2.0,
        max_sec: int = 900,
        fail_window_sec: int = 180,
        account_tag: str = "",
        venue: str = "",
        mode: str = "",
    ):
        self.enabled = bool(enabled)
        self.after_exit_fill_sec = int(after_exit_fill_sec)
        self.after_entry_fill_sec = int(after_entry_fill_sec)
        self.reject_base_sec = int(reject_base_sec)
        self.http400_sec = int(http400_sec)
        self.http401_sec = int(http401_sec)
        self.rate_limit_sec = int(rate_limit_sec)
        self.insufficient_margin_sec = int(insufficient_margin_sec)

        # cause-specific bases
        self.min_notional_sec = int(min_notional_sec)
        self.filter_fail_sec = int(filter_fail_sec)
        self.precision_sec = int(precision_sec)
        self.position_side_sec = int(position_side_sec)
        self.reduce_only_sec = int(reduce_only_sec)
        self.trigger_immediate_sec = int(trigger_immediate_sec)
        self.timestamp_sec = int(timestamp_sec)
        self.max_open_orders_sec = int(max_open_orders_sec)
        self.liquidation_sec = int(liquidation_sec)
        self.backoff_mult = float(backoff_mult)
        self.max_sec = int(max_sec)
        self.fail_window_sec = int(fail_window_sec)

        self.account_tag = str(account_tag or "")
        self.venue = str(venue or "")
        self.mode = str(mode or "")

        self._state: Dict[str, CooldownState] = {}

    def _get(self, symbol: str) -> CooldownState:
        s = str(symbol)
        if s not in self._state:
            self._state[s] = CooldownState(symbol=s)
        return self._state[s]

    def snapshot(self, symbol: str, now_ms: Optional[int] = None) -> Dict[str, Any]:
        st = self._get(symbol)
        n = _now_ms() if now_ms is None else int(now_ms)
        return {
            "symbol": st.symbol,
            "active": bool(self.enabled and st.is_active(n)),
            "until_ms": int(st.until_ms or 0),
            "remaining_sec": max(0.0, (int(st.until_ms or 0) - n) / 1000.0),
            "fail_count": int(st.fail_count or 0),
            "last_fail_ms": int(st.last_fail_ms or 0),
            "last_reason": st.last_reason,
            "last_payload": st.last_payload,
        }

    def last_event(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return last cooldown event payload for a symbol (if any)."""
        try:
            return self._get(symbol).last_payload
        except Exception:
            return None

    def allow_entry(self, symbol: str, now_ms: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        if not self.enabled:
            return True, None
        st = self._get(symbol)
        n = _now_ms() if now_ms is None else int(now_ms)
        if st.is_active(n):
            rem = max(0.0, (int(st.until_ms or 0) - n) / 1000.0)
            return False, f"COOLDOWN_ACTIVE({rem:.1f}s left, reason={st.last_reason})"
        return True, None

    def _reset_fail_count_if_stale(self, st: CooldownState, now_ms: int) -> None:
        if st.fail_count <= 0:
            return
        win_ms = int(max(1, self.fail_window_sec) * 1000)
        if st.last_fail_ms and (now_ms - int(st.last_fail_ms)) > win_ms:
            st.fail_count = 0

    def _apply_cooldown(self, symbol: str, sec: float, reason: str, *, now_ms: Optional[int] = None, fail: bool = False, meta: Optional[Dict[str, Any]] = None) -> None:
        st = self._get(symbol)
        n = _now_ms() if now_ms is None else int(now_ms)
        sec = float(sec or 0.0)
        if sec <= 0:
            return

        until = n + int(sec * 1000)
        st.until_ms = max(int(st.until_ms or 0), int(until))
        st.last_reason = str(reason or "")
        if fail:
            self._reset_fail_count_if_stale(st, n)
            st.fail_count = int(st.fail_count or 0) + 1
            st.last_fail_ms = n

        payload = {
            "ts_ms": n,
            "venue": self.venue,
            "account_tag": self.account_tag,
            "mode": self.mode,
            "symbol": symbol,
            "reason": st.last_reason,
            "cooldown_sec": sec,
            "until_ms": int(st.until_ms or 0),
            "fail_count": int(st.fail_count or 0),
        }
        if meta:
            payload.update(meta)
        try:
            append_cooldown_snapshot(payload)
        except Exception:
            pass
        try:
            st.last_payload = payload
        except Exception:
            pass

    def on_exit_filled(self, symbol: str, *, now_ms: Optional[int] = None) -> None:
        if not self.enabled:
            return
        if self.after_exit_fill_sec > 0:
            self._apply_cooldown(symbol, float(self.after_exit_fill_sec), "EXIT_FILLED", now_ms=now_ms, fail=False)

    def on_entry_filled(self, symbol: str, *, now_ms: Optional[int] = None) -> None:
        if not self.enabled:
            return
        # Usually redundant because position becomes non-flat, but helpful for fast flip-flops.
        if self.after_entry_fill_sec > 0:
            self._apply_cooldown(symbol, float(self.after_entry_fill_sec), "ENTRY_FILLED", now_ms=now_ms, fail=False)
        # Success resets failure count.
        st = self._get(symbol)
        st.fail_count = 0

    def _classify_failure(self, raw: Any) -> Tuple[str, Optional[int], Optional[int], Optional[str]]:
        """Return (category, http_status, code, msg).

        We primarily classify Binance Futures-style errors:
        raw = {
          "error": "http_error",
          "http_status": 400,
          "body": {"code": -4164, "msg": "..."}
        }
        """
        try:
            if not isinstance(raw, dict):
                return "reject", None, None, None
            if raw.get("error") == "http_error":
                hs = int(raw.get("http_status") or 0) or None
                body = raw.get("body")
                msg: Optional[str] = None
                code: Optional[int] = None
                if isinstance(body, dict):
                    code = body.get("code")
                    msg = body.get("msg")
                m = str(msg or "")
                ml = m.lower()

                # HTTP-first
                if hs in (418, 429):
                    return "rate_limit", hs, code if isinstance(code, int) else None, m
                if hs in (401, 403):
                    return "unauthorized", hs, code if isinstance(code, int) else None, m

                # Code/message-based (Binance derivatives)
                if isinstance(code, int):
                    if code == -2015:
                        return "unauthorized", hs, code, m
                    if code == -2019:
                        return "insufficient_margin", hs, code, m
                    if code == -4164:
                        return "min_notional", hs, code, m
                    if code in (-1013, -20204, -20130):
                        return "filter_fail", hs, code, m
                    if code in (-1111,):
                        return "precision", hs, code, m
                    if code in (-4061,):
                        return "position_side", hs, code, m
                    if code in (-2022, -4118):
                        return "reduce_only", hs, code, m
                    if code in (-2021,):
                        return "would_immediately_trigger", hs, code, m
                    if code in (-1021,):
                        return "timestamp", hs, code, m
                    if code in (-2025,):
                        return "max_open_orders", hs, code, m
                    if code in (-2023,):
                        return "liquidation", hs, code, m

                # Message heuristics
                if "timestamp" in ml or "recvwindow" in ml:
                    return "timestamp", hs, code if isinstance(code, int) else None, m
                if "insufficient" in ml and "margin" in ml:
                    return "insufficient_margin", hs, code if isinstance(code, int) else None, m
                if "position side" in ml:
                    return "position_side", hs, code if isinstance(code, int) else None, m
                if "reduceonly" in ml:
                    return "reduce_only", hs, code if isinstance(code, int) else None, m
                if "immediately trigger" in ml:
                    return "would_immediately_trigger", hs, code if isinstance(code, int) else None, m
                if "notional" in ml and ("no smaller" in ml or "minimum" in ml):
                    return "min_notional", hs, code if isinstance(code, int) else None, m
                if "filter failure" in ml:
                    return "filter_fail", hs, code if isinstance(code, int) else None, m
                if "precision" in ml:
                    return "precision", hs, code if isinstance(code, int) else None, m

                # Generic buckets
                if hs == 400:
                    return "http400", hs, code if isinstance(code, int) else None, m
                return "http_error", hs, code if isinstance(code, int) else None, m
            return "reject", None, None, None
        except Exception:
            return "reject", None, None, None


def classify_failure(self, raw: Any) -> Dict[str, Any]:
    """Public wrapper around internal failure classification.

    Returns a dict with: category, http_status, code, msg.
    """
    cat, http_status, code, msg = self._classify_failure(raw)
    return {
        "category": cat,
        "http_status": http_status,
        "code": code,
        "msg": msg,
    }

    def _recommend_action(self, category: str) -> str:
        """Short operator-facing hint for the failure."""
        cat = str(category or "")
        if cat == "min_notional":
            return "minNotional 미달: 레버리지/진입비율↑ 또는 더 작은 심볼로 전환, auto sizing 확인"
        if cat == "insufficient_margin":
            return "증거금 부족: 레버리지↑(앱에서) 또는 진입비율↓/수량↓"
        if cat == "filter_fail":
            return "거래소 필터 실패: stepSize/tickSize 반영 라운딩, exchangeInfo 재조회"
        if cat == "precision":
            return "정밀도 초과: qty/price 소수점 자릿수(stepSize) 맞춰 내림"
        if cat == "position_side":
            return "포지션모드 불일치: Hedge/One-way 확인, positionSide(LONG/SHORT) 지정"
        if cat == "reduce_only":
            return "reduceOnly 거절: 기존 오픈오더 충돌/포지션 없음 → 오픈오더 취소 또는 reduceOnly 재검토"
        if cat == "would_immediately_trigger":
            return "즉시 트리거: stopPrice가 현재가와 너무 가까움 → stop/tp 가격 재조정"
        if cat == "timestamp":
            return "시간오차: 서버 시간 재동기화, recvWindow 조정"
        if cat == "max_open_orders":
            return "오픈 주문 한도: 기존 미체결 주문 취소 후 재시도"
        if cat == "liquidation":
            return "청산 모드: 신규 진입 중단(자금/포지션 정리 필요)"
        if cat == "unauthorized":
            return "권한/IP 문제: API 키 권한(Futures/Trading) & Trusted IP 확인"
        if cat == "rate_limit":
            return "레이트리밋: 호출 빈도 줄이고 백오프 유지"
        return "원인 미상: raw code/msg 확인 후 대응"

    def on_entry_failed(self, symbol: str, *, now_ms: Optional[int] = None, status: str = "REJECTED", raw: Any = None) -> None:
        if not self.enabled:
            return

        n = _now_ms() if now_ms is None else int(now_ms)
        st = self._get(symbol)
        self._reset_fail_count_if_stale(st, n)
        next_fail_count = int(st.fail_count or 0) + 1

        cat, http_status, code, msg = self._classify_failure(raw)
        base = float(self.reject_base_sec)
        reason = "ENTRY_REJECTED"
        meta: Dict[str, Any] = {
            "category": cat,
            "http_status": http_status,
            "code": code,
            "msg": msg,
            "order_status": str(status or ""),
        }

        # Cause-specific recommended bases
        if cat == "rate_limit":
            base = float(self.rate_limit_sec)
            reason = "RATE_LIMIT"
        elif cat == "unauthorized":
            base = float(self.http401_sec)
            reason = "UNAUTHORIZED"
        elif cat == "insufficient_margin":
            base = float(self.insufficient_margin_sec)
            reason = "INSUFFICIENT_MARGIN"
        elif cat == "min_notional":
            base = float(self.min_notional_sec)
            reason = "MIN_NOTIONAL"
        elif cat == "filter_fail":
            base = float(self.filter_fail_sec)
            reason = "FILTER_FAIL"
        elif cat == "precision":
            base = float(self.precision_sec)
            reason = "PRECISION"
        elif cat == "position_side":
            base = float(self.position_side_sec)
            reason = "POSITION_SIDE"
        elif cat == "reduce_only":
            base = float(self.reduce_only_sec)
            reason = "REDUCE_ONLY_REJECT"
        elif cat == "would_immediately_trigger":
            base = float(self.trigger_immediate_sec)
            reason = "WOULD_TRIGGER"
        elif cat == "timestamp":
            base = float(self.timestamp_sec)
            reason = "TIMESTAMP"
        elif cat == "max_open_orders":
            base = float(self.max_open_orders_sec)
            reason = "MAX_OPEN_ORDERS"
        elif cat == "liquidation":
            base = float(self.liquidation_sec)
            reason = "LIQUIDATION"
        elif cat == "http400":
            base = float(self.http400_sec)
            reason = "HTTP_400"
        elif cat in {"http_error"}:
            reason = "HTTP_ERROR"

        meta["recommend_base_sec"] = float(base)
        meta["recommend_action"] = self._recommend_action(cat)

        # Exponential backoff.
        mult = max(1.0, float(self.backoff_mult or 1.0))
        sec = base * (mult ** max(0, next_fail_count - 1))
        sec = min(sec, float(self.max_sec))

        self._apply_cooldown(symbol, sec, reason, now_ms=n, fail=True, meta=meta)
