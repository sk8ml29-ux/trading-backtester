from __future__ import annotations

import pandas as pd

from backtest.indicators import add_donchian
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class DonchianBidirectionalStrategy(Strategy):
    """Donchian channel breakout long in bull; breakdown short in bear."""

    name = "donchian_bidirectional"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = config.reward_risk
        self.entry_period = config.donchian_entry
        self.exit_period = config.donchian_exit
        self.strict_trend = config.donchian_strict_trend

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        return add_donchian(df, self.entry_period, self.exit_period)

    def allows_regime(self, regime: str) -> bool:
        return True

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        regime = str(row.get("regime", "range"))
        if regime == "trend_down":
            return self._short(row)
        if regime in ("trend_up", "range"):
            return self._long(row)
        return None

    def _long(self, row: pd.Series) -> Signal | None:
        required = ["donchian_high", "donchian_entry_low", "ema_slow", "close"]
        if any(pd.isna(row[c]) for c in required):
            return None

        entry = float(row["close"])
        if entry <= float(row["donchian_high"]) or entry <= float(row["ema_slow"]):
            return None

        stop = float(row["donchian_entry_low"])
        if stop >= entry:
            return None

        risk = entry - stop
        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=entry + risk * self.reward_risk,
            reason="donchian_breakout",
        )

    def _short(self, row: pd.Series) -> Signal | None:
        required = ["donchian_entry_low", "donchian_high", "ema_slow", "close"]
        if any(pd.isna(row[c]) for c in required):
            return None

        entry = float(row["close"])
        lower = float(row["donchian_entry_low"])
        if entry >= lower or entry >= float(row["ema_slow"]):
            return None

        stop = float(row["donchian_high"])
        if stop <= entry:
            return None

        risk = stop - entry
        return Signal(
            side=Side.SHORT,
            stop_loss=stop,
            take_profit=entry - risk * self.reward_risk,
            reason="donchian_breakdown",
        )
