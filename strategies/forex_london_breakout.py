"""London open breakout of Asian session range (forex-specific)."""

from __future__ import annotations

import pandas as pd

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexLondonBreakoutStrategy(Strategy):
    """Break Asian high/low during London open (08-11 UTC)."""

    name = "forex_london_breakout"

    def __init__(self, config: BacktestConfig):
        self.rr = getattr(config, "fx_reward_risk", 1.8)
        self.min_range_atr = getattr(config, "fx_min_range_atr", 0.35)
        self.atr_period = getattr(config, "fx_atr_period", 14)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_session_columns(add_atr(df, self.atr_period))
        return out

    def allows_regime(self, regime: str) -> bool:
        return regime in ("trend_up", "trend_down", "range")

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        if not row.get("london_open"):
            return None
        needed = ["asian_high", "asian_low", "asian_range", "atr", "close"]
        if any(pd.isna(row.get(c)) for c in needed):
            return None

        atr = float(row["atr"])
        if atr <= 0 or float(row["asian_range"]) < atr * self.min_range_atr:
            return None

        entry = float(row["close"])
        prev_close = float(prev["close"]) if not pd.isna(prev.get("close")) else entry
        asian_hi = float(row["asian_high"])
        asian_lo = float(row["asian_low"])

        if prev_close <= asian_hi < entry:
            stop = asian_lo
            risk = entry - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=entry + risk * self.rr,
                reason="london_break_asian_high",
            )

        if prev_close >= asian_lo > entry:
            stop = asian_hi
            risk = stop - entry
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=entry - risk * self.rr,
                reason="london_break_asian_low",
            )
        return None
