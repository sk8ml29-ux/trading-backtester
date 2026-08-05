"""
Binance Vision downloader (https://data.binance.vision) — public S3 dumps, NOT
geo-blocked (the live fapi endpoint returns HTTP 451 here). Full-history monthly
CSV dumps for:
  - USDT-M perpetual funding rates  (futures/um/monthly/fundingRate)
  - USDT-M perpetual klines         (futures/um/monthly/klines)
  - spot klines                     (spot/monthly/klines)

Caches merged CSVs to data/cache/vision_*.csv (UTC, tz-naive).
"""
from __future__ import annotations

import io
import ssl
import time
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
_UA = {"User-Agent": "Mozilla/5.0"}
_CTX = ssl.create_default_context()
BASE = "https://data.binance.vision/data"

# Broad, liquid USDT-perp basket (history varies; filtered by length downstream).
BASKET = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
          "LINKUSDT", "LTCUSDT", "BNBUSDT", "AVAXUSDT", "DOTUSDT", "TRXUSDT",
          "ETCUSDT", "BCHUSDT", "NEARUSDT", "ATOMUSDT", "APTUSDT", "ARBUSDT",
          "OPUSDT", "INJUSDT", "SUIUSDT", "FILUSDT", "AAVEUSDT", "UNIUSDT"]


def _months(start: str, end: str) -> list[str]:
    s = pd.Timestamp(start).replace(day=1)
    e = pd.Timestamp(end).replace(day=1)
    out = []
    while s <= e:
        out.append(s.strftime("%Y-%m"))
        s = (s + pd.Timedelta(days=32)).replace(day=1)
    return out


def _get_zip(url: str):
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, context=_CTX, timeout=40) as r:
            return zipfile.ZipFile(io.BytesIO(r.read()))
    except Exception:
        return None


def fetch_funding(symbol: str, start: str, end: str, refresh: bool = False) -> pd.DataFrame:
    path = CACHE / f"vision_funding_{symbol.lower()}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, parse_dates=["time"], index_col="time")
    frames = []
    for m in _months(start, end):
        url = f"{BASE}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{m}.zip"
        z = _get_zip(url)
        if z is None:
            continue
        name = z.namelist()[0]
        df = pd.read_csv(z.open(name))
        # header may or may not be present
        if "calc_time" not in df.columns:
            df = pd.read_csv(z.open(name), header=None,
                             names=["calc_time", "funding_interval_hours", "last_funding_rate"])
        frames.append(df[["calc_time", "last_funding_rate"]])
        time.sleep(0.03)
    if not frames:
        return pd.DataFrame(columns=["funding_rate"])
    out = pd.concat(frames, ignore_index=True)
    out["calc_time"] = pd.to_numeric(out["calc_time"], errors="coerce")
    out = out.dropna(subset=["calc_time"])
    ct = out["calc_time"].astype("int64")
    ct = ct.where(ct < 10**14, ct // 1000)
    out["time"] = pd.to_datetime(ct, unit="ms")
    out["funding_rate"] = pd.to_numeric(out["last_funding_rate"], errors="coerce")
    out = (out[["time", "funding_rate"]].dropna()
           .drop_duplicates("time").set_index("time").sort_index())
    out.to_csv(path)
    return out


def fetch_klines(symbol: str, market: str, interval: str, start: str, end: str,
                 refresh: bool = False) -> pd.DataFrame:
    """market: 'um' (perp) or 'spot'."""
    tag = "perp" if market == "um" else "spot"
    path = CACHE / f"vision_{tag}_{symbol.lower()}_{interval}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, parse_dates=["time"], index_col="time")
    prefix = f"{BASE}/futures/um" if market == "um" else f"{BASE}/spot"
    frames = []
    for m in _months(start, end):
        url = f"{prefix}/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{m}.zip"
        z = _get_zip(url)
        if z is None:
            continue
        name = z.namelist()[0]
        df = pd.read_csv(z.open(name), header=None)
        # Binance kline columns; some months include a header row -> coerce
        df = df.rename(columns={0: "open_time", 1: "open", 2: "high", 3: "low",
                                4: "close", 5: "volume", 6: "close_time"})
        frames.append(
            df[["open_time", "close_time", "open", "high", "low", "close", "volume"]]
        )
        time.sleep(0.03)
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = pd.concat(frames, ignore_index=True)
    out["open_time"] = pd.to_numeric(out["open_time"], errors="coerce")
    out["close_time"] = pd.to_numeric(out["close_time"], errors="coerce")
    out = out.dropna(subset=["open_time", "close_time"])
    # open_time is ms in older dumps and microseconds in newer ones; normalize
    # to ms per-row (values >= 1e14 are microseconds -> divide by 1000).
    ot = out["open_time"].astype("int64")
    ot = ot.where(ot < 10**14, ot // 1000)
    # Index by the first whole millisecond *after* the bar closes. An 8h bar
    # opened at 00:00 closes at 07:59:59.999 and is therefore observable at
    # 08:00. Indexing it by open_time lets a settlement-time signal see eight
    # hours of future prices.
    ct = out["close_time"].astype("int64")
    ct = ct.where(ct < 10**14, ct // 1000)
    out["time"] = pd.to_datetime(ct + 1, unit="ms")
    for c in ["open", "high", "low", "close", "volume"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = (out[["time", "open", "high", "low", "close", "volume"]].dropna()
           .drop_duplicates("time").set_index("time").sort_index())
    out.to_csv(path)
    return out


def download_basket(coins: list[str], start="2023-01-01", end="2026-06-30",
                    interval="8h", with_prices=True, refresh=False):
    out = {}
    for c in coins:
        try:
            f = fetch_funding(c, start, end, refresh)
            perp = fetch_klines(c, "um", interval, start, end, refresh) if with_prices else None
            spot = fetch_klines(c, "spot", interval, start, end, refresh) if with_prices else None
            n = len(f)
            print(f"  {c}: funding={n} perp={len(perp) if perp is not None else '-'} "
                  f"spot={len(spot) if spot is not None else '-'} "
                  f"{'' if f.empty else f.index[0].date()}..{'' if f.empty else f.index[-1].date()}")
            if n > 200:
                out[c] = dict(funding=f, perp=perp, spot=spot)
        except Exception as e:
            print(f"  {c}: FAIL {repr(e)[:100]}")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--interval", default="8h")
    ap.add_argument("--coins", default=",".join(BASKET))
    ap.add_argument("--no-prices", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    coins = args.coins.split(",")
    print(f"Binance Vision: {len(coins)} coins {args.start}..{args.end} interval={args.interval}")
    download_basket(coins, args.start, args.end, args.interval,
                    with_prices=not args.no_prices, refresh=args.refresh)
    print("done")
