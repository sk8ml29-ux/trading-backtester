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
    funding_lookback: int = 24
    min_funding: float = 0.00005
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
    slots: int = 6
    leverage: float = 1.5


def _read(path: Path, column: str) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["time"], index_col="time")
    out = pd.to_numeric(df[column], errors="coerce")
    out = out[~out.index.duplicated(keep="last")].sort_index()
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
    spot_raw = _read(sp, "close")
    perp_df = perp_df[~perp_df.index.duplicated(keep="last")].sort_index()
    perp_raw = pd.to_numeric(perp_df["close"], errors="coerce")
    volume_raw = (
        pd.to_numeric(perp_df["close"], errors="coerce")
        * pd.to_numeric(perp_df["volume"], errors="coerce")
    )
    # Funding can be stamped a few milliseconds after the nominal settlement.
    # Select only the latest kline that had actually closed by that timestamp.
    perp = perp_raw.reindex(funding.index, method="ffill").rename("perp")
    spot = spot_raw.reindex(funding.index, method="ffill").rename("spot")
    dollar_volume = volume_raw.reindex(funding.index, method="ffill").rename(
        "dollar_volume"
    )
    frame = pd.concat([funding, perp, spot, dollar_volume], axis=1)
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
            if not np.isfinite(r):
                # A missing observation is not an exit signal. Hold the prior
                # target until fresh funding and marks are available.
                if state[symbol]:
                    active.append(symbol)
                continue
            if state[symbol]:
                held[symbol] += 1
                if (
                    funding.at[ts, symbol] <= 0
                    or r <= 0
                    or basis.at[ts, symbol] < -0.005
                    or held[symbol] >= params.max_hold_steps
                ):
                    state[symbol] = False
                    held[symbol] = 0
            if state[symbol]:
                active.append(symbol)

        # Keep existing legs, then fill empty slots with the highest causal
        # expected reserve. This enforces the capital/position limit.
        capacity = max(params.slots - len(active), 0)
        if capacity:
            candidates = [
                symbol
                for symbol in reserve.columns
                if not state[symbol]
                and bool(eligible.at[ts, symbol])
                and reserve.at[ts, symbol] >= params.min_reserve
            ]
            candidates.sort(key=lambda s: reserve.at[ts, s], reverse=True)
            for symbol in candidates[:capacity]:
                state[symbol] = True
                held[symbol] = 0
                active.append(symbol)
        if active:
            weights.loc[ts, active] = params.leverage / params.slots
    return weights


def _simulate_steps(
    features: dict[str, pd.DataFrame],
    params: Params,
    turn_cost: float = PER_PAIR_TURN_COST,
) -> tuple[pd.Series, pd.DataFrame, dict]:
    """Run a two-leg ledger with fixed units between entry and exit."""
    weights = target_weights(features, params)
    funding = _panel(features, "funding").reindex(weights.index).fillna(0.0)
    perp = _panel(features, "perp").reindex(weights.index).ffill()
    spot = _panel(features, "spot").reindex(weights.index).ffill()
    step = pd.Series(0.0, index=weights.index)
    symbol_step = pd.DataFrame(0.0, index=weights.index, columns=weights.columns)
    funding_pnl = basis_pnl = trading_cost = turnover = 0.0
    positions: dict[str, tuple[float, float, float]] = {}
    perp_cost_share = 0.00060 / PER_PAIR_TURN_COST
    spot_cost_share = 1.0 - perp_cost_share

    for i, ts in enumerate(weights.index):
        # Mark the book held over (t-1, t), then credit settlement t. New
        # decisions below cannot receive this settlement.
        for symbol, (weight, spot_units, perp_units) in list(positions.items()):
            if i:
                spnl = spot_units * (spot.at[ts, symbol] - spot.iloc[i - 1][symbol])
                ppnl = -perp_units * (
                    perp.at[ts, symbol] - perp.iloc[i - 1][symbol]
                )
                fpnl = perp_units * perp.at[ts, symbol] * funding.at[ts, symbol]
                pair_pnl = spnl + ppnl
                step.at[ts] += pair_pnl + fpnl
                symbol_step.at[ts, symbol] += pair_pnl + fpnl
                basis_pnl += pair_pnl
                funding_pnl += fpnl

        desired = {s for s in weights.columns if weights.at[ts, s] > 0}
        current = set(positions)
        for symbol in current - desired:
            weight, spot_units, perp_units = positions.pop(symbol)
            spot_notional = spot_units * spot.at[ts, symbol]
            perp_notional = perp_units * perp.at[ts, symbol]
            cost = turn_cost * (
                spot_cost_share * spot_notional + perp_cost_share * perp_notional
            )
            step.at[ts] -= cost
            symbol_step.at[ts, symbol] -= cost
            trading_cost += cost
            turnover += (spot_notional + perp_notional) / 2
        for symbol in desired - current:
            weight = float(weights.at[ts, symbol])
            spot_units = weight / spot.at[ts, symbol]
            perp_units = weight / perp.at[ts, symbol]
            positions[symbol] = (weight, spot_units, perp_units)
            cost = weight * turn_cost
            step.at[ts] -= cost
            symbol_step.at[ts, symbol] -= cost
            trading_cost += cost
            turnover += weight

    # Charge a real exit at the final observed marks.
    if len(weights):
        ts = weights.index[-1]
        for symbol, (_, spot_units, perp_units) in positions.items():
            spot_notional = spot_units * spot.at[ts, symbol]
            perp_notional = perp_units * perp.at[ts, symbol]
            cost = turn_cost * (
                spot_cost_share * spot_notional + perp_cost_share * perp_notional
            )
            step.at[ts] -= cost
            symbol_step.at[ts, symbol] -= cost
            trading_cost += cost
            turnover += (spot_notional + perp_notional) / 2

    gross = weights.shift(1).fillna(0.0).abs().sum(axis=1)
    diag = {
        "funding_pnl": float(funding_pnl),
        "basis_pnl": float(basis_pnl),
        "trading_cost": float(trading_cost),
        "turnover": float(turnover),
        "avg_pair_notional": float(gross.mean()),
        "avg_gross_exposure": float(2 * gross.mean()),
        "active_symbols": int((weights.abs().sum(axis=0) > 0).sum()),
    }
    return step, symbol_step, diag


