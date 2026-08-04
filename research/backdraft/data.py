"""
Minute-resolution data for the Backdraft study.

Three series per symbol, all from the public Binance Vision dumps:

  perp 1m klines     OHLCV plus the taker-buy split, which gives order-flow
                     imbalance per minute without touching tick data
  premium index 1m   the perpetual's basis against the spot index; a sharp
                     negative print is the market saying the perp is being
                     dumped faster than spot can follow
  spot 1m klines     the leg actually traded, so execution is priced on the
                     venue the strategy would really use

Open interest comes from the 5-minute metrics already cached by the earlier
work and is joined in later.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

from .vision import (BASE, CACHE, KL_NAMES, build_liquidity_summary, fetch_zip,
                     months, norm_ms, read_csv)

DATASETS = {
    "perp":    ("futures/um", "klines"),
    "premium": ("futures/um", "premiumIndexKlines"),
    "spot":    ("spot", "klines"),
}


def _fetch_1m(symbol: str, kind: str, start: str, end: str,
              pool: ThreadPoolExecutor) -> pd.DataFrame:
    root, folder = DATASETS[kind]
    urls = [f"{BASE}/{root}/monthly/{folder}/{symbol}/1m/{symbol}-1m-{m}.zip"
            for m in months(start, end)]

    def one(u: str):
        z = fetch_zip(u)
        return None if z is None else read_csv(z, KL_NAMES)

    frames = [f for f in pool.map(one, urls) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(norm_ms(df["open_time"]), unit="ms")
    num = ["open", "high", "low", "close", "volume", "quote_volume", "count",
           "taker_buy_base", "taker_buy_quote"]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = (df[["time"] + num].dropna(subset=["time", "close"])
          .drop_duplicates("time").sort_values("time").reset_index(drop=True))
    return df


def spot_symbol(perp: str) -> str:
    """Spot ticker for a perp.

    Binance lists small-denomination coins as `1000X` perps against a plain `X`
    spot pair. The constant factor cancels in returns, so the spot series is a
    valid execution leg once the name is mapped.
    """
    for prefix in ("1000000", "1000", "1M"):
        if perp.startswith(prefix) and perp != f"{prefix}USDT":
            return perp[len(prefix):]
    return perp


def fetch_symbol(symbol: str, kind: str, start: str, end: str,
                 pool: ThreadPoolExecutor, refresh: bool = False) -> pd.DataFrame:
    path = CACHE / f"{kind}1m_{symbol}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    fetch_name = spot_symbol(symbol) if kind == "spot" else symbol
    df = _fetch_1m(fetch_name, kind, start, end, pool)
    if kind == "premium" and not df.empty:
        # Only the OHLC of the basis carries information here.
        df = df[["time", "open", "high", "low", "close"]].rename(
            columns={"open": "prem_open", "high": "prem_high",
                     "low": "prem_low", "close": "prem_close"})
    elif kind == "spot" and not df.empty:
        df = df[["time", "open", "high", "low", "close", "quote_volume",
                 "taker_buy_quote"]].rename(
            columns={"open": "s_open", "high": "s_high", "low": "s_low",
                     "close": "s_close", "quote_volume": "s_qvol",
                     "taker_buy_quote": "s_tbq"})
    df.to_parquet(path)
    return df


def pool_symbols(top_n: int = 150) -> list[str]:
    """Most liquid perps that ever sustained real volume."""
    summ = build_liquidity_summary()
    sel = summ[(summ["peak_liq"] >= 20e6) & (summ["days_above_20m"] >= 60)]
    return sel.sort_values("peak_liq", ascending=False)["symbol"].head(top_n).tolist()


def download(top_n: int, start: str, end: str, workers: int, kinds: list[str],
             refresh: bool = False) -> None:
    syms = pool_symbols(top_n)
    print(f"{len(syms)} symbols x {len(kinds)} series, {start}..{end}", flush=True)
    with ThreadPoolExecutor(workers) as pool:
        for i, s in enumerate(syms, 1):
            got = []
            for k in kinds:
                try:
                    df = fetch_symbol(s, k, start, end, pool, refresh)
                    got.append(f"{k}={len(df)}")
                except Exception as e:
                    got.append(f"{k}=FAIL{repr(e)[:40]}")
            print(f"  [{i}/{len(syms)}] {s}  " + "  ".join(got), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=150)
    ap.add_argument("--start", default="2022-06-01")
    ap.add_argument("--end", default="2026-07-20")
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--kinds", default="perp,premium,spot")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    download(a.top, a.start, a.end, a.workers, a.kinds.split(","), a.refresh)
    print("done")
