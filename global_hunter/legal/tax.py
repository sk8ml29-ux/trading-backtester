"""Net-profit-after-tax math for a Swedish Aktiebolag.

Honesty note: Swedish bolagsskatt is levied on the AB's AGGREGATE ANNUAL
result reported to Skatteverket -- not per trade. The per-trade calculation
here is a *decision-support approximation* ("is this edge still worth it
after an estimated tax drag + costs?"), useful for gating/sizing, not a
substitute for real bookkeeping/annual tax filing. Never report this
per-trade number as "the tax we paid" in any real accounting context.
"""

from __future__ import annotations

from dataclasses import dataclass

from global_hunter.config import CORP_TAX_RATE_SE


@dataclass(frozen=True)
class CostAssumptions:
    """Per-leg cost estimate. Override with real, venue-specific numbers
    (maker/taker fee tiers, spread, FX conversion) before trusting this at
    any real size -- these are conservative generic defaults.
    """

    taker_fee_pct_per_leg: float = 0.10
    fx_conversion_fee_pct: float = 0.0
    misc_fee_sek: float = 0.0


@dataclass(frozen=True)
class NetProfitBreakdown:
    gross_profit_sek: float
    cost_sek: float
    pretax_profit_sek: float
    tax_sek: float
    net_profit_sek: float


class SwedishABTaxCalculator:
    def __init__(self, corp_tax_rate: float = CORP_TAX_RATE_SE) -> None:
        self.corp_tax_rate = corp_tax_rate

    def estimate_transaction_cost_sek(
        self, size_sek: float, legs: int, cost: CostAssumptions
    ) -> float:
        fee = size_sek * (cost.taker_fee_pct_per_leg / 100.0) * legs
        fx = size_sek * (cost.fx_conversion_fee_pct / 100.0)
        return fee + fx + cost.misc_fee_sek

    def net_profit(self, gross_profit_sek: float, cost_sek: float) -> NetProfitBreakdown:
        pretax = gross_profit_sek - cost_sek
        if pretax <= 0:
            # A loss (or a wash) generates no tax on this trade in isolation;
            # in reality it offsets other profit in the annual return.
            return NetProfitBreakdown(gross_profit_sek, cost_sek, pretax, 0.0, pretax)
        tax = pretax * self.corp_tax_rate
        return NetProfitBreakdown(gross_profit_sek, cost_sek, pretax, tax, pretax - tax)
