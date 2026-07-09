"""Bollinger band fade — mean reversion at band extremes."""

from __future__ import annotations

import pandas as pd
from ta.trend import ADXIndicator

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr, add_bollinger, add_rsi
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexBollingerFadeStrategy(Strategy):
    """Fade touches of BB upper/lower with RSI confirmation."""

    name = "forex_bollinger_fade"

    def __init__(self, config: BacktestConfig):
        self.bb_period = getattr(config, "fx_bb_period", 20)
        self.bb_std = getattr(config, "fx_bb_std", 2.0)
        self.rsi_period = getattr(config, "fx_rsi_period", 14)
        self.rsi_ob = getattr(config, "fx_rsi_overbought", 65.0)
        self.rsi_os = getattr(config, "fx_rsi_oversold", 35.0)
        self.rr = getattr(config, "fx_reward_risk", 1.5)
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.atr_sl = getattr(config, "fx_atr_sl", 1.0)
        self.max_adx = getattr(config, "fx_max_adx_range", 28.0)
        self.session = getattr(config, "fx_session_filter", "all")

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_bollinger(
            add_rsi(add_atr(add_session_columns(df), self.atr_period), self.rsi_period),
            period=self.bb_period,
            std=self.bb_std,
        )
        out["adx"] = ADXIndicator(
            high=out["high"], low=out["low"], close=out["close"], window=14
        ).adx()
        return out

    def allows_regime(self, regime: str) -> bool:
        return regime in ("range", "trend_up", "trend_down")

    def _session_ok(self, row: pd.Series) -> bool:
        if self.session == "all":
            return True
        if self.session == "london":
            return bool(row.get("london_open"))
        if self.session == "ny":
            return bool(row.get("ny_overlap"))
        if self.session == "active":
            return bool(row.get("london_open") or row.get("ny_overlap"))
        return True

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        if not self._session_ok(row):
            return None

        needed = ["bb_upper", "bb_lower", "bb_mid", "rsi", "atr", "close", "high", "low", "adx"]
        if any(pd.isna(row.get(c)) for c in needed):
            return None
        if float(row["adx"]) > self.max_adx:
            return None

        entry = float(row["close"])
        atr = float(row["atr"])
        if atr <= 0:
            return None

        upper = float(row["bb_upper"])
        lower = float(row["bb_lower"])
        rsi = float(row["rsi"])

        if float(row["high"]) >= upper and rsi >= self.rsi_ob:
            stop = entry + atr * self.atr_sl
            risk = stop - entry
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=entry - risk * self.rr,
                reason="bb_upper_fade",
            )

        if float(row["low"]) <= lower and rsi <= self.rsi_os:
            stop = entry - atr * self.atr_sl
            risk = entry - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=entry + risk * self.rr,
                reason="bb_lower_fade",
            )
        return None
