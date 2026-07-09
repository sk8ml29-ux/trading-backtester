from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.engine import BacktestResult

# Bars per year per timeframe — used for Sharpe/Sortino annualisation.
# Crypto runs 24/7; forex ~252 trading days; stocks ~252 × 6.5h.
# We detect the actual bar frequency from the equity index when possible.
_BARS_PER_YEAR: dict[str, float] = {
    "1m":  525_600,
    "5m":  105_120,
    "15m":  35_040,
    "30m":  17_520,
    "1h":   8_760,
    "4h":   2_190,
    "1d":     252,
}


def _periods_per_year(equity: pd.Series, timeframe: str) -> float:
    """Annualisation factor: actual bars / elapsed years."""
    if len(equity) > 1:
        years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
        return len(equity) / years
    return _BARS_PER_YEAR.get(timeframe, 17_520)


def _sharpe(returns: pd.Series, periods_per_year: float) -> float:
    """Annualised Sharpe ratio from per-bar log-returns."""
    if len(returns) < 5 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def _sortino(returns: pd.Series, periods_per_year: float) -> float:
    """Annualised Sortino ratio — only downside deviation in denominator."""
    if len(returns) < 5:
        return 0.0
    downside = returns[returns < 0]
    if len(downside) == 0 or downside.std() == 0:
        return float(returns.mean() * np.sqrt(periods_per_year) * 10)  # cap at 10× Sharpe
    return float(returns.mean() / downside.std() * np.sqrt(periods_per_year))


def _calmar(cagr_pct: float, max_drawdown_pct: float) -> float:
    """Calmar ratio: CAGR / |max drawdown|. Returns 0 when DD is 0."""
    dd = abs(max_drawdown_pct)
    return round(cagr_pct / dd, 3) if dd > 0 else 0.0


def compute_metrics(result: BacktestResult) -> dict:
    trades = result.trades
    equity = result.equity_curve
    tf = result.timeframe or "30m"
    initial = result.config.initial_capital if result.config else (
        float(equity.iloc[0]) if len(equity) else 10_000.0
    )

    _empty = {
        "strategy": result.strategy,
        "symbol": result.symbol,
        "timeframe": tf,
        "total_trades": 0,
        "win_rate_pct": 0.0,
        "profit_factor": 0.0,
        "total_return_pct": 0.0,
        "cagr_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "calmar": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "expectancy": 0.0,
        "final_equity": float(initial),
    }
    if not trades:
        return _empty

    pnls   = [t.pnl for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate     = len(wins) / len(trades) * 100
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    final_equity = float(equity.iloc[-1]) if len(equity) else initial
    total_return = (final_equity / initial - 1) * 100

    if len(equity) > 1:
        years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
        cagr  = ((final_equity / initial) ** (1 / years) - 1) * 100
        trades_per_year = round(len(trades) / years, 1)
    else:
        years, cagr, trades_per_year = 1.0, 0.0, 0.0

    roll_max = equity.cummax()
    drawdown = (equity - roll_max) / roll_max
    max_dd   = float(drawdown.min() * 100) if len(drawdown) else 0.0

    avg_win  = float(np.mean(wins))   if wins   else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    expectancy = float(np.mean(pnls))

    breakeven_wr = 0.0
    if avg_win and avg_loss:
        rr = abs(avg_win / avg_loss)
        breakeven_wr = 100 / (1 + rr) if rr else 0

    # ── Risk-adjusted metrics ────────────────────────────────────────────────
    ppy = _periods_per_year(equity, tf)
    log_returns = np.log(equity / equity.shift(1)).dropna() if len(equity) > 1 else pd.Series(dtype=float)
    sharpe  = _sharpe(log_returns, ppy)
    sortino = _sortino(log_returns, ppy)
    calmar  = _calmar(cagr, max_dd)

    return {
        "strategy":               result.strategy,
        "symbol":                 result.symbol,
        "timeframe":              tf,
        "total_trades":           len(trades),
        "trades_per_year":        trades_per_year,
        "win_rate_pct":           round(win_rate, 2),
        "profit_factor":          round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        "total_return_pct":       round(total_return, 2),
        "cagr_pct":               round(cagr, 2),
        "max_drawdown_pct":       round(max_dd, 2),
        "sharpe":                 round(sharpe, 3),
        "sortino":                round(sortino, 3),
        "calmar":                 round(calmar, 3),
        "avg_win":                round(avg_win, 2),
        "avg_loss":               round(avg_loss, 2),
        "expectancy":             round(expectancy, 2),
        "approx_breakeven_wr_pct": round(breakeven_wr, 2),
        "final_equity":           round(final_equity, 2),
    }


def trades_to_dataframe(result: BacktestResult) -> pd.DataFrame:
    rows = []
    for t in result.trades:
        rows.append({
            "entry_time":  t.entry_time,
            "exit_time":   t.exit_time,
            "side":        t.side.value,
            "entry_price": t.entry_price,
            "exit_price":  t.exit_price,
            "stop_loss":   t.stop_loss,
            "take_profit": t.take_profit,
            "pnl":         round(t.pnl, 2),
            "pnl_pct":     round(t.pnl_pct * 100, 2),
            "reason":      t.reason,
            "exit_reason": t.exit_reason,
        })
    return pd.DataFrame(rows)
