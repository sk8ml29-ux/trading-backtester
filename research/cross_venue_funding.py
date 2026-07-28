"""Cross-Venue Funding Dispersion (CVFD) research simulator.

Hold equal coin units in opposite perpetual positions on Binance and OKX:

* positive spread (Binance funding > OKX): short Binance, long OKX;
* negative spread: long Binance, short OKX.

The strategy is direction-neutral but not risk-free. Venue-basis divergence,
liquidation, counterparty failure and asynchronous execution remain material.
Parameters below were pre-registered before inspecting the OKX history.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from research.binance_vision import CACHE as BINANCE_CACHE
from research.okx_data import CACHE as OKX_CACHE, BASKET
from research.settlement_memory_carry import _survival_reserve

PER_YEAR = 365
BINANCE_ONE_WAY = 0.00060
OKX_ONE_WAY = 0.00070
PAIR_ONE_WAY = BINANCE_ONE_WAY + OKX_ONE_WAY


@dataclass(frozen=True)
class Params:
    spread_lookback: int = 9
    min_spread: float = 0.00010
    min_reserve: float = 0.00050
    round_trip_cost: float = 2 * PAIR_ONE_WAY
    basis_capture: float = 0.25
    max_entry_basis: float = 0.005
    max_exit_basis: float = 0.020
    survival_horizon: int = 24
    survival_prior: float = 0.65
    min_history_steps: int = 90
    liquidity_lookback: int = 90
    slots: int = 5
    leverage: float = 2.0
    max_hold_steps: int = 90


def _read(path: Path, column: str) -> pd.Series:
    frame = pd.read_csv(path, parse_dates=["time"], index_col="time")
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    return series[~series.index.duplicated(keep="last")].sort_index()


def _funding_on_binance_grid(
    binance: pd.Series, okx: pd.Series
) -> pd.DataFrame:
    """Aggregate all OKX settlements into each Binance funding interval."""
    b = binance.copy()
    o = okx.copy()
    b.index = b.index.round("h")
    o.index = o.index.round("h")
    b = b.groupby(level=0).last().sort_index()
    o = o.groupby(level=0).sum().sort_index()
    rows = []
    previous = b.index[0] - pd.Timedelta(hours=8)
    for timestamp, rate in b.items():
        okx_rate = float(o[(o.index > previous) & (o.index <= timestamp)].sum())
        rows.append((timestamp, float(rate), okx_rate))
        previous = timestamp
    return pd.DataFrame(
        rows, columns=["time", "binance_funding", "okx_funding"]
    ).set_index("time")


def load_pair(coin: str) -> pd.DataFrame | None:
    symbol = f"{coin}USDT"
    bf = BINANCE_CACHE / f"vision_funding_{symbol.lower()}.csv"
    bp = BINANCE_CACHE / f"vision_perp_{symbol.lower()}_8h.csv"
    of = OKX_CACHE / f"okx_funding_{coin.lower()}.csv"
    op = OKX_CACHE / f"okx_swap_{coin.lower()}_4H.csv"
    if not all(path.exists() for path in (bf, bp, of, op)):
        return None

    funding = _funding_on_binance_grid(
        _read(bf, "funding_rate"), _read(of, "funding_rate")
    )
    binance_frame = pd.read_csv(bp, parse_dates=["time"], index_col="time")
    okx_frame = pd.read_csv(op, parse_dates=["time"], index_col="time")
    for frame in (binance_frame, okx_frame):
        frame.sort_index(inplace=True)
        frame.drop_duplicates(inplace=True)

    index = funding.index
    binance_price = (
        pd.to_numeric(binance_frame["close"], errors="coerce")
        .reindex(index, method="ffill")
        .rename("binance_price")
    )
    okx_price = (
        pd.to_numeric(okx_frame["close"], errors="coerce")
        .reindex(index, method="ffill")
        .rename("okx_price")
    )
    binance_dollar_volume = (
        pd.to_numeric(binance_frame["close"], errors="coerce")
        * pd.to_numeric(binance_frame["volume"], errors="coerce")
    ).reindex(index, method="ffill")
    # OKX volume is in base currency for spot/swap history.
    okx_dollar_volume = (
        pd.to_numeric(okx_frame["close"], errors="coerce")
        * pd.to_numeric(okx_frame["volume"], errors="coerce")
    ).reindex(index, method="ffill")
    frame = pd.concat(
        [
            funding,
            binance_price,
            okx_price,
            binance_dollar_volume.rename("binance_dollar_volume"),
            okx_dollar_volume.rename("okx_dollar_volume"),
        ],
        axis=1,
    ).replace([np.inf, -np.inf], np.nan).dropna()
    return frame if len(frame) >= 300 else None


def load_all(coins: list[str]) -> dict[str, pd.DataFrame]:
    return {coin: data for coin in coins if (data := load_pair(coin)) is not None}


def build_features(data: dict[str, pd.DataFrame], params: Params) -> dict[str, pd.DataFrame]:
    features = {}
    survival_params = type(
        "SurvivalParams",
        (),
        {
            "survival_horizon": params.survival_horizon,
            "survival_prior": params.survival_prior,
            "funding_lookback": params.spread_lookback,
        },
    )()
    for coin, raw in data.items():
        frame = raw.copy()
        spread = frame["binance_funding"] - frame["okx_funding"]
        positive_remaining, _ = _survival_reserve(spread, survival_params)
        negative_remaining, _ = _survival_reserve(-spread, survival_params)
        forecast = spread.rolling(
            params.spread_lookback, min_periods=params.spread_lookback
        ).median()
        side = np.sign(forecast).fillna(0.0)
        expected = np.where(side >= 0, positive_remaining, negative_remaining)
        basis = frame["binance_price"] / frame["okx_price"] - 1.0
        aligned_basis = side * basis
        liquidity = pd.concat(
            [frame["binance_dollar_volume"], frame["okx_dollar_volume"]], axis=1
        ).min(axis=1)
        frame["spread"] = spread
        frame["forecast"] = forecast
        frame["side"] = side
        frame["basis"] = basis
        frame["liquidity"] = liquidity.rolling(
            params.liquidity_lookback,
            min_periods=params.liquidity_lookback,
        ).median()
        frame["age"] = np.arange(len(frame))
        frame["reserve"] = (
            forecast.abs() * expected
            + params.basis_capture * aligned_basis.clip(lower=0.0)
            - params.round_trip_cost
        )
        features[coin] = frame
    return features


def _panel(features: dict[str, pd.DataFrame], column: str) -> pd.DataFrame:
    return pd.DataFrame({coin: frame[column] for coin, frame in features.items()})


def target_sides(features: dict[str, pd.DataFrame], params: Params) -> pd.DataFrame:
    reserve = _panel(features, "reserve")
    forecast = _panel(features, "forecast").reindex(reserve.index)
    side = _panel(features, "side").reindex(reserve.index)
    basis = _panel(features, "basis").reindex(reserve.index)
    liquidity = _panel(features, "liquidity").reindex(reserve.index)
    age = _panel(features, "age").reindex(reserve.index)
    eligible = (
        (age >= params.min_history_steps)
        & (forecast.abs() >= params.min_spread)
        & ((side * basis).abs() <= params.max_entry_basis)
        & liquidity.notna()
        & (reserve >= params.min_reserve)
    )

    targets = pd.DataFrame(0.0, index=reserve.index, columns=reserve.columns)
    states = {coin: 0.0 for coin in reserve.columns}
    held = {coin: 0 for coin in reserve.columns}
    for timestamp in reserve.index:
        active = []
        exited = set()
        for coin in reserve.columns:
            if not states[coin]:
                continue
            if not np.isfinite(reserve.at[timestamp, coin]):
                active.append(coin)
                continue
            held[coin] += 1
            if (
                side.at[timestamp, coin] != states[coin]
                or reserve.at[timestamp, coin] <= 0
                or abs(basis.at[timestamp, coin]) >= params.max_exit_basis
                or held[coin] >= params.max_hold_steps
            ):
                states[coin] = 0.0
                held[coin] = 0
                exited.add(coin)
            else:
                active.append(coin)

        capacity = max(params.slots - len(active), 0)
        candidates = [
            coin
            for coin in reserve.columns
            if not states[coin]
            and coin not in exited
            and bool(eligible.at[timestamp, coin])
        ]
        candidates.sort(key=lambda coin: reserve.at[timestamp, coin], reverse=True)
        for coin in candidates[:capacity]:
            states[coin] = float(side.at[timestamp, coin])
            active.append(coin)
        for coin in active:
            targets.at[timestamp, coin] = states[coin] * params.leverage / params.slots
    return targets


def simulate(
    features: dict[str, pd.DataFrame],
    params: Params,
    cost_multiplier: float = 1.0,
) -> tuple[pd.Series, pd.DataFrame, dict]:
    targets = target_sides(features, params)
    bp = _panel(features, "binance_price").reindex(targets.index).ffill()
    op = _panel(features, "okx_price").reindex(targets.index).ffill()
    bf = _panel(features, "binance_funding").reindex(targets.index).fillna(0.0)
    of = _panel(features, "okx_funding").reindex(targets.index).fillna(0.0)
    symbol_step = pd.DataFrame(0.0, index=targets.index, columns=targets.columns)
    positions: dict[str, tuple[float, float, float]] = {}
    funding_pnl = basis_pnl = trading_cost = turnover = 0.0

    for i, timestamp in enumerate(targets.index):
        for coin, (signed_weight, binance_units, okx_units) in list(positions.items()):
            side = np.sign(signed_weight)
            if i:
                price_pnl = side * (
                    okx_units * (op.at[timestamp, coin] - op.iloc[i - 1][coin])
                    - binance_units * (bp.at[timestamp, coin] - bp.iloc[i - 1][coin])
                )
                carry = side * (
                    binance_units * bp.at[timestamp, coin] * bf.at[timestamp, coin]
                    - okx_units * op.at[timestamp, coin] * of.at[timestamp, coin]
                )
                symbol_step.at[timestamp, coin] += price_pnl + carry
                basis_pnl += price_pnl
                funding_pnl += carry

        desired = {
            coin: float(targets.at[timestamp, coin])
            for coin in targets.columns
            if targets.at[timestamp, coin] != 0
        }
        to_exit = {
            coin
            for coin, position in positions.items()
            if coin not in desired
            or np.sign(position[0]) != np.sign(desired[coin])
        }
        for coin in to_exit:
            _, binance_units, okx_units = positions.pop(coin)
            cost = cost_multiplier * (
                BINANCE_ONE_WAY * binance_units * bp.at[timestamp, coin]
                + OKX_ONE_WAY * okx_units * op.at[timestamp, coin]
            )
            symbol_step.at[timestamp, coin] -= cost
            trading_cost += cost
            turnover += (
                binance_units * bp.at[timestamp, coin]
                + okx_units * op.at[timestamp, coin]
            ) / 2
        for coin, signed_weight in desired.items():
            old = positions.get(coin)
            if old is not None and np.sign(old[0]) == np.sign(signed_weight):
                continue
            weight = abs(signed_weight)
            binance_units = weight / bp.at[timestamp, coin]
            okx_units = weight / op.at[timestamp, coin]
            positions[coin] = (signed_weight, binance_units, okx_units)
            cost = cost_multiplier * weight * PAIR_ONE_WAY
            symbol_step.at[timestamp, coin] -= cost
            trading_cost += cost
            turnover += weight

    if len(targets):
        timestamp = targets.index[-1]
        for coin, (_, binance_units, okx_units) in positions.items():
            cost = cost_multiplier * (
                BINANCE_ONE_WAY * binance_units * bp.at[timestamp, coin]
                + OKX_ONE_WAY * okx_units * op.at[timestamp, coin]
            )
            symbol_step.at[timestamp, coin] -= cost
            trading_cost += cost
            turnover += (
                binance_units * bp.at[timestamp, coin]
                + okx_units * op.at[timestamp, coin]
            ) / 2

    step = symbol_step.sum(axis=1)
    gross = targets.shift(1).fillna(0).abs().sum(axis=1)
    diagnostics = {
        "funding_pnl": float(funding_pnl),
        "venue_basis_pnl": float(basis_pnl),
        "trading_cost": float(trading_cost),
        "turnover": float(turnover),
        "avg_pair_notional": float(gross.mean()),
        "avg_gross_exposure": float(2 * gross.mean()),
    }
    return step, symbol_step, diagnostics


def metrics(step: pd.Series) -> dict:
    daily = step.groupby(step.index.floor("D")).sum()
    monthly = step.groupby(step.index.to_period("M")).sum()
    equity = 1 + daily.cumsum()
    elapsed_years = max(
        (daily.index[-1] - daily.index[0]).total_seconds() / (365 * 86400),
        1 / 365,
    )
    std = daily.std(ddof=0)
    drawdown = equity / equity.cummax() - 1
    return {
        "days": int(len(daily)),
        "months": int(len(monthly)),
        "net_return_pct": round(float(daily.sum() * 100), 3),
        "annual_return_pct": round(float(daily.sum() / elapsed_years * 100), 3),
        "average_month_pct": round(float(monthly.mean() * 100), 3),
        "median_month_pct": round(float(monthly.median() * 100), 3),
        "best_month_pct": round(float(monthly.max() * 100), 3),
        "worst_month_pct": round(float(monthly.min() * 100), 3),
        "winning_months_pct": round(float((monthly > 0).mean() * 100), 2),
        "sharpe": round(float(daily.mean() / std * math.sqrt(PER_YEAR)), 3)
        if std
        else 0.0,
        "max_drawdown_pct": round(float(drawdown.min() * 100), 3),
        "worst_day_pct": round(float(daily.min() * 100), 3),
    }


def evaluate(
    features: dict[str, pd.DataFrame],
    params: Params,
    cost_multiplier: float = 1.0,
    folds: int = 6,
) -> dict:
    step, symbol_step, diagnostics = simulate(features, params, cost_multiplier)
    index = step.index
    boundaries = [index[int(i * len(index) / folds)] for i in range(folds)]
    boundaries.append(index[-1] + pd.Timedelta(hours=8))
    fold_rows = []
    for i in range(folds):
        segment = step[(step.index >= boundaries[i]) & (step.index < boundaries[i + 1])]
        fold_rows.append(
            {
                "fold": i + 1,
                "start": str(boundaries[i]),
                "end": str(boundaries[i + 1]),
                "metrics": metrics(segment),
            }
        )
    overall = metrics(step)
    monthly_target = overall["median_month_pct"] >= 6.0
    verdict = {
        "positive": overall["net_return_pct"] > 0,
        "median_month_ge_6pct": monthly_target,
        "annual_return_ge_100pct": overall["annual_return_pct"] >= 100,
        "sharpe_ge_1_5": overall["sharpe"] >= 1.5,
        "max_drawdown_le_15pct": abs(overall["max_drawdown_pct"]) <= 15,
        "worst_month_ge_minus_10pct": overall["worst_month_pct"] >= -10,
        "positive_folds_ge_5": sum(
            row["metrics"]["net_return_pct"] > 0 for row in fold_rows
        ) >= 5,
    }
    verdict["meets_user_return_target"] = bool(
        verdict["median_month_ge_6pct"] and verdict["annual_return_ge_100pct"]
    )
    verdict["research_gate_pass"] = bool(
        verdict["positive"]
        and verdict["sharpe_ge_1_5"]
        and verdict["max_drawdown_le_15pct"]
        and verdict["positive_folds_ge_5"]
    )
    return {
        "strategy": "cross_venue_funding_dispersion",
        "parameters_preregistered_before_okx_results": asdict(params),
        "cost_multiplier": cost_multiplier,
        "symbols": sorted(features),
        "folds": fold_rows,
        "overall": overall,
        "symbol_net_pnl": {
            coin: round(float(value), 6)
            for coin, value in symbol_step.sum().sort_values(ascending=False).items()
        },
        "diagnostics": diagnostics,
        "verdict": verdict,
        "production_ready": False,
        "limitations": [
            "Static surviving-contract basket omits delisted perpetuals.",
            "Candle marks do not model simultaneous fills or order-book depth.",
            "Capital, margin and liquidation are venue-specific and separately funded.",
            "Historical research cannot prove legal venue access in the user's jurisdiction.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coins", default=",".join(BASKET))
    parser.add_argument("--cost-multiplier", type=float, default=1.0)
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--out")
    args = parser.parse_args()
    data = load_all([coin.strip().upper() for coin in args.coins.split(",")])
    if not data:
        raise SystemExit(
            "No aligned Binance/OKX data. Run research.binance_vision and research.okx_data."
        )
    params = Params()
    result = evaluate(
        build_features(data, params), params, args.cost_multiplier, args.folds
    )
    print(json.dumps(result, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
