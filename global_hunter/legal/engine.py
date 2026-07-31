"""LegalAndTaxFilter: the ONLY gate between a detected Opportunity and real
capital. Deterministic, testable, fail-closed (rule #6 in .cursorrules).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from global_hunter.config import MAX_POSITION_PCT_OF_CAPITAL
from global_hunter.contracts import ApprovedOrder, Opportunity, RejectedOpportunity
from global_hunter.legal import rules_se_ab
from global_hunter.legal.tax import CostAssumptions, SwedishABTaxCalculator

logger = logging.getLogger("global_hunter.legal")


@dataclass
class LegalConfig:
    max_position_pct_of_capital: float = MAX_POSITION_PCT_OF_CAPITAL
    min_net_profit_sek: float = 50.0  # skip if the expected net-of-tax edge is this thin
    rule_context: rules_se_ab.RuleContext = field(default_factory=rules_se_ab.RuleContext)
    cost_assumptions: CostAssumptions = field(default_factory=CostAssumptions)
    legs_per_trade: int = 2


class LegalAndTaxFilter:
    def __init__(
        self,
        config: LegalConfig | None = None,
        tax_calculator: SwedishABTaxCalculator | None = None,
    ) -> None:
        self.config = config or LegalConfig()
        self.tax_calculator = tax_calculator or SwedishABTaxCalculator()

    async def evaluate(
        self, opportunity: Opportunity, available_capital_sek: float
    ) -> "ApprovedOrder | RejectedOpportunity":
        allowed, reason = rules_se_ab.evaluate_all(opportunity, self.config.rule_context)
        if not allowed:
            return self._reject(opportunity, reason)

        size_sek = self._position_size(available_capital_sek)
        if size_sek <= 0:
            return self._reject(opportunity, "no_capital_available")

        gross_profit_sek = size_sek * (opportunity.edge_pct / 100.0) * opportunity.confidence
        cost_sek = self.tax_calculator.estimate_transaction_cost_sek(
            size_sek, self.config.legs_per_trade, self.config.cost_assumptions
        )
        breakdown = self.tax_calculator.net_profit(gross_profit_sek, cost_sek)

        if breakdown.net_profit_sek < self.config.min_net_profit_sek:
            return self._reject(
                opportunity,
                f"net_profit_below_threshold:{breakdown.net_profit_sek:.2f}sek",
            )

        return ApprovedOrder(
            opportunity=opportunity,
            action=opportunity.action,
            size_sek=size_sek,
            expected_gross_profit_sek=breakdown.gross_profit_sek,
            expected_cost_sek=breakdown.cost_sek,
            expected_tax_sek=breakdown.tax_sek,
            expected_net_profit_sek=breakdown.net_profit_sek,
            legal_basis=reason,
        )

    def _position_size(self, available_capital_sek: float) -> float:
        return max(0.0, available_capital_sek * self.config.max_position_pct_of_capital)

    @staticmethod
    def _reject(opportunity: Opportunity, reason: str) -> RejectedOpportunity:
        logger.info("REJECT %s (%s): %s", opportunity.id, opportunity.source, reason)
        return RejectedOpportunity(opportunity=opportunity, reason=reason, rejected_at=datetime.now(timezone.utc))
