"""GlobalMarketNeutralArbitrage.

Compares a "comparable value" (spot price, perp funding rate, ...) for the
SAME underlying asset across two independent venues -- crypto/crypto,
crypto/TradFi, or (once you plug in more adapters) any other pair -- and
emits an Opportunity whenever the spread net of an estimated cost buffer
exceeds a configurable threshold.

This is the closest thing to "riskfri vinst": once both legs are filled the
position is delta-neutral and the edge is locked in mechanically, not a
directional market bet. Confidence is high (~0.95-0.99) but NOT 1.0 -- legs
can fail to fill simultaneously (execution slippage), which is why
DynamicExecutionEngine still enforces timeouts and idempotency.

Adding a new venue pair = write one VenueAdapter subclass + one
ArbitragePair entry. This class and UniversalAnomalyScanner never change.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.scanner.base import UniversalValueModule, VenueAdapter, VenueQuote
from global_hunter.scanner.http_venue import HttpVenueMixin

logger = logging.getLogger("global_hunter.scanner.arbitrage")


class BinanceSpotVenue(HttpVenueMixin, VenueAdapter):
    """Live spot price from Binance's public market-data mirror (no key
    required). Uses `data-api.binance.vision` rather than `api.binance.com`
    because the latter geo-blocks a number of cloud/VPS IP ranges with HTTP
    451; the `.vision` data mirror is Binance's official unrestricted
    market-data endpoint and serves the same spot ticker payload.
    """

    name = "binance_spot"
    BASE_URL = "https://data-api.binance.vision/api/v3/ticker/price"

    def __init__(self, symbol_map: dict[str, str]) -> None:
        HttpVenueMixin.__init__(self)
        self.symbol_map = symbol_map

    async def quote(self, instrument: str) -> VenueQuote:
        symbol = self.symbol_map[instrument]
        data = await self.get_json(self.BASE_URL, params={"symbol": symbol})
        return VenueQuote(
            venue=self.name, instrument=instrument, price=float(data["price"]),
            ts=datetime.now(timezone.utc),
        )


class OKXSpotVenue(HttpVenueMixin, VenueAdapter):
    """Live spot ticker from OKX's public REST API (no key required)."""

    name = "okx_spot"
    BASE_URL = "https://www.okx.com/api/v5/market/ticker"

    def __init__(self, inst_map: dict[str, str]) -> None:
        HttpVenueMixin.__init__(self)
        self.inst_map = inst_map

    async def quote(self, instrument: str) -> VenueQuote:
        inst_id = self.inst_map[instrument]
        data = await self.get_json(self.BASE_URL, params={"instId": inst_id})
        row = data["data"][0]
        return VenueQuote(
            venue=self.name, instrument=instrument, price=float(row["last"]),
            ts=datetime.now(timezone.utc),
            extra={"bid": float(row["bidPx"]), "ask": float(row["askPx"])},
        )


class BinancePerpFundingVenue(HttpVenueMixin, VenueAdapter):
    """Current predicted funding rate for a Binance USDT-M perpetual.

    `price` here is the funding rate itself (e.g. 0.0001 = 0.01% / 8h) so it
    can be diff'ed against another funding venue with the exact same math
    used for spot-price spreads -- this is the "cross-venue funding" edge
    from the entrepreneur's data archive.

    NOTE: `fapi.binance.com` geo-blocks some cloud/VPS IP ranges with HTTP
    451 (unlike the spot `.vision` mirror, there is no unrestricted mirror
    for futures data at the time of writing). This fails soft -- the pair
    is just skipped for that cycle -- but verify reachability from your own
    server; swap in another funding-rate source (Bybit, Deribit, dYdX, ...)
    as a drop-in VenueAdapter if your server's IP is blocked.
    """

    name = "binance_perp_funding"
    BASE_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

    def __init__(self, symbol_map: dict[str, str]) -> None:
        HttpVenueMixin.__init__(self)
        self.symbol_map = symbol_map

    async def quote(self, instrument: str) -> VenueQuote:
        symbol = self.symbol_map[instrument]
        data = await self.get_json(self.BASE_URL, params={"symbol": symbol})
        return VenueQuote(
            venue=self.name, instrument=instrument, price=float(data["lastFundingRate"]),
            ts=datetime.now(timezone.utc), extra={"mark_price": float(data["markPrice"])},
        )


