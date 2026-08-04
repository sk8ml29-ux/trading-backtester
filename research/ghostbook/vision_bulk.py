"""
Bulk loader for Binance Vision public S3 dumps (https://data.binance.vision).

Two datasets power the Ghost Book research:

  metrics  futures/um/daily/metrics/<SYM>/<SYM>-metrics-<YYYY-MM-DD>.zip
           5-minute open interest (coins + USD notional) and crowd positioning
           ratios. This is the raw material for the position-map reconstruction.
           Only published as daily files, so a full universe means ~10^5 objects
           -> everything here is threaded and cached to parquet.

  klines   futures/um/monthly/klines/<SYM>/1h/<SYM>-1h-<YYYY-MM>.zip
           Hourly OHLCV, used for tradeable prices and the point-in-time
           liquidity screen that defines the universe.

  funding  futures/um/monthly/fundingRate/<SYM>/<SYM>-fundingRate-<YYYY-MM>.zip
           Realised funding, charged on the perp legs in the backtest.

The live fapi REST endpoint is geo-blocked from this environment (HTTP 451);
these S3 dumps are not, which is why everything routes through them.
"""
from __future__ import annotations

import io
import ssl
import threading
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

BASE = "https://data.binance.vision/data"
S3_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "ghostbook"
CACHE.mkdir(parents=True, exist_ok=True)

_UA = {"User-Agent": "Mozilla/5.0"}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

METRIC_COLS = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
               "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
               "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]

