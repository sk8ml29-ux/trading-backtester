"""
Lab Evaluator — runs OOS backtests and produces normalized metrics.

Wraps the existing BacktestEngine with no modifications.
Output dict uses the keys expected by RiskGatekeeper:
  n_trades, profit_factor, sharpe, max_drawdown (fraction 0-1)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent


@dataclass
class EvaluationResult:
    strategy_id: str
    symbol: str
    timeframe: str
    oos_metrics: dict          # Gatekeeper-compatible keys
    oos_days: int
    passed: bool
    verdict_reason: str


_EMPTY_METRICS = {
    "n_trades": 0,
    "profit_factor": 0.0,
    "sharpe": 0.0,
    "max_drawdown": 1.0,
    "total_return_pct": 0.0,
    "win_rate_pct": 0.0,
}


def _sharpe_from_equity(equity: pd.Series) -> float:
    """Annualised Sharpe from an equity curve (using daily log-returns)."""
    if len(equity) < 10:
        return 0.0
    returns = np.log(equity / equity.shift(1)).dropna()
    if returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(252))


def _normalize_metrics(raw: dict, equity_curve: pd.Series) -> dict:
    """
    Convert compute_metrics() output to Gatekeeper-compatible format.
    - total_trades → n_trades
    - max_drawdown_pct (pct, negative) → max_drawdown (fraction, positive)
    - adds sharpe from equity curve
    """
    pf_raw = raw.get("profit_factor", 0.0)
    pf = float(pf_raw) if pf_raw != "inf" else 99.0

    mdd_pct = abs(float(raw.get("max_drawdown_pct", 0.0)))  # e.g. 25.0
    mdd_frac = mdd_pct / 100.0

    return {
        "n_trades": int(raw.get("total_trades", 0)),
        "profit_factor": pf,
        "sharpe": _sharpe_from_equity(equity_curve),
        "max_drawdown": mdd_frac,
        "total_return_pct": float(raw.get("total_return_pct", 0.0)),
        "win_rate_pct": float(raw.get("win_rate_pct", 0.0)),
        "cagr_pct": float(raw.get("cagr_pct", 0.0)),
    }


class LabEvaluator:
    """
    Evaluates a strategy candidate on the OOS slice of a DataFrame.
    oos_ratio=0.30 → last 30 % of bars are OOS, first 70 % are in-sample.
    """

    def __init__(self, oos_ratio: float = 0.30):
        self.oos_ratio = oos_ratio

    # ─────────────────────────────────────────────────────────────────────────

    def evaluate(
        self,
        strategy_id: str,
        symbol: str,
        timeframe: str,
        params: dict,
        df: pd.DataFrame,
    ) -> Optional[EvaluationResult]:
        """
        Run OOS evaluation.  Returns None on fatal error.
        Never raises — failures are logged and returned as passed=False.
        """
        try:
            return self._evaluate(strategy_id, symbol, timeframe, params, df)
        except Exception as e:
            logger.error(
                "[Evaluator] Fatal error %s/%s: %s", strategy_id, symbol, e, exc_info=True
            )
            return None

    def _evaluate(
        self,
        strategy_id: str,
        symbol: str,
        timeframe: str,
        params: dict,
        df: pd.DataFrame,
    ) -> EvaluationResult:
        if len(df) < 600:
            return EvaluationResult(
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=timeframe,
                oos_metrics=dict(_EMPTY_METRICS),
                oos_days=0,
                passed=False,
                verdict_reason=f"Insufficient data: {len(df)} bars",
            )

        # IS/OOS split
        split = int(len(df) * (1.0 - self.oos_ratio))
        oos_df = df.iloc[split:].copy()

        # Calendar days in OOS window
        try:
            oos_days = int((oos_df.index[-1] - oos_df.index[0]).days)
        except Exception:
            bars_per_day = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48, "1h": 24, "4h": 6, "1d": 1}
            oos_days = len(oos_df) // bars_per_day.get(timeframe, 48)

        # Resolve strategy class
        from strategies import STRATEGIES
        if strategy_id not in STRATEGIES:
            return EvaluationResult(
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=timeframe,
                oos_metrics=dict(_EMPTY_METRICS),
                oos_days=oos_days,
                passed=False,
                verdict_reason=f"Unknown strategy: {strategy_id}",
            )

        # Build config with lab params
        from config import BacktestConfig
        cfg = BacktestConfig()
        for k, v in params.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)

        strategy = STRATEGIES[strategy_id](cfg)

        # Run backtest on OOS slice only
        from backtest.engine import BacktestEngine
        from backtest.metrics import compute_metrics

        engine = BacktestEngine(cfg)
        result = engine.run(oos_df, strategy)

        if not result or not result.trades:
            return EvaluationResult(
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=timeframe,
                oos_metrics=dict(_EMPTY_METRICS),
                oos_days=oos_days,
                passed=False,
                verdict_reason="No trades in OOS period",
            )

        raw = compute_metrics(result)
        metrics = _normalize_metrics(raw, result.equity_curve)

        return EvaluationResult(
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            oos_metrics=metrics,
            oos_days=oos_days,
            passed=True,
            verdict_reason="ok",
        )

    # ─────────────────────────────────────────────────────────────────────────

    def score(self, metrics: dict) -> float:
        """
        Composite scalar score for ranking candidates.
        Higher = better.  Returns -999 for invalid metrics.
        """
        pf = float(metrics.get("profit_factor", 0.0))
        sharpe = float(metrics.get("sharpe", 0.0))
        mdd = float(metrics.get("max_drawdown", 1.0))
        n = int(metrics.get("n_trades", 0))

        if n < 5 or pf <= 0.0:
            return -999.0

        # Log PF (stability) + Sharpe (risk-adj), scaled by drawdown penalty
        # and sample-size factor (saturates at 30 trades)
        sample_weight = min(n / 30.0, 1.0)
        raw = (np.log(max(pf, 0.01)) * 0.4 + sharpe * 0.4) * (1.0 - mdd)
        return float(raw * sample_weight)
