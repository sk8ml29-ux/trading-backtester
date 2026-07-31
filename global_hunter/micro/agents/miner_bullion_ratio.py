"""Micro-Agent #4: MinerBullionRatioReversion. LIVE data (Yahoo daily bars).

WHY this has a statistical edge
--------------------------------
Gold-miner equities (e.g. GDX, a basket of gold-mining stocks) are a
LEVERED play on the gold price: mining costs are roughly fixed in the short
run, so a given percentage move in gold translates into a larger percentage
move in miner profits (operating leverage). But miners ALSO carry
idiosyncratic equity risk that bullion doesn't have -- broad equity-market
sell-offs, financing costs, labor/political risk at specific mines, ETF
fund-flow effects. The GDX/Gold RATIO therefore tends to oscillate around a
slower-moving fundamental relationship; large dislocations driven by
equity-market-wide moves unrelated to gold (e.g. a broad risk-off day that
hits ALL equities including miners, while physical gold is bid as a safe
haven) tend to mean-revert over subsequent weeks as the "pure gold price"
signal reasserts itself relative to the miners' equity-beta noise. This is
a classic PAIRS/RATIO trade -- a fundamentally different mechanism from
spot-price arbitrage (there is no mechanical convergence force here, only
a statistical/historical tendency, hence a lower confidence score and a
BUY_AND_HOLD-style multi-week horizon instead of instant execution).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pandas as pd

from global_hunter.contracts import ActionType, MarketType, Opportunity
from global_hunter.micro.base import MicroAgent


class MinerBullionRatioReversion(MicroAgent):
    name = "micro_miner_bullion_ratio_reversion"
    market_types = (MarketType.EQUITY, MarketType.COMMODITY)
    poll_interval_s = 3600.0
    asset_class = "equity/commodity pairs trade (gold miners vs bullion)"
    target_daily_sek = 100.0
    edge_rationale = (
        "GDX (gold miners) is a levered, equity-beta-contaminated proxy for gold; "
        "extreme dislocations in the GDX/gold ratio vs its own trailing history "
        "tend to mean-revert once equity-market-wide noise fades."
    )

    def __init__(
        self, miner_symbol: str = "GDX", bullion_symbol: str = "GC=F",
        rolling_window_days: int = 90, zscore_threshold: float = 2.0, horizon_days: float = 20.0,
    ) -> None:
        self.miner_symbol = miner_symbol
        self.bullion_symbol = bullion_symbol
        self.rolling_window_days = rolling_window_days
        self.zscore_threshold = zscore_threshold
        self.horizon_days = horizon_days

    async def scan(self) -> list[Opportunity]:
        from backtest.data_loader import fetch_ohlcv  # local import: keep global_hunter decoupled from backtest/ at import time

        miner_df, bullion_df = await asyncio.gather(
            asyncio.to_thread(fetch_ohlcv, self.miner_symbol, "1d", "2018-01-01", None, None, True, False, "yahoo"),
            asyncio.to_thread(fetch_ohlcv, self.bullion_symbol, "1d", "2018-01-01", None, None, True, False, "yahoo"),
        )
        joined = pd.DataFrame({"miner": miner_df["close"], "bullion": bullion_df["close"]}).dropna()
        if len(joined) < self.rolling_window_days + 10:
            return []

        ratio = joined["miner"] / joined["bullion"]
        rolling_mean = ratio.rolling(self.rolling_window_days).mean()
        rolling_std = ratio.rolling(self.rolling_window_days).std()
        current_ratio, mean, std = ratio.iloc[-1], rolling_mean.iloc[-1], rolling_std.iloc[-1]
        if pd.isna(std) or std == 0:
            return []

        zscore = (current_ratio - mean) / std
        if abs(zscore) < self.zscore_threshold:
            return []

        # zscore < 0: miners cheap relative to gold -> long the miners, expecting reversion up.
        # zscore > 0: miners rich relative to gold -> short the miners (or long gold outright).
        direction = "long" if zscore < 0 else "short"
        edge_pct = min(10.0, abs(zscore) * float(std / mean) * 100.0) if mean else 0.0
        confidence = min(0.75, 0.45 + 0.05 * abs(zscore))

        return [
            Opportunity(
                id=f"{self.name}:{self.miner_symbol}:{int(time.time() * 1000)}",
                source=self.name,
                instrument=f"{self.miner_symbol}_vs_{self.bullion_symbol}_ratio",
                market_type=MarketType.EQUITY,
                edge_pct=edge_pct,
                confidence=confidence,
                action=ActionType.BUY_AND_HOLD,
                horizon_days=self.horizon_days,
                detected_at=datetime.now(timezone.utc),
                raw={
                    "direction": direction, "current_ratio": float(current_ratio),
                    "rolling_mean": float(mean), "rolling_std": float(std), "zscore": float(zscore),
                },
            )
        ]
