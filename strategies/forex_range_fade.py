"""Range fade at session extremes — low ADX, higher R:R, selective entries."""

from __future__ import annotations

import pandas as pd
from ta.trend import ADXIndicator

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr, add_rsi
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexRangeFadeStrategy(Strategy):
    """Fade Asian/London range edges when ADX low and RSI extreme."""

    name = "forex_range_fade"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = getattr(config, "fx_reward_risk", 2.0)
        self.rsi_period = getattr(config, "fx_rsi_period", 14)
        self.rsi_ob = getattr(config, "fx_rsi_overbought", 70.0)
        self.rsi_os = getattr(config, "fx_rsi_oversold", 30.0)
        self.max_adx = getattr(config, "fx_max_adx_range", 20.0)
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.min_range_atr = getattr(config, "fx_min_range_atr", 0.4)
        self.session = getattr(config, "fx_session_filter", "london")

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_rsi(add_atr(add_session_columns(df), self.atr_period), self.rsi_period)
        out["adx"] = ADXIndicator(
            high=out["high"], low=out["low"], close=out["close"], window=14
        ).adx()
        return out

    def allows_regime(self, regime: str) -> bool:
        return regime in ("range", "trend_up", "trend_down")

    def _session_ok(self, row: pd.Series) -> bool:
        if self.session == "london":
            return bool(row.get("london_open"))
        if self.session == "ny":
            return bool(row.get("ny_overlap"))
        if self.session == "asian":
            return bool(row.get("asian_session"))
        return bool(row.get("london_open") or row.get("ny_overlap"))

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        if not self._session_ok(row):
            return None

        needed = ["asian_high", "asian_low", "asian_range", "rsi", "adx", "atr", "close", "high", "low"]
        if any(pd.isna(row.get(c)) for c in needed):
            return None

        if float(row["adx"]) > self.max_adx:
            return None

        atr = float(row["atr"])
        if atr <= 0 or float(row["asian_range"]) < atr * self.min_range_atr:
            return None

        entry = float(row["close"])
        asian_hi = float(row["asian_high"])
        asian_lo = float(row["asian_low"])
        rsi = float(row["rsi"])

        if float(row["high"]) >= asian_hi and rsi >= self.rsi_ob:
            stop = entry + atr * 1.0
            risk = stop - entry
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=entry - risk * self.reward_risk,
                reason="fx_range_fade_high",
            )

        if float(row["low"]) <= asian_lo and rsi <= self.rsi_os:
            stop = entry - atr * 1.0
            risk = entry - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=entry + risk * self.reward_risk,
                reason="fx_range_fade_low",
            )
        return None
