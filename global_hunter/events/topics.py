"""Watchlist configuration for AlphaEventScanner.

Each EventTopic maps a news-coverage query to the tradable instruments that
should react if the underlying event is real and large. The DIRECTION
("long"/"short") is domain knowledge you set explicitly -- it is
deliberately NOT inferred from news tone, because tone-sign and
price-direction correlate differently per topic (e.g. "extreme cold snap"
is negative-toned coverage that is BULLISH for gas/power; "ceasefire
reached" is positive-toned coverage that is typically BEARISH for gold's
flight-to-safety premium).
"""

from __future__ import annotations

from dataclasses import dataclass

from global_hunter.contracts import MarketType


@dataclass(frozen=True)
class AffectedInstrument:
    instrument: str
    symbol: str                 # fetch_ohlcv-resolvable ticker, for reference/logging
    market_type: MarketType
    direction: str = "long"      # "long" or "short" -- read from Opportunity.raw["direction"] downstream
    sensitivity: float = 1.0     # scales the (uncalibrated) heuristic edge_pct estimate


@dataclass(frozen=True)
class EventTopic:
    name: str
    gdelt_query: str
    affected_instruments: tuple[AffectedInstrument, ...]
    polymarket_slug: str | None = None
    volume_zscore_threshold: float = 2.5
    tone_extreme_threshold: float = 4.0   # abs(avg GDELT tone) considered "dramatic" coverage
    horizon_days: float = 14.0
    base_confidence: float = 0.55


def default_topics() -> list[EventTopic]:
    """Entrepreneur's starting watchlist. Add a topic in ~1 minute: no code
    change needed beyond appending an EventTopic here (or load your own list
    and pass it into AlphaEventScanner(topics=...)).
    """
    return [
        EventTopic(
            name="nordic_energy_supply_shock",
            gdelt_query=(
                '"Nordic cold snap" OR "Sweden electricity price" OR '
                '"Nordic heatwave" OR "Sweden power grid"'
            ),
            affected_instruments=(
                AffectedInstrument("NATURAL_GAS_LONG", "NG=F", MarketType.ENERGY, direction="long", sensitivity=1.0),
            ),
            horizon_days=10.0,
        ),
        EventTopic(
            name="opec_supply_shift",
            gdelt_query='"OPEC production cut" OR "OPEC output increase" OR "OPEC+ agreement"',
            affected_instruments=(
                AffectedInstrument("CRUDE_OIL", "CL=F", MarketType.COMMODITY, direction="long", sensitivity=1.0),
            ),
            horizon_days=14.0,
        ),
        EventTopic(
            name="geopolitical_risk_flight_to_safety",
            gdelt_query='"war escalation" OR "geopolitical crisis" OR "sanctions imposed" OR "ceasefire collapse"',
            affected_instruments=(
                AffectedInstrument("GOLD", "GC=F", MarketType.COMMODITY, direction="long", sensitivity=0.8),
            ),
            volume_zscore_threshold=3.0,  # geopolitical noise floor is high -- demand a bigger spike
            horizon_days=21.0,
        ),
    ]
