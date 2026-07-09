"""Harmonic XABCD patterns (Butterfly, Gartley, Bat, Crab) with session filter."""

from __future__ import annotations

import pandas as pd

from backtest.forex_sessions import add_session_columns
from backtest.harmonic_patterns import annotate_harmonic_signals
from backtest.indicators import add_atr
from config import BacktestConfig
from strategies.base import Side, Signal, Strategy


class ForexHarmonicStrategy(Strategy):
    """Trade harmonic PRZ completions during active forex sessions."""

    name = "forex_harmonic"

    def __init__(self, config: BacktestConfig):
        self.reward_risk = getattr(config, "fx_reward_risk", 2.0)
        self.atr_period = getattr(config, "fx_atr_period", 14)
        self.session = getattr(config, "fx_session_filter", "active")
        self.pivot_left = getattr(config, "fx_harmonic_pivot_left", 3)
        self.pivot_right = getattr(config, "fx_harmonic_pivot_right", 3)
        self.pivot_lookback = getattr(config, "fx_harmonic_pivot_lookback", 80)
        self.d_tolerance_atr = getattr(config, "fx_harmonic_d_tol", 0.35)
        raw = getattr(config, "fx_harmonic_patterns", "butterfly,gartley,bat")
        self.patterns = [p.strip() for p in str(raw).split(",") if p.strip()]

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = add_session_columns(add_atr(df, self.atr_period))
        return annotate_harmonic_signals(
            out,
            pivot_left=self.pivot_left,
            pivot_right=self.pivot_right,
            pivot_lookback=self.pivot_lookback,
            patterns=self.patterns,
            d_tolerance_atr=self.d_tolerance_atr,
            reward_risk=self.reward_risk,
        )

    def allows_regime(self, regime: str) -> bool:
        return regime in ("range", "trend_up", "trend_down")

    def _session_ok(self, row: pd.Series) -> bool:
        if self.session in ("all", ""):
            return True
        if self.session == "london":
            return bool(row.get("london_open"))
        if self.session == "ny":
            return bool(row.get("ny_overlap"))
        if self.session == "asian":
            return bool(row.get("asian_session"))
        if self.session == "active":
            return bool(row.get("london_open") or row.get("ny_overlap"))
        return True

    def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
        if not self._session_ok(row):
            return None
        if pd.isna(row.get("harmonic_side")):
            return None

        side_str = str(row["harmonic_side"])
        stop = float(row["harmonic_stop"])
        target = float(row["harmonic_target"])
        pat = str(row.get("harmonic_pattern", ""))

        if side_str == "long":
            return Signal(Side.LONG, stop, target, f"harmonic_{pat}_long")
        if side_str == "short":
            return Signal(Side.SHORT, stop, target, f"harmonic_{pat}_short")
        return None