def simulate(
    features: dict[str, pd.DataFrame],
    params: Params,
    turn_cost: float | None = None,
) -> tuple[pd.Series, dict]:
    """Return daily portfolio returns and diagnostics."""
    cost = params.round_trip_cost / 2 if turn_cost is None else turn_cost
    step, _, diag = _simulate_steps(features, params, cost)
    # Ledger notionals are fractions of the original capital and remain fixed
    # until exit. Therefore P&L is additive; compounding would assume unmodelled
    # resizing of both legs.
    daily = step.groupby(step.index.floor("D")).sum()
    return daily, diag


def metrics(daily: pd.Series, benchmark: pd.Series | None = None) -> dict:
    dr = daily.replace([np.inf, -np.inf], np.nan).dropna()
    if len(dr) < 10:
        return {"n_days": int(len(dr))}
    equity = 1.0 + dr.cumsum()
    dd = equity / equity.cummax() - 1
    std = dr.std(ddof=0)
    beta = 0.0
    if benchmark is not None:
        aligned = pd.concat([dr, benchmark], axis=1, join="inner").dropna()
        if len(aligned) > 2 and aligned.iloc[:, 1].var(ddof=0) > 0:
            beta = aligned.cov(ddof=0).iloc[0, 1] / aligned.iloc[:, 1].var(ddof=0)
    elapsed_years = max(
        (dr.index[-1] - dr.index[0]).total_seconds() / (365.0 * 86400), 1 / 365
    )
    return {
        "n_days": int(len(dr)),
        "net_return_pct": round((equity.iloc[-1] - 1) * 100, 3),
        "ann_return_pct": round((equity.iloc[-1] - 1) / elapsed_years * 100, 3),
        "net_per_day_pct": round(dr.mean() * 100, 5),
        "sharpe": round(dr.mean() / std * math.sqrt(PER_YEAR), 3) if std else 0.0,
        "max_drawdown_pct": round(dd.min() * 100, 3),
        "worst_day_pct": round(dr.min() * 100, 3),
        "win_days_pct": round((dr > 0).mean() * 100, 2),
        "btc_beta": round(float(beta), 4),
    }


def sliced(features: dict[str, pd.DataFrame], lo: pd.Timestamp, hi: pd.Timestamp) -> dict:
    return {s: d[(d.index >= lo) & (d.index < hi)] for s, d in features.items()}


