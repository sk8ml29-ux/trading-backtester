"""DynamicExecutionEngine: consumes ApprovedOrder from the legal filter and
routes it to whichever ExecutionAdapter is registered for it. Idempotent
(dedupes on opportunity id), timeout-guarded, and capital-ledger-aware so a
stuck adapter can never double-spend the entrepreneur's cash.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from global_hunter.config import CapitalLedger
from global_hunter.contracts import ActionType, ApprovedOrder, ExecutionResult
from global_hunter.engine.base import ExecutionAdapter

logger = logging.getLogger("global_hunter.engine")


class DynamicExecutionEngine:
    def __init__(
        self,
        adapters: dict[str, ExecutionAdapter],
        default_adapter: str,
        ledger: CapitalLedger,
        order_timeout_s: float = 20.0,
    ) -> None:
        if default_adapter not in adapters:
            raise ValueError(f"default_adapter={default_adapter!r} not in adapters={list(adapters)}")
        self.adapters = adapters
        self.default_adapter = default_adapter
        self.ledger = ledger
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
        if not self.ledger.reserve(order.size_sek):
            logger.warning("Insufficient available capital for %s (%.2f SEK requested)", opp_id, order.size_sek)
            return ExecutionResult(
                order_id=f"nocap-{opp_id}", opportunity_id=opp_id, status="rejected",
                realized_or_expected_sek=0.0, executed_at=datetime.now(timezone.utc), adapter=adapter.name,
            )

        try:
            result = await asyncio.wait_for(adapter.place_order(order), timeout=self.order_timeout_s)
        except Exception:
            logger.exception("Execution failed for %s via %s", opp_id, adapter.name)
            self.ledger.release(order.size_sek)
            raise
        finally:
            # EXECUTE_NOW positions close/net out immediately -> capital frees up.
            # BUY_AND_HOLD keeps capital reserved until an explicit close (tracked
            # by the adapter's position book) -- see orchestrator's close-out loop.
            if order.action is ActionType.EXECUTE_NOW:
                self.ledger.release(order.size_sek)
        return result

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
