"""Micro-Agent #10: PostEarningsAnnouncementDrift (PEAD). SKELETON -- needs
an earnings-surprise data source (a fundamentals API such as Polygon,
Zacks, or IEX Cloud); the SUE math is complete and is one of the most
heavily replicated findings in empirical finance.

WHY this has a statistical edge
--------------------------------
Post-Earnings-Announcement Drift is one of the oldest and most-replicated
anomalies in academic finance (Bernard & Thomas, 1989, and hundreds of
follow-up studies across markets and decades): stocks with a large POSITIVE
earnings surprise continue drifting UP for roughly 60-90 days afterward,
and large negative surprises keep drifting down, because a meaningful share
of market participants underreact to the full information content of an
earnings surprise and the price only slowly incorporates it. The standard
measure is Standardized Unexpected Earnings (SUE) -- the earnings surprise
scaled by its own historical volatility, so a "big" surprise is judged
relative to that specific stock's normal noise, not an absolute number.
This is a slow, multi-week equity anomaly -- a completely different time
horizon and mechanism from every other agent in this portfolio (which are
either instant arbitrage or multi-day mean-reversion), adding genuine
diversification in HOLDING PERIOD as well as asset class.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.micro.base import MicroAgent

logger = logging.getLogger("global_hunter.micro.earnings_drift")


class PostEarningsAnnouncementDrift(MicroAgent):
    name = "micro_post_earnings_drift"
    market_types = (MarketType.EQUITY,)
    poll_interval_s = 21600.0  # earnings surprises are a daily-at-most event; 6h poll is plenty
    asset_class = "equities (post-earnings-announcement drift)"
    target_daily_sek = 130.0
    edge_rationale = (
        "One of the most-replicated findings in empirical finance: stocks with "
        "a large earnings surprise (high |SUE|) continue drifting in the "
        "surprise's direction for 60-90 days as the market slowly underreacts."
    )

    def __init__(self, watchlist: tuple[str, ...] = ("AAPL", "MSFT", "GOOGL"), min_abs_sue: float = 2.0, horizon_days: float = 60.0) -> None:
        self.watchlist = watchlist
        self.min_abs_sue = min_abs_sue
        self.horizon_days = horizon_days
        self._warned_no_source = False

    async def _fetch_earnings_surprise(self, symbol: str) -> tuple[float, float] | None:
        """TODO: wire a real earnings-surprise data source. Concrete options:
        - Polygon.io fundamentals/earnings endpoint (POLYGON_API_KEY already
          supported elsewhere in this repo -- see backtest/providers/polygon.py).
        - A dedicated estimates vendor (Zacks, IEX Cloud, Refinitiv).
        Must return (actual_eps, consensus_eps) for the most recent report,
        or None if no data source is wired / no recent report exists.
        """
        return None

    @staticmethod
    def _standardized_unexpected_earnings(actual_eps: float, consensus_eps: float, eps_std: float) -> float:
        if eps_std == 0:
            return 0.0
        return (actual_eps - consensus_eps) / eps_std

    async def scan(self) -> list[Opportunity]:
        opportunities: list[Opportunity] = []
        any_source_missing = False
        for symbol in self.watchlist:
            surprise = await self._fetch_earnings_surprise(symbol)
            if surprise is None:
                any_source_missing = True
                continue
            actual_eps, consensus_eps = surprise
            # A real implementation should compute eps_std from several
            # quarters of the SAME stock's surprise history, not a constant --
            # left as a TODO alongside the data source itself.
            eps_std = max(abs(consensus_eps) * 0.15, 0.01)
            sue = self._standardized_unexpected_earnings(actual_eps, consensus_eps, eps_std)
            if abs(sue) < self.min_abs_sue:
                continue

            direction = "long" if sue > 0 else "short"
            opportunities.append(
                Opportunity(
                    id=f"{self.name}:{symbol}:{int(time.time() * 1000)}",
                    source=self.name,
                    instrument=symbol,
                    market_type=MarketType.EQUITY,
                    edge_pct=min(6.0, abs(sue) * 0.8),
                    confidence=0.55,
                    action=ActionType.BUY_AND_HOLD,
                    horizon_days=self.horizon_days,
                    detected_at=datetime.now(timezone.utc),
                    raw={"direction": direction, "sue": sue, "actual_eps": actual_eps, "consensus_eps": consensus_eps},
                )
            )

        if any_source_missing and not self._warned_no_source:
            logger.warning(
                "%s: no earnings-surprise data source wired yet for one or more symbols "
                "(see _fetch_earnings_surprise docstring) -- those idle until configured.", self.name,
            )
            self._warned_no_source = True
        return opportunities
