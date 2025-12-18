from __future__ import annotations
from abc import ABC, abstractmethod
from quantbot.common.types import OrderRequest, OrderUpdate

class BrokerAdapter(ABC):
    @abstractmethod
    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        ...

    @abstractmethod
    async def get_last_price(self, symbol: str) -> float:
        ...

    @abstractmethod
    async def get_equity(self) -> float:
        ...

    @abstractmethod
    async def get_positions(self) -> dict[str, float]:
        ...
