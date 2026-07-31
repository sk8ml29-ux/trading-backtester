"""Micro-Agent #3: DualListingArbitrage. LIVE data (Yahoo daily bars).

WHY this has a statistical edge
--------------------------------
Some companies trade simultaneously on two exchanges in two currencies --
e.g. Ericsson's Class B share on Nasdaq Stockholm (SEK) and its 1:1 ADR on
Nasdaq New York (USD). Both instruments are claims on the exact same
underlying equity, so after converting one into the other's currency they
should track almost perfectly; persistent gaps arise from time-zone
non-overlap (Stockholm closes ~09:30 ET, hours before the NYSE session
ends), FX conversion frictions, and different local investor bases driving
short-term supply/demand imbalances on each exchange. This is a completely
different mechanism from crypto peg/triangular arbitrage: it's an EQUITY
cross-listing convergence trade, with FX risk as the main extra
consideration (hedged here by directly using the live USD/SEK rate in the
comparison, not by trading FX separately).

Caveat (disclosed honestly, not hidden): this agent compares Yahoo's LATEST
DAILY bars for both legs, which are not perfectly time-synchronized intraday
quotes -- real execution of this edge needs live simultaneous quotes on both
venues. Treat this as a detector of PERSISTENT structural gaps, not a
sub-second arbitrage signal.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.micro.base import MicroAgent


@dataclass(frozen=True)
class DualListingPair:
    instrument: str
    primary_symbol: str      # local-currency listing, e.g. "ERIC-B.ST" (SEK)
    adr_symbol: str          # USD-denominated listing, e.g. "ERIC" (NYSE ADR)
    fx_symbol: str            # e.g. "USDSEK=X"
    adr_ratio: float = 1.0    # how many primary shares one ADR represents


DEFAULT_PAIRS: tuple[DualListingPair, ...] = (
    DualListingPair("ERICSSON", "ERIC-B.ST", "ERIC", "USDSEK=X", adr_ratio=1.0),
)


class DualListingArbitrage(MicroAgent):
    name = "micro_dual_listing_arbitrage"
    market_types = (MarketType.EQUITY,)
    poll_interval_s = 300.0  # daily-bar data -- no point polling faster
    asset_class = "equity (cross-listed shares / ADRs)"
    target_daily_sek = 110.0
    edge_rationale = (
        "Ericsson's Stockholm B-share and NYSE ADR are claims on the same equity; "
        "FX-adjusted gaps between them reflect session non-overlap and local "
        "supply/demand, not a fundamental valuation disagreement."
    )

    def __init__(self, pairs: tuple[DualListingPair, ...] = DEFAULT_PAIRS, min_net_edge_pct: float = 1.0, cost_buffer_pct: float = 0.5) -> None:
        self.pairs = pairs
        self.min_net_edge_pct = min_net_edge_pct
        self.cost_buffer_pct = cost_buffer_pct  # FX conversion + two-market commission buffer

    async def scan(self) -> list[Opportunity]:
        results = await asyncio.gather(*(self._check(p) for p in self.pairs), return_exceptions=True)
        return [r for r in results if isinstance(r, Opportunity)]

    async def _last_close(self, symbol: str) -> float:
        from backtest.data_loader import fetch_ohlcv  # local import: keep global_hunter decoupled from backtest/ at import time

        df = await asyncio.to_thread(fetch_ohlcv, symbol, "1d", "2023-01-01", None, None, True, False, "yahoo")
        return float(df["close"].iloc[-1])

    async def _check(self, pair: DualListingPair) -> Opportunity | None:
        primary_price, adr_price_usd, usdsek = await asyncio.gather(
            self._last_close(pair.primary_symbol), self._last_close(pair.adr_symbol), self._last_close(pair.fx_symbol)
        )
        if adr_price_usd <= 0 or usdsek <= 0:
            return None

        adr_equivalent_sek = adr_price_usd * usdsek * pair.adr_ratio
        deviation_pct = (primary_price - adr_equivalent_sek) / adr_equivalent_sek * 100.0
        net_edge_pct = abs(deviation_pct) - self.cost_buffer_pct
        if net_edge_pct <= self.min_net_edge_pct:
            return None

        cheaper_leg = pair.primary_symbol if deviation_pct < 0 else pair.adr_symbol
        return Opportunity(
            id=f"{self.name}:{pair.instrument}:{int(time.time() * 1000)}",
            source=self.name,
            instrument=pair.instrument,
            market_type=MarketType.EQUITY,
            edge_pct=net_edge_pct,
            confidence=0.75,  # lower than pure crypto arb: daily-bar timing mismatch + FX drift risk
            action=ActionType.EXECUTE_NOW,
            detected_at=datetime.now(timezone.utc),
            raw={
                "primary_symbol": pair.primary_symbol, "adr_symbol": pair.adr_symbol,
                "primary_price_sek": primary_price, "adr_equivalent_sek": adr_equivalent_sek,
                "usdsek": usdsek, "deviation_pct": deviation_pct, "cheaper_leg": cheaper_leg,
            },
        )