class OKXPerpFundingVenue(HttpVenueMixin, VenueAdapter):
    """Current funding rate for an OKX perpetual swap."""

    name = "okx_perp_funding"
    BASE_URL = "https://www.okx.com/api/v5/public/funding-rate"

    def __init__(self, inst_map: dict[str, str]) -> None:
        HttpVenueMixin.__init__(self)
        self.inst_map = inst_map

    async def quote(self, instrument: str) -> VenueQuote:
        inst_id = self.inst_map[instrument]
        data = await self.get_json(self.BASE_URL, params={"instId": inst_id})
        row = data["data"][0]
        return VenueQuote(
            venue=self.name, instrument=instrument, price=float(row["fundingRate"]),
            ts=datetime.now(timezone.utc),
        )


class YahooTradFiVenue(VenueAdapter):
    """TradFi comparison leg, reusing the repo's existing sync data loader.

    Blocking call -> wrapped in `asyncio.to_thread` so it never stalls the
    event loop (rule #1). Used e.g. to compare tokenized-gold (PAXG, on
    Binance) against real gold futures (GC=F) -- the "krypto/TradFi-spread"
    the entrepreneur asked for.
    """

    name = "yahoo_tradfi"

    def __init__(self, symbol_map: dict[str, str], refresh: bool = False) -> None:
        self.symbol_map = symbol_map
        self.refresh = refresh

    async def quote(self, instrument: str) -> VenueQuote:
        from backtest.data_loader import fetch_ohlcv  # local import: keep global_hunter decoupled from backtest/ at import time

        symbol = self.symbol_map[instrument]
        df = await asyncio.to_thread(
            fetch_ohlcv, symbol, "1d", "2020-01-01", None, None, True, self.refresh, "yahoo"
        )
        last = df.iloc[-1]
        return VenueQuote(
            venue=self.name, instrument=instrument, price=float(last["close"]),
            ts=datetime.now(timezone.utc),
        )


@dataclass(frozen=True)
class ArbitragePair:
    instrument: str
    market_type: MarketType
    venue_a: VenueAdapter
    venue_b: VenueAdapter
    min_net_edge_pct: float = 0.10  # skip below this net-of-cost edge (percent)
    cost_buffer_pct: float | None = None  # override module default cost estimate for this pair


def default_pairs() -> list[ArbitragePair]:
    """The entrepreneur's starting universe: crypto cross-venue spot + funding.
    Expand this list (or feed a custom one into the constructor) to reach any
    other market -- see `commodity_basis_pairs()` below for why the
    crypto/TradFi leg is NOT included here by default.
    """
    binance_spot = BinanceSpotVenue({"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"})
    okx_spot = OKXSpotVenue({"BTC": "BTC-USDT", "ETH": "ETH-USDT", "SOL": "SOL-USDT"})
    # Funding pairs use their own instrument labels ("BTC-funding") so their
    # Opportunity ids never collide with the spot pairs above -- the venue
    # symbol maps must therefore key off those same labels.
    binance_funding = BinancePerpFundingVenue({"BTC-funding": "BTCUSDT", "ETH-funding": "ETHUSDT"})
    okx_funding = OKXPerpFundingVenue({"BTC-funding": "BTC-USDT-SWAP", "ETH-funding": "ETH-USDT-SWAP"})

    return [
        ArbitragePair("BTC", MarketType.CRYPTO, binance_spot, okx_spot, min_net_edge_pct=0.06),
        ArbitragePair("ETH", MarketType.CRYPTO, binance_spot, okx_spot, min_net_edge_pct=0.06),
        ArbitragePair("SOL", MarketType.CRYPTO, binance_spot, okx_spot, min_net_edge_pct=0.08),
        ArbitragePair(
            "BTC-funding", MarketType.CRYPTO, binance_funding, okx_funding,
            min_net_edge_pct=0.015, cost_buffer_pct=0.01,
        ),
        ArbitragePair(
            "ETH-funding", MarketType.CRYPTO, binance_funding, okx_funding,
            min_net_edge_pct=0.015, cost_buffer_pct=0.01,
        ),
    ]


