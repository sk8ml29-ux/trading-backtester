"""ExecutionAdapter: the one interface every order-placement target must
implement -- an exchange, a broker, a webshop "buy" action, or a digital
contract settlement. DynamicExecutionEngine only ever talks to this ABC.
"""

from __future__ import annotations

import abc

from global_hunter.contracts import ApprovedOrder, ExecutionResult


class ExecutionAdapter(abc.ABC):
    name: str = "unnamed_adapter"

    @abc.abstractmethod
    async def place_order(self, order: ApprovedOrder) -> ExecutionResult:
        ...

    async def get_position(self, instrument: str) -> dict | None:
        return None

    async def close_position(self, instrument: str) -> ExecutionResult | None:
        """Unwind a currently-held position (used by ExecutionGovernor when
        preempting a slower BUY_AND_HOLD commitment for a faster one).
        Return None if this adapter has no such position / doesn't support
        an explicit close.
        """
        return None

    async def cancel_order(self, order_id: str) -> bool:
        return False

    async def healthcheck(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass
