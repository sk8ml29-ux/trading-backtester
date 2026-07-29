"""
Event study: what happens after forced selling.

Scans every minute of every symbol, keeps only the minutes that look like a
liquidation cascade, and records what the traded leg did afterwards. The output
is a compact event table rather than the full minute panel, which is what makes
a 300-million-row study fit in memory.

The comparison that matters is not "does price rise after an event" — in a bull
market everything rises. It is whether the post-event return beats the same
symbol's unconditional drift over the same horizon.
"""
from __future__ import annotations

import argparse
import gc
from pathlib import Path

import numpy as np
import pandas as pd

from .data import CACHE, pool_symbols
from .features import build

EVENTS_PATH = CACHE / "events.parquet"
BASELINE_PATH = CACHE / "baseline.parquet"
HORIZONS = (5, 15, 30, 60, 120, 240)

KEEP = ["time", "symbol", "z_drop", "r_drop", "sigma", "ofi", "ofi_1m", "ofi_recover",
        "sell_burst", "vol_burst", "cnt_burst", "prem", "prem_z", "prem_z_min",
        "prem_recover", "range_pos", "dollar_vol_24h", "entry_px", "mae_60m",
        "spot_ok"] + [f"fwd_{h}m" for h in HORIZONS] + [f"pfwd_{h}m" for h in HORIZONS]


def load_symbol(sym: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    def rd(kind: str) -> pd.DataFrame:
        p = CACHE / f"{kind}1m_{sym}.parquet"
        return pd.read_parquet(p) if p.exists() else pd.DataFrame()
    return rd("perp"), rd("premium"), rd("spot")


def scan(symbols: list[str], z_trigger: float = -3.0, min_liq: float = 20e6,
         drop_win: int = 15, verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (events, baseline) tables.

    `baseline` holds each symbol's unconditional forward returns over the same
    minutes it was eligible to trade, which is the benchmark the events have to
    beat.
    """
    ev_rows, base_rows = [], []
    for i, sym in enumerate(symbols, 1):
        perp, prem, spot = load_symbol(sym)
        if perp.empty or spot.empty:
            continue
        try:
            f = build(perp, prem, spot, HORIZONS, drop_win)
        except Exception as e:
            if verbose:
                print(f"  {sym}: FAIL {repr(e)[:70]}", flush=True)
            continue
        if f.empty:
            del perp, prem, spot
            gc.collect()
            continue

        eligible = (f["dollar_vol_24h"] >= min_liq) & f["spot_ok"] & f["entry_px"].notna()
        f = f[eligible]
        if f.empty:
            continue

        f["symbol"] = sym
        base_rows.append(pd.DataFrame({
            "symbol": [sym], "n_minutes": [len(f)],
            **{f"base_{h}m": [f[f"fwd_{h}m"].mean()] for h in HORIZONS},
            **{f"basesd_{h}m": [f[f"fwd_{h}m"].std()] for h in HORIZONS},
        }))

        hit = f[(f["z_drop"] <= z_trigger) & f["z_drop"].notna()]
        if not hit.empty:
            ev_rows.append(hit[[c for c in KEEP if c in hit.columns]].copy())

        del perp, prem, spot, f
        gc.collect()
        if verbose and i % 10 == 0:
            n = sum(len(x) for x in ev_rows)
            print(f"  scanned {i}/{len(symbols)}  events={n:,}", flush=True)

    events = pd.concat(ev_rows, ignore_index=True) if ev_rows else pd.DataFrame()
    baseline = pd.concat(base_rows, ignore_index=True) if base_rows else pd.DataFrame()
    return events, baseline


def decluster(ev: pd.DataFrame, gap_min: int = 60) -> pd.DataFrame:
    """Keep one event per symbol per cascade.

    Consecutive minutes inside a single crash would otherwise be counted dozens
    of times and make a handful of crashes look like a large sample.
    """
    if ev.empty:
        return ev
    ev = ev.sort_values(["symbol", "time"]).reset_index(drop=True).copy()
    minutes = ev["time"].to_numpy().astype("datetime64[m]").astype("int64")
    gap = np.diff(minutes, prepend=minutes[0] - 10**9)
    syms = ev["symbol"].to_numpy()
    new_group = (syms != np.roll(syms, 1)) | (gap > gap_min)
    new_group[0] = True
    ev["cascade_id"] = np.cumsum(new_group)
    return ev


def first_of_cascade(ev: pd.DataFrame) -> pd.DataFrame:
    """One tradeable row per cascade: the first minute that triggered it."""
    if ev.empty or "cascade_id" not in ev.columns:
        return ev
    return ev.sort_values("time").groupby("cascade_id", as_index=False).first()


def summarise(ev: pd.DataFrame, baseline: pd.DataFrame,
              horizons=HORIZONS) -> pd.DataFrame:
    """Event returns against the unconditional benchmark, per horizon."""
    if ev.empty:
        return pd.DataFrame()
    base = baseline.set_index("symbol") if not baseline.empty else None
    rows = []
    for h in horizons:
        col = f"fwd_{h}m"
        x = ev[col].dropna()
        if x.empty:
            continue
        b = np.nan
        if base is not None:
            bmap = ev["symbol"].map(base[f"base_{h}m"])
            b = float((ev[col] - bmap).dropna().mean())
        # Cluster by cascade so overlapping minutes do not inflate the t-stat.
        if "cascade_id" in ev.columns:
            g = ev.groupby("cascade_id")[col].mean().dropna()
        else:
            g = x
        se = g.std(ddof=1) / np.sqrt(len(g)) if len(g) > 2 else np.nan
        rows.append(dict(horizon_m=h, n_events=int(len(x)), n_clusters=int(len(g)),
                         mean_bps=float(x.mean() * 1e4),
                         excess_bps=float(b * 1e4) if np.isfinite(b) else np.nan,
                         t_stat=float(g.mean() / se) if se and se > 0 else np.nan,
                         hit=float((x > 0).mean()),
                         median_bps=float(x.median() * 1e4)))
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=150)
    ap.add_argument("--z", type=float, default=-3.0)
    ap.add_argument("--min-liq", type=float, default=20e6)
    ap.add_argument("--drop-win", type=int, default=15)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    syms = pool_symbols(a.top)
    if a.limit:
        syms = syms[:a.limit]
    print(f"scanning {len(syms)} symbols, trigger z<={a.z} over {a.drop_win}m", flush=True)
    ev, base = scan(syms, a.z, a.min_liq, a.drop_win)
    if ev.empty:
        print("no events")
        return
    ev = decluster(ev)
    ev.to_parquet(EVENTS_PATH)
    base.to_parquet(BASELINE_PATH)
    print(f"\n{len(ev):,} event-minutes in {ev['cascade_id'].nunique():,} cascades "
          f"across {ev['symbol'].nunique()} symbols")
    print(f"span {ev['time'].min()} .. {ev['time'].max()}\n")
    pd.set_option("display.width", 200)
    print(summarise(ev, base).to_string(index=False, float_format=lambda v: f"{v:,.3f}"))


if __name__ == "__main__":
    main()
