"""
Self-contained loader for the public Binance Vision dumps.

Kept independent of anything else in the repo so this strategy stands on its
own. The live fapi REST endpoint is geo-blocked from this environment
(HTTP 451); the S3 dumps are not, which is why everything routes through them.
"""
from __future__ import annotations

import io
import ssl
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

BASE = "https://data.binance.vision/data"
S3_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "backdraft"
CACHE.mkdir(parents=True, exist_ok=True)

_UA = {"User-Agent": "Mozilla/5.0"}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

KL_NAMES = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "count", "taker_buy_base", "taker_buy_quote", "ignore"]


def fetch_zip(url: str, retries: int = 3) -> zipfile.ZipFile | None:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, context=_CTX, timeout=60) as r:
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


# Vision is inconsistent about kline column naming between datasets and eras.
_ALIASES = {
    "taker_buy_volume": "taker_buy_base",
    "taker_buy_base_asset_volume": "taker_buy_base",
    "taker_buy_quote_volume": "taker_buy_quote",
    "taker_buy_quote_asset_volume": "taker_buy_quote",
    "number_of_trades": "count",
    "quote_asset_volume": "quote_volume",
}


def read_csv(z: zipfile.ZipFile, names: list[str] | None) -> pd.DataFrame | None:
    """Read one dump, tolerating both headered and headerless files."""
    try:
        raw = z.read(z.namelist()[0])
        if not raw:
            return None
        head = raw[:300].decode("utf-8", "ignore").split("\n", 1)[0]
        has_header = any(c.isalpha() for c in head.split(",")[0])
        if has_header:
            df = pd.read_csv(io.BytesIO(raw))
            return df.rename(columns=_ALIASES)
        return pd.read_csv(io.BytesIO(raw), header=None, names=names)
    except Exception:
        return None


def months(start: str, end: str) -> list[str]:
    s = pd.Timestamp(start).replace(day=1)
    e = pd.Timestamp(end).replace(day=1)
    out = []
    while s <= e:
        out.append(s.strftime("%Y-%m"))
        s = (s + pd.Timedelta(days=32)).replace(day=1)
    return out


def norm_ms(s: pd.Series) -> pd.Series:
    """Vision switched from milliseconds to microseconds mid-2025."""
    v = pd.to_numeric(s, errors="coerce").astype("Int64")
    return v.where(v < 10**14, v // 1000)


def usdt_perp_symbols(refresh: bool = False) -> list[str]:
    path = CACHE / "symbols.txt"
    if path.exists() and not refresh:
        return path.read_text().split()
    prefix = "data/futures/um/daily/metrics/"
    req = urllib.request.Request(f"{S3_LIST}?delimiter=/&prefix={prefix}", headers=_UA)
    with urllib.request.urlopen(req, context=_CTX, timeout=90) as r:
        xml = r.read().decode()
    found = [p.split("</Prefix>")[0] for p in xml.split("<Prefix>")[1:]]
    syms = sorted({f[len(prefix):].rstrip("/") for f in found if f.startswith(prefix)})
    drop = {"BTCDOMUSDT", "DEFIUSDT", "BTCSTUSDT", "BLUEBIRDUSDT"}
    syms = [s for s in syms if s.endswith("USDT") and s and s not in drop]
    path.write_text("\n".join(syms))
    return syms


def fetch_klines_1h(symbol: str, start: str, end: str,
                    pool: ThreadPoolExecutor) -> pd.DataFrame:
    path = CACHE / f"kl1h_{symbol}.parquet"
    if path.exists():
        return pd.read_parquet(path)

    def one(m: str):
        z = fetch_zip(f"{BASE}/futures/um/monthly/klines/{symbol}/1h/{symbol}-1h-{m}.zip")
        return None if z is None else read_csv(z, KL_NAMES)

    frames = [f for f in pool.map(one, months(start, end)) if f is not None and not f.empty]
    if not frames:
        df = pd.DataFrame(columns=["time", "close", "quote_volume"])
        df.to_parquet(path)
        return df
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(norm_ms(df["open_time"]), unit="ms")
    for c in ["open", "high", "low", "close", "quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = (df[["time", "open", "high", "low", "close", "quote_volume"]].dropna()
          .drop_duplicates("time").sort_values("time").reset_index(drop=True))
    df.to_parquet(path)
    return df


def build_liquidity_summary(start="2022-06-01", end="2026-07-20",
                            workers: int = 48) -> pd.DataFrame:
    """Peak and median trailing dollar volume per symbol, for universe selection.

    Reuses a summary produced by earlier work if one is already on disk, since
    it is a pure cache artefact and costs an hour to rebuild.
    """
    path = CACHE / "liquidity_summary.parquet"
    if path.exists():
        return pd.read_parquet(path)
    legacy = CACHE.parent / "ghostbook" / "liquidity_summary.parquet"
    if legacy.exists():
        df = pd.read_parquet(legacy)
        df.to_parquet(path)
        return df

    syms = usdt_perp_symbols()
    rows = []
    with ThreadPoolExecutor(workers) as pool:
        for i, s in enumerate(syms, 1):
            kl = fetch_klines_1h(s, start, end, pool)
            if kl.empty or len(kl) < 24 * 60:
                continue
            v = kl.set_index("time")["quote_volume"].astype(float)
            daily = v.rolling(24, min_periods=12).sum()
            liq = daily.rolling(24 * 30, min_periods=24 * 5).median().shift(1)
            if liq.dropna().empty:
                continue
            rows.append(dict(symbol=s, peak_liq=liq.max(), med_liq=liq.median(),
                             days_above_20m=int((liq >= 20e6).sum() / 24),
                             first=kl["time"].min(), last=kl["time"].max()))
            if i % 50 == 0:
                print(f"  liquidity {i}/{len(syms)}", flush=True)
    df = pd.DataFrame(rows)
    df.to_parquet(path)
    return df
