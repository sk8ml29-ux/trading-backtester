"""
Where is the effect actually big enough to trade?

A -3 sigma drop is followed by a bounce, but the bounce is worth about 13 basis
points an hour, and a spot round trip costs roughly 20. So the raw effect is
real and untradeable at the same time.

This module answers the only question that matters next: which conditions
select the subset of cascades whose bounce clears costs by a wide margin. It
slices the event table by severity, by how dislocated the perpetual got against
spot, and by whether the selling has actually stopped, and reports the forward
return in each bucket with cluster-corrected statistics.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from .events import BASELINE_PATH, EVENTS_PATH, HORIZONS

COST_BPS_SPOT = 20.0      # 10bps taker each way on a MiCA-licensed venue
COST_BPS_PERP = 10.0      # 5bps taker each way


def _cluster_stats(g: pd.DataFrame, col: str) -> tuple[float, float, int]:
    """Mean and t-stat with one observation per cascade."""
    x = g.groupby("cascade_id")[col].mean().dropna()
    if len(x) < 5:
        return np.nan, np.nan, len(x)
    se = x.std(ddof=1) / np.sqrt(len(x))
    return float(x.mean()), (float(x.mean() / se) if se > 0 else np.nan), len(x)


def bucket_report(ev: pd.DataFrame, by: str, edges: list[float], horizon: int,
                  leg: str = "fwd", cost_bps: float = COST_BPS_SPOT) -> pd.DataFrame:
    col = f"{leg}_{horizon}m"
    if col not in ev.columns:
        return pd.DataFrame()
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sub = ev[(ev[by] > lo) & (ev[by] <= hi)]
        if sub.empty:
            continue
        m, t, n = _cluster_stats(sub, col)
        if not np.isfinite(m):
            continue
        rows.append(dict(bucket=f"({lo:g}, {hi:g}]", n_clusters=n,
                         mean_bps=m * 1e4, net_bps=m * 1e4 - cost_bps, t_stat=t,
                         hit=float((sub[col] > 0).mean())))
    return pd.DataFrame(rows)


def horizon_report(ev: pd.DataFrame, leg: str = "fwd",
                   cost_bps: float = COST_BPS_SPOT) -> pd.DataFrame:
    rows = []
    for h in HORIZONS:
        col = f"{leg}_{h}m"
        if col not in ev.columns:
            continue
        m, t, n = _cluster_stats(ev, col)
        if not np.isfinite(m):
            continue
        rows.append(dict(horizon_m=h, n_clusters=n, mean_bps=m * 1e4,
                         net_bps=m * 1e4 - cost_bps, t_stat=t,
                         hit=float((ev[col] > 0).mean())))
    return pd.DataFrame(rows)


def two_way(ev: pd.DataFrame, a: str, a_edges: list[float], b: str,
            b_edges: list[float], horizon: int, leg: str = "fwd") -> pd.DataFrame:
    """Mean forward return in basis points across a grid of two conditions."""
    col = f"{leg}_{horizon}m"
    out = pd.DataFrame(index=[f"({a_edges[i]:g},{a_edges[i+1]:g}]"
                              for i in range(len(a_edges) - 1)],
                       columns=[f"({b_edges[j]:g},{b_edges[j+1]:g}]"
                                for j in range(len(b_edges) - 1)], dtype=float)
    for i in range(len(a_edges) - 1):
        for j in range(len(b_edges) - 1):
            sub = ev[(ev[a] > a_edges[i]) & (ev[a] <= a_edges[i + 1]) &
                     (ev[b] > b_edges[j]) & (ev[b] <= b_edges[j + 1])]
            m, t, n = _cluster_stats(sub, col)
            out.iloc[i, j] = m * 1e4 if np.isfinite(m) and n >= 20 else np.nan
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=60)
    ap.add_argument("--leg", default="fwd", choices=["fwd", "pfwd"])
    ap.add_argument("--split", default="2025-04-01")
    a = ap.parse_args()

    pd.set_option("display.width", 220)
    ev = pd.read_parquet(EVENTS_PATH)
    cost = COST_BPS_SPOT if a.leg == "fwd" else COST_BPS_PERP
    leg_name = "SPOT" if a.leg == "fwd" else "PERP"
    print(f"{len(ev):,} event-minutes, {ev['cascade_id'].nunique():,} cascades, "
          f"{ev['symbol'].nunique()} symbols   leg={leg_name} cost={cost:.0f}bps rt")

    print(f"\n--- unconditional, by horizon ({leg_name}) ---")
    print(horizon_report(ev, a.leg, cost).to_string(index=False,
                                                    float_format=lambda v: f"{v:,.2f}"))

    print(f"\n--- by severity of the drop (z_drop), horizon {a.horizon}m ---")
    print(bucket_report(ev, "z_drop", [-100, -12, -8, -6, -5, -4, -3], a.horizon,
                        a.leg, cost).to_string(index=False,
                                               float_format=lambda v: f"{v:,.2f}"))

    print(f"\n--- by perp discount to spot index (prem_z_min), horizon {a.horizon}m ---")
    print(bucket_report(ev, "prem_z_min", [-100, -10, -6, -4, -2, 0, 100], a.horizon,
                        a.leg, cost).to_string(index=False,
                                               float_format=lambda v: f"{v:,.2f}"))

    print(f"\n--- by aggressive-sell burst, horizon {a.horizon}m ---")
    print(bucket_report(ev, "sell_burst", [0, 5, 20, 50, 150, 1e9], a.horizon,
                        a.leg, cost).to_string(index=False,
                                               float_format=lambda v: f"{v:,.2f}"))

    print(f"\n--- by exhaustion: position in the window range, horizon {a.horizon}m ---")
    print(bucket_report(ev, "range_pos", [-0.01, 0.05, 0.2, 0.4, 0.7, 1.01], a.horizon,
                        a.leg, cost).to_string(index=False,
                                               float_format=lambda v: f"{v:,.2f}"))

    print(f"\n--- by exhaustion: order-flow recovery, horizon {a.horizon}m ---")
    print(bucket_report(ev, "ofi_recover", [-0.01, 0.2, 0.5, 0.9, 1.3, 3.0], a.horizon,
                        a.leg, cost).to_string(index=False,
                                               float_format=lambda v: f"{v:,.2f}"))

    print(f"\n--- severity x perp discount, mean bps at {a.horizon}m ---")
    print(two_way(ev, "z_drop", [-100, -8, -5, -4, -3], "prem_z_min",
                  [-100, -6, -3, -1, 100], a.horizon, a.leg)
          .to_string(float_format=lambda v: f"{v:,.1f}"))

    print(f"\n--- severity x exhaustion (range_pos), mean bps at {a.horizon}m ---")
    print(two_way(ev, "z_drop", [-100, -8, -5, -4, -3], "range_pos",
                  [-0.01, 0.1, 0.3, 0.6, 1.01], a.horizon, a.leg)
          .to_string(float_format=lambda v: f"{v:,.1f}"))


if __name__ == "__main__":
    main()
