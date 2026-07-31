"""The entrepreneur's Micro-Agent portfolio: 8-10 independent, isolated
micro-strategies, deliberately spread across unrelated asset classes and
mechanisms so that no single API, exchange, or market regime can take out
more than a small slice of the system's revenue.

Run `python -m global_hunter.micro.registry` to print the full portfolio
with each agent's edge rationale and target -- this is the "presentera din
egenvalda portfolj" deliverable.
"""

from __future__ import annotations

from global_hunter.micro.agents.carbon_roll_yield import CarbonAllowanceRollYield
from global_hunter.micro.agents.dual_listing import DualListingArbitrage
from global_hunter.micro.agents.earnings_drift import PostEarningsAnnouncementDrift
from global_hunter.micro.agents.funding_seasonality import FundingRateSeasonality
from global_hunter.micro.agents.giftcard_marketplace import GiftCardMarketplaceArbitrage
from global_hunter.micro.agents.miner_bullion_ratio import MinerBullionRatioReversion
from global_hunter.micro.agents.sports_surebet import SportsBettingSurebetScanner
from global_hunter.micro.agents.stablecoin_peg import StablecoinPegArbitrage
from global_hunter.micro.agents.triangular_crypto import TriangularCryptoArbitrage
from global_hunter.micro.agents.volatility_risk_premium import VolatilityRiskPremiumHarvester
from global_hunter.micro.base import MicroAgent

#: LIVE = real data sources verified against production APIs during
#: development. SKELETON = structurally complete and legally/mathematically
#: correct, but idles gracefully until a paid/partner data source (noted in
#: each file's docstring) is wired in -- never crashes, never fabricates data.
STATUS = {
    "micro_stablecoin_peg_arbitrage": "LIVE",
    "micro_triangular_crypto_arbitrage": "LIVE",
    "micro_dual_listing_arbitrage": "LIVE",
    "micro_miner_bullion_ratio_reversion": "LIVE",
    "micro_funding_rate_seasonality": "LIVE (partial -- see file docstring)",
    "micro_sports_betting_surebet": "SKELETON (needs ODDS_API_KEY)",
    "micro_volatility_risk_premium": "SKELETON (needs options IV source)",
    "micro_giftcard_marketplace_arbitrage": "SKELETON (needs marketplace API key)",
    "micro_carbon_allowance_roll_yield": "SKELETON (needs EUA futures curve source)",
    "micro_post_earnings_drift": "SKELETON (needs earnings-surprise data source)",
}


def default_micro_agents() -> list[MicroAgent]:
    return [
        StablecoinPegArbitrage(),
        TriangularCryptoArbitrage(),
        DualListingArbitrage(),
        MinerBullionRatioReversion(),
        FundingRateSeasonality(),
        SportsBettingSurebetScanner(),
        VolatilityRiskPremiumHarvester(),
        GiftCardMarketplaceArbitrage(),
        CarbonAllowanceRollYield(),
        PostEarningsAnnouncementDrift(),
    ]


def _print_portfolio() -> None:
    agents = default_micro_agents()
    total_target = sum(a.target_daily_sek for a in agents)
    print(f"Micro-Agent portfolio: {len(agents)} independent, isolated strategies")
    print(f"Combined target: {total_target:,.0f} SEK/day (~{total_target * 21:,.0f} SEK/month, 21 trading days)\n")
    for agent in agents:
        profile = agent.profile()
        status = STATUS.get(profile.name, "?")
        print(f"[{status}] {profile.name}")
        print(f"    Asset class: {profile.asset_class}")
        print(f"    Target:      {profile.target_daily_sek:,.0f} SEK/day")
        print(f"    Edge:        {profile.edge_rationale}")
        print()


if __name__ == "__main__":
    _print_portfolio()
