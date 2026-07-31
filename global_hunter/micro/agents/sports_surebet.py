"""Micro-Agent #6: SportsBettingSurebetScanner. SKELETON -- needs a paid
odds-data API key (e.g. the-odds-api.com) to go live; math and structure
are complete and correct, just gated on `ODDS_API_KEY`.

WHY this has a statistical edge
--------------------------------
Different bookmakers price the same event independently, based on their own
liability books and customer flow, not a shared order book. When you can
back EVERY outcome of an event across different bookmakers such that the
sum of (1 / best_decimal_odds) across all outcomes is < 1.0, staking
proportionally guarantees a profit regardless of the result -- classic
"surebet" arbitrage. The edge exists because bookmakers are slow to
converge on a single "true" price (unlike financial exchanges with a
shared limit order book), and because some accept different amounts of risk
on the same event. This is legally distinct from OpportunisticMarketScraper's
Polymarket outcome-sum check (a CLOB/AMM prediction market, not a
book-based wagering product) -- Swedish gambling law treats sports betting
as its own licensable product category, hence the separate
`allow_sports_betting` gate in legal/rules_se_ab.py.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.micro.base import MicroAgent
from global_hunter.scanner.http_venue import HttpVenueMixin

logger = logging.getLogger("global_hunter.micro.sports_surebet")

ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"


class SportsBettingSurebetScanner(HttpVenueMixin, MicroAgent):
    name = "micro_sports_betting_surebet"
    market_types = (MarketType.PREDICTION_MARKET,)
    poll_interval_s = 120.0
    asset_class = "sports betting (cross-bookmaker surebets)"
    target_daily_sek = 130.0
    edge_rationale = (
        "Independent bookmakers price the same event from different liability "
        "books; when the sum of 1/best-odds across all outcomes < 1.0 across "
        "books, backing every outcome locks in a profit regardless of result."
    )

    def __init__(self, sport_key: str = "soccer_epl", regions: str = "eu,uk", min_net_edge_pct: float = 1.5) -> None:
        HttpVenueMixin.__init__(self)
        self.sport_key = sport_key
        self.regions = regions
        self.min_net_edge_pct = min_net_edge_pct
        self._warned_missing_key = False

    async def scan(self) -> list[Opportunity]:
        api_key = os.environ.get("ODDS_API_KEY", "")
        if not api_key:
            if not self._warned_missing_key:
                logger.warning(
                    "%s: ODDS_API_KEY not set -- this is a licensed data feed "
                    "(entrepreneur decision), skeleton stays idle until configured.",
                    self.name,
                )
                self._warned_missing_key = True
            return []

        events = await self.get_json(
            ODDS_API_URL.format(sport=self.sport_key),
            params={"apiKey": api_key, "regions": self.regions, "markets": "h2h", "oddsFormat": "decimal"},
        )
        opportunities = []
        for event in events:
            opp = self._evaluate_event(event)
            if opp is not None:
                opportunities.append(opp)
        return opportunities

    def _evaluate_event(self, event: dict) -> Opportunity | None:
        best_odds: dict[str, float] = {}
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    name, price = outcome.get("name"), float(outcome.get("price", 0.0))
                    if name and price > best_odds.get(name, 0.0):
                        best_odds[name] = price

        if len(best_odds) < 2 or any(p <= 0 for p in best_odds.values()):
            return None

        implied_sum_pct = sum(1.0 / p for p in best_odds.values()) * 100.0
        net_edge_pct = (100.0 - implied_sum_pct)
        if net_edge_pct <= self.min_net_edge_pct:
            return None

        return Opportunity(
            id=f"{self.name}:{event.get('id')}:{int(time.time() * 1000)}",
            source=self.name,
            instrument=f"{event.get('home_team')}_vs_{event.get('away_team')}",
            market_type=MarketType.PREDICTION_MARKET,
            edge_pct=net_edge_pct,
            confidence=0.85,
            action=ActionType.EXECUTE_NOW,
            detected_at=datetime.now(timezone.utc),
            raw={"product_type": "sports_betting", "best_odds": best_odds, "implied_sum_pct": implied_sum_pct},
        )
