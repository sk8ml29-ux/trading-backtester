"""Micro-Agent #1: StablecoinPegArbitrage. LIVE (real Binance/OKX quotes).

WHY this has a statistical edge
--------------------------------
A fiat-backed stablecoin (USDC, USDT, ...) has a HARD real-world anchor: the
issuer redeems 1 token for $1 (minus, for some issuers, a small fee/minimum).
That redemption mechanism means any market price that drifts away from
$1.00 is fighting a mechanical arbitrage force that professional market
makers exploit constantly -- so residual price gaps that persist for even a
few seconds are almost certainly transient liquidity imbalances, not
"informed" price discovery. That is a fundamentally different (and safer)
statistical claim than "these two venues disagree about what BTC is worth"
(scanner/arbitrage.py's GlobalMarketNeutralArbitrage) -- here we're betting
on a redemption-anchored mean-reversion, not on two market-clearing prices
converging. Near-zero volatility of the underlying also means the position
carries almost no directional risk while the (tiny) spread is captured.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.micro.base import MicroAgent
from global_hunter.scanner.arbitrage import BinanceSpotVenue, OKXSpotVenue


class StablecoinPegArbitrage(MicroAgent):
    name = "micro_stablecoin_peg_arbitrage"
    market_types = (MarketType.CRYPTO,)
    poll_interval_s = 20.0
    asset_class = "crypto (fiat-backed stablecoins)"
    target_daily_sek = 120.0
    edge_rationale = (
        "Issuer redemption mechanism anchors USDC/USDT to $1 -- any cross-venue "
        "deviation is a mechanical mean-reversion bet, not a directional one."
    )

    def __init__(self, pairs: tuple[str, ...] = ("USDC",), min_net_edge_pct: float = 0.03, cost_buffer_pct: float = 0.05) -> None:
        self.pairs = pairs
        self.min_net_edge_pct = min_net_edge_pct
        self.cost_buffer_pct = cost_buffer_pct
        self.binance = BinanceSpotVenue({coin: f"{coin}USDT" for coin in pairs})
        self.okx = OKXSpotVenue({coin: f"{coin}-USDT" for coin in pairs})

    async def scan(self) -> list[Opportunity]:
        results = await asyncio.gather(*(self._check(coin) for coin in self.pairs), return_exceptions=True)
        return [r for r in results if isinstance(r, Opportunity)]

    async def _check(self, coin: str) -> Opportunity | None:
        quote_a, quote_b = await asyncio.gather(self.binance.quote(coin), self.okx.quote(coin))
        if quote_a.price <= 0:
            return None
        raw_spread_pct = (quote_b.price - quote_a.price) / quote_a.price * 100.0
        net_edge_pct = abs(raw_spread_pct) - self.cost_buffer_pct
        if net_edge_pct <= self.min_net_edge_pct:
            return None
        buy_venue, sell_venue = (
            (self.binance.name, self.okx.name) if raw_spread_pct > 0 else (self.okx.name, self.binance.name)
        )
        return Opportunity(
            id=f"{self.name}:{coin}:{int(time.time() * 1000)}",
            source=self.name,
            instrument=f"{coin}USD_PEG",
            market_type=MarketType.CRYPTO,
            edge_pct=net_edge_pct,
            confidence=0.99,  # redemption-anchored -- among the highest-confidence edges in the whole system
            action=ActionType.EXECUTE_NOW,
            detected_at=datetime.now(timezone.utc),
            raw={"buy_venue": buy_venue, "sell_venue": sell_venue, "price_a": quote_a.price, "price_b": quote_b.price},
        )

    async def aclose(self) -> None:
        await asyncio.gather(self.binance.aclose(), self.okx.aclose(), return_exceptions=True)
