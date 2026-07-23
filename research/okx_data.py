"""
OKX public data downloader (no API key). Binance futures is geo-blocked (HTTP 451)
from this environment, so we use OKX for funding-rate history and candles.

Provides:
  - funding rate history (8h) for USDT perpetuals   -> data/cache/okx_funding_<coin>.csv
  - candles (spot + swap) at a given bar            -> data/cache/okx_<kind>_<coin>_<bar>.csv

All timestamps are UTC (tz-naive) to match the rest of the repo.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.request
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
_UA = {"User-Agent": "Mozilla/5.0"}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

BASE = "https://www.okx.com"

# Liquid USDT perps with long OKX history.
BASKET = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "LTC",
          "BNB", "AVAX", "DOT", "TRX", "ETC", "BCH", "FIL"]


def _get(url: str, tries: int = 5):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, context=_CTX, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if a == tries - 1:
                raise
            time.sleep(1.5 * (a + 1))
    return {}


def fetch_funding(coin: str, start_ms: int, refresh: bool = False) -> pd.DataFrame:
    inst = f"{coin}-USDT-SWAP"
    path = CACHE / f"okx_funding_{coin.lower()}.csv"
    if path.exists() and not refresh:
        df = pd.read_csv(path, parse_dates=["time"], index_col="time")
        return df
    rows = []
    after = ""
    while True:
        url = f"{BASE}/api/v5/public/funding-rate-history?instId={inst}&limit=100"
        if after:
            url += f"&after={after}"
        d = _get(url)
        data = d.get("data", [])
        if not data:
            break
        rows.extend(data)
        oldest = int(data[-1]["fundingTime"])
        after = str(oldest)
        if oldest <= start_ms or len(data) < 100:
            break
        time.sleep(0.08)
    if not rows:
        return pd.DataFrame(columns=["funding_rate"])
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms")
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df = df[["time", "funding_rate"]].dropna().drop_duplicates("time").set_index("time").sort_index()
    df.to_csv(path)
    return df


def fetch_candles(coin: str, kind: str, bar: str, start_ms: int,
                  refresh: bool = False) -> pd.DataFrame:
    """kind: 'swap' (BTC-USDT-SWAP) or 'spot' (BTC-USDT)."""
    inst = f"{coin}-USDT-SWAP" if kind == "swap" else f"{coin}-USDT"
    path = CACHE / f"okx_{kind}_{coin.lower()}_{bar}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, parse_dates=["time"], index_col="time")
    rows = []
    after = ""
    while True:
        url = f"{BASE}/api/v5/market/history-candles?instId={inst}&bar={bar}&limit=100"
        if after:
            url += f"&after={after}"
        d = _get(url)
        data = d.get("data", [])
        if not data:
            break
        rows.extend(data)
        oldest = int(data[-1][0])
        after = str(oldest)
        if oldest <= start_ms or len(data) < 100:
            break
        time.sleep(0.06)
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df[0].astype("int64"), unit="ms")
    for i, c in enumerate(["open", "high", "low", "close"], start=1):
        df[c] = pd.to_numeric(df[i], errors="coerce")
    df["volume"] = pd.to_numeric(df[5], errors="coerce")
    df = df[["time", "open", "high", "low", "close", "volume"]].dropna()
    df = df.drop_duplicates("time").set_index("time").sort_index()
    df.to_csv(path)
    return df


def download_basket(coins: list[str], bar: str = "4H", start: str = "2023-01-01",
                    with_spot: bool = True, refresh: bool = False):
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    out = {}
    for c in coins:
        try:
            f = fetch_funding(c, start_ms, refresh)
            sw = fetch_candles(c, "swap", bar, start_ms, refresh)
            sp = fetch_candles(c, "spot", bar, start_ms, refresh) if with_spot else None
            ok = len(f) > 100 and len(sw) > 100
            print(f"  {c}: funding={len(f)} swap={len(sw)} "
                  f"spot={len(sp) if sp is not None else '-'} "
                  f"{'OK' if ok else 'THIN'}")
            if ok:
                out[c] = dict(funding=f, swap=sw, spot=sp)
        except Exception as e:
            print(f"  {c}: FAIL {repr(e)[:100]}")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--bar", default="4H")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--coins", default=",".join(BASKET))
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    coins = args.coins.split(",")
    print(f"Downloading OKX data for {len(coins)} coins, bar={args.bar}, start={args.start}")
    download_basket(coins, args.bar, args.start, with_spot=True, refresh=args.refresh)
    print("done")
