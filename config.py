"""Default backtest configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BacktestConfig:
    symbol: str = "GC=F"
    timeframe: str = "30m"
    entry_timeframe: str | None = "30m"
    regime_timeframe: str = "1d"
    start: str = "2015-01-01"
    end: str | None = None
    initial_capital: float = 10_000.0
    risk_per_trade: float = 0.01
    reward_risk: float = 2.0
    commission_pct: float = 0.0005
    slippage_pct: float = 0.0003
    spread_pct: float = 0.0002
    spread_check_enabled: bool = True
    regime_ema_fast: int = 9
    regime_ema_slow: int = 200
    adx_period: int = 14
    adx_trend_threshold: float = 0.0
    macd_strict_trend: bool = False

    # MACD tunables (30m optimized defaults)
    swing_lookback: int = 16
    macd_require_below_zero: bool = False
    macd_signal_mode: str = "cross_below_zero"  # cross_below_zero | histogram_flip | either

    # Donchian tunables (30m optimized)
    donchian_entry: int = 24
    donchian_exit: int = 3
    donchian_strict_trend: bool = False

    # RSI tunables (30m optimized)
    rsi_period: int = 18
    rsi_oversold: float = 32.0
    rsi_atr_sl: float = 1.0
    rsi_atr_tp: float = 1.5
    rsi_reward_risk: float = 1.0

    # Adaptive trend pullback
    trend_pullback_ema: int = 21

    # Squeeze breakout
    squeeze_bb_period: int = 20
    squeeze_width_pct_max: float = 0.25
    squeeze_width_lookback: int = 100

    # KES — Kinetic Equilibrium Score (proprietary indicator)
    kes_entry_threshold: float = 0.15
    kes_kinetic_span: int = 5
    kes_equilibrium_ema: int = 21
    kes_structure_lookback: int = 8

    # ECI — Edge Compression Index (proprietary indicator)
    eci_entry_threshold: float = 0.35
    eci_compression_pct: float = 0.25
    eci_pressure_window: int = 10

    # Active Pulse — higher trade frequency, 1:1.5 style R:R
    pulse_rsi_period: int = 14
    pulse_rsi_buy: float = 40.0
    pulse_atr_sl: float = 1.0
    pulse_reward_risk: float = 1.5
    pulse_require_above_200: bool = True

    # Triple TF confluence (None = all relevant timeframes must agree)
    mtf_min_align: int | None = None

    # Velocity Rejection Scalp (VRS) — 15m proprietary scalping
    vrs_swing_lookback: int = 8
    vrs_wick_ratio: float = 0.48
    vrs_body_max_ratio: float = 0.42
    vrs_sweep_atr: float = 0.07
    vrs_stop_pad_atr: float = 0.05
    vrs_reward_risk: float = 1.28
    vrs_volume_mult: float = 1.05
    vrs_use_1h_filter: bool = True
    vrs_snap_pullback: float = 0.42

    # Forex session strategies
    fx_reward_risk: float = 1.8
    fx_atr_period: int = 14
    fx_min_range_atr: float = 0.35
    fx_rsi_period: int = 14
    fx_rsi_overbought: float = 68.0
    fx_rsi_oversold: float = 32.0
    fx_max_adx_range: float = 22.0
    fx_ema_period: int = 21
    fx_atr_sl: float = 1.1
    fx_session_filter: str = "active"  # active | london | ny | asian | all
    fx_min_adx_trend: float = 18.0
    fx_max_adx_trend: float = 28.0
    fx_require_trend: bool = True
    fx_mtf_use_1h: bool = True
    fx_squeeze_pct: float = 0.30
    fx_breakout_lookback: int = 12

    # Harmonic patterns (forex)
    fx_harmonic_pivot_left: int = 3
    fx_harmonic_pivot_right: int = 3
    fx_harmonic_pivot_lookback: int = 80
    fx_harmonic_d_tol: float = 0.35
    fx_harmonic_patterns: str = "butterfly,gartley,bat"

    # Harmonic / swing patterns
    fx_swing_lookback: int = 4
    fx_fib_tolerance: float = 0.15
    fx_harmonic_pattern: str = "both"  # gartley | butterfly | both
    fx_bb_period: int = 20
    fx_bb_std: float = 2.0
    fx_ema_fast: int = 9


@dataclass
class LiveConfig:
    symbol: str = "GC=F"
    timeframe: str = "30m"
    strategy: str = "macd_pullback"
    mode: str = "paper"
    initial_capital: float = 10_000.0
    risk_per_trade: float = 0.01
    reward_risk: float = 2.0
    commission_pct: float = 0.0005
    poll_seconds: int = 180
    spread_check_enabled: bool = True
    state_file: str = "data/live_state.json"
    log_file: str = "data/live_trades.log"
    regime_ema_fast: int = 9
    regime_ema_slow: int = 200
    adx_period: int = 14
    adx_trend_threshold: float = 0.0
    macd_strict_trend: bool = False
    swing_lookback: int = 16
    macd_require_below_zero: bool = False
    macd_signal_mode: str = "cross_below_zero"
    donchian_entry: int = 24
    donchian_exit: int = 3
    donchian_strict_trend: bool = False
    rsi_period: int = 18
    rsi_oversold: float = 32.0
    rsi_atr_sl: float = 1.0
    rsi_atr_tp: float = 1.5
    trend_pullback_ema: int = 21
    squeeze_bb_period: int = 20
    squeeze_width_pct_max: float = 0.25
    squeeze_width_lookback: int = 100
    kes_entry_threshold: float = 0.15
    kes_kinetic_span: int = 5
    kes_equilibrium_ema: int = 21
    kes_structure_lookback: int = 8
    eci_entry_threshold: float = 0.35
    eci_compression_pct: float = 0.25
    eci_pressure_window: int = 10
    pulse_rsi_period: int = 14
    pulse_rsi_buy: float = 40.0
    pulse_atr_sl: float = 1.0
    pulse_reward_risk: float = 1.5
    pulse_require_above_200: bool = True
    regime_timeframe: str = "1d"

    def to_backtest_config(self) -> BacktestConfig:
        return BacktestConfig(
            symbol=self.symbol,
            timeframe=self.timeframe,
            entry_timeframe=self.timeframe,
            regime_timeframe=self.regime_timeframe,
            initial_capital=self.initial_capital,
            risk_per_trade=self.risk_per_trade,
            reward_risk=self.reward_risk,
            commission_pct=self.commission_pct,
            slippage_pct=0.0003,
            spread_pct=0.0002,
            spread_check_enabled=self.spread_check_enabled,
            regime_ema_fast=self.regime_ema_fast,
            regime_ema_slow=self.regime_ema_slow,
            adx_period=self.adx_period,
            adx_trend_threshold=self.adx_trend_threshold,
            macd_strict_trend=self.macd_strict_trend,
            swing_lookback=self.swing_lookback,
            macd_require_below_zero=self.macd_require_below_zero,
            macd_signal_mode=self.macd_signal_mode,
            donchian_entry=self.donchian_entry,
            donchian_exit=self.donchian_exit,
            donchian_strict_trend=self.donchian_strict_trend,
            rsi_period=self.rsi_period,
            rsi_oversold=self.rsi_oversold,
            rsi_atr_sl=self.rsi_atr_sl,
            rsi_atr_tp=self.rsi_atr_tp,
            trend_pullback_ema=self.trend_pullback_ema,
            squeeze_bb_period=self.squeeze_bb_period,
            squeeze_width_pct_max=self.squeeze_width_pct_max,
            squeeze_width_lookback=self.squeeze_width_lookback,
            kes_entry_threshold=self.kes_entry_threshold,
            kes_kinetic_span=self.kes_kinetic_span,
            kes_equilibrium_ema=self.kes_equilibrium_ema,
            kes_structure_lookback=self.kes_structure_lookback,
            eci_entry_threshold=self.eci_entry_threshold,
            eci_compression_pct=self.eci_compression_pct,
            eci_pressure_window=self.eci_pressure_window,
            pulse_rsi_period=self.pulse_rsi_period,
            pulse_rsi_buy=self.pulse_rsi_buy,
            pulse_atr_sl=self.pulse_atr_sl,
            pulse_reward_risk=self.pulse_reward_risk,
            pulse_require_above_200=self.pulse_require_above_200,
        )
