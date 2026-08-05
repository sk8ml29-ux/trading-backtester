"""The aggregated mathematical model behind the 30 000 SEK/month target.

Formula (linear, per-module, then summed):

    Gross_i        = C_i * e_i * f_i * w_i
    Gross_total     = sum_i Gross_i
    Pretax_total    = Gross_total - FixedCosts
    Tax_total       = max(0, Pretax_total) * CORP_TAX_RATE_SE
    Net_total       = Pretax_total - Tax_total

Where, per module i:
    C_i = average capital deployed (SEK)
    e_i = average net edge per trade, net of estimated transaction costs (fraction, e.g. 0.001 = 0.10%)
    f_i = trades (or position-cycles) per month
    w_i = empirical win rate / capture probability [0, 1]

This models the four Opportunity-producing edge sources (GlobalMarketNeutralArbitrage,
PredictiveValueAccumulation, OpportunisticMarketScraper, AlphaEventScanner).
GlobalIngestionEngine feeds more trades into the arbitrage/scraper buckets
via config rather than being a bucket of its own; ExecutionGovernor changes
capital efficiency, not the per-trade formula -- see illustrative_scenario()
below for the full reasoning.

This is a LINEAR, NON-COMPOUNDING approximation -- deliberately conservative
and simple to reason about. It ignores compounding within the month and
ignores correlation between modules (in reality a crash can hit several
modules' win rates at once). Treat every e_i/f_i/w_i below as a hypothesis
to be confirmed with real walk-forward/paper-forward data (rule #9 in
.cursorrules) -- NOT as a promise. Running this file only tells you whether
a set of ASSUMED parameters is internally consistent with hitting the goal;
it says nothing about whether those parameters are realistic in tomorrow's
market until you validate them.
"""

from __future__ import annotations

from dataclasses import dataclass

from global_hunter.config import CORP_TAX_RATE_SE, TARGET_NET_SEK_PER_MONTH


@dataclass(frozen=True)
class ModuleAssumption:
    name: str
    capital_sek: float
    net_edge_pct_per_trade: float  # already net of estimated transaction costs
    trades_per_month: float
    win_rate: float

    @property
    def gross_sek_per_month(self) -> float:
        return self.capital_sek * (self.net_edge_pct_per_trade / 100.0) * self.trades_per_month * self.win_rate


@dataclass(frozen=True)
class ProjectionResult:
    modules: tuple[ModuleAssumption, ...]
    gross_sek: float
    fixed_costs_sek: float
    pretax_sek: float
    tax_sek: float
    net_sek: float
    total_capital_sek: float

    @property
    def hits_target(self) -> bool:
        return self.net_sek >= TARGET_NET_SEK_PER_MONTH

    @property
    def capital_needed_for_target_sek(self) -> float:
        """Capital required (scaling every module proportionally) to hit the
        target, holding every per-SEK assumption (e_i, f_i, w_i) fixed."""
        if self.net_sek <= 0:
            return float("inf")
        scale = TARGET_NET_SEK_PER_MONTH / self.net_sek
        return self.total_capital_sek * scale


def project_monthly_net_sek(
    modules: list[ModuleAssumption],
    fixed_costs_sek: float = 0.0,
    corp_tax_rate: float = CORP_TAX_RATE_SE,
) -> ProjectionResult:
    gross = sum(m.gross_sek_per_month for m in modules)
    pretax = gross - fixed_costs_sek
    tax = max(0.0, pretax) * corp_tax_rate
    net = pretax - tax
    return ProjectionResult(
        modules=tuple(modules),
        gross_sek=gross,
        fixed_costs_sek=fixed_costs_sek,
        pretax_sek=pretax,
        tax_sek=tax,
        net_sek=net,
        total_capital_sek=sum(m.capital_sek for m in modules),
    )


def required_avg_monthly_return_pct(
    target_net_sek: float, total_capital_sek: float,
    fixed_costs_sek: float = 0.0, corp_tax_rate: float = CORP_TAX_RATE_SE,
) -> float:
    """Solve backwards: what blended pre-tax monthly return on capital is
    needed to net `target_net_sek` after Swedish corp tax + fixed costs?"""
    pretax_needed = target_net_sek / (1.0 - corp_tax_rate) + fixed_costs_sek
    return pretax_needed / total_capital_sek * 100.0


