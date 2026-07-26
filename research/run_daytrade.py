"""
Walk-forward driver for the daytrade lab.

Pipeline
--------
1. Load full-history intraday data per symbol from the Binance cache.
2. Rolling/anchored walk-forward: on each fold, grid-search parameters on the
   in-sample block, then run the chosen parameters on the *next* out-of-sample
   block. Concatenate all OOS trades.
3. Report OOS daily metrics per symbol and for the combined portfolio, plus a
   PASS/FAIL scorecard against the mandate's targets.

Usage:
    python3 research/run_daytrade.py --strategy mean_reversion_bb --tf 15m
    python3 research/run_daytrade.py --portfolio --tf 15m --out research_daytrade.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.daytrade_lab import (
    Costs, load, slice_dates, simulate, daily_metrics,
)
from research.daytrade_strategies import STRATS, GRIDS

# Default liquid-major universe on Binance 15m (all have history from 2023-01).
DEFAULT_SYMBOLS = {
    "BTC": "binance_btc_usd_{tf}",
    "ETH": "binance_eth_usd_{tf}",
    "SOL": "binance_sol_usd_{tf}",
    "XRP": "binance_xrp_usd_{tf}",
    "ADA": "binance_ada_usd_{tf}",
    "DOGE": "binance_doge_usd_{tf}",
    "LINK": "binance_link_usd_{tf}",
}

INITIAL = 10_000.0
RISK_FRAC = 0.0075          # 0.75% risk per trade
PPY = 365                   # crypto trades every calendar day


def in_sample_score(res, base) -> tuple[float, dict]:
    """Objective used to pick parameters in-sample: daily Sharpe, gated on trades."""
    m = daily_metrics(res.exit_ts, res.pnl, INITIAL, PPY, base=base)
    if m["n_trades"] < 20 or m["net_return_pct"] <= 0:
        return -1e9, m
    return m["sharpe"], m


def walk_forward_symbol(
    df: pd.DataFrame,
    strat_name: str,
    costs: Costs,
    n_folds: int = 5,
    risk_frac: float = RISK_FRAC,
    verbose: bool = False,
):
    """Anchored walk-forward. Returns concatenated OOS trades + per-fold picks."""
    fn = STRATS[strat_name]
    grid = GRIDS[strat_name]
    n = len(df)
    block = n // (n_folds + 1)
    if block < 500:
        return None

    oos_entry, oos_exit, oos_pnl = [], [], []
    picks = []

    for k in range(n_folds):
        train = df.iloc[: (k + 1) * block].copy()
        test = df.iloc[(k + 1) * block : (k + 2) * block].copy()
        if len(test) < 200:
            break

        best_score = -1e18
        best_p = None
        for p in grid:
            long_s, short_s = fn(train, p)
            res = simulate(
                train, long_s, short_s, p["sl_atr"], p["tp_atr"], p["max_hold"],
                costs, INITIAL, risk_frac, allow_short=True, compound=False,
            )
            score, _ = in_sample_score(res, INITIAL)
            if score > best_score:
                best_score = score
                best_p = p

        if best_p is None:
            continue

        long_s, short_s = fn(test, best_p)
        res = simulate(
            test, long_s, short_s, best_p["sl_atr"], best_p["tp_atr"],
            best_p["max_hold"], costs, INITIAL, risk_frac,
            allow_short=True, compound=False,
        )
        oos_entry.append(res.entry_ts)
        oos_exit.append(res.exit_ts)
        oos_pnl.append(res.pnl)
        picks.append(dict(
            fold=k, train_bars=len(train), test_bars=len(test),
            test_start=str(test.index[0]), test_end=str(test.index[-1]),
            params=best_p, oos_trades=res.n_trades,
        ))
        if verbose:
            print(f"  fold {k}: test {test.index[0].date()}..{test.index[-1].date()} "
                  f"trades={res.n_trades} sl={best_p['sl_atr']} tp={best_p['tp_atr']}")

    if not oos_pnl:
        return None
    entry = np.concatenate(oos_entry) if oos_entry else np.array([], dtype="datetime64[ns]")
    exit_ = np.concatenate(oos_exit) if oos_exit else np.array([], dtype="datetime64[ns]")
    pnl = np.concatenate(oos_pnl) if oos_pnl else np.array([], dtype=float)
    return dict(entry=entry, exit=exit_, pnl=pnl, picks=picks)


def _load_universe(symbols: dict, tf: str, start, end) -> dict:
    data = {}
    for label, tmpl in symbols.items():
        fname = tmpl.format(tf=tf)
        try:
            df = slice_dates(load(fname), start, end)
        except FileNotFoundError:
            continue
        if len(df) >= 3000:
            data[label] = df
    return data


def _combo_portfolio_trades(data: dict, fn, p, costs, risk_frac, rng):
    """Run one param combo across symbols over an index range (lo,hi fraction)
    and return combined (exit_ts, pnl, gross, cost)."""
    lo, hi = rng
    exits, pnls = [], []
    gross = cost = 0.0
    sym_risk = risk_frac / max(1, len(data))
    for df in data.values():
        n = len(df)
        seg = df.iloc[int(lo * n): int(hi * n)]
        if len(seg) < 500:
            continue
        long_s, short_s = fn(seg, p)
        res = simulate(seg, long_s, short_s, p["sl_atr"], p["tp_atr"],
                       p["max_hold"], costs, INITIAL, sym_risk,
                       allow_short=True, compound=False)
        if res.n_trades:
            exits.append(res.exit_ts)
            pnls.append(res.pnl)
            gross += res.gross_pnl
            cost += res.cost_pnl
    if not pnls:
        return np.array([], dtype="datetime64[ns]"), np.array([], dtype=float), 0.0, 0.0
    e = np.concatenate(exits)
    pn = np.concatenate(pnls)
    order = np.argsort(e)
    return e[order], pn[order], gross, cost


def optimize_shared(
    data: dict, strat_name: str, costs: Costs, risk_frac: float,
    train_ratio: float = 0.55, min_train_trades: int = 120,
    require_positive: bool = True,
):
    """Grid-search ONE parameter set shared across all symbols, chosen on the
    in-sample (train) portfolio Sharpe, then evaluated purely OOS (test).

    Also tracks the best-by-net combo even when negative (landscape mapping),
    and the best GROSS (pre-cost) edge to reveal whether any raw signal exists.
    """
    fn = STRATS[strat_name]
    grid = GRIDS[strat_name]
    best_score, best_p, best_train_m = -1e18, None, None
    best_net, best_net_p, best_net_m = -1e18, None, None
    best_gross = -1e18
    for p in grid:
        e, pn, gross, cost = _combo_portfolio_trades(data, fn, p, costs, risk_frac, (0.0, train_ratio))
        m = daily_metrics(e, pn, INITIAL, PPY, base=INITIAL)
        if m["n_trades"] < min_train_trades:
            continue
        gross_edge = gross / INITIAL * 100
        best_gross = max(best_gross, gross_edge)
        if m["net_return_pct"] > best_net:
            best_net, best_net_p, best_net_m = m["net_return_pct"], p, m
        if require_positive and m["net_return_pct"] <= 0:
            continue
        if m["sharpe"] > best_score:
            best_score, best_p, best_train_m = m["sharpe"], p, m
    if best_p is None:
        # fall back to best-by-net so we can still inspect OOS behaviour
        best_p, best_train_m = best_net_p, best_net_m
    return best_p, best_train_m, round(best_net, 2), round(best_gross, 2)


def yearly_walk_forward(data: dict, strat_name: str, best_p: dict, costs: Costs, risk_frac: float):
    """Apply the FIXED chosen params across the OOS block split by calendar year
    to show temporal stability (no re-optimization)."""
    fn = STRATS[strat_name]
    # OOS = last 45% of each series; report per-year within it
    e, pn, _, _ = _combo_portfolio_trades(data, fn, best_p, costs, risk_frac, (0.55, 1.0))
    if len(pn) == 0:
        return {}
    df = pd.DataFrame({"exit": pd.to_datetime(e), "pnl": pn})
    out = {}
    for yr, g in df.groupby(df["exit"].dt.year):
        m = daily_metrics(g["exit"].values, g["pnl"].values, INITIAL, PPY, base=INITIAL)
        out[str(yr)] = dict(trades=m["n_trades"], net_pct=m["net_return_pct"],
                            net_per_day=m["net_per_day_pct"], win_days=m["win_days_pct"],
                            sharpe=m["sharpe"], max_dd=m["max_dd_pct"])
    return out


def run_shared(symbols, strat_name, tf, costs, start, end, risk_frac,
               train_ratio=0.55, verbose=False):
    data = _load_universe(symbols, tf, start, end)
    if not data:
        return dict(strategy=strat_name, tf=tf, error="no data")
    if verbose:
        for k, v in data.items():
            print(f"[{k}] {len(v)} bars {v.index[0].date()}..{v.index[-1].date()}")
    best_p, train_m, best_net_is, best_gross_is = optimize_shared(
        data, strat_name, costs, risk_frac, train_ratio, require_positive=False)
    if best_p is None:
        return dict(strategy=strat_name, tf=tf, error="no combo met min trades")

    fn = STRATS[strat_name]
    e, pn, gross, cost = _combo_portfolio_trades(data, fn, best_p, costs, risk_frac, (train_ratio, 1.0))
    oos_m = daily_metrics(e, pn, INITIAL, PPY, base=INITIAL)
    cs = round(cost / gross * 100, 1) if gross > 0 else float("inf")

    # per-symbol OOS breakdown with the shared params
    per_symbol = {}
    sym_risk = risk_frac / max(1, len(data))
    for label, df in data.items():
        n = len(df)
        seg = df.iloc[int(train_ratio * n):]
        long_s, short_s = fn(seg, best_p)
        res = simulate(seg, long_s, short_s, best_p["sl_atr"], best_p["tp_atr"],
                       best_p["max_hold"], costs, INITIAL, sym_risk,
                       allow_short=True, compound=False)
        per_symbol[label] = daily_metrics(res.exit_ts, res.pnl, INITIAL, PPY, base=INITIAL)

    return dict(
        strategy=strat_name, tf=tf, costs=vars(costs), risk_frac=risk_frac,
        train_ratio=train_ratio, best_params=best_p,
        is_best_net_pct=best_net_is, is_best_gross_pct=best_gross_is,
        train_metrics=train_m, oos_metrics=oos_m, oos_cost_share_pct=cs,
        oos_scorecard=scorecard(oos_m),
        per_symbol_oos=per_symbol,
        yearly_oos=yearly_walk_forward(data, strat_name, best_p, costs, risk_frac),
    )


def scorecard(m: dict) -> dict:
    """PASS/FAIL against the mandate's numeric targets."""
    checks = {
        "net_per_day>=0.25%": m["net_per_day_pct"] >= 0.25,
        "win_days>=60%": m["win_days_pct"] >= 60.0,
        "win_days>=50%_floor": m["win_days_pct"] >= 50.0,
        "max_dd<=10%": abs(m["max_dd_pct"]) <= 10.0,
        "worst_day>=-2%": m["worst_day_pct"] >= -2.0,
        "sharpe>=1.5": m["sharpe"] >= 1.5,
        "trades>=200": m["n_trades"] >= 200,
    }
    return {k: ("PASS" if v else "FAIL") for k, v in checks.items()}