def evaluate(
    features: dict[str, pd.DataFrame],
    params: Params,
    folds: int = 6,
    validation_start: str = "2023-07-01",
) -> dict:
    idx = pd.Index([])
    for frame in features.values():
        idx = idx.union(frame.index)
    idx = idx.sort_values()
    validation_idx = idx[idx >= pd.Timestamp(validation_start)]
    if len(validation_idx) < folds:
        raise ValueError("validation period is too short for requested folds")
    boundaries = [
        validation_idx[int(i * len(validation_idx) / folds)] for i in range(folds)
    ]
    boundaries.append(validation_idx[-1] + pd.Timedelta(hours=8))
    step, symbol_step, diag = _simulate_steps(
        features, params, turn_cost=params.round_trip_cost / 2
    )
    fold_rows = []
    oos = []
    for i in range(folds):
        lo, hi = boundaries[i], boundaries[i + 1]
        # Exact settlement boundaries: no floor-to-day leakage. The strategy
        # runs continuously across reporting folds without synthetic exits.
        test_steps = step[(step.index >= lo) & (step.index < hi)]
        test = test_steps.groupby(test_steps.index.floor("D")).sum()
        oos.append(test_steps)
        fold_rows.append(
            {
                "fold": i + 1,
                "start": str(lo),
                "end": str(hi),
                "metrics": metrics(test),
            }
        )
    combined_steps = pd.concat(oos).sort_index()
    combined = combined_steps.groupby(combined_steps.index.floor("D")).sum()
    btc = features.get("BTCUSDT")
    benchmark = None
    if btc is not None:
        benchmark = btc["spot"].resample("1D").last().pct_change(fill_method=None)
    combined_metrics = metrics(combined, benchmark)
    oos_symbol_pnl = symbol_step.loc[combined_steps.index].sum().sort_values(
        ascending=False
    )
    positive_total = float(oos_symbol_pnl.clip(lower=0).sum())
    top_profit_share = (
        float(oos_symbol_pnl.iloc[0] / positive_total) if positive_total > 0 else 1.0
    )
    negative_folds = sum(r["metrics"].get("net_return_pct", 0) < 0 for r in fold_rows)
    active_folds = sum(
        r["metrics"].get("net_return_pct", 0) != 0 for r in fold_rows
    )
    verdict = {
        "positive": bool(combined_metrics.get("net_return_pct", 0) > 0),
        "annual_return_ge_5": bool(combined_metrics.get("ann_return_pct", 0) >= 5),
        "sharpe_ge_1_5": bool(combined_metrics.get("sharpe", 0) >= 1.5),
        "max_drawdown_le_5": bool(
            abs(combined_metrics.get("max_drawdown_pct", 100)) <= 5
        ),
        "abs_btc_beta_le_0_10": bool(
            abs(combined_metrics.get("btc_beta", 100)) <= 0.10
        ),
        "negative_folds_le_1": bool(negative_folds <= 1),
        "active_folds_ge_4": bool(active_folds >= 4),
        "top_symbol_profit_share_le_25pct": bool(top_profit_share <= 0.25),
    }
    verdict["pass"] = all(verdict.values())
    return {
        "strategy": "settlement_memory_reserve_carry",
        "params": asdict(params),
        "parameter_selection_cutoff": str(
            pd.Timestamp(validation_start) - pd.Timedelta(microseconds=1)
        ),
        "post_training_start": str(pd.Timestamp(validation_start)),
        "folds": fold_rows,
        "combined_post_training": combined_metrics,
        "post_training_symbol_net_pnl": {
            symbol: round(float(value), 6)
            for symbol, value in oos_symbol_pnl.items()
        },
        "top_symbol_positive_profit_share": round(top_profit_share, 4),
        "diagnostics_full_history": diag,
        "verdict": verdict,
        "production_ready": False,
        "research_limitations": [
            "The post-training period was inspected during development and is not untouched OOS.",
            "Static downloaded symbol basket excludes historical delistings and retains survivorship risk.",
            "Historical fills cannot prove two-leg execution quality on a live venue.",
            "Paper-forward validation is required before any capital decision.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(BASKET))
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--validation-start", default="2023-07-01")
    parser.add_argument("--cost-multiplier", type=float, default=1.0)
    parser.add_argument("--out")
    args = parser.parse_args()

    params = Params(round_trip_cost=2 * PER_PAIR_TURN_COST * args.cost_multiplier)
    data = load_all([s.strip().upper() for s in args.symbols.split(",") if s.strip()])
    if not data:
        raise SystemExit("No Vision data. Run: python -m research.binance_vision")
    print(f"loaded {len(data)} symbols")
    result = evaluate(
        build_features(data, params), params, args.folds, args.validation_start
    )
    print(json.dumps(result, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
