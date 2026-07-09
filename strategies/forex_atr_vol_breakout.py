"""Volatility compression then ATR-scaled breakout — fewer false signals."""

from __future__ import annotations

import pandas as pd
from ta.trend import ADXIndicator

from backtest.forex_sessions import add_session_columns
from backtest.indicators import add_atr, add_bb_width_percentile, add_bollinger
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexAtrVolBreakoutStrategy(Strategy):
    """Squeeze (BB width low) then break N-bar high/low with wide ATR stop."""

    name = "forex_atr_vol_breakout"

    def __init__(self, config: BacktestConfig):
        self.rr = getattr(config, "fx_reward_risk", 2.0)
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.atr_sl = getattr(config, "fx_atr_sl", 1.5)
        self.squeeze_pct = getattr(config, "fx_squeeze_pct", 0.30)
        self.breakout_lookback = getattr(config, "fx_breakout_lookback", 12)
        self.session = getattr(config, "fx_session_filter", "active")
        self.min_adx = getattr(config, "fx_min_adx_trend", 15.0)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_bb_width_percentile(
            add_bollinger(add_atr(add_session_columns(df), self.atr_period), period=20),
            lookback=100,
        )
        out["adx"] = ADXIndicator(
            high=out["high"], low=out["low"], close=out["close"], window=14
        ).adx()
        lb = self.breakout_lookback
        out["range_high"] = out["high"].shift(1).rolling(lb).max()
        out["range_low"] = out["low"].shift(1).rolling(lb).min()
        out["was_squeezed"] = out["bb_width_pct"].shift(1).rolling(5).min() <= self.squeeze_pct
        return out

    def allows_regime(self, regime: str) -> bool:
        return regime in ("trend_up", "trend_down")

    def _session_ok(self, row: pd.Series) -> bool:
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

        needed = ["atr", "close", "range_high", "range_low", "was_squeezed", "adx"]
        if any(pd.isna(row.get(c)) for c in needed):
            return None
        if not bool(row["was_squeezed"]):
            return None

        adx = float(row["adx"])
        if adx < self.min_adx:
            return None

        entry = float(row["close"])
        atr = float(row["atr"])
        if atr <= 0:
            return None

        regime = str(row.get("regime", "range"))
        prev_close = float(prev["close"]) if not pd.isna(prev.get("close")) else entry
        rh = float(row["range_high"])
        rl = float(row["range_low"])

        if regime in ("trend_up", "range") and prev_close <= rh < entry:
            stop = entry - atr * self.atr_sl
            risk = entry - stop
            if risk <= 0:
                return None
            return Signal(
                side=Side.LONG,
                stop_loss=stop,
                take_profit=entry + risk * self.rr,
                reason="atr_vol_breakout_long",
            )

        if regime in ("trend_down", "range") and prev_close >= rl > entry:
            stop = entry + atr * self.atr_sl
            risk = stop - entry
            if risk <= 0:
                return None
            return Signal(
                side=Side.SHORT,
                stop_loss=stop,
                take_profit=entry - risk * self.rr,
                reason="atr_vol_breakout_short",
            )
        return None
