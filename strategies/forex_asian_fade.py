"""Fade Asian session range extremes when volatility is compressed."""

from __future__ import annotations

import pandas as pd
from ta.trend import ADXIndicator

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr, add_rsi
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexAsianFadeStrategy(Strategy):
    """Mean-revert touches of Asian range during low-ADX Asian hours."""

    name = "forex_asian_fade"

    def __init__(self, config: BacktestConfig):
        self.rr = getattr(config, "fx_reward_risk", 1.5)
        self.rsi_period = getattr(config, "fx_rsi_period", 14)
        self.rsi_ob = getattr(config, "fx_rsi_overbought", 68.0)
        self.rsi_os = getattr(config, "fx_rsi_oversold", 32.0)
        self.max_adx = getattr(config, "fx_max_adx_range", 22.0)
        self.atr_period = getattr(config, "fx_atr_period", 14)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_rsi(add_atr(add_session_columns(df), self.atr_period), self.rsi_period)
        out["adx"] = ADXIndicator(
            high=out["high"], low=out["low"], close=out["close"], window=14
        ).adx()
        return out

    def allows_regime(self, regime: str) -> bool:
        return regime in ("range", "trend_up", "trend_down")

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        if not row.get("asian_session"):
            return None
        if pd.isna(row.get("adx")) or float(row["adx"]) > self.max_adx:
            return None

        needed = ["asian_high", "asian_low", "rsi", "atr", "close", "high", "low"]
        if any(pd.isna(row.get(c)) for c in needed):
            return None

        entry = float(row["close"])
        atr = float(row["atr"])
        asian_hi = float(row["asian_high"])
        asian_lo = float(row["asian_low"])
        rsi = float(row["rsi"])

        if float(row["high"]) >= asian_hi * 0.9999 and rsi >= self.rsi_ob:
            stop = entry + atr * 0.8
            risk = stop - entry
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=entry - risk * self.rr,
                reason="asian_fade_high",
            )

        if float(row["low"]) <= asian_lo * 1.0001 and rsi <= self.rsi_os:
            stop = entry - atr * 0.8
            risk = entry - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=entry + risk * self.rr,
                reason="asian_fade_low",
            )
        return None
