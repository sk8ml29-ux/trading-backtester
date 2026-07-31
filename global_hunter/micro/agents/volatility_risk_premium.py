"""Micro-Agent #7: VolatilityRiskPremiumHarvester. PARTIAL SKELETON: the
realized-volatility leg is LIVE (real Yahoo daily bars); the implied-
volatility leg needs an options-chain data source (e.g. an Alpaca options
account -- live/alpaca_broker.py already exists in this repo as a starting
point -- or a paid IV feed) and is a clearly-marked TODO.

WHY this has a statistical edge
--------------------------------
This is one of the most heavily documented risk premia in all of finance:
options' IMPLIED volatility tends to systematically exceed the volatility
subsequently REALIZED by the underlying (the "variance risk premium" /
VRP), because option buyers (portfolio hedgers, tail-risk buyers) pay for
insurance and are willing to overpay for it, especially on broad equity
indices. Systematically selling short-dated premium (covered calls,
cash-secured puts, or defined-risk short strangles/iron condors) harvests
that persistent overpayment on average, across many small trades -- exactly
the "several small skims instead of one big bet" shape the entrepreneur
wants, PROVIDED position sizing stays small and defined-risk (this is the
one micro-agent in the portfolio with genuine tail risk if oversized, which
is precisely why it should stay a small, capital-throttled slice of the
book rather than a large one -- CapitalAllocator's health/PnL throttle is
especially relevant here).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone

import numpy as np

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.micro.base import MicroAgent

logger = logging.getLogger("global_hunter.micro.volatility_risk_premium")


class VolatilityRiskPremiumHarvester(MicroAgent):
    name = "micro_volatility_risk_premium"
    market_types = (MarketType.EQUITY,)
    poll_interval_s = 3600.0
    asset_class = "equity index options (variance risk premium)"
    target_daily_sek = 150.0
    edge_rationale = (
        "Index option implied volatility persistently exceeds subsequently "
        "realized volatility (the documented variance risk premium); selling "
        "small, defined-risk short-dated premium harvests that overpayment."
    )

    def __init__(self, underlying_symbol: str = "SPY", realized_vol_window_days: int = 20, min_vrp_pct: float = 3.0) -> None:
        self.underlying_symbol = underlying_symbol
        self.realized_vol_window_days = realized_vol_window_days
        self.min_vrp_pct = min_vrp_pct
        self._warned_no_iv_source = False

    async def _realized_volatility_pct(self) -> float:
        from backtest.data_loader import fetch_ohlcv  # local import: keep global_hunter decoupled from backtest/ at import time

        df = await asyncio.to_thread(
            fetch_ohlcv, self.underlying_symbol, "1d", "2023-01-01", None, None, True, False, "yahoo"
        )
        closes = df["close"].tail(self.realized_vol_window_days + 1)
        log_returns = np.diff(np.log(closes.values))
        annualized_vol = float(np.std(log_returns) * math.sqrt(252)) * 100.0
        return annualized_vol

    async def _implied_volatility_pct(self) -> float | None:
        """TODO: wire a real options-chain IV source. Two concrete paths:
        1. Alpaca options data (see live/alpaca_broker.py for the existing
           account wrapper) -- pull the ATM straddle IV for the nearest
           monthly expiry.
        2. A dedicated options-data vendor (CBOE DataShop, Tradier, ORATS...).
        Returns None (not NotImplementedError) so `scan()` can log once and
        idle gracefully instead of tripping this agent's own circuit breaker
        on every single poll -- a missing OPTIONAL data source is not the
        same failure mode as a broken one.
        """
        return None

    async def scan(self) -> list[Opportunity]:
        implied_vol_pct = await self._implied_volatility_pct()
        if implied_vol_pct is None:
            if not self._warned_no_iv_source:
                logger.warning(
                    "%s: no implied-volatility data source wired yet (see _implied_volatility_pct docstring) "
                    "-- skeleton stays idle.", self.name,
                )
                self._warned_no_iv_source = True
            return []

        realized_vol_pct = await self._realized_volatility_pct()
        vrp_pct = implied_vol_pct - realized_vol_pct
        if vrp_pct < self.min_vrp_pct:
            return []

        return [
            Opportunity(
                id=f"{self.name}:{self.underlying_symbol}:{int(time.time() * 1000)}",
                source=self.name,
                instrument=f"{self.underlying_symbol}_short_premium",
                market_type=MarketType.EQUITY,
                edge_pct=min(5.0, vrp_pct / 4.0),  # conservative fraction of the VRP as capturable edge after theta decay/hedging costs
                confidence=0.6,
                action=ActionType.BUY_AND_HOLD,
                horizon_days=30.0,
                detected_at=datetime.now(timezone.utc),
                raw={
                    "implied_vol_pct": implied_vol_pct, "realized_vol_pct": realized_vol_pct,
                    "vrp_pct": vrp_pct, "strategy": "short_dated_defined_risk_premium_sale",
                },
            )
        ]
