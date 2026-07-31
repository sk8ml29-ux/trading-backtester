"""DynamicExecutionEngine: consumes ApprovedOrder from ExecutionGovernor and
routes it to whichever ExecutionAdapter is registered for it. Idempotent
(dedupes on opportunity id) and timeout-guarded.

Capital reservation lives ONE level up, in ExecutionGovernor -- this class's
only job is "place/close this order via the right adapter, safely, without
crashing the process." Keeping capital-allocation logic out of the
execution layer is what lets the governor preempt/reallocate across
multiple modules without this engine needing to know anything about it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from global_hunter.contracts import ApprovedOrder, ExecutionResult
from global_hunter.engine.base import ExecutionAdapter

logger = logging.getLogger("global_hunter.engine")


class DynamicExecutionEngine:
    def __init__(
        self,
        adapters: dict[str, ExecutionAdapter],
        default_adapter: str,
        order_timeout_s: float = 20.0,
    ) -> None:
        if default_adapter not in adapters:
            raise ValueError(f"default_adapter={default_adapter!r} not in adapters={list(adapters)}")
        self.adapters = adapters
        self.default_adapter = default_adapter
        self.order_timeout_s = order_timeout_s
        self._seen_opportunity_ids: set[str] = set()

    async def execute(self, order: ApprovedOrder, adapter_name: str | None = None) -> ExecutionResult:
        opp_id = order.opportunity.id
        if opp_id in self._seen_opportunity_ids:
            logger.warning("Duplicate order for %s ignored (idempotency guard)", opp_id)
            return ExecutionResult(
                order_id=f"dup-{opp_id}", opportunity_id=opp_id, status="rejected",
                realized_or_expected_sek=0.0, executed_at=datetime.now(timezone.utc), adapter="none",
            )
        self._seen_opportunity_ids.add(opp_id)

        adapter = self.adapters[adapter_name or self.default_adapter]
        try:
            return await asyncio.wait_for(adapter.place_order(order), timeout=self.order_timeout_s)
        except Exception:
            logger.exception("Execution failed for %s via %s", opp_id, adapter.name)
            raise

    async def close(self, order: ApprovedOrder, adapter_name: str | None = None) -> ExecutionResult:
        """Unwind a previously-placed BUY_AND_HOLD order early. Used by
        ExecutionGovernor when preempting a slower holding for a faster
        opportunity. If the adapter has nothing to close (already closed,
        or doesn't track positions), returns a synthetic zero-value
        "closed" result rather than raising -- a BUY_AND_HOLD commitment
        that can't be found is a data-consistency bug, not a retry-able
        transient failure, so surface it via a log line instead of a crash.
        """
        adapter = self.adapters[adapter_name or self.default_adapter]
        result = await asyncio.wait_for(adapter.close_position(order.opportunity.instrument), timeout=self.order_timeout_s)
        if result is not None:
            return result
        logger.warning(
            "close(): adapter %s has no open position for %s -- treating as already closed",
            adapter.name, order.opportunity.id,
        )
        return ExecutionResult(
            order_id=f"close-noop-{order.opportunity.id}", opportunity_id=order.opportunity.id,
            status="closed", realized_or_expected_sek=0.0,
            executed_at=datetime.now(timezone.utc), adapter=adapter.name,
        )

    async def run_forever(
        self,
        orders_queue: "asyncio.Queue[ApprovedOrder]",
        results_queue: "asyncio.Queue[ExecutionResult] | None" = None,
    ) -> None:
        while True:
            order = await orders_queue.get()
            try:
                result = await self.execute(order)
                if results_queue is not None:
                    await results_queue.put(result)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad order must never kill the engine loop
                logger.exception("Unhandled error processing order %s", order.opportunity.id)
            finally:
                orders_queue.task_done()

    async def aclose(self) -> None:
        await asyncio.gather(*(a.aclose() for a in self.adapters.values()), return_exceptions=True)
