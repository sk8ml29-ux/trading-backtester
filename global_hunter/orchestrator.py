"""GlobalValueHunter: wires UniversalAnomalyScanner -> LegalAndTaxFilter ->
DynamicExecutionEngine through plain asyncio.Queue objects, and tracks
realized/expected SEK against the entrepreneur's monthly target so progress
is visible from the dashboard-style JSON-lines log.

This file is intentionally thin: it contains ZERO market-specific logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from global_hunter.config import CapitalLedger, TARGET_NET_SEK_PER_MONTH
from global_hunter.contracts import ApprovedOrder, ExecutionResult, Opportunity, RejectedOpportunity
from global_hunter.engine.engine import DynamicExecutionEngine
from global_hunter.legal.engine import LegalAndTaxFilter
from global_hunter.scanner.engine import UniversalAnomalyScanner

logger = logging.getLogger("global_hunter.orchestrator")

DECISION_LOG_PATH = Path("data/live/global_hunter/decisions.jsonl")


class GlobalValueHunter:
    def __init__(
        self,
        scanner: UniversalAnomalyScanner,
        legal_filter: LegalAndTaxFilter,
        execution_engine: DynamicExecutionEngine,
        ledger: CapitalLedger,
        decision_log_path: Path = DECISION_LOG_PATH,
    ) -> None:
        self.scanner = scanner
        self.legal_filter = legal_filter
        self.execution_engine = execution_engine
        self.ledger = ledger
        self.decision_log_path = decision_log_path
        self.orders_queue: "asyncio.Queue[ApprovedOrder]" = asyncio.Queue()
        self.results_queue: "asyncio.Queue[ExecutionResult]" = asyncio.Queue()
        self.month_to_date_net_sek: float = 0.0

    async def run_forever(self) -> None:
        await asyncio.gather(
            self.scanner.run(),
            self._filter_loop(),
            self.execution_engine.run_forever(self.orders_queue, self.results_queue),
            self._reporting_loop(),
        )

    async def run_once(self) -> list["ApprovedOrder | RejectedOpportunity"]:
        """Single scan -> filter pass, no execution. Use for dry-run/testing."""
        opportunities = await self.scanner.scan_once()
        decisions = []
        for opp in opportunities:
            decision = await self.legal_filter.evaluate(opp, self.ledger.available_sek)
            self._log_decision(decision)
            decisions.append(decision)
        return decisions

    async def _filter_loop(self) -> None:
        while True:
            opportunity: Opportunity = await self.scanner.queue.get()
            try:
                decision = await self.legal_filter.evaluate(opportunity, self.ledger.available_sek)
                self._log_decision(decision)
                if isinstance(decision, ApprovedOrder):
                    await self.orders_queue.put(decision)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad opportunity must never kill the filter loop
                logger.exception("Filter failed for opportunity %s", opportunity.id)
            finally:
                self.scanner.queue.task_done()

    async def _reporting_loop(self) -> None:
        while True:
            result: ExecutionResult = await self.results_queue.get()
            if result.status in ("filled", "closed"):
                self.month_to_date_net_sek += result.realized_or_expected_sek
                pct_of_target = self.month_to_date_net_sek / TARGET_NET_SEK_PER_MONTH * 100.0
                logger.info(
                    "Execution %s: %.2f SEK realized/expected. Month-to-date: %.2f / %.0f SEK (%.1f%%)",
                    result.order_id, result.realized_or_expected_sek,
                    self.month_to_date_net_sek, TARGET_NET_SEK_PER_MONTH, pct_of_target,
                )
            self.results_queue.task_done()

    def _log_decision(self, decision: "ApprovedOrder | RejectedOpportunity") -> None:
        self.decision_log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "decision_type": "approved" if isinstance(decision, ApprovedOrder) else "rejected",
            "opportunity_id": decision.opportunity.id,
            "source": decision.opportunity.source,
            "instrument": decision.opportunity.instrument,
        }
        if isinstance(decision, ApprovedOrder):
            payload.update(
                size_sek=decision.size_sek,
                expected_net_profit_sek=decision.expected_net_profit_sek,
            )
        else:
            payload["reason"] = decision.reason
        with open(self.decision_log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")

    async def aclose(self) -> None:
        await asyncio.gather(
            self.scanner.aclose(), self.execution_engine.aclose(), return_exceptions=True
        )