_print_lock = threading.Lock()


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _fetch_zip(url: str, retries: int = 3) -> zipfile.ZipFile | None:
    """Return the zip at `url`, or None for a genuine 404 (data does not exist)."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, context=_CTX, timeout=45) as r:
                return zipfile.ZipFile(io.BytesIO(r.read()))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            if attempt == retries - 1:
                return None
        except Exception:
            if attempt == retries - 1:
                return None
    return None


def _read_csv(z: zipfile.ZipFile, names: list[str] | None) -> pd.DataFrame | None:
    try:
        name = z.namelist()[0]
        raw = z.read(name)
        if not raw:
            return None
        head = raw[:200].decode("utf-8", "ignore").split("\n", 1)[0]
        # Vision dumps are inconsistent: some months carry a header row, some don't.
        has_header = any(c.isalpha() for c in head.split(",")[0])
        if has_header:
            return pd.read_csv(io.BytesIO(raw))
        return pd.read_csv(io.BytesIO(raw), header=None, names=names)
    except Exception:
        return None


# --------------------------------------------------------------------------
# symbol discovery
# --------------------------------------------------------------------------

def list_symbols(dataset: str = "metrics", refresh: bool = False) -> list[str]:
    """All symbols that have at least one object under the given dataset prefix."""
    path = CACHE / f"symbols_{dataset}.txt"
    if path.exists() and not refresh:
        return path.read_text().split()
    prefix = f"data/futures/um/daily/{dataset}/"
    out: list[str] = []
    token = ""
    while True:
        url = f"{S3_LIST}?delimiter=/&prefix={prefix}"
        if token:
            url += f"&marker={token}"
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, context=_CTX, timeout=60) as r:
            xml = r.read().decode()
        found = pd.Series(xml.split("<Prefix>")[1:]).str.split("</Prefix>").str[0]
        found = [p[len(prefix):].rstrip("/") for p in found if p.startswith(prefix)]
        found = [f for f in found if f]
        out.extend(found)
        if "<IsTruncated>true</IsTruncated>" not in xml or not found:
            break
        token = prefix + found[-1] + "/"
    out = sorted(set(out))
    path.write_text("\n".join(out))
    return out


def usdt_perp_symbols(refresh: bool = False) -> list[str]:
    """USDT-margined perps only. Excludes BUSD/USDC duplicates and index products."""
    syms = list_symbols("metrics", refresh)
    drop = {"BTCDOMUSDT", "DEFIUSDT", "BTCSTUSDT", "BLUEBIRDUSDT"}
    return [s for s in syms if s.endswith("USDT") and s not in drop]


# --------------------------------------------------------------------------
# monthly datasets (klines, funding)
# --------------------------------------------------------------------------

def _months(start: str, end: str) -> list[str]:
    s = pd.Timestamp(start).replace(day=1)
    e = pd.Timestamp(end).replace(day=1)
    out = []
    while s <= e:
        out.append(s.strftime("%Y-%m"))
        s = (s + pd.Timedelta(days=32)).replace(day=1)
    return out


_KL_NAMES = ["open_time", "open", "high", "low", "close", "volume", "close_time",
             "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]


def _norm_ms(s: pd.Series) -> pd.Series:
    """Vision switched from ms to microseconds mid-2025; normalise per row."""
    v = pd.to_numeric(s, errors="coerce").astype("Int64")
    return v.where(v < 10**14, v // 1000)


def fetch_klines_1h(symbol: str, start: str, end: str, pool: ThreadPoolExecutor,
                    refresh: bool = False) -> pd.DataFrame:
    path = CACHE / f"kl1h_{symbol}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)

    def one(m: str):
        url = f"{BASE}/futures/um/monthly/klines/{symbol}/1h/{symbol}-1h-{m}.zip"
        z = _fetch_zip(url)
        return None if z is None else _read_csv(z, _KL_NAMES)

    frames = [f for f in pool.map(one, _months(start, end)) if f is not None and not f.empty]
    if not frames:
        df = pd.DataFrame(columns=["time", "open", "high", "low", "close", "quote_volume"])
        df.to_parquet(path)
        return df
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(_norm_ms(df["open_time"]), unit="ms")
    for c in ["open", "high", "low", "close", "quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = (df[["time", "open", "high", "low", "close", "quote_volume"]]
          .dropna().drop_duplicates("time").sort_values("time").reset_index(drop=True))
    df.to_parquet(path)
    return df


def fetch_funding(symbol: str, start: str, end: str, pool: ThreadPoolExecutor,
                  refresh: bool = False) -> pd.DataFrame:
    path = CACHE / f"fund_{symbol}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)

    def one(m: str):
        url = f"{BASE}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{m}.zip"
        z = _fetch_zip(url)
        return None if z is None else _read_csv(
            z, ["calc_time", "funding_interval_hours", "last_funding_rate"])

    frames = [f for f in pool.map(one, _months(start, end)) if f is not None and not f.empty]
    if not frames:
        df = pd.DataFrame(columns=["time", "funding_rate"])
        df.to_parquet(path)
        return df
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(_norm_ms(df["calc_time"]), unit="ms")
    df["funding_rate"] = pd.to_numeric(df["last_funding_rate"], errors="coerce")
    df = (df[["time", "funding_rate"]].dropna()
          .drop_duplicates("time").sort_values("time").reset_index(drop=True))
    df.to_parquet(path)
    return df


# --------------------------------------------------------------------------
# daily metrics (5-minute open interest + positioning)
# --------------------------------------------------------------------------

def fetch_metrics(symbol: str, start: str, end: str, pool: ThreadPoolExecutor,
                  refresh: bool = False) -> pd.DataFrame:
    """5-minute open interest and crowd positioning for one symbol.

    Adds `price`, the OI-implied mark price (USD notional / coin notional). That
    saves downloading a separate 5-minute kline series and is exactly the price
    at which the exchange values the open book.
    """
    path = CACHE / f"metrics_{symbol}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)

    days = [d.strftime("%Y-%m-%d") for d in pd.date_range(start, end, freq="D")]

    def one(d: str):
        url = f"{BASE}/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{d}.zip"
        z = _fetch_zip(url)
        return None if z is None else _read_csv(z, METRIC_COLS)

    frames = [f for f in pool.map(one, days) if f is not None and not f.empty]
    if not frames:
        df = pd.DataFrame(columns=["time", "oi", "oi_usd", "price", "tt_pos_ls",
                                   "tt_acct_ls", "acct_ls", "taker_ls"])
        df.to_parquet(path)
        return df

    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["create_time"], errors="coerce")
    ren = {"sum_open_interest": "oi", "sum_open_interest_value": "oi_usd",
           "sum_toptrader_long_short_ratio": "tt_pos_ls",
           "count_toptrader_long_short_ratio": "tt_acct_ls",
           "count_long_short_ratio": "acct_ls",
           "sum_taker_long_short_vol_ratio": "taker_ls"}
    for src, dst in ren.items():
        df[dst] = pd.to_numeric(df.get(src), errors="coerce")
    df = df.dropna(subset=["time", "oi", "oi_usd"])
    df = df[df["oi"] > 0]
    df["price"] = df["oi_usd"] / df["oi"]
    df = (df[["time", "oi", "oi_usd", "price", "tt_pos_ls", "tt_acct_ls", "acct_ls", "taker_ls"]]
          .drop_duplicates("time").sort_values("time").reset_index(drop=True))
    df.to_parquet(path)
    return df


# --------------------------------------------------------------------------
# universe-level drivers
# --------------------------------------------------------------------------

def load_universe_klines(symbols: list[str], start: str, end: str, workers: int = 48,
                         refresh: bool = False) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(workers) as pool:
        for i, s in enumerate(symbols, 1):
            df = fetch_klines_1h(s, start, end, pool, refresh)
            if len(df) > 24 * 30:
                out[s] = df
            if i % 50 == 0:
                _log(f"  klines {i}/{len(symbols)} kept={len(out)}")
    return out


def load_universe_metrics(symbols: list[str], start: str, end: str, workers: int = 48,
                          refresh: bool = False) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(workers) as pool:
        for i, s in enumerate(symbols, 1):
            df = fetch_metrics(s, start, end, pool, refresh)
            if len(df) > 288 * 30:
                out[s] = df
            _log(f"  metrics {i}/{len(symbols)} {s} rows={len(df)} kept={len(out)}")
    return out


def load_universe_funding(symbols: list[str], start: str, end: str, workers: int = 48,
                          refresh: bool = False) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(workers) as pool:
        for i, s in enumerate(symbols, 1):
            df = fetch_funding(s, start, end, pool, refresh)
            if not df.empty:
                out[s] = df
            if i % 50 == 0:
                _log(f"  funding {i}/{len(symbols)}")
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Bulk-download Binance Vision data")
    ap.add_argument("--what", choices=["klines", "metrics", "funding", "symbols"],
                    default="symbols")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2026-07-20")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    syms = args.symbols.split(",") if args.symbols else usdt_perp_symbols(args.refresh)
    print(f"{len(syms)} USDT perp symbols with metrics history")
    if args.what == "klines":
        got = load_universe_klines(syms, args.start, args.end, args.workers, args.refresh)
        print(f"klines cached for {len(got)} symbols")
    elif args.what == "metrics":
        got = load_universe_metrics(syms, args.start, args.end, args.workers, args.refresh)
        print(f"metrics cached for {len(got)} symbols")
    elif args.what == "funding":
        got = load_universe_funding(syms, args.start, args.end, args.workers, args.refresh)
        print(f"funding cached for {len(got)} symbols")
