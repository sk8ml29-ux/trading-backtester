"""
Bar panel: minute dumps aggregated into a wide (time x symbol) view.

Everything downstream works on matrices rather than a long frame, because the
strategy is a cross-section: at each bar it compares every coin against every
other one, and that is a row operation.

Per bar and symbol we keep

  ret      log return of the perpetual
  ofi      order-flow imbalance, the share of the bar's volume that was
           aggressive buying rescaled to [-1, 1]
  qvol     dollar volume
  cnt      trade count
  prem     mean basis against the spot index
  prem_x   the most extreme basis print inside the bar, which is where a
           liquidity squeeze shows up even if the bar closes calmly
"""
from __future__ import annotations

import argparse
import gc

import numpy as np
import pandas as pd

from .data import CACHE, pool_symbols

FIELDS = ["ret", "ofi", "qvol", "cnt", "prem", "prem_x", "close", "open"]


def _bar_frame(sym: str, bar_min: int) -> pd.DataFrame | None:
    p = CACHE / f"perp1m_{sym}.parquet"
    if not p.exists():
        return None
    perp = pd.read_parquet(p)
    if perp.empty or len(perp) < 20000:
        return None
    prem_path = CACHE / f"premium1m_{sym}.parquet"
    prem = pd.read_parquet(prem_path) if prem_path.exists() else pd.DataFrame()

    df = perp.set_index("time").sort_index()
    rule = f"{bar_min}min"
    agg = pd.DataFrame({
        "open": df["open"].resample(rule).first(),
        "close": df["close"].resample(rule).last(),
        "qvol": df["quote_volume"].resample(rule).sum(),
        "tbq": df["taker_buy_quote"].resample(rule).sum(),
        "cnt": df["count"].resample(rule).sum(),
    })
    agg["ret"] = np.log(agg["close"]).diff()
    q = agg["qvol"].to_numpy()
    b = agg["tbq"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        agg["ofi"] = np.where(q > 0, (2.0 * b - q) / q, np.nan)

    if not prem.empty:
        pr = prem.set_index("time").sort_index()["prem_close"]
        agg["prem"] = pr.resample(rule).mean()
        # Most negative print inside the bar: a squeeze leaves a mark here even
        # when the close looks ordinary.
        agg["prem_x"] = pr.resample(rule).min()
    else:
        agg["prem"] = np.nan
        agg["prem_x"] = np.nan

    agg = agg.drop(columns=["tbq"])
    del perp, prem, df
    gc.collect()
    return agg


def build(symbols: list[str], bar_min: int = 60,
          verbose: bool = True) -> dict[str, pd.DataFrame]:
    """Wide matrices, one per field."""
    per_sym: dict[str, pd.DataFrame] = {}
    for i, s in enumerate(symbols, 1):
        f = _bar_frame(s, bar_min)
        if f is not None and len(f) > 5000:
            per_sym[s] = f
        if verbose and i % 20 == 0:
            print(f"  bars {i}/{len(symbols)} kept={len(per_sym)}", flush=True)
    if not per_sym:
        return {}
    out: dict[str, pd.DataFrame] = {}
    for field in FIELDS:
        out[field] = pd.DataFrame({s: f[field] for s, f in per_sym.items()}).sort_index()
    return out


def save(mats: dict[str, pd.DataFrame], bar_min: int) -> None:
    for field, m in mats.items():
        m.to_parquet(CACHE / f"mat_{field}_{bar_min}m.parquet")


def load(bar_min: int = 60) -> dict[str, pd.DataFrame]:
    out = {}
    for field in FIELDS:
        p = CACHE / f"mat_{field}_{bar_min}m.parquet"
        if p.exists():
            out[field] = pd.read_parquet(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--bar", type=int, default=60)
    a = ap.parse_args()
    syms = pool_symbols(a.top)
    print(f"building {a.bar}m bars for {len(syms)} symbols", flush=True)
    mats = build(syms, a.bar)
    if not mats:
        print("nothing built")
        return
    save(mats, a.bar)
    r = mats["ret"]
    print(f"saved: {r.shape[0]:,} bars x {r.shape[1]} symbols  "
          f"{r.index.min()} .. {r.index.max()}")


if __name__ == "__main__":
    main()
