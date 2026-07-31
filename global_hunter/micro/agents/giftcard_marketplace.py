"""Micro-Agent #8: GiftCardMarketplaceArbitrage. SKELETON -- needs a
marketplace partner API key (e.g. CardCash, Raise, GiftCash) to go live.
Internally reuses global_hunter.ingestion's config-driven `threshold` rule
(see ingestion/schema.py) rather than reimplementing price-extraction logic,
demonstrating the intended composability of that engine.

WHY this has a statistical edge
--------------------------------
Gift-card resale marketplaces let consumers trade convenience (an unwanted
card) for immediate liquidity at a discount to face value. Most listings
cluster tightly around each marketplace's typical discount rate for a given
retailer (driven by that retailer's own redemption/fraud risk and resale
demand), but individual sellers occasionally misprice a listing well below
the marketplace's own norm (impatience, unfamiliarity with the going rate).
Buying below the marketplace's OWN typical discount and reselling at (or
redeeming toward) face value captures that individual mispricing -- a
retail/logistics arbitrage rather than a financial-market one, which is
exactly the diversification the entrepreneur asked for ("jag vill inte lasa
dig vid specifika marknader").
"""

from __future__ import annotations

import logging

from global_hunter.contracts import MarketType, Opportunity
from global_hunter.ingestion.engine import GlobalIngestionEngine
from global_hunter.ingestion.schema import FeedConfig
from global_hunter.micro.base import MicroAgent

logger = logging.getLogger("global_hunter.micro.giftcard_marketplace")


class GiftCardMarketplaceArbitrage(MicroAgent):
    name = "micro_giftcard_marketplace_arbitrage"
    market_types = (MarketType.MARKETPLACE,)
    poll_interval_s = 300.0
    asset_class = "retail marketplace (gift-card resale)"
    target_daily_sek = 100.0
    edge_rationale = (
        "Individual gift-card listings occasionally price well below the "
        "marketplace's own typical discount rate for that retailer; buying "
        "those and redeeming/reselling toward face value captures the gap."
    )

    def __init__(self, feeds: list[FeedConfig] | None = None) -> None:
        """`feeds` is a list of `global_hunter.ingestion.schema.FeedConfig`
        (kind="threshold") pointed at a real marketplace API -- see
        global_hunter/ingestion/feeds/auction_watch_template.yaml for the
        exact config shape (this agent uses the identical rule engine).
        Leave empty (default) until you have a marketplace API key; scan()
        then idles gracefully instead of erroring.
        """
        self.feeds = feeds or []
        self._engine = GlobalIngestionEngine(feeds=self.feeds) if self.feeds else None
        self._warned_empty = False

    async def scan(self) -> list[Opportunity]:
        if self._engine is None:
            if not self._warned_empty:
                logger.warning(
                    "%s: no marketplace feeds configured -- pass FeedConfig(s) for a real "
                    "gift-card API (see ingestion/feeds/auction_watch_template.yaml for the shape).",
                    self.name,
                )
                self._warned_empty = True
            return []
        return await self._engine.scan()

    async def aclose(self) -> None:
        if self._engine is not None:
            await self._engine.aclose()
