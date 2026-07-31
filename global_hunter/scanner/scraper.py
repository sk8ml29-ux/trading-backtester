"""OpportunisticMarketScraper.

A generic, pluggable class that hits ANY external web API or order book and
flags a mispricing using a user-supplied `extractor` + `evaluator` pair --
this is the "koppla mot valfria externa API:er" requirement made literal:
adding a new target is a ~10-line config entry, not a new code path.

Ships with one concrete, real-world example: `PolymarketOutcomeSumScraper`,
which finds genuine near-arbitrage on Polymarket's public Gamma API. Every
market's outcome prices should sum to ~1.0 (buying every outcome guarantees
a $1 payout at resolution); when the *ask-side* sum drops meaningfully below
1.0, buying the complete set locks in a risk-adjusted edge, which is exactly
the "felprissatta digitala kontrakt" the entrepreneur described.

IMPORTANT legal note lives in `global_hunter/legal/rules_se_ab.py`:
prediction markets are gated OFF by default because wagering-style
contracts can fall under Swedish gambling law (Spellagen) -- an explicit
entrepreneur opt-in + legal sign-off is required before this module's
opportunities are ever approved for execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.scanner.base import UniversalValueModule
from global_hunter.scanner.http_venue import HttpVenueMixin

logger = logging.getLogger("global_hunter.scanner.scraper")

#: (raw_json_from_one_endpoint) -> list of candidate dicts to evaluate
Extractor = Callable[[Any], list[dict]]
#: (candidate_dict) -> Opportunity | None
Evaluator = Callable[[dict], "Opportunity | None"]


@dataclass(frozen=True)
class ScrapeTarget:
    """One externally-scraped endpoint plus the logic to turn its JSON into Opportunities."""

    name: str
    url: str
    market_type: MarketType
    extractor: Extractor
    evaluator: Evaluator
    params: dict[str, Any] = field(default_factory=dict)


class OpportunisticMarketScraper(HttpVenueMixin, UniversalValueModule):
    """Generic external-API/orderbook mispricing scanner.

    `scan()` fans out to every registered ScrapeTarget concurrently, runs
    each target's `extractor` -> `evaluator` pipeline, and collects
    whatever Opportunities fall out. No target-specific logic lives here.
    """

    name = "opportunistic_market_scraper"
    market_types = (MarketType.PREDICTION_MARKET, MarketType.MARKETPLACE, MarketType.DIGITAL_CONTRACT)
    poll_interval_s = 45.0

    def __init__(self, targets: list[ScrapeTarget]) -> None:
        HttpVenueMixin.__init__(self)
        self.targets = targets

    async def scan(self) -> list[Opportunity]:
        results = await asyncio.gather(
            *(self._scan_target(t) for t in self.targets), return_exceptions=True
        )
        opportunities: list[Opportunity] = []
        for target, result in zip(self.targets, results):
            if isinstance(result, BaseException):
                logger.debug("%s: target %s failed: %s", self.name, target.name, result)
                continue
            opportunities.extend(result)
        return opportunities

    async def _scan_target(self, target: ScrapeTarget) -> list[Opportunity]:
        raw = await self.get_json(target.url, params=target.params)
        candidates = target.extractor(raw)
        opportunities: list[Opportunity] = []
        for candidate in candidates:
            try:
                opp = target.evaluator(candidate)
            except Exception:  # noqa: BLE001 - one malformed row must not drop the whole batch
                logger.debug("%s: evaluator raised on a candidate from %s", self.name, target.name, exc_info=True)
                continue
            if opp is not None:
                opportunities.append(opp)
        return opportunities


# ---------------------------------------------------------------------------
# Concrete example #1: Polymarket outcome-sum arbitrage (real, public API).
# ---------------------------------------------------------------------------

POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com/markets"


def _polymarket_extractor(raw: Any) -> list[dict]:
    return list(raw) if isinstance(raw, list) else []


def _make_polymarket_evaluator(min_net_edge_pct: float, fee_buffer_pct: float) -> Evaluator:
    def _evaluate(market: dict) -> "Opportunity | None":
        try:
            outcomes = json.loads(market["outcomes"])
            prices = [float(p) for p in json.loads(market["outcomePrices"])]
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if len(prices) < 2 or not market.get("enableOrderBook", False):
            return None

        price_sum_pct = sum(prices) * 100.0
        # Buying the complete outcome set for < 100% guarantees a 100% payout
        # at resolution. Net edge = (100 - sum) minus an execution/fee buffer.
        net_edge_pct = (100.0 - price_sum_pct) - fee_buffer_pct
        if net_edge_pct <= min_net_edge_pct:
            return None

        return Opportunity(
            id=f"scrape:polymarket:{market.get('id', market.get('slug'))}:{int(time.time() * 1000)}",
            source="opportunistic_market_scraper.polymarket_outcome_sum",
            instrument=str(market.get("slug", market.get("id"))),
            market_type=MarketType.PREDICTION_MARKET,
            edge_pct=net_edge_pct,
            confidence=0.90,  # locked in at resolution absent counterparty/settlement risk
            action=ActionType.EXECUTE_NOW,
            detected_at=datetime.now(timezone.utc),
            raw={
                "question": market.get("question"),
                "outcomes": outcomes,
                "outcome_prices": prices,
                "price_sum_pct": price_sum_pct,
                "liquidity": market.get("liquidity"),
                "end_date": market.get("endDate"),
            },
        )

    return _evaluate


def polymarket_outcome_sum_target(
    limit: int = 100, min_net_edge_pct: float = 1.0, fee_buffer_pct: float = 1.0
) -> ScrapeTarget:
    """Build the ScrapeTarget for Polymarket's public Gamma API.

    `fee_buffer_pct` should be calibrated to real taker fees (Polymarket's
    `feeSchedule` varies per market) before this is trusted with real money.
    """
    return ScrapeTarget(
        name="polymarket_outcome_sum",
        url=POLYMARKET_GAMMA_URL,
        market_type=MarketType.PREDICTION_MARKET,
        extractor=_polymarket_extractor,
        evaluator=_make_polymarket_evaluator(min_net_edge_pct, fee_buffer_pct),
        params={"limit": limit, "active": "true", "closed": "false"},
    )


def default_targets() -> list[ScrapeTarget]:
    return [polymarket_outcome_sum_target()]
