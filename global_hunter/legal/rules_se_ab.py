"""Explicit, testable legality rules for trading as a Swedish Aktiebolag (AB).

Design principle (rule #6 in .cursorrules): this is NOT an LLM call in the
production path. Every rule is a small pure function returning
`(allowed: bool, reason: str)`. Unclear cases fail CLOSED (blocked), never
open -- see the profitability mandate's "rapportera arligt" clause: it is
better to miss an opportunity than to (mis)trade something illegally.

This is engineering scaffolding, not legal advice. Get real legal/compliance
sign-off before flipping any of the opt-in flags below for actual capital.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from global_hunter.contracts import MarketType, Opportunity

#: Venues this system is coded to talk to today. Extend as you add adapters;
#: an unknown venue is blocked by default (fail closed), not silently allowed.
KNOWN_VENUES: frozenset[str] = frozenset(
    {
        "binance_spot", "binance_perp_funding",
        "okx_spot", "okx_perp_funding",
        "yahoo_tradfi",
        "polymarket_outcome_sum", "opportunistic_market_scraper.polymarket_outcome_sum",
        "global_market_neutral_arbitrage", "predictive_value_accumulation",
    }
)

#: Jurisdictions/venues that are categorically off-limits for a Swedish AB
#: (sanctions / embargo risk). Populate with real screening data before going
#: live with new venues -- this is a placeholder guard, not a compliance feed.
SANCTIONED_VENUE_KEYWORDS: frozenset[str] = frozenset({"iran", "north_korea", "russia_moex", "syria"})


@dataclass
class RuleContext:
    """Toggles the entrepreneur must explicitly set -- business/legal
    decisions, never inferred by the bot itself (see .cursorrules mandate:
    "fraga aldrig om tekniskt arbete du kan gora sjalv" but licensing IS a
    business decision).
    """

    allow_prediction_markets: bool = False  # requires Spelinspektionen sign-off -- see note below
    extra_allowed_sources: frozenset[str] = field(default_factory=frozenset)


def rule_known_source(opportunity: Opportunity, ctx: RuleContext) -> tuple[bool, str]:
    if opportunity.source in KNOWN_VENUES or opportunity.source in ctx.extra_allowed_sources:
        return True, "source_registered"
    return False, f"unknown_source:{opportunity.source}"


def rule_no_sanctioned_venue(opportunity: Opportunity, ctx: RuleContext) -> tuple[bool, str]:
    haystack = " ".join(
        str(opportunity.raw.get(k, "")) for k in ("buy_venue", "sell_venue", "venue")
    ).lower()
    haystack += f" {opportunity.instrument}".lower()
    for keyword in SANCTIONED_VENUE_KEYWORDS:
        if keyword in haystack:
            return False, f"sanctioned_venue_keyword:{keyword}"
    return True, "no_sanction_match"


def rule_no_flagged_insider_info(opportunity: Opportunity, ctx: RuleContext) -> tuple[bool, str]:
    """Any module that ever tags an Opportunity as sourced from non-public
    information must set raw['insider_flag']=True; this hard-blocks it.
    Insider trading / market manipulation (MAR, Sw. marknadsmissbruk) is a
    criminal offence -- there is no risk/reward tradeoff here, ever.
    """
    if opportunity.raw.get("insider_flag"):
        return False, "insider_or_non_public_information"
    return True, "no_insider_flag"


def rule_prediction_market_license(opportunity: Opportunity, ctx: RuleContext) -> tuple[bool, str]:
    """Prediction-market contracts can be classed as wagering under Swedish
    gambling law (Spellagen 2018:1138), which requires a Spelinspektionen
    license to offer/trade systematically as a business. Default: BLOCKED.
    The entrepreneur must explicitly set `allow_prediction_markets=True`
    (i.e. after confirming the specific product's legal status / license)
    before these opportunities are ever approved.
    """
    if opportunity.market_type is not MarketType.PREDICTION_MARKET:
        return True, "not_a_prediction_market"
    if ctx.allow_prediction_markets:
        return True, "prediction_market_explicitly_enabled"
    return False, "prediction_market_requires_explicit_legal_opt_in"


def rule_settlement_currency_supported(opportunity: Opportunity, ctx: RuleContext) -> tuple[bool, str]:
    """Placeholder for currency/settlement-rail checks (e.g. an AB needs a
    bank/exchange account that can actually receive the settlement currency).
    Always passes today; wire in real account capability checks before you
    add a venue that settles in something your AB cannot bank.
    """
    return True, "settlement_check_not_yet_restrictive"


#: Ordered so the cheapest/most decisive checks run first.
ALL_RULES = (
    rule_known_source,
    rule_no_sanctioned_venue,
    rule_no_flagged_insider_info,
    rule_prediction_market_license,
    rule_settlement_currency_supported,
)


def evaluate_all(opportunity: Opportunity, ctx: RuleContext) -> tuple[bool, str]:
    """Run every rule; short-circuit and fail CLOSED on the first violation."""
    for rule in ALL_RULES:
        allowed, reason = rule(opportunity, ctx)
        if not allowed:
            return False, reason
    return True, "all_rules_passed"
