from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional
from quantbot.config import get_settings
from quantbot.common.types import Signal
from quantbot.risk.global_exposure import GlobalExposureStore

settings = get_settings()

def _parse_symbol_base_quote(venue: str, symbol: str) -> tuple[str, str]:
    if venue == "upbit":
        if "-" in symbol:
            q, b = symbol.split("-", 1)
            return b, q
        return symbol, "KRW"
    if venue == "binance":
        quotes = ["USDT","USDC","BUSD","FDUSD","TUSD","BTC","ETH","BNB","TRY","EUR","GBP","BRL","AUD","KRW","JPY"]
        for q in sorted(quotes, key=len, reverse=True):
            if symbol.endswith(q) and len(symbol) > len(q):
                return symbol[:-len(q)], q
        return symbol, ""
    return symbol, ""


@dataclass
class PortfolioState:
    equity: float
    day_start_equity: float
    positions: Dict[str, float]
    prices: Dict[str, float]

class RiskManager:
    def __init__(self, *, max_position_per_symbol: float | None = None, max_daily_loss: float | None = None):
        self.max_pos = float(settings.MAX_POSITION_PER_SYMBOL if max_position_per_symbol is None else max_position_per_symbol)
        self.max_daily_loss = float(settings.MAX_DAILY_LOSS if max_daily_loss is None else max_daily_loss)

    def daily_loss_stop(self, pf: PortfolioState) -> bool:
        if pf.day_start_equity <= 0:
            return False
        dd = (pf.equity - pf.day_start_equity) / pf.day_start_equity
        return dd <= -self.max_daily_loss

    def position_value(self, pf: PortfolioState, symbol: str, venue: str = "upbit") -> float:
        # positions can be keyed by base-asset (e.g., BTC) while prices are keyed by symbol (e.g., KRW-BTC).
        qty = pf.positions.get(symbol, 0.0)
        if qty == 0.0:
            base, _ = _parse_symbol_base_quote(venue, symbol)
            qty = pf.positions.get(base, 0.0)
        px = pf.prices.get(symbol, 0.0)
        return abs(float(qty) * float(px))

    def approve(
        self,
        pf: PortfolioState,
        sig: Signal,
        intended_notional: float,
        venue: str = "upbit",
        *,
        global_store: Optional[GlobalExposureStore] = None,
        account_tag: str = "",
        max_account_exposure_frac: Optional[float] = None,
        max_total_exposure_frac: Optional[float] = None,
        max_account_notional: Optional[float] = None,
        max_total_notional: Optional[float] = None,
    ) -> tuple[bool, Dict[str, Any]]:
        """Risk gate for *new entries*.

        Notes:
          - Explicit exits are always allowed (stop-loss / trailing / take-profit).
          - Short opens are treated as entries (sig.side == SELL with meta.intent == OPEN_SHORT).
        """
        meta = sig.meta or {}

        # Explicit exits (created by ExitManager in live loop)
        if meta.get("exit_reason"):
            return True, {"reason": "EXIT_ALWAYS_ALLOWED", "exit_reason": meta.get("exit_reason")}

        is_open_short = (sig.side == "SELL") and (meta.get("intent") == "OPEN_SHORT")

        # Allow SELL exits for long positions
        if sig.side == "SELL" and not is_open_short:
            return True, {"reason": "EXIT_ALWAYS_ALLOWED"}

        # Allow BUY exits for short positions (if signal is marked as cover)
        if sig.side == "BUY" and meta.get("intent") == "COVER_SHORT":
            return True, {"reason": "EXIT_ALWAYS_ALLOWED"}

        # Block *new entries* when daily stop is hit.
        if self.daily_loss_stop(pf):
            return False, {"reason": "DAILY_LOSS_STOP"}

        if pf.equity > 0:
            cur_val = self.position_value(pf, sig.symbol, venue=venue)
            if (cur_val + intended_notional) / pf.equity > self.max_pos:
                return False, {"reason": "MAX_POSITION_PER_SYMBOL", "cur_val": cur_val}

        # --- Global / multi-process exposure caps ---
        # Enabled only when user sets max_* below defaults.
        if global_store is not None:
            try:
                acct_tag = account_tag or "default"
                per, total = global_store.summary(max_age_sec=30)
                acct = per.get(acct_tag)
                acct_abs = float(acct.abs_notional) if acct else 0.0
                acct_eq = float(acct.equity) if acct else float(pf.equity or 0.0)

                # notional caps (absolute)
                man = settings.MAX_ACCOUNT_NOTIONAL if max_account_notional is None else float(max_account_notional)
                mtn = settings.MAX_TOTAL_NOTIONAL if max_total_notional is None else float(max_total_notional)
                if man and man > 0 and (acct_abs + intended_notional) > man:
                    return False, {"reason": "MAX_ACCOUNT_NOTIONAL", "account_abs": acct_abs, "limit": man}
                if mtn and mtn > 0 and (float(total.abs_notional) + intended_notional) > mtn:
                    return False, {"reason": "MAX_TOTAL_NOTIONAL", "total_abs": float(total.abs_notional), "limit": mtn}

                # exposure fraction caps
                maf = settings.MAX_ACCOUNT_EXPOSURE_FRAC if max_account_exposure_frac is None else float(max_account_exposure_frac)
                mtf = settings.MAX_TOTAL_EXPOSURE_FRAC if max_total_exposure_frac is None else float(max_total_exposure_frac)
                if acct_eq > 0 and maf < 1.0:
                    if (acct_abs + intended_notional) / acct_eq > maf:
                        return False, {"reason": "MAX_ACCOUNT_EXPOSURE", "account_abs": acct_abs, "equity": acct_eq, "limit": maf}
                if float(total.equity) > 0 and mtf < 1.0:
                    if (float(total.abs_notional) + intended_notional) / float(total.equity) > mtf:
                        return False, {"reason": "MAX_TOTAL_EXPOSURE", "total_abs": float(total.abs_notional), "equity": float(total.equity), "limit": mtf}
            except Exception:
                # Do not block trading because of shared-risk I/O issues; rely on local rules.
                pass

        return True, {"reason": "OK"}