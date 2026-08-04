"""
Download the 5-minute metrics history for the tradeable pool.

The pool is every USDT perp that ever sustained real volume, including names
that have since been delisted, so the later study is not limited to survivors.
Each symbol is fetched only over its own listed lifetime, which cuts the object
count roughly in half versus a naive full-range sweep.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .vision_bulk import CACHE, fetch_funding, fetch_metrics

MIN_PEAK_LIQ = 20e6
MIN_DAYS_ABOVE = 60
PAD_DAYS = 40          # metrics start before the kline history to warm the book


def pool_symbols() -> pd.DataFrame:
    summ = pd.read_parquet(CACHE / "liquidity_summary.parquet")
    sel = summ[(summ["peak_liq"] >= MIN_PEAK_LIQ) & (summ["days_above_20m"] >= MIN_DAYS_ABOVE)]
    return sel.sort_values("peak_liq", ascending=False).reset_index(drop=True)


def main(workers: int = 56, what: str = "metrics") -> None:
    sel = pool_symbols()
    total_days = int(((sel["last"] - sel["first"]).dt.days + PAD_DAYS).sum())
    print(f"pool={len(sel)} symbols, ~{total_days} symbol-days to fetch", flush=True)

    done = 0
    with ThreadPoolExecutor(workers) as pool:
        for i, r in sel.iterrows():
            sym = r["symbol"]
            start = (r["first"] - pd.Timedelta(days=PAD_DAYS)).strftime("%Y-%m-%d")
            end = min(r["last"] + pd.Timedelta(days=2), pd.Timestamp("2026-07-20")).strftime("%Y-%m-%d")
            if what == "metrics":
                df = fetch_metrics(sym, start, end, pool)
            else:
                df = fetch_funding(sym, start, end, pool)
            done += 1
            if len(df) > 0:
                print(f"  [{done}/{len(sel)}] {sym} rows={len(df)}", flush=True)
            else:
                print(f"  [{done}/{len(sel)}] {sym} EMPTY", flush=True)


if __name__ == "__main__":
    main(workers=int(sys.argv[1]) if len(sys.argv) > 1 else 56,
         what=sys.argv[2] if len(sys.argv) > 2 else "metrics")
