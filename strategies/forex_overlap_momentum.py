"""EMA pullback momentum during London-NY overlap (forex-specific)."""

from __future__ import annotations

import pandas as pd
from ta.trend import EMAIndicator

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexOverlapMomentumStrategy(Strategy):
    """Trend pullback to EMA21 in NY overlap window (12-16 UTC)."""

    name = "forex_overlap_momentum"

    def __init__(self, config: BacktestConfig):
        self.rr = getattr(config, "fx_reward_risk", 2.0)
        self.ema_period = getattr(config, "fx_ema_period", 21)
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.atr_sl = getattr(config, "fx_atr_sl", 1.1)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_session_columns(add_atr(df, self.atr_period))
        out["ema_pullback"] = EMAIndicator(
            close=out["close"], window=self.ema_period
        ).ema_indicator()
        return out

    def allows_regime(self, regime: str) -> bool:
        return regime in ("trend_up", "trend_down")

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        if not row.get("ny_overlap"):
            return None

        needed = ["ema_pullback", "atr", "close", "regime"]
        if any(pd.isna(row.get(c)) for c in needed):
            return None

        regime = str(row["regime"])
        entry = float(row["close"])
        ema = float(row["ema_pullback"])
        prev_close = float(prev["close"]) if not pd.isna(prev.get("close")) else entry
        prev_ema = float(prev["ema_pullback"]) if not pd.isna(prev.get("ema_pullback")) else ema
        atr = float(row["atr"])

        if regime == "trend_up" and prev_close <= prev_ema and entry > ema:
            stop = entry - atr * self.atr_sl
            risk = entry - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=entry + risk * self.rr,
                reason="overlap_pullback_long",
            )

        if regime == "trend_down" and prev_close >= prev_ema and entry < ema:
            stop = entry + atr * self.atr_sl
            risk = stop - entry
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=entry - risk * self.rr,
                reason="overlap_pullback_short",
            )
        return None
