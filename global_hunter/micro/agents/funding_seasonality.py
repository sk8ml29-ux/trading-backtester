"""Micro-Agent #5: FundingRateSeasonality. LIVE historical baseline (Binance
Vision bulk data, already cached in this repo); the "today's actual rate"
leg degrades gracefully if fapi.binance.com is geo-blocked (see AGENTS.md).

WHY this has a statistical edge
--------------------------------
Perpetual-futures funding rates are driven by retail-dominated directional
skew (more longs than shorts pay shorts, and vice versa). Retail order flow
has documented CALENDAR PATTERNS -- e.g. weekend sessions have thinner
order books and a more retail-heavy participant mix than weekday sessions
dominated by market-making desks, which tends to make weekend funding rates
noisier and more likely to overshoot. This repo already has a delta-neutral
funding-harvest strategy (research/funding_harvest.py); this micro-agent
does NOT reinvent that mechanism -- it adds a SEASONAL TIMING layer on top:
instead of harvesting funding on a fixed schedule, it only flags an entry
when today's rate is a statistical outlier relative to ITS OWN day-of-week
history, which is a genuinely different decision rule (and therefore a
different source of bugs/false signals) from the other funding-based module
in this system (GlobalMarketNeutralArbitrage's cross-venue funding spread),
even though both ultimately touch the same asset class.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pandas as pd

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.micro.base import MicroAgent
from global_hunter.scanner.arbitrage import BinancePerpFundingVenue


class FundingRateSeasonality(MicroAgent):
    name = "micro_funding_rate_seasonality"
    market_types = (MarketType.CRYPTO,)
    poll_interval_s = 3600.0
    asset_class = "crypto (perpetual-futures funding, calendar-timed)"
    target_daily_sek = 90.0
    edge_rationale = (
        "Retail-driven perpetual funding has documented day-of-week seasonality "
        "(thinner weekend books skew more); entering a delta-neutral funding "
        "harvest only when today's rate is an outlier vs its own weekday "
        "baseline improves entry timing over a fixed schedule."
    )

    def __init__(self, symbol: str = "BTCUSDT", instrument: str = "BTC", lookback_days: int = 180, zscore_threshold: float = 1.5) -> None:
        self.symbol = symbol
        self.instrument = instrument
        self.lookback_days = lookback_days
        self.zscore_threshold = zscore_threshold
        self.live_funding_venue = BinancePerpFundingVenue({instrument: symbol})

    async def scan(self) -> list[Opportunity]:
        from research.binance_vision import fetch_funding  # local import: keep global_hunter decoupled from research/ at import time

        end = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
        start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")
        history = await asyncio.to_thread(fetch_funding, self.symbol, start, end, False)
        if history is None or len(history) < 20:
            return []

        history = history.copy()
        history["dow"] = history.index.dayofweek
        today_dow = pd.Timestamp.now(tz="UTC").dayofweek
        same_day = history[history["dow"] == today_dow]["funding_rate"]
        if len(same_day) < 4:
            return []
        baseline_mean, baseline_std = float(same_day.mean()), float(same_day.std())

        try:
            live_quote = await self.live_funding_venue.quote(self.instrument)
            current_rate = live_quote.price
        except Exception:
            # fapi.binance.com may be geo-blocked on this server (see AGENTS.md) --
            # fall back to the most recent bulk-archive observation instead of
            # failing the whole agent. Slightly stale, but keeps the seasonality
            # signal alive rather than throttling this agent to zero on a
            # purely infrastructural (not data-quality) failure.
            current_rate = float(history["funding_rate"].iloc[-1])

        if baseline_std == 0:
            return []
        zscore = (current_rate - baseline_mean) / baseline_std
        if abs(zscore) < self.zscore_threshold:
            return []

        direction = "short_perp_long_spot" if zscore > 0 else "long_perp_short_spot"
        edge_pct = min(2.0, abs(current_rate - baseline_mean) * 100.0 * 3)  # 3x 8h periods/day, bounded
        return [
            Opportunity(
                id=f"{self.name}:{self.instrument}:{int(time.time() * 1000)}",
                source=self.name,
                instrument=f"{self.instrument}-funding-seasonal",
                market_type=MarketType.CRYPTO,
                edge_pct=edge_pct,
                confidence=min(0.80, 0.5 + 0.1 * abs(zscore)),
                action=ActionType.BUY_AND_HOLD,
                horizon_days=1.0,
                detected_at=datetime.now(timezone.utc),
                raw={
                    "direction": direction, "current_rate": current_rate,
                    "day_of_week": int(today_dow), "baseline_mean": baseline_mean,
                    "baseline_std": baseline_std, "zscore": float(zscore),
                },
            )
        ]

    async def aclose(self) -> None:
        await self.live_funding_venue.aclose()
