"""Thin helper reusing Polymarket's public Gamma API (same one used by
scanner/scraper.py's PolymarketOutcomeSumScraper) to check a specific
market's recent implied-probability move -- a "did the crowd just reprice
this event" confirming signal for AlphaEventScanner.
"""

from __future__ import annotations

from global_hunter.scanner.http_venue import HttpVenueMixin

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"


class PolymarketWatcher(HttpVenueMixin):
    async def fetch_market(self, slug: str) -> dict | None:
        rows = await self.get_json(GAMMA_MARKETS_URL, params={"slug": slug})
        return rows[0] if rows else None

    async def recent_price_move_pct(self, slug: str) -> float:
        """Largest absolute recent price-change field available for this
        market, in percentage points (Gamma reports these as fractions).
        Returns 0.0 if the market/fields aren't found.
        """
        market = await self.fetch_market(slug)
        if market is None:
            return 0.0
        candidates = [
            abs(float(market.get(field, 0.0) or 0.0))
            for field in ("oneDayPriceChange", "oneWeekPriceChange")
        ]
        return max(candidates) * 100.0 if candidates else 0.0
