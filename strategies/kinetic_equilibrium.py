from __future__ import annotations

import pandas as pd

from backtest.custom_indicators import compute_kes
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class KineticEquilibriumStrategy(Strategy):
    """
    Proprietary KES (Kinetic Equilibrium Score) strategy.
    Long when KES histogram crosses positive near equilibrium after higher-low structure.
    """

    name = "kinetic_equilibrium"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = config.reward_risk
        self.kes_entry = config.kes_entry_threshold
        self.kinetic_span = config.kes_kinetic_span
        self.equilibrium_ema = config.kes_equilibrium_ema
        self.structure_lb = config.kes_structure_lookback
        self.swing_lookback = config.swing_lookback
        self.strict_trend = config.macd_strict_trend

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        from backtest.indicators import swing_low

        data = compute_kes(
            df,
            kinetic_span=self.kinetic_span,
            equilibrium_ema=self.equilibrium_ema,
            structure_lookback=self.structure_lb,
        )
        data["swing_low"] = swing_low(data["low"], lookback=self.swing_lookback)
        return data

    def allows_regime(self, regime: str) -> bool:
        if self.strict_trend:
            return regime == "trend_up"
        return regime != "trend_down"

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        required = ["kes_hist", "kes", "ema_slow", "swing_low", "close", "kes_equilibrium"]
        if any(pd.isna(row[c]) for c in required):
            return None

        close = float(row["close"])
        if close <= float(row["ema_slow"]):
            return None

        hist_cross = float(prev["kes_hist"]) <= 0 and float(row["kes_hist"]) > 0
        kes_ok = float(row["kes"]) >= self.kes_entry
        eq_ok = float(row["kes_equilibrium"]) >= 0.35

        if not (hist_cross and kes_ok and eq_ok):
            return None

        stop = float(row["swing_low"])
        if stop >= close:
            stop = close - float(row.get("atr", close * 0.01)) * 1.2
        risk = close - stop
        if risk <= 0:
            return None

        return Signal(
            side=Side.LONG,
            stop_loss=stop,
            take_profit=close + risk * self.reward_risk,
            reason="kes_cross",
        )
