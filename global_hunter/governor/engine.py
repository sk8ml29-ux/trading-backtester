"""ExecutionGovernor: the autonomous decision window between LegalAndTaxFilter
and DynamicExecutionEngine that answers, with zero human input and in a
single in-memory (no I/O) critical section:

    "Om el-arbitraget ger 5 000 kr stabilt, men Polymarket-orderboken
    plotsligt visar en felprissattning som kan ge 15 000 kr pa en timme,
    hur omfordelar systemet kapitalet blixtsnabbt?"

Mechanism: every admitted order/holding gets a `velocity_sek_per_hour`
(expected net SEK / expected hours to realize -- see governor/base.py). A
new opportunity that can't be funded from free capital alone is only
allowed to preempt (close early) existing BUY_AND_HOLD holdings if:

  1. its velocity is at least `preempt_multiplier`x every holding it would
     close (default 2x -- a 15 000 kr/h opportunity comfortably clears a
     5 000 kr/day~=208 kr/h holding; a fair fight isn't a "reallocation
     opportunity"), AND
  2. the net SEK gain (incoming profit minus forfeited expected profit of
     what gets closed) clears `min_preempt_gain_sek` (guards against
     thrashing on marginal calls and provides slack for the early-close's
     own transaction cost), AND
  3. EXECUTE_NOW opportunities are NEVER preempted -- their entire edge
     depends on closing within seconds, unwinding one early would just
     realize a loss for no reason.

Latency: `admit()` touches only in-memory dicts/lists (ledger check, sort
over a handful of active commitments) inside a single asyncio.Lock -- no
network I/O in the critical section, so this decision is effectively
instantaneous relative to any of the scanner's network round-trips. The
only I/O is the (async, non-blocking) call to actually close a preempted
position, which happens AFTER the decision is made.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from global_hunter.config import CapitalLedger
from global_hunter.contracts import ActionType, ApprovedOrder, ExecutionResult
from global_hunter.engine.engine import DynamicExecutionEngine
from global_hunter.governor.base import Commitment

logger = logging.getLogger("global_hunter.governor")


class ExecutionGovernor:
    def __init__(
        self,
        ledger: CapitalLedger,
        execution_engine: DynamicExecutionEngine,
        preempt_multiplier: float = 2.0,
        min_preempt_gain_sek: float = 200.0,
    ) -> None:
        self.ledger = ledger
        self.execution_engine = execution_engine
        self.preempt_multiplier = preempt_multiplier
        self.min_preempt_gain_sek = min_preempt_gain_sek
        self._commitments: dict[str, Commitment] = {}
        self._decision_lock = asyncio.Lock()

    def active_commitments(self) -> list[Commitment]:
        return [c for c in self._commitments.values() if c.status == "active"]

    async def admit(self, order: ApprovedOrder) -> ApprovedOrder | None:
        """Reserve capital for `order`, preempting slower holdings if
        needed and justified. Returns `order` if admitted, else None
        (defer -- the opportunity is not executed this cycle).
        """
        async with self._decision_lock:
            if self.ledger.reserve(order.size_sek):
                self._commitments[order.opportunity.id] = Commitment(order, datetime.now(timezone.utc))
                return order

            if not await self._try_preempt(order):
                logger.info(
                    "DEFER %s (%s): insufficient free capital and no slower holding worth preempting",
                    order.opportunity.id, order.opportunity.source,
                )
                return None

            if not self.ledger.reserve(order.size_sek):
                logger.warning(
                    "Preemption freed capital but reservation still failed for %s -- deferring", order.opportunity.id
                )
                return None
            self._commitments[order.opportunity.id] = Commitment(order, datetime.now(timezone.utc))
            return order

    async def _try_preempt(self, incoming: ApprovedOrder) -> bool:
        """`incoming` can be either action type (a fleeting EXECUTE_NOW
        arbitrage edge or a new BUY_AND_HOLD thesis) -- what matters is only
        that whatever gets CLOSED to fund it is a BUY_AND_HOLD commitment
        (EXECUTE_NOW commitments already release capital almost instantly
        on their own, so there is never anything useful to preempt there).
        """
        needed_sek = incoming.size_sek - self.ledger.available_sek
        if needed_sek <= 0:
            return True

        incoming_velocity = Commitment(incoming, datetime.now(timezone.utc)).velocity_sek_per_hour
        # Ascending by velocity: sacrifice the SLOWEST holdings first.
        candidates = sorted(
            (c for c in self.active_commitments() if c.order.action is ActionType.BUY_AND_HOLD),
            key=lambda c: c.velocity_sek_per_hour,
        )

        to_close: list[Commitment] = []
        freed_sek = 0.0
        for candidate in candidates:
            if freed_sek >= needed_sek:
                break
            # Ascending order means this threshold only gets harder to
            # clear as we move to faster candidates -- first failure means
            # every remaining candidate fails too, so it's safe to stop.
            if incoming_velocity < candidate.velocity_sek_per_hour * self.preempt_multiplier:
                break
            to_close.append(candidate)
            freed_sek += candidate.order.size_sek

        if freed_sek < needed_sek or not to_close:
            return False

        forfeited_sek = sum(c.order.expected_net_profit_sek for c in to_close)
        net_gain_sek = incoming.expected_net_profit_sek - forfeited_sek
        if net_gain_sek < self.min_preempt_gain_sek:
            logger.info(
                "SKIP preempt for %s: net gain %.2f SEK below min_preempt_gain_sek=%.2f SEK",
                incoming.opportunity.id, net_gain_sek, self.min_preempt_gain_sek,
            )
            return False

        logger.warning(
            "PREEMPT: closing %d holding(s) worth %.2f SEK (forfeiting ~%.2f SEK expected profit) "
            "to fund %s (%s, velocity %.1f SEK/h) -- net gain ~%.2f SEK",
            len(to_close), freed_sek, forfeited_sek, incoming.opportunity.id,
            incoming.opportunity.source, incoming_velocity, net_gain_sek,
        )
        for commitment in to_close:
            await self._close_commitment(commitment)
        return True

    async def _close_commitment(self, commitment: Commitment) -> None:
        commitment.status = "closing"
        try:
            result = await self.execution_engine.close(commitment.order)
        except Exception:
            logger.exception(
                "Failed to close commitment %s during preemption -- capital stays reserved (safe failure mode)",
                commitment.order.opportunity.id,
            )
            commitment.status = "active"
            raise
        self.ledger.release(commitment.order.size_sek)
        commitment.status = "closed"
        self._commitments.pop(commitment.order.opportunity.id, None)
        logger.info(
            "Closed %s early: %.2f SEK realized/forfeited", commitment.order.opportunity.id,
            result.realized_or_expected_sek,
        )

    def on_execution_result(self, result: ExecutionResult) -> None:
        """Call after every ExecutionResult (from the orchestrator's
        reporting loop). Releases capital for anything that finished
        (filled/rejected/closed); a "held" result leaves the commitment (and
        its capital reservation) active until an explicit close.
        """
        commitment = self._commitments.get(result.opportunity_id)
        if commitment is None:
            return
        if result.status in ("filled", "rejected", "closed"):
            self.ledger.release(commitment.order.size_sek)
            commitment.status = "closed"
            self._commitments.pop(result.opportunity_id, None)
