"""
daytrade_lab.py — self-contained, fast, honest intraday research harness.

Why this exists
---------------
The repo's `run_backtest.py` clamps intraday history to ~60 days (a Yahoo-era
assumption) which throws away years of high-quality Binance data. This module
loads the Binance CSVs directly from ``data/cache`` (full history), runs a fast
numpy event-driven simulator with realistic costs, and reports the *daily*
statistics the mandate asks for (net return/day, % winning days, Sharpe on daily
returns, max drawdown, trade count, hit-rate, avg win/loss, cost share) — plus a
proper rolling walk-forward that picks parameters in-sample and measures them
out-of-sample.

Everything here is additive (new file); the core engine/strategies are untouched.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"

# ---------------------------------------------------------------------------
# Cost model (per side, fraction of price). Conservative for Binance majors.
#   commission : taker fee (0.05% is a realistic fee-tier / BNB-discount level;
#                stress-tested separately at 0.075% spot default).
#   slippage   : adverse fill for a market order of modest size.
#   half_spread: half the bid/ask spread paid on each side.
# Round-trip friction on a major ~ 2*(0.05+0.02+0.02) = 0.18%.
# ---------------------------------------------------------------------------
@dataclass
class Costs:
    commission: float = 0.0005
    slippage: float = 0.0002
    half_spread: float = 0.0002

    @property
    def per_side(self) -> float:
        return self.slippage + self.half_spread


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load(symbol_file: str) -> pd.DataFrame:
    """Load a cache CSV by base name (without .csv), e.g. 'binance_btc_usd_15m'."""
    path = CACHE / f"{symbol_file}.csv"
    df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")
    df = df[~df.index.duplicated(keep="first")].sort_index()
    df.index = pd.to_datetime(df.index)
    return df[["open", "high", "low", "close", "volume"]].dropna()


def slice_dates(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    out = df
    if start:
        out = out[out.index >= pd.Timestamp(start)]
    if end:
        out = out[out.index < pd.Timestamp(end)]
    return out


# ---------------------------------------------------------------------------
# Vectorized indicators (numpy/pandas)
# ---------------------------------------------------------------------------
def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0.0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def rolling_z(s: pd.Series, period: int) -> pd.Series:
    m = s.rolling(period).mean()
    sd = s.rolling(period).std(ddof=0)
    return (s - m) / sd.replace(0.0, np.nan)


# ---------------------------------------------------------------------------
# Trade record + fast simulator
# ---------------------------------------------------------------------------
@dataclass
class SimResult:
    entry_ts: np.ndarray          # datetime64[ns]
    exit_ts: np.ndarray
    pnl: np.ndarray               # currency PnL per trade (after costs)
    ret_pct: np.ndarray           # trade return as fraction of price move (net)
    equity_final: float
    n_trades: int
    gross_pnl: float
    cost_pnl: float
    trades_detail: list = field(default_factory=list)


def simulate(
    df: pd.DataFrame,
    long_sig: np.ndarray,
    short_sig: np.ndarray,
    sl_atr: float,
    tp_atr: float,
    max_hold: int,
    costs: Costs,
    initial: float = 10_000.0,
    risk_frac: float = 0.01,
    allow_short: bool = True,
    trail_atr: float = 0.0,
    compound: bool = True,
) -> SimResult:
    """
    Event simulator, one position at a time.
    Signals are evaluated on bar i (closed) and the position is opened at bar
    i+1 OPEN. Stops/targets are checked intrabar on high/low; if both are touched
    in the same bar the STOP is assumed first (worst case). Costs applied to
    entry and exit fills. Position size from ATR stop distance and risk_frac.

    ``compound=False`` sizes every trade off the fixed ``initial`` base, giving a
    stationary, additive PnL stream that is honest to aggregate across
    walk-forward folds (no compounding illusion).
    """
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    a = df["atr"].to_numpy(float)
    ts = df.index.values
    n = len(df)

    ps = costs.per_side
    fee = costs.commission

    equity = initial
    gross_total = 0.0
    cost_total = 0.0
    entries, exits, pnls, rets, detail = [], [], [], [], []

    i = 0
    while i < n - 1:
        go_long = long_sig[i]
        go_short = short_sig[i] and allow_short
        if not (go_long or go_short) or not np.isfinite(a[i]) or a[i] <= 0:
            i += 1
            continue

        side = 1 if go_long else -1
        entry_idx = i + 1
        entry_price = o[entry_idx]
        atr_at = a[i]
        if side == 1:
            stop = entry_price - sl_atr * atr_at
            target = entry_price + tp_atr * atr_at
        else:
            stop = entry_price + sl_atr * atr_at
            target = entry_price - tp_atr * atr_at

        stop_dist = abs(entry_price - stop)
        if stop_dist <= 0:
            i += 1
            continue
        base = equity if compound else initial
        size = (base * risk_frac) / stop_dist

        # entry fill (adverse)
        entry_fill = entry_price * (1 + ps) if side == 1 else entry_price * (1 - ps)

        exit_price = None
        exit_idx = None
        best = entry_price  # for trailing
        j = entry_idx
        end_j = min(entry_idx + max_hold, n - 1)
        while j <= end_j:
            hi, lo, cl = h[j], l[j], c[j]
            if side == 1:
                # trailing stop update
                if trail_atr > 0:
                    best = max(best, hi)
                    stop = max(stop, best - trail_atr * atr_at)
                if lo <= stop:
                    exit_price = stop
                    exit_idx = j
                    break
                if hi >= target:
                    exit_price = target
                    exit_idx = j
                    break
            else:
                if trail_atr > 0:
                    best = min(best, lo)
                    stop = min(stop, best + trail_atr * atr_at)
                if hi >= stop:
                    exit_price = stop
                    exit_idx = j
                    break
                if lo <= target:
                    exit_price = target
                    exit_idx = j
                    break
            j += 1

        if exit_price is None:
            exit_idx = end_j
            exit_price = c[end_j]

        exit_fill = exit_price * (1 - ps) if side == 1 else exit_price * (1 + ps)
        if side == 1:
            gross = (exit_fill - entry_fill) * size
        else:
            gross = (entry_fill - exit_fill) * size
        commission = (entry_fill + exit_fill) * size * fee
        friction = (abs(entry_price - entry_fill) + abs(exit_price - exit_fill)) * size
        net = gross - commission
        equity += net
        gross_total += (exit_price - entry_price) * size * side
        cost_total += commission + friction

        entries.append(ts[entry_idx])
        exits.append(ts[exit_idx])
        pnls.append(net)
        rets.append(side * (exit_fill - entry_fill) / entry_fill)
        detail.append(
            dict(
                entry_ts=str(pd.Timestamp(ts[entry_idx])),
                exit_ts=str(pd.Timestamp(ts[exit_idx])),
                side="long" if side == 1 else "short",
                entry=float(entry_price),
                exit=float(exit_price),
                pnl=float(net),
            )
        )

        i = exit_idx + 1  # no re-entry until the bar after exit

    return SimResult(
        entry_ts=np.array(entries, dtype="datetime64[ns]"),
        exit_ts=np.array(exits, dtype="datetime64[ns]"),
        pnl=np.array(pnls, dtype=float),
        ret_pct=np.array(rets, dtype=float),
        equity_final=equity,
        n_trades=len(pnls),
        gross_pnl=gross_total,
        cost_pnl=cost_total,
        trades_detail=detail,
    )


# ---------------------------------------------------------------------------
# Daily metrics from a set of trades on a single shared account
# ---------------------------------------------------------------------------
def daily_metrics(
    exit_ts: np.ndarray,
    pnl: np.ndarray,
    initial: float,
    periods_per_year: int = 365,
    base: float | None = None,
) -> dict:
    """If ``base`` is given, daily returns are computed against that fixed base
    (stationary, additive). Otherwise they compound off a growing equity curve."""
    if len(pnl) == 0:
        return dict(
            n_trades=0, net_return_pct=0.0, net_per_day_pct=0.0, win_days_pct=0.0,
            active_win_days_pct=0.0, sharpe=0.0, sortino=0.0, max_dd_pct=0.0,
            hit_rate_pct=0.0, avg_win=0.0, avg_loss=0.0, payoff=0.0,
            worst_day_pct=0.0, n_days=0, n_active_days=0, final_equity=initial,
        )
    df = pd.DataFrame({"exit": pd.to_datetime(exit_ts), "pnl": pnl})
    df["day"] = df["exit"].dt.floor("D")
    # realized PnL per calendar day, forward equity
    daily_pnl = df.groupby("day")["pnl"].sum()
    full_range = pd.date_range(daily_pnl.index.min(), daily_pnl.index.max(), freq="D")
    daily_pnl = daily_pnl.reindex(full_range, fill_value=0.0)

    equity = initial + daily_pnl.cumsum()
    if base is not None:
        daily_ret = daily_pnl / base
    else:
        equity_prev = equity.shift(1).fillna(initial)
        daily_ret = daily_pnl / equity_prev

    n_days = len(daily_ret)
    active = daily_pnl != 0.0
    n_active = int(active.sum())
    win_days = (daily_ret > 0).sum()
    active_win = ((daily_ret > 0) & active).sum()

    mean_r = daily_ret.mean()
    std_r = daily_ret.std(ddof=0)
    downside = daily_ret[daily_ret < 0]
    dstd = downside.std(ddof=0) if len(downside) else 0.0
    sharpe = (mean_r / std_r * math.sqrt(periods_per_year)) if std_r > 0 else 0.0
    sortino = (mean_r / dstd * math.sqrt(periods_per_year)) if dstd > 0 else 0.0

    roll_max = equity.cummax()
    dd = (equity - roll_max) / roll_max
    max_dd = float(dd.min()) * 100

    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    hit = len(wins) / len(pnl) * 100
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

    final_equity = float(equity.iloc[-1])
    net_return = (final_equity / initial - 1) * 100

    return dict(
        n_trades=int(len(pnl)),
        net_return_pct=round(net_return, 2),
        net_per_day_pct=round(float(mean_r) * 100, 4),
        win_days_pct=round(win_days / n_days * 100, 2),
        active_win_days_pct=round(active_win / n_active * 100, 2) if n_active else 0.0,
        sharpe=round(float(sharpe), 3),
        sortino=round(float(sortino), 3),
        max_dd_pct=round(max_dd, 2),
        hit_rate_pct=round(hit, 2),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        payoff=round(payoff, 3),
        worst_day_pct=round(float(daily_ret.min()) * 100, 3),
        n_days=int(n_days),
        n_active_days=n_active,
        final_equity=round(final_equity, 2),
    )


def cost_share(res: SimResult) -> float:
    """Costs as a fraction of gross profit (only meaningful when gross>0)."""
    if res.gross_pnl <= 0:
        return float("inf")
    return round(res.cost_pnl / res.gross_pnl * 100, 1)
