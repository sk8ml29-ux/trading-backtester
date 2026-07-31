"""AlphaEventScanner: news-coverage-volume spike detector -> BUY_AND_HOLD
Opportunity for instruments expected to react to a real macro/weather/
political event.

Honesty note (same spirit as value_accumulation.py): a spike in GDELT's
global news-coverage volume for a topic is a real, measurable, falsifiable
signal ("the world is suddenly talking about X much more than its own
baseline"). Whether that maps to a specific tradable edge_pct is NOT
independently proven here -- edge_pct is a bounded heuristic
(zscore * sensitivity, capped) that MUST be backtested against realized
post-event returns (research/paper_forward.py pattern) before being trusted
with real capital. What IS enforced honestly: the module never fabricates
a signal when coverage volume is normal, and it caps the heuristic edge to
avoid absurd numbers on a single data point.

Two-stage design to stay well under GDELT's rate courtesy limit: cheap
volume-only calls run for every topic every cycle; the more expensive tone
(sentiment) confirmation call only runs for a topic that already tripped
its volume threshold.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections import defaultdict, deque

from datetime import datetime, timezone

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.events.gdelt import GdeltClient
from global_hunter.events.polymarket_watch import PolymarketWatcher
from global_hunter.events.topics import EventTopic, default_topics
from global_hunter.scanner.base import UniversalValueModule

logger = logging.getLogger("global_hunter.events")

MAX_HEURISTIC_EDGE_PCT = 15.0


class AlphaEventScanner(UniversalValueModule):
    name = "alpha_event_scanner"
    # Actual market types are config-driven per topic's affected_instruments;
    # this is just the illustrative default watchlist's coverage for introspection.
    market_types = (MarketType.ENERGY, MarketType.COMMODITY, MarketType.EQUITY)
    poll_interval_s = 900.0  # news-driven signals move on the order of hours, not seconds

    def __init__(self, topics: list[EventTopic] | None = None) -> None:
        self.topics = topics if topics is not None else default_topics()
        self.gdelt = GdeltClient()
        self.polymarket = PolymarketWatcher()
        self._volume_baseline: dict[str, "deque[float]"] = defaultdict(lambda: deque(maxlen=200))

    async def scan(self) -> list[Opportunity]:
        # GDELT is rate-limited per-IP -- scan topics sequentially so the
        # client's internal throttle produces one predictable queue instead
        # of N tasks racing for the same lock.
        opportunities: list[Opportunity] = []
        for topic in self.topics:
            try:
                opportunities.extend(await self._evaluate_topic(topic))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one topic failing must not block the rest
                logger.debug("%s: topic %s failed", self.name, topic.name, exc_info=True)
        return opportunities

    async def _evaluate_topic(self, topic: EventTopic) -> list[Opportunity]:
        timeline = await self.gdelt.volume_timeline(topic.gdelt_query, timespan="3d")
        if len(timeline) < 10:
            return []

        values = [v for _, v in timeline]
        baseline = self._volume_baseline[topic.name]
        if not baseline:
            baseline.extend(values[:-1])  # seed with everything except the freshest point
        current = values[-1]

        history_for_stats = list(baseline)[-100:]
        if len(history_for_stats) < 8:
            baseline.append(current)
            return []
        mean = statistics.fmean(history_for_stats)
        std = statistics.pstdev(history_for_stats)
        baseline.append(current)

        if std == 0:
            return []
        zscore = (current - mean) / std
        if zscore < topic.volume_zscore_threshold:
            return []  # only upward coverage spikes count as "a big event just happened"

        confidence = topic.base_confidence
        avg_tone = 0.0
        try:
            tone_timeline = await self.gdelt.tone_timeline(topic.gdelt_query, timespan="1d")
            if tone_timeline:
                avg_tone = statistics.fmean(v for _, v in tone_timeline)
        except Exception:  # noqa: BLE001 - tone is a confidence booster only, never load-bearing
            logger.debug("%s: tone confirmation failed for %s", self.name, topic.name, exc_info=True)

        if zscore >= topic.volume_zscore_threshold * 1.5:
            confidence += 0.10
        if abs(avg_tone) >= topic.tone_extreme_threshold:
            confidence += 0.10

        polymarket_move_pct = 0.0
        if topic.polymarket_slug:
            try:
                polymarket_move_pct = await self.polymarket.recent_price_move_pct(topic.polymarket_slug)
                if polymarket_move_pct >= 5.0:
                    confidence += 0.10
            except Exception:  # noqa: BLE001 - same: confirming signal only
                logger.debug("%s: polymarket confirmation failed for %s", self.name, topic.name, exc_info=True)

        confidence = min(0.85, confidence)
        now = datetime.now(timezone.utc)
        opportunities = []
        for target in topic.affected_instruments:
            edge_pct = min(MAX_HEURISTIC_EDGE_PCT, zscore * target.sensitivity * 1.5)
            opportunities.append(
                Opportunity(
                    id=f"event:{topic.name}:{target.instrument}:{int(time.time() * 1000)}",
                    source=self.name,
                    instrument=target.instrument,
                    market_type=target.market_type,
                    edge_pct=edge_pct,
                    confidence=confidence,
                    action=ActionType.BUY_AND_HOLD,
                    horizon_days=topic.horizon_days,
                    detected_at=now,
                    raw={
                        "topic": topic.name,
                        "direction": target.direction,
                        "news_volume_zscore": zscore,
                        "avg_tone": avg_tone,
                        "polymarket_move_pct": polymarket_move_pct,
                        "heuristic_edge_uncalibrated": True,
                    },
                )
            )
        return opportunities

    async def aclose(self) -> None:
        await asyncio.gather(self.gdelt.aclose(), self.polymarket.aclose(), return_exceptions=True)
