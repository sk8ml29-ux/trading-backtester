"""Base classes for every plug-in that feeds the UniversalAnomalyScanner.

Rule (see .cursorrules #2/#3): a new market, exchange, or data source is
ALWAYS a new small adapter/module class -- never an `if source == "x"`
branch inside shared logic. Everything below is designed so the scanner
engine (scanner/engine.py) never needs to change when you add one.
"""

from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from global_hunter.contracts import MarketType, Opportunity

logger = logging.getLogger("global_hunter.scanner")


@dataclass(frozen=True)
class VenueQuote:
    """A single comparable value observed at one venue.

    `price` is deliberately generic: for a spot venue it is a price, for a
    funding-rate venue it is a funding rate. Anything that can be diffed
    across two venues to find an edge fits this shape.
    """

    venue: str
    instrument: str
    price: float
    ts: datetime
    extra: dict[str, Any] = field(default_factory=dict)


class VenueAdapter(abc.ABC):
    """One venue (an exchange, a data API, a marketplace...). Stateless per call."""

    name: str = "unnamed_venue"

    @abc.abstractmethod
    async def quote(self, instrument: str) -> VenueQuote:
        """Fetch the latest comparable value for `instrument` from this venue."""

    async def healthcheck(self, probe_instrument: str) -> bool:
        try:
            await self.quote(probe_instrument)
            return True
        except Exception:  # noqa: BLE001 - healthcheck must never raise
            return False

    async def aclose(self) -> None:
        """Override to release sockets/sessions. No-op by default."""


class UniversalValueModule(abc.ABC):
    """Base class for every detection module plugged into UniversalAnomalyScanner.

    Concrete subclasses (GlobalMarketNeutralArbitrage, PredictiveValueAccumulation,
    OpportunisticMarketScraper, ...) implement `scan()` and know NOTHING about
    legality, tax, position sizing, or execution -- pure detection only.
    """

    name: str = "unnamed_module"
    market_types: tuple[MarketType, ...] = ()
    poll_interval_s: float = 60.0
    max_backoff_s: float = 600.0

    @abc.abstractmethod
    async def scan(self) -> list[Opportunity]:
        """Return every Opportunity currently detectable. Must not raise for
        a single failed sub-check; isolate those internally and skip them."""

    async def healthcheck(self) -> bool:
        return True

    async def aclose(self) -> None:
        """Override to release sockets/sessions. No-op by default."""

    async def run_forever(self, queue: "asyncio.Queue[Opportunity]") -> None:
        """Supervisor loop: never let a single failure kill the whole scanner."""
        backoff = self.poll_interval_s
        try:
            while True:
                try:
                    for opportunity in await self.scan():
                        await queue.put(opportunity)
                    backoff = self.poll_interval_s
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("%s: scan() failed, backing off %.0fs", self.name, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.max_backoff_s)
                    continue
                await asyncio.sleep(self.poll_interval_s)
        finally:
            await self.aclose()