def commodity_basis_pairs() -> list[ArbitragePair]:
    """Crypto/TradFi leg: tokenized gold (PAXG, spot-tracking) vs. `GC=F`.

    NOT included in `default_pairs()` by design: `GC=F` is a FUTURES
    contract, not spot gold (Yahoo has no free spot XAUUSD feed). A
    PAXG-vs-futures gap is dominated by cost-of-carry/contango, NOT a
    delta-neutral arbitrage that "closes now" -- treating it as
    ActionType.EXECUTE_NOW would misrepresent basis risk as risk-free edge,
    which violates the "rapportera arligt" mandate. Wire in a real spot XAU
    feed (LBMA fix, a broker quote, etc.) before enabling this pair, and
    consider re-classifying it as a PredictiveValueAccumulation-style
    basis-convergence trade (mean-reversion of the futures basis toward
    expiry) rather than instant arbitrage.
    """
    paxg_binance = BinanceSpotVenue({"GOLD": "PAXGUSDT"})
    xau_yahoo_futures_proxy = YahooTradFiVenue({"GOLD": "GC=F"})
    return [
        ArbitragePair(
            "GOLD", MarketType.COMMODITY, paxg_binance, xau_yahoo_futures_proxy,
            min_net_edge_pct=5.0,  # deliberately high: this is a basis proxy, not a clean spread
        )
    ]


class GlobalMarketNeutralArbitrage(UniversalValueModule):
    """Scans every registered ArbitragePair concurrently, every poll cycle."""

    name = "global_market_neutral_arbitrage"
    market_types = (MarketType.CRYPTO, MarketType.COMMODITY, MarketType.FX)
    poll_interval_s = 15.0

    def __init__(self, pairs: list[ArbitragePair] | None = None, default_cost_buffer_pct: float = 0.15) -> None:
        self.pairs = pairs if pairs is not None else default_pairs()
        self.default_cost_buffer_pct = default_cost_buffer_pct

    async def scan(self) -> list[Opportunity]:
        results = await asyncio.gather(
            *(self._check_pair(pair) for pair in self.pairs), return_exceptions=True
        )
        opportunities: list[Opportunity] = []
        for pair, result in zip(self.pairs, results):
            if isinstance(result, BaseException):
                logger.debug("%s: pair %s failed: %s", self.name, pair.instrument, result)
                continue
            if result is not None:
                opportunities.append(result)
        return opportunities

    async def _check_pair(self, pair: ArbitragePair) -> Opportunity | None:
        quote_a, quote_b = await asyncio.gather(
            pair.venue_a.quote(pair.instrument), pair.venue_b.quote(pair.instrument)
        )
        if quote_a.price == 0 or quote_b.price == 0:
            return None

        raw_spread_pct = (quote_b.price - quote_a.price) / abs(quote_a.price) * 100.0
        cost_buffer = pair.cost_buffer_pct if pair.cost_buffer_pct is not None else self.default_cost_buffer_pct
        net_edge_pct = abs(raw_spread_pct) - cost_buffer
        if net_edge_pct <= pair.min_net_edge_pct:
            return None

        buy_venue, sell_venue = (
            (pair.venue_a, pair.venue_b) if raw_spread_pct > 0 else (pair.venue_b, pair.venue_a)
        )
        return Opportunity(
            id=f"arb:{pair.instrument}:{buy_venue.name}->{sell_venue.name}:{int(time.time() * 1000)}",
            source=self.name,
            instrument=pair.instrument,
            market_type=pair.market_type,
            edge_pct=net_edge_pct,
            confidence=0.97,
            action=ActionType.EXECUTE_NOW,
            detected_at=datetime.now(timezone.utc),
            raw={
                "buy_venue": buy_venue.name,
                "sell_venue": sell_venue.name,
                "price_a": quote_a.price,
                "price_b": quote_b.price,
                "raw_spread_pct": raw_spread_pct,
                "cost_buffer_pct": cost_buffer,
            },
        )

    async def aclose(self) -> None:
        venues = {id(v): v for pair in self.pairs for v in (pair.venue_a, pair.venue_b)}
        await asyncio.gather(*(v.aclose() for v in venues.values()), return_exceptions=True)
