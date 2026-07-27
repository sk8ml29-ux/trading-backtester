"""Settlement Memory Reserve Carry (SMRC).

An original, falsifiable delta-neutral carry mechanism:

* hold long spot / short perpetual only (no short-spot borrow);
* estimate how many positive funding settlements remain from an expanding,
  point-in-time survival table of funding streaks;
* enter only when expected remaining carry plus conservative basis convergence
  exceeds the complete round-trip trading reserve;
* select liquid contracts from trailing information only.

The position decided immediately after settlement ``t`` first earns funding at
``t+1``. Prices are indexed by kline close availability, never kline open time.
This module is research/paper software; it does not place orders.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

from research.binance_vision import BASKET, CACHE

PER_PAIR_TURN_COST = 0.0015  # perp + spot, one-way
PER_YEAR = 365


@dataclass(frozen=True)
class Params:
    funding_lookback: int = 18
    min_funding: float = 0.00010
    min_reserve: float = 0.00050
    round_trip_cost: float = 2 * PER_PAIR_TURN_COST
    basis_capture: float = 0.50
    max_basis: float = 0.010
    max_hold_steps: int = 90
    survival_horizon: int = 36
    survival_prior: float = 0.70
    min_history_steps: int = 180 * 3
    liquidity_lookback: int = 90
    top_liquid: int = 20
    slots: int = 10
    leverage: float = 1.0


def _read(path: Path, column: str) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["time"], index_col="time")
    out = pd.to_numeric(df[column], errors="coerce")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index = out.index.round("h")
    return out


def load_coin(symbol: str) -> pd.DataFrame | None:
    """Load a point-in-time aligned funding/perp/spot frame."""
    stem = symbol.lower()
    fp = CACHE / f"vision_funding_{stem}.csv"
    pp = CACHE / f"vision_perp_{stem}_8h.csv"
    sp = CACHE / f"vision_spot_{stem}_8h.csv"
    if not (fp.exists() and pp.exists() and sp.exists()):
        return None

    funding = _read(fp, "funding_rate").rename("funding")
    perp_df = pd.read_csv(pp, parse_dates=["time"], index_col="time")
    spot = _read(sp, "close").rename("spot")
    perp_df.index = perp_df.index.round("h")
    perp_df = perp_df[~perp_df.index.duplicated(keep="last")].sort_index()
    perp = pd.to_numeric(perp_df["close"], errors="coerce").rename("perp")
    dollar_volume = (
        pd.to_numeric(perp_df["close"], errors="coerce")
        * pd.to_numeric(perp_df["volume"], errors="coerce")
    ).rename("dollar_volume")
    frame = pd.concat([funding, perp, spot, dollar_volume], axis=1, join="inner")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    if len(frame) < 300:
        return None
    return frame


def load_all(symbols: list[str]) -> dict[str, pd.DataFrame]:
    return {s: d for s in symbols if (d := load_coin(s)) is not None}


def _survival_reserve(funding: pd.Series, params: Params) -> tuple[np.ndarray, np.ndarray]:
    """Point-in-time expected positive-settlement count and funding forecast.

    Transition counts are updated only after the transition is observed. Beta
    shrinkage prevents one early streak from producing an extreme estimate.
    """
    f = funding.to_numpy(float)
    n = len(f)
    max_age = params.survival_horizon * 2
    exposures = np.zeros(max_age + 1)
    successes = np.zeros(max_age + 1)
    expected = np.zeros(n)
    forecast = (
        funding.rolling(params.funding_lookback, min_periods=params.funding_lookback)
        .median()
        .to_numpy()
    )
    age = 0
    prior_strength = 10.0

    for i in range(n):
        positive = f[i] > 0
        if i and age:
            bucket = min(age, max_age)
            exposures[bucket] += 1
            successes[bucket] += float(positive)
        age = age + 1 if positive else 0
        if not age:
            continue

        alive = 1.0
        remaining = 0.0
        for step in range(1, params.survival_horizon + 1):
            bucket = min(age + step - 1, max_age)
            q = (
                successes[bucket] + params.survival_prior * prior_strength
            ) / (exposures[bucket] + prior_strength)
            alive *= q
            remaining += alive
        expected[i] = remaining
    return expected, forecast


def build_features(data: dict[str, pd.DataFrame], params: Params) -> dict[str, pd.DataFrame]:
    out = {}
    for symbol, raw in data.items():
        df = raw.copy()
        expected, forecast = _survival_reserve(df["funding"], params)
        df["expected_remaining"] = expected
        df["funding_forecast"] = forecast
        df["basis"] = df["perp"] / df["spot"] - 1.0
        df["liquidity"] = (
            df["dollar_volume"]
            .rolling(params.liquidity_lookback, min_periods=params.liquidity_lookback)
            .median()
        )
        df["age"] = np.arange(len(df))
        basis_credit = df["basis"].clip(lower=0.0, upper=params.max_basis)
        df["reserve"] = (
            df["funding_forecast"].clip(lower=0.0) * df["expected_remaining"]
            + params.basis_capture * basis_credit
            - params.round_trip_cost
        )
        out[symbol] = df
    return out


def _panel(features: dict[str, pd.DataFrame], column: str) -> pd.DataFrame:
    return pd.DataFrame({s: d[column] for s, d in features.items()}).sort_index()


def target_weights(features: dict[str, pd.DataFrame], params: Params) -> pd.DataFrame:
    """Generate post-settlement target weights using information at that time."""
    reserve = _panel(features, "reserve")
    forecast = _panel(features, "funding_forecast").reindex(reserve.index)
    funding = _panel(features, "funding").reindex(reserve.index)
    basis = _panel(features, "basis").reindex(reserve.index)
    liquidity = _panel(features, "liquidity").reindex(reserve.index)
    age = _panel(features, "age").reindex(reserve.index)

    eligible = (
        (age >= params.min_history_steps)
        & (forecast >= params.min_funding)
        & (funding > 0)
        & (basis >= 0)
        & (basis <= params.max_basis)
        & liquidity.notna()
    )
    liquid_rank = liquidity.where(eligible).rank(
        axis=1, ascending=False, method="first"
    )
    eligible &= liquid_rank <= params.top_liquid

    weights = pd.DataFrame(0.0, index=reserve.index, columns=reserve.columns)
    state = {s: False for s in reserve.columns}
    held = {s: 0 for s in reserve.columns}
    for ts in reserve.index:
        active = []
        for symbol in reserve.columns:
            r = reserve.at[ts, symbol]
            if state[symbol]:
                held[symbol] += 1
                if (
                    not np.isfinite(r)
                    or funding.at[ts, symbol] <= 0
                    or r <= 0
                    or basis.at[ts, symbol] < -0.005
                    or held[symbol] >= params.max_hold_steps
                ):
                    state[symbol] = False
                    held[symbol] = 0
            elif bool(eligible.at[ts, symbol]) and r >= params.min_reserve:
                state[symbol] = True
                held[symbol] = 0
            if state[symbol]:
                active.append(symbol)
        denominator = max(len(active), params.slots)
        if active:
            weights.loc[ts, active] = params.leverage / denominator
    return weights


def simulate(
    features: dict[str, pd.DataFrame],
    params: Params,
    turn_cost: float = PER_PAIR_TURN_COST,
) -> tuple[pd.Series, dict]:
    """Return daily portfolio returns and diagnostics."""
    weights = target_weights(features, params)
    funding = _panel(features, "funding").reindex(weights.index).fillna(0.0)
    perp = _panel(features, "perp").reindex(weights.index)
    spot = _panel(features, "spot").reindex(weights.index)
    perp_ret = perp.pct_change(fill_method=None).fillna(0.0)
    spot_ret = spot.pct_change(fill_method=None).fillna(0.0)

    # A target chosen after t cannot receive settlement t. Shift one complete
    # interval; it earns funding and basis movement at t+1.
    held = weights.shift(1).fillna(0.0)
    funding_pnl = (held * funding).sum(axis=1)
    basis_pnl = (held * (spot_ret - perp_ret)).sum(axis=1)
    turnover = (weights - held).abs().sum(axis=1)
    trading_cost = turnover * turn_cost
    step = funding_pnl + basis_pnl - trading_cost

    # Liquidate at the evaluation boundary; otherwise every segment gets a free
    # exit and walk-forward results are biased upward.
    if len(step) and weights.iloc[-1].abs().sum() > 0:
        step.iloc[-1] -= weights.iloc[-1].abs().sum() * turn_cost
    daily = (1.0 + step).groupby(step.index.floor("D")).prod() - 1.0
    diag = {
        "funding_pnl": float(funding_pnl.sum()),
        "basis_pnl": float(basis_pnl.sum()),
        "trading_cost": float(trading_cost.sum()),
        "turnover": float(turnover.sum()),
        "avg_gross": float(held.abs().sum(axis=1).mean()),
        "active_symbols": int((weights.abs().sum(axis=0) > 0).sum()),
    }
    return daily, diag


def metrics(daily: pd.Series, benchmark: pd.Series | None = None) -> dict:
    dr = daily.replace([np.inf, -np.inf], np.nan).dropna()
    if len(dr) < 10:
        return {"n_days": int(len(dr))}
    equity = (1 + dr).cumprod()
    dd = equity / equity.cummax() - 1
    std = dr.std(ddof=0)
    beta = 0.0
    if benchmark is not None:
        aligned = pd.concat([dr, benchmark], axis=1, join="inner").dropna()
        if len(aligned) > 2 and aligned.iloc[:, 1].var(ddof=0) > 0:
            beta = aligned.cov(ddof=0).iloc[0, 1] / aligned.iloc[:, 1].var(ddof=0)
    return {
        "n_days": int(len(dr)),
        "net_return_pct": round((equity.iloc[-1] - 1) * 100, 3),
        "ann_return_pct": round((equity.iloc[-1] ** (PER_YEAR / len(dr)) - 1) * 100, 3),
        "net_per_day_pct": round(dr.mean() * 100, 5),
        "sharpe": round(dr.mean() / std * math.sqrt(PER_YEAR), 3) if std else 0.0,
        "max_drawdown_pct": round(dd.min() * 100, 3),
        "worst_day_pct": round(dr.min() * 100, 3),
        "win_days_pct": round((dr > 0).mean() * 100, 2),
        "btc_beta": round(float(beta), 4),
    }


def sliced(features: dict[str, pd.DataFrame], lo: pd.Timestamp, hi: pd.Timestamp) -> dict:
    return {s: d[(d.index >= lo) & (d.index < hi)] for s, d in features.items()}


def evaluate(features: dict[str, pd.DataFrame], params: Params, folds: int = 6) -> dict:
    idx = pd.Index([])
    for frame in features.values():
        idx = idx.union(frame.index)
    idx = idx.sort_values()
    boundaries = [idx[int(i * len(idx) / (folds + 1))] for i in range(1, folds + 1)]
    boundaries.append(idx[-1] + pd.Timedelta(hours=8))
    start = idx[0]
    fold_rows = []
    oos = []
    for i in range(folds):
        lo, hi = boundaries[i], boundaries[i + 1]
        segment = sliced(features, start, hi)
        # Preserve expanding feature/state history, then score only this OOS block.
        daily, diag = simulate(segment, params)
        test = daily[(daily.index >= lo.floor("D")) & (daily.index < hi.floor("D"))]
        oos.append(test)
        fold_rows.append(
            {
                "fold": i + 1,
                "start": str(lo),
                "end": str(hi),
                "metrics": metrics(test),
                "diagnostics": diag,
            }
        )
    combined = pd.concat(oos).groupby(level=0).last().sort_index()
    btc = features.get("BTCUSDT")
    benchmark = None
    if btc is not None:
        benchmark = btc["spot"].resample("1D").last().pct_change(fill_method=None)
    combined_metrics = metrics(combined, benchmark)
    losing_folds = sum(r["metrics"].get("net_return_pct", 0) <= 0 for r in fold_rows)
    verdict = {
        "positive": combined_metrics.get("net_return_pct", 0) > 0,
        "annual_return_ge_5": combined_metrics.get("ann_return_pct", 0) >= 5,
        "sharpe_ge_1_5": combined_metrics.get("sharpe", 0) >= 1.5,
        "max_drawdown_le_5": abs(combined_metrics.get("max_drawdown_pct", 100)) <= 5,
        "abs_btc_beta_le_0_10": abs(combined_metrics.get("btc_beta", 100)) <= 0.10,
        "losing_folds_le_1": losing_folds <= 1,
    }
    verdict["pass"] = all(verdict.values())
    return {
        "strategy": "settlement_memory_reserve_carry",
        "params": asdict(params),
        "folds": fold_rows,
        "combined_oos": combined_metrics,
        "verdict": verdict,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(BASKET))
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--cost-multiplier", type=float, default=1.0)
    parser.add_argument("--out")
    args = parser.parse_args()

    params = Params(round_trip_cost=2 * PER_PAIR_TURN_COST * args.cost_multiplier)
    data = load_all([s.strip().upper() for s in args.symbols.split(",") if s.strip()])
    if not data:
        raise SystemExit("No Vision data. Run: python -m research.binance_vision")
    print(f"loaded {len(data)} symbols")
    result = evaluate(build_features(data, params), params, args.folds)
    print(json.dumps(result, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
