from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any

from quantbot.risk.position_tracker import PositionTracker


@dataclass
class ExitConfig:
    # Stop-loss and trailing-stop are defined on *raw* return (before fees) at 1x leverage.
    stop_loss_pct: float = 0.0
    trailing_stop_pct: float = 0.0

    # Take-profit is defined on *net* return (after fees) at 1x leverage.
    take_profit_net_pct: float = 0.0

    # Fee rate used for estimating net return if venue does not provide exact fees.
    fee_rate: float = 0.0004

    # Slippage rate used for estimating net return (entry+exit). For IOC/market exits,
    # slippage can be non-trivial; set to 0.0 to disable.
    slippage_rate: float = 0.0

    # Leverage used for reporting / equity-return conversion.
    leverage: float = 1.0


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str = ""
    raw_return: float = 0.0
    net_return: float = 0.0
    meta: Dict[str, Any] | None = None


class ExitManager:
    """Decides when to exit an open position (stop-loss / trailing / take-profit)."""

    def __init__(self, tracker: PositionTracker, cfg: ExitConfig):
        self.tracker = tracker
        self.cfg = cfg

    def check_exit(self, symbol: str, last_price: float) -> Optional[Tuple[str, str]]:
        """Backward-compatible helper.

        Returns (reason, close_side) if should exit.
        """
        d = self.check(symbol, last_price)
        if not d.should_exit:
            return None
        close_side = "SELL" if self.tracker.get(symbol).qty > 0 else "BUY"
        return (d.reason, close_side)

    def check(self, symbol: str, last_price: float) -> ExitDecision:
        p = self.tracker.get(symbol)
        if p.qty == 0 or p.avg_cost == 0:
            return ExitDecision(should_exit=False)

        entry = float(p.avg_cost)
        px = float(last_price)

        if p.qty > 0:
            raw_ret = (px - entry) / entry
            close_side = "SELL"
            # trailing stop uses high watermark
            if self.cfg.trailing_stop_pct > 0 and p.high_water > 0:
                trail_price = float(p.high_water) * (1 - float(self.cfg.trailing_stop_pct))
                if px <= trail_price:
                    return ExitDecision(
                        should_exit=True,
                        reason="TRAIL",
                        raw_return=raw_ret,
                        net_return=raw_ret - (2 * self.cfg.fee_rate) - (2 * self.cfg.slippage_rate),
                        meta={"close_side": close_side, "trail_price": trail_price},
                    )
            if self.cfg.stop_loss_pct > 0 and raw_ret <= -abs(self.cfg.stop_loss_pct):
                return ExitDecision(
                    should_exit=True,
                    reason="STOP",
                    raw_return=raw_ret,
                    net_return=raw_ret - (2 * self.cfg.fee_rate) - (2 * self.cfg.slippage_rate),
                    meta={"close_side": close_side},
                )
        else:
            raw_ret = (entry - px) / entry
            close_side = "BUY"
            # trailing stop for shorts uses low watermark
            if self.cfg.trailing_stop_pct > 0 and p.low_water > 0:
                trail_price = float(p.low_water) * (1 + float(self.cfg.trailing_stop_pct))
                if px >= trail_price:
                    return ExitDecision(
                        should_exit=True,
                        reason="TRAIL",
                        raw_return=raw_ret,
                        net_return=raw_ret - (2 * self.cfg.fee_rate) - (2 * self.cfg.slippage_rate),
                        meta={"close_side": close_side, "trail_price": trail_price},
                    )
            if self.cfg.stop_loss_pct > 0 and raw_ret <= -abs(self.cfg.stop_loss_pct):
                return ExitDecision(
                    should_exit=True,
                    reason="STOP",
                    raw_return=raw_ret,
                    net_return=raw_ret - (2 * self.cfg.fee_rate) - (2 * self.cfg.slippage_rate),
                    meta={"close_side": close_side},
                )

        # take-profit (net-of-fee)
        est_fee = 2 * self.cfg.fee_rate  # in+out
        est_slip = 2 * self.cfg.slippage_rate  # in+out
        
        net_ret = raw_ret - est_fee - est_slip
        if self.cfg.take_profit_net_pct > 0 and net_ret >= self.cfg.take_profit_net_pct:
            return ExitDecision(
                should_exit=True,
                reason="TP",
                raw_return=raw_ret,
                net_return=net_ret,
                meta={"close_side": close_side},
            )

        return ExitDecision(should_exit=False, raw_return=raw_ret, net_return=net_ret, meta={"close_side": close_side})
