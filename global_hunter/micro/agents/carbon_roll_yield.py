"""Micro-Agent #9: CarbonAllowanceRollYield. SKELETON -- needs an EU ETS
(EUA) futures curve data source (e.g. ICE Endex, EEX, or a data vendor) and
a futures-capable broker; formula and structure are complete, data feed is
a clearly-marked TODO.

WHY this has a statistical edge
--------------------------------
EU Emissions Trading System allowance (EUA) futures typically trade in
CONTANGO: longer-dated contracts price above near-dated ones, reflecting
financing/cost-of-carry plus the market's expectation that the EU's
declining annual emissions cap makes allowances scarcer (and pricier) over
time. As a near contract approaches expiry and rolls into the next one, a
trader long the calendar spread (short near, long far -- or simply holding
through the roll) can systematically capture that structural term-structure
slope, similar in spirit to commodity futures roll-yield strategies but
tied to a policy-driven scarcity mechanism (the shrinking cap) rather than
storage costs -- a genuinely different, non-crypto, non-equity asset class
from everything else in this portfolio.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.micro.base import MicroAgent

logger = logging.getLogger("global_hunter.micro.carbon_roll_yield")


class CarbonAllowanceRollYield(MicroAgent):
    name = "micro_carbon_allowance_roll_yield"
    market_types = (MarketType.COMMODITY,)
    poll_interval_s = 3600.0
    asset_class = "EU carbon allowance futures (term-structure roll yield)"
    target_daily_sek = 110.0
    edge_rationale = (
        "EUA futures typically trade in contango driven by the EU's declining "
        "annual emissions cap; holding through the near-contract roll "
        "systematically captures that structural term-structure slope."
    )

    def __init__(self, min_annualized_roll_yield_pct: float = 5.0) -> None:
        self.min_annualized_roll_yield_pct = min_annualized_roll_yield_pct
        self._warned_no_source = False

    async def _fetch_curve(self) -> tuple[float, float, int] | None:
        """TODO: wire a real EUA futures curve source. Concrete options:
        - ICE Endex / EEX EUA futures via a data vendor subscription.
        - A futures broker's market-data API if it lists EUA contracts.
        Returns (near_price, far_price, days_between_contracts) or None.
        """
        return None

    async def scan(self) -> list[Opportunity]:
        curve = await self._fetch_curve()
        if curve is None:
            if not self._warned_no_source:
                logger.warning(
                    "%s: no EUA futures curve source wired yet (see _fetch_curve docstring) "
                    "-- skeleton stays idle.", self.name,
                )
                self._warned_no_source = True
            return []

        near_price, far_price, days_between = curve
        if near_price <= 0 or days_between <= 0:
            return []
        roll_yield_pct = (far_price - near_price) / near_price * 100.0
        annualized_pct = roll_yield_pct * (365.0 / days_between)
        if annualized_pct < self.min_annualized_roll_yield_pct:
            return []

        return [
            Opportunity(
                id=f"{self.name}:EUA:{int(time.time() * 1000)}",
                source=self.name,
                instrument="EUA_calendar_roll",
                market_type=MarketType.COMMODITY,
                edge_pct=min(4.0, annualized_pct / 6.0),  # fraction of the annualized roll captured per short hold, net of financing
                confidence=0.65,
                action=ActionType.BUY_AND_HOLD,
                horizon_days=30.0,
                detected_at=datetime.now(timezone.utc),
                raw={"near_price": near_price, "far_price": far_price, "annualized_roll_yield_pct": annualized_pct},
            )
        ]
