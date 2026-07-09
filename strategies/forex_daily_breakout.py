"""Prior-day high/low breakout during London open (classic forex setup)."""

from __future__ import annotations

import pandas as pd

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexDailyBreakoutStrategy(Strategy):
    """Break yesterday's range during London; stop at opposite extreme, trend-aligned."""

    name = "forex_daily_breakout"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = getattr(config, "fx_reward_risk", 2.0)
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.min_range_atr = getattr(config, "fx_min_range_atr", 0.5)
        self.require_trend = getattr(config, "fx_require_trend", True)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_session_columns(add_atr(df, self.atr_period))
        dates = pd.to_datetime(out.index).date
        out["_date"] = dates
        daily = out.groupby("_date").agg(
            day_high=("high", "max"),
            day_low=("low", "min"),
        )
        daily["prev_high"] = daily["day_high"].shift(1)
        daily["prev_low"] = daily["day_low"].shift(1)
        daily["prev_range"] = daily["prev_high"] - daily["prev_low"]
        out = out.join(daily[["prev_high", "prev_low", "prev_range"]], on="_date")
        out.drop(columns=["_date"], inplace=True, errors="ignore")
        return out

    def allows_regime(self, regime: str) -> bool:
        if self.require_trend:
            return regime in ("trend_up", "trend_down")
        return True

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        if not row.get("london_open"):
            return None

        needed = ["prev_high", "prev_low", "prev_range", "atr", "close", "regime"]
        if any(pd.isna(row.get(c)) for c in needed):
            return None

        atr = float(row["atr"])
        if atr <= 0 or float(row["prev_range"]) < atr * self.min_range_atr:
            return None

        entry = float(row["close"])
        prev_close = float(prev["close"]) if not pd.isna(prev.get("close")) else entry
        prev_hi = float(row["prev_high"])
        prev_lo = float(row["prev_low"])
        regime = str(row["regime"])

        if regime in ("trend_up", "range") and prev_close <= prev_hi < entry:
            stop = prev_lo
            risk = entry - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=entry + risk * self.reward_risk,
                reason="daily_breakout_long",
            )

        if regime in ("trend_down", "range") and prev_close >= prev_lo > entry:
            stop = prev_hi
            risk = stop - entry
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=entry - risk * self.reward_risk,
                reason="daily_breakout_short",
            )
        return None
