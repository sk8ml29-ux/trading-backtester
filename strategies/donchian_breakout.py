from __future__ import annotations

import pandas as pd

from backtest.indicators import add_donchian
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class DonchianBreakoutStrategy(Strategy):
    """
    Turtle / Rayner Donchian breakout.
    Enter long on close above 20-day high; exit via engine at 10-day low proxy TP/SL.
  For backtest we use channel-based stop and R:R target on entry.
    """

    name = "donchian_breakout"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = config.reward_risk
        self.entry_period = config.donchian_entry
        self.exit_period = config.donchian_exit
        self.strict_trend = config.donchian_strict_trend

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        return add_donchian(df, self.entry_period, self.exit_period)

    def allows_regime(self, regime: str) -> bool:
        if self.strict_trend:
            return regime == "trend_up"
        return regime != "trend_down"

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["donchian_high", "donchian_entry_low", "ema_slow", "close"]
        if any(pd.isna(row[c]) for c in required):
            return None

        entry = float(row["close"])
        upper = float(row["donchian_high"])
        channel_low = float(row["donchian_entry_low"])

        if entry <= upper:
            return None
        if entry <= float(row["ema_slow"]):
            return None

        stop = channel_low
        if stop >= entry:
            return None

        risk = entry - stop
        take_profit = entry + risk * self.reward_risk

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=take_profit,
            reason="donchian_20_breakout",
        )
