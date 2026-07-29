"""
Backtest sweeps: rebalance cadence, cost sensitivity, and the null tests.

Loads the scored panel once and reuses it, since it is the expensive artefact.
Every sweep prints in-sample and out-of-sample side by side so a configuration
that only works before the split date is obvious immediately.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from .backtest import BTConfig, CostModel, run, yearly
from .vision_bulk import CACHE

SCORED_PATH = CACHE / "panel_scored.parquet"


def _fmt(st: dict) -> str:
    return (f"CAGR={st['cagr']*100:7.2f}%  Sharpe={st['sharpe']:6.2f}  "
            f"maxDD={st['max_dd']*100:7.2f}%  hit={st['hit']*100:4.1f}%  "
            f"turn={st['turnover_per_rebal']:.3f}")


def one(panel: pd.DataFrame, score_col: str, cfg: BTConfig, split: str,
        label: str = "") -> dict:
    res = {}
    for tag, (s, e) in {"IS": (None, split), "OOS": (split, None)}.items():
        r = run(panel, score_col, cfg, start=s, end=e)
        if not r.get("ok"):
            print(f"    {label} {tag}: {r.get('reason')}")
            continue
        res[tag] = r["stats"]
        print(f"    {label:28s} {tag:3s}  {_fmt(r['stats'])}")
    return res


def sweep_rebal(panel: pd.DataFrame, score_col: str, split: str,
                cadences=(8, 24, 48, 96, 168), **kw) -> dict:
    print(f"\n--- rebalance cadence sweep [{score_col}] ---")
    out = {}
    for h in cadences:
        cfg = BTConfig(rebal_h=h, **kw)
        out[h] = one(panel, score_col, cfg, split, label=f"rebal={h}h")
    return out


def sweep_cost(panel: pd.DataFrame, score_col: str, split: str, rebal_h: int,
               levels=(0, 4, 8, 12, 20, 30), **kw) -> dict:
    print(f"\n--- cost sensitivity [{score_col}, rebal={rebal_h}h] ---")
    out = {}
    for bps in levels:
        cfg = BTConfig(rebal_h=rebal_h,
                       cost=CostModel(taker_bps=bps, spread_bps=0.0), **kw)
        out[bps] = one(panel, score_col, cfg, split, label=f"{bps}bps/side")
    return out


def sweep_signals(panel: pd.DataFrame, cols: list[str], split: str, rebal_h: int,
                  **kw) -> dict:
    print(f"\n--- signal comparison [rebal={rebal_h}h] ---")
    out = {}
    for c in cols:
        if c not in panel.columns:
            continue
        cfg = BTConfig(rebal_h=rebal_h, **kw)
        out[c] = one(panel, c, cfg, split, label=c)
    return out


def null_test(panel: pd.DataFrame, score_col: str, split: str, rebal_h: int,
              n_trials: int = 30, seed: int = 7, **kw) -> dict:
    """Shuffle the signal across symbols within each timestamp.

    This destroys the cross-sectional ordering while preserving every other
    property of the panel: the same names, the same dates, the same turnover
    profile and the same cost model. If the real signal is not clearly outside
    this distribution, it is not a signal.
    """
    print(f"\n--- null test [{score_col}, rebal={rebal_h}h, {n_trials} shuffles] ---")
    cfg = BTConfig(rebal_h=rebal_h, **kw)
    real = run(panel, score_col, cfg, start=split)
    real_sharpe = real["stats"]["sharpe"] if real.get("ok") else np.nan

    rng = np.random.default_rng(seed)
    p = panel[["time", "symbol", "exec_px", "liq_usd", score_col]].copy()
    sharpes = []
    for i in range(n_trials):
        shuffled = p.copy()
        vals = shuffled[score_col].to_numpy()
        # Permute within each timestamp only.
        idx = shuffled.groupby("time").cumcount().to_numpy()
        order = np.lexsort((rng.random(len(idx)), shuffled["time"].to_numpy()))
        shuffled[score_col] = vals[order]
        r = run(shuffled, score_col, cfg, start=split)
        if r.get("ok"):
            sharpes.append(r["stats"]["sharpe"])
    sharpes = np.array([s for s in sharpes if np.isfinite(s)])
    pct = float((sharpes >= real_sharpe).mean()) if len(sharpes) else np.nan
    print(f"    real OOS Sharpe = {real_sharpe:.2f}")
    print(f"    null  OOS Sharpe: mean={sharpes.mean():.2f} sd={sharpes.std():.2f} "
          f"max={sharpes.max():.2f}   p(null >= real) = {pct:.3f}")
    return dict(real=real_sharpe, null_mean=float(sharpes.mean()),
                null_sd=float(sharpes.std()), null_max=float(sharpes.max()),
                p_value=pct)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2025-04-01")
    ap.add_argument("--score-col", default="gb_oh_168")
    ap.add_argument("--rebal", type=int, default=24)
    ap.add_argument("--gross", type=float, default=1.0)
    ap.add_argument("--max-weight", type=float, default=0.06)
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--what", default="rebal,cost,signals,null")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    print("loading scored panel ...", flush=True)
    cols = None
    panel = pd.read_parquet(SCORED_PATH, columns=cols)
    print(f"  rows={len(panel):,}  symbols={panel['symbol'].nunique()}  "
          f"{panel['time'].min()} .. {panel['time'].max()}", flush=True)

    kw = dict(gross=args.gross, max_weight=args.max_weight,
              capital_usd=args.capital, long_only=args.long_only)
    what = set(args.what.split(","))
    res = {}

    if "signals" in what:
        cands = ["gb_oh_168", "gb_oh_168_resid", "gb_ohv_168", "gb_oh_72",
                 "gb_oh_336", "z_tli", "gb_core", "z_disp",
                 "z_ma_osc_72", "z_mom_24"]
        res["signals"] = sweep_signals(panel, cands, args.split, args.rebal, **kw)
    if "rebal" in what:
        res["rebal"] = sweep_rebal(panel, args.score_col, args.split, **kw)
    if "cost" in what:
        res["cost"] = sweep_cost(panel, args.score_col, args.split, args.rebal, **kw)
    if "null" in what:
        res["null"] = null_test(panel, args.score_col, args.split, args.rebal, **kw)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(res, f, indent=2, default=str)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