def run(
    symbols: dict,
    strat_name: str,
    tf: str,
    costs: Costs,
    start: str | None,
    end: str | None,
    n_folds: int,
    risk_frac: float,
    verbose: bool = False,
):
    per_symbol = {}
    all_entry, all_exit, all_pnl = [], [], []
    n_sym = len(symbols)
    # divide risk across symbols so concurrent positions don't over-leverage
    sym_risk = risk_frac / max(1, n_sym)

    for label, tmpl in symbols.items():
        fname = tmpl.format(tf=tf)
        try:
            df = slice_dates(load(fname), start, end)
        except FileNotFoundError:
            print(f"! missing cache: {fname}")
            continue
        if len(df) < 3000:
            print(f"! too short: {label} ({len(df)} bars)")
            continue
        if verbose:
            print(f"[{label}] {fname}: {len(df)} bars "
                  f"{df.index[0].date()}..{df.index[-1].date()}")
        wf = walk_forward_symbol(df, strat_name, costs, n_folds, sym_risk, verbose)
        if wf is None:
            print(f"! no WF result for {label}")
            continue
        m = daily_metrics(wf["exit"], wf["pnl"], INITIAL, PPY, base=INITIAL)
        # cost share needs gross; recompute quickly over the OOS trades already priced
        per_symbol[label] = dict(metrics=m, picks=wf["picks"])
        all_entry.append(wf["entry"])
        all_exit.append(wf["exit"])
        all_pnl.append(wf["pnl"])

    if not all_pnl:
        return dict(strategy=strat_name, tf=tf, per_symbol={}, portfolio=None)

    port_exit = np.concatenate(all_exit)
    port_pnl = np.concatenate(all_pnl)
    order = np.argsort(port_exit)
    port_exit = port_exit[order]
    port_pnl = port_pnl[order]
    port_m = daily_metrics(port_exit, port_pnl, INITIAL, PPY, base=INITIAL)

    return dict(
        strategy=strat_name,
        tf=tf,
        costs=vars(costs),
        risk_frac=risk_frac,
        per_symbol={k: v["metrics"] for k, v in per_symbol.items()},
        picks={k: v["picks"] for k, v in per_symbol.items()},
        portfolio=port_m,
        portfolio_scorecard=scorecard(port_m),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="mean_reversion_bb",
                    choices=list(STRATS.keys()) + ["all"])
    ap.add_argument("--tf", default="15m")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--train-ratio", type=float, default=0.55)
    ap.add_argument("--risk", type=float, default=RISK_FRAC)
    ap.add_argument("--commission", type=float, default=0.0005)
    ap.add_argument("--slippage", type=float, default=0.0002)
    ap.add_argument("--half-spread", type=float, default=0.0002)
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    costs = Costs(args.commission, args.slippage, args.half_spread)
    strat_names = list(STRATS.keys()) if args.strategy == "all" else [args.strategy]

    results = {}
    for sn in strat_names:
        print(f"\n===== {sn} ({args.tf}) =====")
        r = run_shared(DEFAULT_SYMBOLS, sn, args.tf, costs, args.start, args.end,
                       args.risk, train_ratio=args.train_ratio, verbose=not args.quiet)
        results[sn] = r
        if r.get("error"):
            print(f"  {r['error']}")
            continue
        tm = r["train_metrics"]
        p = r["oos_metrics"]
        print(f"  IS landscape: best_net={r['is_best_net_pct']}% best_GROSS(pre-cost)={r['is_best_gross_pct']}%")
        print(f"  IS train: net/day={tm['net_per_day_pct']}% win_days={tm['win_days_pct']}% "
              f"sharpe={tm['sharpe']} trades={tm['n_trades']}")
        print(f"  OOS test: net/day={p['net_per_day_pct']}% win_days={p['win_days_pct']}% "
              f"(active {p['active_win_days_pct']}%) sharpe={p['sharpe']} "
              f"maxDD={p['max_dd_pct']}% trades={p['n_trades']} hit={p['hit_rate_pct']}% "
              f"net={p['net_return_pct']}% cost_share={r['oos_cost_share_pct']}%")
        print(f"  best_params: {r['best_params']}")
        print(f"  scorecard: {r['oos_scorecard']}")
        if r.get("yearly_oos"):
            print(f"  yearly OOS: {r['yearly_oos']}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, default=str))
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
