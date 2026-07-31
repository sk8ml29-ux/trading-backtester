"""GlobalIngestionEngine: turns FeedConfig objects (see schema.py) into
Opportunity objects, fully config-driven. Zero market-specific Python code
in this file -- it only knows how to fetch JSON, walk a dot-path, and apply
one of three generic rule types.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from global_hunter.contracts import MarketType, Opportunity
from global_hunter.ingestion.config_loader import DEFAULT_FEEDS_DIR, load_feed_configs
from global_hunter.ingestion.schema import (
    CrossFeedSpreadRule,
    FeedConfig,
    ThresholdRule,
    ZScoreRule,
    extract_numeric,
    resolve_path,
)
from global_hunter.scanner.base import UniversalValueModule
from global_hunter.scanner.http_venue import HttpVenueMixin

logger = logging.getLogger("global_hunter.ingestion")


class GlobalIngestionEngine(HttpVenueMixin, UniversalValueModule):
    name = "global_ingestion_engine"
    market_types = tuple(MarketType)  # config-driven -- any market type is possible
    poll_interval_s = 60.0

    def __init__(self, feeds: list[FeedConfig] | None = None, feeds_dir: Path = DEFAULT_FEEDS_DIR) -> None:
        HttpVenueMixin.__init__(self)
        self.feeds = feeds if feeds is not None else load_feed_configs(feeds_dir)
        # Rolling history per (feed.name, endpoint.id), used only by zscore_window rules.
        self._history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))

    async def scan(self) -> list[Opportunity]:
        results = await asyncio.gather(*(self._scan_feed(f) for f in self.feeds), return_exceptions=True)
        opportunities: list[Opportunity] = []
        for feed, result in zip(self.feeds, results):
            if isinstance(result, BaseException):
                logger.debug("%s: feed %s failed: %s", self.name, feed.name, result)
                continue
            if result is not None:
                opportunities.append(result)
        return opportunities

    async def _scan_feed(self, feed: FeedConfig) -> Opportunity | None:
        values: dict[str, float] = {}
        instruments: dict[str, str] = {}
        for endpoint in feed.endpoints:
            if endpoint.method.upper() != "GET":
                raise NotImplementedError(
                    f"{feed.name}/{endpoint.id}: only GET is implemented today "
                    f"(got method={endpoint.method!r}); extend HttpVenueMixin if you need POST."
                )
            raw = await self.get_json(endpoint.url, params=endpoint.params)
            values[endpoint.id] = extract_numeric(
                resolve_path(raw, endpoint.value_path), endpoint.value_regex
            )
            if endpoint.instrument_static is not None:
                instruments[endpoint.id] = endpoint.instrument_static
            elif endpoint.instrument_path is not None:
                instruments[endpoint.id] = str(resolve_path(raw, endpoint.instrument_path))

        return self._evaluate(feed, values, instruments)

    def _evaluate(
        self, feed: FeedConfig, values: dict[str, float], instruments: dict[str, str]
    ) -> Opportunity | None:
        rule = feed.rule
        if isinstance(rule, ThresholdRule):
            return self._evaluate_threshold(feed, rule, values, instruments)
        if isinstance(rule, CrossFeedSpreadRule):
            return self._evaluate_cross_feed_spread(feed, rule, values, instruments)
        if isinstance(rule, ZScoreRule):
            return self._evaluate_zscore(feed, rule, values, instruments)
        raise TypeError(f"Unhandled rule type: {type(rule)}")

    def _make_opportunity(
        self, feed: FeedConfig, edge_pct: float, instrument: str, extra_raw: dict
    ) -> Opportunity:
        return Opportunity(
            id=f"ingest:{feed.name}:{instrument}:{int(time.time() * 1000)}",
            source=f"global_ingestion_engine:{feed.name}",
            instrument=instrument,
            market_type=feed.market_type,
            edge_pct=edge_pct,
            confidence=feed.confidence,
            action=feed.action,
            horizon_days=feed.horizon_days,
            detected_at=datetime.now(timezone.utc),
            raw={"feed": feed.name, "notes": feed.notes, **extra_raw},
        )

    def _evaluate_threshold(
        self, feed: FeedConfig, rule: ThresholdRule, values: dict[str, float], instruments: dict[str, str]
    ) -> Opportunity | None:
        endpoint_id = feed.endpoints[0].id
        value = values[endpoint_id]
        edge_pct = (rule.reference_value - value) / rule.reference_value * 100.0
        if rule.direction == "above":
            edge_pct = -edge_pct
        if edge_pct < rule.min_edge_pct:
            return None
        instrument = instruments.get(endpoint_id, feed.name)
        return self._make_opportunity(
            feed, edge_pct, instrument,
            {"value": value, "reference_value": rule.reference_value, "direction": rule.direction},
        )

    def _evaluate_cross_feed_spread(
        self, feed: FeedConfig, rule: CrossFeedSpreadRule, values: dict[str, float], instruments: dict[str, str]
    ) -> Opportunity | None:
        value_a, value_b = values[rule.endpoint_a], values[rule.endpoint_b]
        if value_a == 0:
            return None
        raw_spread_pct = (value_b - value_a) / abs(value_a) * 100.0
        net_edge_pct = abs(raw_spread_pct) - rule.cost_buffer_pct
        if net_edge_pct < rule.min_edge_pct:
            return None
        instrument = instruments.get(rule.endpoint_a, feed.name)
        buy_endpoint, sell_endpoint = (
            (rule.endpoint_a, rule.endpoint_b) if raw_spread_pct > 0 else (rule.endpoint_b, rule.endpoint_a)
        )
        return self._make_opportunity(
            feed, net_edge_pct, instrument,
            {
                "buy_endpoint": buy_endpoint, "sell_endpoint": sell_endpoint,
                "value_a": value_a, "value_b": value_b, "raw_spread_pct": raw_spread_pct,
            },
        )

    def _evaluate_zscore(
        self, feed: FeedConfig, rule: ZScoreRule, values: dict[str, float], instruments: dict[str, str]
    ) -> Opportunity | None:
        endpoint_id = feed.endpoints[0].id
        value = values[endpoint_id]
        history_key = f"{feed.name}:{endpoint_id}"
        history = self._history[history_key]
        history.append(value)
        if len(history) < max(10, rule.window_size // 3):
            return None  # not enough history yet to trust a z-score

        window = list(history)[-rule.window_size:]
        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / max(1, len(window) - 1)
        std = variance ** 0.5
        if std == 0:
            return None
        zscore = (value - mean) / std
        if abs(zscore) < rule.zscore_threshold:
            return None

        edge_pct = abs(zscore) * std / mean * 100.0 if mean else 0.0
        instrument = instruments.get(endpoint_id, feed.name)
        return self._make_opportunity(
            feed, edge_pct, instrument,
            {"value": value, "zscore": zscore, "rolling_mean": mean, "rolling_std": std},
        )
