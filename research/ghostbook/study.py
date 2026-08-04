"""
Research driver: raw dumps -> position maps -> panel -> IC study -> backtest.

Intermediate artefacts are cached so signal iteration does not re-pay the
reconstruction cost. The expensive steps are the reconstruction (a few seconds
per symbol) and the panel join.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from . import ic as icmod
from .backtest import BTConfig, CostModel, run, yearly
from .panel import FEATURES, apply_universe, build_panel
from .positionmap import MapConfig, reconstruct_universe
from .signals import (CONTROL_COLS, HYPOTHESIS_COLS, add_controls, add_derived,
                      residualise)
from .vision_bulk import CACHE

PANEL_PATH = CACHE / "panel.parquet"
SCORED_PATH = CACHE / "panel_scored.parquet"


def load_metrics_cache(limit: int | None = None) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    paths = sorted(CACHE.glob("metrics_*.parquet"))
    if limit:
        paths = paths[:limit]
    for p in paths:
        sym = p.stem.replace("metrics_", "")
        try:
            df = pd.read_parquet(p)
        except Exception:
            continue
        if len(df) > 288 * 30:
            out[sym] = df
    return out


def build(args) -> pd.DataFrame:
    print("loading 5-minute metrics cache ...", flush=True)
    metrics = load_metrics_cache(args.limit)
    print(f"  {len(metrics)} symbols with usable history", flush=True)

    cfg = MapConfig(checkpoint_min=60, fuel_leverage=args.fuel_leverage,
                    close_rule=args.close_rule)
    print("reconstructing position maps ...", flush=True)
    maps = reconstruct_universe(metrics, cfg)
    print(f"  {len(maps)} reconstructions", flush=True)

    print("building panel ...", flush=True)
    panel = build_panel(maps, horizon_h=args.horizon)
    print(f"  raw panel rows={len(panel):,}", flush=True)

    panel = apply_universe(panel, min_liq_usd=args.min_liq, max_names=args.max_names)
    print(f"  after universe screen rows={len(panel):,} "
          f"symbols={panel['symbol'].nunique()}", flush=True)
    panel.to_parquet(PANEL_PATH)
    return panel


def score(panel: pd.DataFrame) -> pd.DataFrame:
    print("adding controls and derived scores ...", flush=True)
    panel = add_controls(panel)
    panel = add_derived(panel)
    panel.to_parquet(SCORED_PATH)
    print(f"  scored rows={len(panel):,}", flush=True)
    return panel


def study(panel: pd.DataFrame, args) -> None:
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 50)

    print("\n" + "=" * 78)
    print(f"INFORMATION COEFFICIENTS   split={args.split}   horizon={args.horizon}h")
    print("=" * 78)
    rep = icmod.feature_report(panel, HYPOTHESIS_COLS + CONTROL_COLS, args.split,
                               sample_gap_h=1, horizon_h=args.horizon)
    print(rep.to_string(index=False, float_format=lambda v: f"{v:,.4f}"))

    print("\n" + "=" * 78)
    print("IS THE SIGNAL JUST A MOVING AVERAGE?  (control-residualised IC)")
    print("=" * 78)
    resid_targets = ["z_tli", "gb_core", "z_disp", "gb_oh_168", "gb_oh_336",
                     "gb_ohv_168"]
    for col in resid_targets:
        if col not in panel.columns:
            continue
        panel[f"{col}_resid"] = residualise(panel, col, CONTROL_COLS)
    rep2 = icmod.feature_report(panel, [f"{c}_resid" for c in resid_targets
                                        if f"{c}_resid" in panel.columns],
                                args.split, sample_gap_h=1, horizon_h=args.horizon)
    print(rep2.to_string(index=False, float_format=lambda v: f"{v:,.4f}"))

    best = rep.iloc[0]["feature"] if not rep.empty else "gb_core"
    print(f"\nquantile monotonicity for {best} (in-sample):")
    isp = panel[panel["time"] < pd.Timestamp(args.split)]
    print(icmod.quantile_returns(isp, best).to_string(index=False))

    if args.decay:
        cols = [c.strip() for c in args.decay.split(",") if c.strip() in panel.columns]
        if cols:
            print("\n" + "=" * 78)
            print("INFORMATION DECAY BY HOLDING HORIZON")
            print("=" * 78)
            horizons = [4, 8, 24, 48, 96, 168]
            sub = panel_with_horizons(panel, horizons)
            for c in cols:
                d = icmod.decay_report(sub, c, horizons, sample_gap_h=1)
                print(f"\n{c}:")
                print(d.to_string(index=False, float_format=lambda v: f"{v:,.4f}"))


def panel_with_horizons(panel: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    """Attach several forward-return columns using the cached hourly klines."""
    from .panel import load_klines
    out = panel.copy()
    for h in horizons:
        out[f"fwd_{h}h"] = np.nan
    for sym, g in out.groupby("symbol", sort=False):
        kl = load_klines(sym)
        if kl.empty:
            continue
        k = kl.set_index("time")["open"].astype(float)
        idx = pd.DatetimeIndex(g["time"])
        ex = k.shift(-1).reindex(idx).to_numpy()
        for h in horizons:
            fw = k.shift(-1 - h).reindex(idx).to_numpy()
            out.loc[g.index, f"fwd_{h}h"] = fw / ex - 1.0
    return out


def backtest(panel: pd.DataFrame, score_col: str, args) -> dict:
    cost = CostModel(taker_bps=args.taker_bps, spread_bps=args.spread_bps,
                     use_funding=not args.no_funding)
    cfg = BTConfig(rebal_h=args.rebal, scheme=args.scheme, gross=args.gross,
                   max_weight=args.max_weight, vol_scale=not args.no_vol_scale,
                   long_only=args.long_only, capital_usd=args.capital, cost=cost)
    out = {}
    for tag, (s, e) in {"IS": (None, args.split), "OOS": (args.split, None),
                        "ALL": (None, None)}.items():
        r = run(panel, score_col, cfg, start=s, end=e)
        if not r.get("ok"):
            print(f"  {tag}: {r.get('reason')}")
            continue
        out[tag] = r["stats"]
        st = r["stats"]
        print(f"  {tag:4s} n={st['n']:5d} yrs={st['years']:.2f} "
              f"CAGR={st['cagr']*100:7.2f}%  Sharpe={st['sharpe']:6.2f}  "
              f"maxDD={st['max_dd']*100:7.2f}%  hit={st['hit']*100:.1f}%  "
              f"turn/rebal={st['turnover_per_rebal']:.2f}")
        if tag == "ALL":
            out["yearly"] = yearly(r["net"]).to_dict()
            out["_run"] = r
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Ghost Book research pipeline")
    ap.add_argument("--stage", default="all",
                    choices=["build", "score", "study", "backtest", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--horizon", type=int, default=8)
    ap.add_argument("--rebal", type=int, default=8)
    ap.add_argument("--split", default="2025-04-01")
    ap.add_argument("--min-liq", type=float, default=20e6)
    ap.add_argument("--max-names", type=int, default=120)
    ap.add_argument("--fuel-leverage", type=float, default=10.0)
    ap.add_argument("--close-rule", default="proportional")
    ap.add_argument("--score-col", default="gb_core")
    ap.add_argument("--scheme", default="rank")
    ap.add_argument("--gross", type=float, default=1.0)
    ap.add_argument("--max-weight", type=float, default=0.06)
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--taker-bps", type=float, default=5.0)
    ap.add_argument("--spread-bps", type=float, default=3.0)
    ap.add_argument("--no-funding", action="store_true")
    ap.add_argument("--no-vol-scale", action="store_true")
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--decay", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    args.limit = args.limit or None

    if args.stage in ("build", "all"):
        panel = build(args)
        panel = score(panel)
    elif args.stage == "score":
        panel = score(pd.read_parquet(PANEL_PATH))
    else:
        panel = pd.read_parquet(SCORED_PATH)

    if args.stage in ("study", "score", "all"):
        study(panel, args)

    if args.stage in ("backtest", "all"):
        print("\n" + "=" * 78)
        print(f"BACKTEST  score={args.score_col}  rebal={args.rebal}h  "
              f"cost={args.taker_bps + args.spread_bps:.0f}bps/side")
        print("=" * 78)
        res = backtest(panel, args.score_col, args)
        if args.out:
            res.pop("_run", None)
            with open(args.out, "w") as f:
                json.dump(res, f, indent=2, default=str)
            print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
