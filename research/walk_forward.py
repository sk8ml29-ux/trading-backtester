"""Walk-forward / out-of-sample validation."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.engine import BacktestEngine, BacktestResult
from backtest.metrics import compute_metrics
from backtest.mtf import apply_regime_to_entry, prepare_entry_frame
from config import BacktestConfig
from strategies.base import Strategy


@dataclass
class WalkForwardResult:
    symbol: str
    strategy: str
    timeframe: str
    train_metrics: dict
    test_metrics: dict
    test_pass: bool
    split_date: str


def split_train_test(df: pd.DataFrame, train_ratio: float = 0.7) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    if len(df) < 100:
        raise ValueError("Need at least 100 bars for walk-forward")
    idx = int(len(df) * train_ratio)
    split_ts = df.index[idx]
    return df.iloc[:idx].copy(), df.iloc[idx:].copy(), split_ts


def run_walk_forward(
    entry_df: pd.DataFrame,
    regime_df: pd.DataFrame,
    cfg: BacktestConfig,
    strategy: Strategy,
    train_ratio: float = 0.7,
    min_test_trades: int = 15,       # raised from 3 — 3 trades is not statistically meaningful
    min_test_return_pct: float = 0.0,
    min_profit_factor: float = 1.10, # raised from 1.0 — need genuine edge
    min_sharpe: float = 0.5,         # new — require positive risk-adjusted return
) -> WalkForwardResult:
    entry = prepare_entry_frame(entry_df, cfg)
    full = apply_regime_to_entry(entry, regime_df, cfg)
    if len(full) < 100:
        raise ValueError("Need at least 100 bars for walk-forward")

    idx = int(len(full) * train_ratio)
    split_ts = full.index[idx]
    train = full.iloc[:idx]
    test = full.iloc[idx:]

    engine = BacktestEngine(cfg)
    train_res = engine.run(train, strategy)
    test_res  = engine.run(test, strategy)

    train_m = compute_metrics(train_res)
    test_m  = compute_metrics(test_res)

    test_pass = (
        int(test_m.get("total_trades", 0)) >= min_test_trades
        and float(test_m.get("total_return_pct", 0)) > min_test_return_pct
        and _profit_factor_ok(test_m.get("profit_factor"), min_pf=min_profit_factor)
        and float(test_m.get("sharpe", 0.0)) >= min_sharpe
    )

    return WalkForwardResult(
        symbol=cfg.symbol,
        strategy=strategy.name,
        timeframe=cfg.timeframe,
        train_metrics=train_m,
        test_metrics=test_m,
        test_pass=test_pass,
        split_date=str(split_ts),
    )


def _profit_factor_ok(pf, min_pf: float = 1.10) -> bool:
    if pf in (0, "0", None):
        return False
    if pf == "inf":
        return True
    try:
        return float(pf) >= min_pf
    except (TypeError, ValueError):
        return False
