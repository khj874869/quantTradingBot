from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any
from quantbot.config import get_settings
from quantbot.common.types import Signal

settings = get_settings()

@dataclass
class PortfolioState:
    equity: float
    day_start_equity: float
    positions: Dict[str, float]
    prices: Dict[str, float]

class RiskManager:
    def __init__(self):
        self.max_pos = settings.MAX_POSITION_PER_SYMBOL
        self.max_daily_loss = settings.MAX_DAILY_LOSS

    def daily_loss_stop(self, pf: PortfolioState) -> bool:
        if pf.day_start_equity <= 0:
            return False
        dd = (pf.equity - pf.day_start_equity) / pf.day_start_equity
        return dd <= -self.max_daily_loss

    def position_value(self, pf: PortfolioState, symbol: str) -> float:
        qty = pf.positions.get(symbol, 0.0)
        px = pf.prices.get(symbol, 0.0)
        return abs(qty * px)

    def approve(self, pf: PortfolioState, sig: Signal, intended_notional: float) -> tuple[bool, Dict[str, Any]]:
        if self.daily_loss_stop(pf):
            return False, {"reason": "DAILY_LOSS_STOP"}
        if pf.equity > 0:
            if (self.position_value(pf, sig.symbol) + intended_notional) / pf.equity > self.max_pos:
                return False, {"reason": "MAX_POSITION_PER_SYMBOL"}
        return True, {"reason": "OK"}
