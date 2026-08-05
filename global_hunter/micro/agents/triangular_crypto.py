"""Micro-Agent #2: TriangularCryptoArbitrage. LIVE (real OKX quotes).

WHY this has a statistical edge
--------------------------------
Within a SINGLE exchange, three prices (BTC/USDT, ETH/USDT, ETH/BTC) must
be mutually consistent: 1 ETH priced in USDT must equal 1 ETH priced in BTC
converted to USDT via the BTC/USDT rate. Momentary imbalances happen because
the three order books are updated by different market-making bots with
slightly different latencies/inventories. This is mechanically the same
"three prices must agree" logic as scanner/arbitrage.py's cross-venue
spreads, but the EXECUTION RISK PROFILE is fundamentally different and
better: everything happens inside ONE exchange's matching engine, so there
is no cross-venue transfer, no custody/withdrawal delay, and no risk of one
leg filling while the other doesn't because of network latency between two
different companies' APIs. That materially lower execution risk is why this
is coded as an independent micro-agent rather than folded into the
cross-venue module.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.micro.base import MicroAgent
from global_hunter.scanner.arbitrage import OKXSpotVenue


class TriangularCryptoArbitrage(MicroAgent):
    name = "micro_triangular_crypto_arbitrage"
    market_types = (MarketType.CRYPTO,)
    poll_interval_s = 10.0
    asset_class = "crypto (single-exchange triangular)"
    target_daily_sek = 100.0
    edge_rationale = (
        "BTC/USDT, ETH/USDT and ETH/BTC must be mutually consistent within one "
        "exchange; momentary desyncs between market-making bots are the edge, "
        "and single-exchange execution removes cross-venue transfer risk."
    )

    def __init__(self, base: str = "ETH", quote: str = "BTC", min_net_edge_pct: float = 0.10, cost_buffer_pct: float = 0.12) -> None:
        self.base = base
        self.quote = quote
        self.min_net_edge_pct = min_net_edge_pct
        self.cost_buffer_pct = cost_buffer_pct  # 3 taker legs' worth of fees, roughly
        self.venue = OKXSpotVenue(
            {
                f"{quote}_USDT": f"{quote}-USDT",
                f"{base}_USDT": f"{base}-USDT",
                f"{base}_{quote}": f"{base}-{quote}",
            }
        )

    async def scan(self) -> list[Opportunity]:
        quote_base_usdt, quote_quote_usdt, quote_cross = await asyncio.gather(
            self.venue.quote(f"{self.base}_USDT"),
            self.venue.quote(f"{self.quote}_USDT"),
            self.venue.quote(f"{self.base}_{self.quote}"),
        )
        if quote_quote_usdt.price <= 0 or quote_base_usdt.price <= 0:
            return []

        implied_base_usdt = quote_cross.price * quote_quote_usdt.price
        deviation_pct = (quote_base_usdt.price - implied_base_usdt) / implied_base_usdt * 100.0
        net_edge_pct = abs(deviation_pct) - self.cost_buffer_pct
        if net_edge_pct <= self.min_net_edge_pct:
            return []

        route = f"direct_{self.base}_USDT" if deviation_pct > 0 else f"via_{self.quote}"
        return [
            Opportunity(
                id=f"{self.name}:{self.base}{self.quote}:{int(time.time() * 1000)}",
                source=self.name,
                instrument=f"{self.base}-{self.quote}-USDT-triangle",
                market_type=MarketType.CRYPTO,
                edge_pct=net_edge_pct,
                confidence=0.95,
                action=ActionType.EXECUTE_NOW,
                detected_at=datetime.now(timezone.utc),
                raw={
                    "cheaper_route": route, "direct_price": quote_base_usdt.price,
                    "implied_price": implied_base_usdt, "deviation_pct": deviation_pct,
                },
            )
        ]

    async def aclose(self) -> None:
        await self.venue.aclose()