def illustrative_scenario(total_capital_sek: float = 400_000.0) -> ProjectionResult:
    """Four capital-consuming edge sources, sized so their capital sums to
    `total_capital_sek` (no leverage -- every krona is accounted for once).
    Parameters are STARTING HYPOTHESES -- replace every number with your own
    backtested statistic before trusting this.

    GlobalIngestionEngine is NOT a separate bucket here: its config-driven
    feeds (Steam marketplace zscore watch, extra Binance/OKX pairs, future
    auction/logistics templates) are additional INSTANCES of the same
    arbitrage/threshold/zscore edge types already modeled under
    GlobalMarketNeutralArbitrage and OpportunisticMarketScraper -- so its
    contribution shows up as more trades/month and more diversification
    within those two buckets, not a new formula.

    ExecutionGovernor is NOT modeled as a separate edge source either: it
    changes CAPITAL UTILIZATION (how quickly idle/slow-holding capital gets
    redeployed into faster opportunities), which this linear, static-
    allocation model can't represent without a full event-driven
    simulation. Directionally it can only ever push net_sek UP relative to
    this model (idle capital earning nothing while "stuck" in a slow hold
    is the thing it eliminates) -- treat these projections as a
    conservative floor, not a ceiling.
    """
    weights = {"arbitrage": 0.30, "accumulation": 0.35, "scraper": 0.15, "event": 0.20}
    modules = [
        ModuleAssumption(
            name="GlobalMarketNeutralArbitrage",
            capital_sek=total_capital_sek * weights["arbitrage"],
            net_edge_pct_per_trade=0.10,   # net of ~0.15% round-trip cost buffer
            trades_per_month=60,           # ~2/day across BTC/ETH/SOL/XRP spot + funding pairs (incl. GlobalIngestionEngine config-only pairs)
            win_rate=0.97,
        ),
        ModuleAssumption(
            name="PredictiveValueAccumulation",
            capital_sek=total_capital_sek * weights["accumulation"],
            net_edge_pct_per_trade=4.0,    # avg realized move over a ~30d horizon, per historical analogs
            trades_per_month=2,            # ~2 concurrent buy-and-hold slots opened per month
            win_rate=0.72,                 # empirical historical win rate, gated at >=0.70 by the module itself
        ),
        ModuleAssumption(
            name="OpportunisticMarketScraper",
            capital_sek=total_capital_sek * weights["scraper"],
            net_edge_pct_per_trade=1.5,    # Polymarket outcome-set / mispriced-listing edge, net of fee buffer
            trades_per_month=8,
            win_rate=0.85,
        ),
        ModuleAssumption(
            name="AlphaEventScanner",
            capital_sek=total_capital_sek * weights["event"],
            net_edge_pct_per_trade=6.0,    # heuristic, UNCALIBRATED (see events/engine.py docstring) -- validate before trusting
            trades_per_month=1.0,          # macro/weather/political events are rare by nature
            win_rate=0.55,                 # deliberately the weakest win-rate in the book: least-proven module
        ),
    ]
    return project_monthly_net_sek(modules, fixed_costs_sek=500.0)


def _print_report(result: ProjectionResult) -> None:
    print(f"Total capital deployed: {result.total_capital_sek:,.0f} SEK\n")
    for m in result.modules:
        print(
            f"  {m.name:<28} capital={m.capital_sek:>9,.0f} SEK  "
            f"edge/trade={m.net_edge_pct_per_trade:>5.2f}%  trades/mo={m.trades_per_month:>4.1f}  "
            f"win_rate={m.win_rate:>4.2f}  -> gross={m.gross_sek_per_month:>9,.0f} SEK/mo"
        )
    print()
    print(f"  Gross profit/month:        {result.gross_sek:>10,.0f} SEK")
    print(f"  Fixed costs/month:         {result.fixed_costs_sek:>10,.0f} SEK")
    print(f"  Pre-tax profit/month:      {result.pretax_sek:>10,.0f} SEK")
    print(f"  Bolagsskatt ({CORP_TAX_RATE_SE:.1%}):        {result.tax_sek:>10,.0f} SEK")
    print(f"  NET profit/month:          {result.net_sek:>10,.0f} SEK")
    print(f"  Target:                    {TARGET_NET_SEK_PER_MONTH:>10,.0f} SEK")
    print(f"  Hits target:               {result.hits_target}")
    print(f"  Capital needed for target: {result.capital_needed_for_target_sek:>10,.0f} SEK "
          f"(holding per-SEK assumptions fixed)")


if __name__ == "__main__":
    print("=== Scenario: 400 000 SEK (mid-point of 100k-800k range) ===\n")
    _print_report(illustrative_scenario(400_000.0))

    print("\n=== Scenario: capital scaled to hit target, same per-SEK assumptions ===\n")
    scaled_capital = illustrative_scenario(400_000.0).capital_needed_for_target_sek
    _print_report(illustrative_scenario(scaled_capital))

    print("\n=== Required blended pre-tax monthly return, by capital level ===\n")
    for capital in (100_000, 250_000, 400_000, 600_000, 800_000):
        pct = required_avg_monthly_return_pct(TARGET_NET_SEK_PER_MONTH, capital, fixed_costs_sek=500.0)
        print(f"  {capital:>7,.0f} SEK capital -> needs {pct:>5.2f}% pre-tax blended return/month")
