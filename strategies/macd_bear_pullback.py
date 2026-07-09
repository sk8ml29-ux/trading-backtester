from __future__ import annotations

import pandas as pd

from backtest.indicators import add_macd, swing_high
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class MacdBearPullbackStrategy(Strategy):
    """MACD bearish pullback short in downtrends — mirror of macd_pullback."""

    name = "macd_bear_pullback"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = config.reward_risk
        self.swing_lookback = config.swing_lookback
        self.signal_mode = config.macd_signal_mode

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        data = add_macd(df)
        data["swing_high"] = swing_high(data["high"], lookback=self.swing_lookback)
        return data

    def allows_regime(self, regime: str) -> bool:
        return regime == "trend_down"

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["macd", "macd_signal", "ema_slow", "swing_high", "close"]
        if any(pd.isna(row[c]) for c in required):
            return None

        if float(row["close"]) >= float(row["ema_slow"]):
            return None

        cross_down = prev["macd"] >= prev["macd_signal"] and row["macd"] < row["macd_signal"]
        above_zero = float(row["macd"]) > 0
        hist_flip = float(prev["macd_hist"]) >= 0 and float(row["macd_hist"]) < 0

        signal_ok = False
        if self.signal_mode == "histogram_flip":
            signal_ok = hist_flip
        else:
            signal_ok = (cross_down and above_zero) or hist_flip

        if not signal_ok:
            return None

        entry = float(row["close"])
        stop = float(row["swing_high"])
        if stop <= entry:
            stop = entry * 1.02

        risk = stop - entry
        if risk <= 0:
            return None

        return Signal(
            side=Side.SHORT,
            stop_loss=stop,
            take_profit=entry - risk * self.reward_risk,
            reason="macd_bear_pullback",
        )
