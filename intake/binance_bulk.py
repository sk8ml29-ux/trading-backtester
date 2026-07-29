"""Resumable Binance Vision bulk archive for historical USDT perpetuals.

The archive is raw-first:
1. discover every symbol ever present in official funding archives;
2. infer first/last archive month (including delisted contracts);
3. plan tiered funding/kline jobs;
4. download monthly ZIPs with official SHA-256 verification and resume;
5. merge verified raw files into causal CSVs for research.

No authenticated or trading endpoint is used.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

WORKSPACE = Path(__file__).resolve().parent.parent
ROOT = WORKSPACE / "data" / "cache" / "binance_bulk"
RAW = ROOT / "raw"
MERGED = ROOT / "merged"
CATALOG_PATH = ROOT / "usdt_perp_lifecycle.json"
MANIFEST_PATH = ROOT / "download_manifest.json"
S3_ENDPOINT = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DATA_BASE = "https://data.binance.vision/data"
FUNDING_PREFIX = "data/futures/um/monthly/fundingRate/"
SCHEMA_VERSION = 1

SYMBOL_RE = re.compile(r"^[^/\\\x00-\x1f\x7f]{2,80}$")
MONTH_RE = re.compile(r"-(\d{4}-\d{2})\.zip$")
TIER_DATASETS = {
    "core": ("funding", "8h"),
    "swing": ("funding", "8h", "1h"),
    "intraday": ("funding", "8h", "1h", "15m"),
}
# Conservative rough ZIP + merged CSV estimate per symbol-month.
ESTIMATED_TOTAL_BYTES = {
    "funding": 45_000,
    "8h": 70_000,
    "1h": 260_000,
    "15m": 900_000,
}


@dataclass(frozen=True)
class Job:
    dataset: str
    symbol: str
    month: str
    interval: str | None
    url: str
    path: Path

    @property
    def key(self) -> str:
        if self.dataset == "funding":
            return f"funding/{self.symbol}/{self.month}"
        return f"perp/{self.interval}/{self.symbol}/{self.month}"


@contextlib.contextmanager
def _file_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.write("\n")
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _get(url: str, timeout: int = 40, retries: int = 4) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "binance-bulk-research/1.0"}
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError:
            raise
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _list_xml(prefix: str, delimiter: str | None = None) -> ET.Element:
    combined = ET.Element("CombinedList")
    continuation = None
    while True:
        query = {"list-type": "2", "prefix": prefix}
        if delimiter is not None:
            query["delimiter"] = delimiter
        if continuation:
            query["continuation-token"] = continuation
        url = S3_ENDPOINT + "?" + urllib.parse.urlencode(query)
        page = ET.fromstring(_get(url))
        for name in ("CommonPrefixes", "Contents"):
            for element in _elements(page, name):
                combined.append(element)
        truncated = next(
            (element.text for element in _elements(page, "IsTruncated")),
            "false",
        )
        if str(truncated).lower() != "true":
            break
        continuation = next(
            (
                element.text
                for element in _elements(page, "NextContinuationToken")
                if element.text
            ),
            None,
        )
        if not continuation:
            raise ValueError("S3 listing is truncated without continuation token")
    return combined


def _elements(root: ET.Element, local_name: str):
    return [
        element
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == local_name
    ]


def validate_symbol(symbol: str) -> str:
    symbol = symbol.strip()
    if not SYMBOL_RE.fullmatch(symbol) or symbol in {".", ".."}:
        raise ValueError(f"Unsafe symbol: {symbol!r}")
    return symbol


def storage_key(symbol: str) -> str:
    return urllib.parse.quote(validate_symbol(symbol), safe="")


def discover_symbols(quote_asset: str = "USDT") -> list[str]:
    root = _list_xml(FUNDING_PREFIX, delimiter="/")
    symbols = []
    for element in _elements(root, "Prefix"):
        text = element.text or ""
        if text == FUNDING_PREFIX or not text.startswith(FUNDING_PREFIX):
            continue
        symbol = text[len(FUNDING_PREFIX):].strip("/")
        if symbol.endswith(quote_asset):
            symbols.append(validate_symbol(symbol))
    return sorted(set(symbols))


def funding_months(symbol: str) -> list[str]:
    symbol = validate_symbol(symbol)
    prefix = f"{FUNDING_PREFIX}{symbol}/"
    root = _list_xml(prefix)
    months = []
    for element in _elements(root, "Key"):
        match = MONTH_RE.search(element.text or "")
        if match:
            months.append(match.group(1))
    return sorted(set(months))


def _previous_complete_month(today: date | None = None) -> str:
    current = pd.Timestamp(today or date.today()).replace(day=1)
    return (current - pd.Timedelta(days=1)).strftime("%Y-%m")


def build_catalog(
    quote_asset: str = "USDT",
    workers: int = 12,
    max_symbols: int | None = None,
    output: Path = CATALOG_PATH,
) -> dict:
    previous = (
        json.loads(output.read_text())
        if output.exists()
        else {"symbols": {}}
    )
    discovered = discover_symbols(quote_asset)
    symbols = sorted(set(discovered) | set(previous.get("symbols", {})))
    if max_symbols:
        symbols = symbols[:max_symbols]
    lifecycle = {}
    errors = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(funding_months, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                months = future.result()
                if months:
                    lifecycle[symbol] = {
                        "symbol": symbol,
                        "storage_key": storage_key(symbol),
                        "first_month": months[0],
                        "last_month": months[-1],
                        "funding_months": months,
                        "month_count": len(months),
                        "status_inferred": (
                            "recent"
                            if months[-1] >= _previous_complete_month()
                            else "historical_or_delisted"
                        ),
                        "source": "official_binance_vision_s3",
                    }
            except Exception as exc:
                errors[symbol] = repr(exc)
                if symbol in previous.get("symbols", {}):
                    lifecycle[symbol] = {
                        **previous["symbols"][symbol],
                        "catalog_refresh": "preserved_after_error",
                    }
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "quote_asset": quote_asset,
        "symbols_discovered": len(discovered),
        "symbols_attempted": len(symbols),
        "symbols_with_history": len(lifecycle),
        "historical_or_delisted": sum(
            row["status_inferred"] == "historical_or_delisted"
            for row in lifecycle.values()
        ),
        "symbols": dict(sorted(lifecycle.items())),
        "errors": errors,
    }
    with _file_lock(output):
        # Re-read under lock and preserve any rows another completed refresh
        # added while this network scan was running.
        if output.exists():
            latest = json.loads(output.read_text())
            for symbol, row in latest.get("symbols", {}).items():
                lifecycle.setdefault(symbol, row)
            catalog["symbols"] = dict(sorted(lifecycle.items()))
            catalog["symbols_with_history"] = len(lifecycle)
            catalog["historical_or_delisted"] = sum(
                row["status_inferred"] == "historical_or_delisted"
                for row in lifecycle.values()
            )
        _atomic_json(output, catalog)
    return catalog


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"No catalog at {path}. Run: python -m intake.binance_bulk catalog"
        )
    return json.loads(path.read_text())


def symbols_at(catalog: dict, month: str) -> list[str]:
    return sorted(
        symbol
        for symbol, row in catalog["symbols"].items()
        if row["first_month"] <= month <= row["last_month"]
        and month in row["funding_months"]
    )


def _month_range(start: str, end: str) -> set[str]:
    first = pd.Timestamp(start + "-01")
    last = pd.Timestamp(end + "-01")
    if last < first:
        raise ValueError("end month precedes start month")
    return set(pd.period_range(first, last, freq="M").astype(str))


def _job(dataset: str, symbol: str, month: str) -> Job:
    encoded = urllib.parse.quote(symbol, safe="")
    key = storage_key(symbol)
    if dataset == "funding":
        filename = f"{symbol}-fundingRate-{month}.zip"
        url = (
            f"{DATA_BASE}/futures/um/monthly/fundingRate/"
            f"{encoded}/{urllib.parse.quote(filename, safe='')}"
        )
        path = RAW / "funding" / key / f"{month}.zip"
        return Job(dataset, symbol, month, None, url, path)
    interval = dataset
    filename = f"{symbol}-{interval}-{month}.zip"
    url = (
        f"{DATA_BASE}/futures/um/monthly/klines/"
        f"{encoded}/{interval}/{urllib.parse.quote(filename, safe='')}"
    )
    path = RAW / "perp" / interval / key / f"{month}.zip"
    return Job("perp", symbol, month, interval, url, path)


def plan_jobs(
    catalog: dict,
    tier: str,
    start: str,
    end: str,
    symbols: Iterable[str] | None = None,
    max_symbols: int | None = None,
) -> list[Job]:
    if tier not in TIER_DATASETS:
        raise ValueError(f"Unknown tier: {tier}")
    allowed_months = _month_range(start, end)
    selected = sorted(symbols or catalog["symbols"].keys())
    unknown = set(selected) - set(catalog["symbols"])
    if unknown:
        raise ValueError("Symbols absent from official catalog: " + ", ".join(sorted(unknown)))
    if max_symbols:
        selected = selected[:max_symbols]
    jobs = []
    for symbol in selected:
        available = set(catalog["symbols"][symbol]["funding_months"]) & allowed_months
        for month in sorted(available):
            for dataset in TIER_DATASETS[tier]:
                jobs.append(_job(dataset, symbol, month))
    return jobs


def estimate_plan(jobs: list[Job]) -> dict:
    counts = {}
    estimate = 0
    for job in jobs:
        dataset = "funding" if job.dataset == "funding" else str(job.interval)
        counts[dataset] = counts.get(dataset, 0) + 1
        estimate += ESTIMATED_TOTAL_BYTES[dataset]
    return {
        "jobs": len(jobs),
        "counts": counts,
        "rough_total_gb_raw_plus_merged": round(estimate / 1024**3, 2),
        "already_downloaded": sum(job.path.exists() for job in jobs),
        "remaining": sum(not job.path.exists() for job in jobs),
    }


class DiskBudget:
    def __init__(self, root: Path, max_gb: float):
        self.root = root
        self.maximum = int(max_gb * 1024**3)
        self.starting = sum(
            path.stat().st_size for path in root.rglob("*") if path.is_file()
        ) if root.exists() else 0
        self.active_reserved = 0
        self.downloaded = 0
        self.lock = threading.Lock()

    def reserve(self, size: int) -> None:
        if size <= 0:
            raise RuntimeError("missing Content-Length; refusing unbounded download")
        with self.lock:
            free = shutil.disk_usage(self.root.parent if self.root.parent.exists() else WORKSPACE).free
            if free - self.active_reserved - size < 5 * 1024**3:
                raise RuntimeError("disk safety stop: less than 5 GB would remain")
            if (
                self.starting
                + self.downloaded
                + self.active_reserved
                + size
                > self.maximum
            ):
                raise RuntimeError("configured bulk download size limit reached")
            self.active_reserved += size

    def release(self, size: int, keep: bool, actual: int = 0) -> None:
        with self.lock:
            self.active_reserved = max(0, self.active_reserved - size)
            if keep:
                self.downloaded += actual


def _official_checksum(job: Job) -> str:
    text = _get(job.url + ".CHECKSUM").decode().strip()
    checksum = text.split()[0]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise ValueError(f"Invalid official checksum for {job.key}")
    return checksum.lower()


def _head_status(url: str) -> int:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "binance-bulk-research/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def download_job(job: Job, budget: DiskBudget) -> dict:
    try:
        checksum = _official_checksum(job)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and _head_status(job.url) == 404:
            return {
                "key": job.key,
                "status": "missing",
                "http_status": exc.code,
            }
        raise RuntimeError(
            f"official checksum unavailable for existing/forbidden job {job.key}"
        ) from exc
    if job.path.exists() and _sha256(job.path) == checksum:
        return {
            "key": job.key, "status": "verified_existing",
            "bytes": job.path.stat().st_size, "sha256": checksum,
            "path": str(job.path),
        }
    job.path.unlink(missing_ok=True)
    request = urllib.request.Request(
        job.url, headers={"User-Agent": "binance-bulk-research/1.0"}
    )
    job.path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    reserved = 0
    reservation_active = False
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            reserved = int(response.headers.get("Content-Length", "0"))
            budget.reserve(reserved)
            reservation_active = True
            with tempfile.NamedTemporaryFile(
                dir=job.path.parent, prefix=f".{job.path.name}.", suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                digest = hashlib.sha256()
                written = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    written += len(block)
                    if not reserved and written > budget.maximum:
                        raise RuntimeError("download exceeded configured size limit")
                    digest.update(block)
                    handle.write(block)
        if digest.hexdigest() != checksum:
            raise ValueError(f"checksum mismatch for {job.key}")
        os.replace(temporary, job.path)
        temporary = None
        budget.release(reserved, keep=True, actual=written)
        reservation_active = False
        return {
            "key": job.key, "status": "downloaded", "bytes": written,
            "sha256": checksum, "path": str(job.path),
        }
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"archive unavailable after checksum verification for {job.key}: "
            f"HTTP {exc.code}"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if reservation_active:
            budget.release(reserved, keep=False)


def _download_jobs_locked(
    jobs: list[Job],
    max_gb: float,
    workers: int = 6,
    manifest_path: Path = MANIFEST_PATH,
) -> dict:
    previous = (
        json.loads(manifest_path.read_text()) if manifest_path.exists()
        else {"schema_version": SCHEMA_VERSION, "jobs": {}}
    )
    budget = DiskBudget(ROOT, max_gb)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(download_job, job, budget): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {"key": job.key, "status": "error", "error": repr(exc)}
            row["updated_at"] = pd.Timestamp.now(tz="UTC").isoformat()
            previous["jobs"][job.key] = row
            results.append(row)
            if len(results) % 25 == 0:
                previous["updated_at"] = pd.Timestamp.now(tz="UTC").isoformat()
                _atomic_json(manifest_path, previous)
    previous["updated_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    _atomic_json(manifest_path, previous)
    return {
        "jobs": len(results),
        "downloaded": sum(row["status"] == "downloaded" for row in results),
        "verified_existing": sum(
            row["status"] == "verified_existing" for row in results
        ),
        "missing": sum(row["status"] == "missing" for row in results),
        "errors": [row for row in results if row["status"] == "error"],
        "manifest": str(manifest_path),
    }


def download_jobs(
    jobs: list[Job],
    max_gb: float,
    workers: int = 6,
    manifest_path: Path = MANIFEST_PATH,
) -> dict:
    with _file_lock(manifest_path):
        return _download_jobs_locked(jobs, max_gb, workers, manifest_path)


def _safe_csv_member(archive: zipfile.ZipFile, job: Job) -> str:
    members = [info for info in archive.infolist() if not info.is_dir()]
    if len(members) != 1:
        raise ValueError(f"{job.key} ZIP must contain exactly one file")
    info = members[0]
    member_path = Path(info.filename)
    if (
        member_path.is_absolute()
        or ".." in member_path.parts
        or member_path.suffix.lower() != ".csv"
    ):
        raise ValueError(f"unsafe ZIP member for {job.key}")
    if info.file_size > 512 * 1024**2:
        raise ValueError(f"decompressed member too large for {job.key}")
    if info.compress_size and info.file_size / info.compress_size > 1_000:
        raise ValueError(f"suspicious ZIP compression ratio for {job.key}")
    return info.filename


def _funding_frame(job: Job) -> pd.DataFrame:
    with zipfile.ZipFile(job.path) as archive:
        name = _safe_csv_member(archive, job)
        frame = pd.read_csv(archive.open(name))
        if "calc_time" not in frame.columns:
            frame = pd.read_csv(
                archive.open(name), header=None,
                names=["calc_time", "funding_interval_hours", "last_funding_rate"],
            )
    numeric = pd.to_numeric(frame["calc_time"], errors="coerce").dropna().astype("int64")
    numeric = numeric.where(numeric < 10**14, numeric // 1000)
    output = pd.DataFrame(
        {
            # Conservative availability: a settlement-stamped rate is usable
            # only after that timestamp, never at the exact boundary.
            "time": pd.to_datetime(numeric + 1, unit="ms"),
            "funding_rate": pd.to_numeric(
                frame.loc[numeric.index, "last_funding_rate"], errors="coerce"
            ),
        }
    )
    return output.dropna()


def _kline_frame(job: Job) -> pd.DataFrame:
    with zipfile.ZipFile(job.path) as archive:
        name = _safe_csv_member(archive, job)
        frame = pd.read_csv(archive.open(name), header=None)
    if frame.shape[1] < 7:
        raise ValueError(f"kline schema has fewer than 7 columns for {job.key}")
    frame = frame.rename(
        columns={
            0: "open_time", 1: "open", 2: "high", 3: "low",
            4: "close", 5: "volume", 6: "close_time",
        }
    )
    close_time = pd.to_numeric(frame["close_time"], errors="coerce").dropna().astype("int64")
    close_time = close_time.where(close_time < 10**14, close_time // 1000)
    output = pd.DataFrame({"time": pd.to_datetime(close_time + 1, unit="ms")})
    for column in ("open", "high", "low", "close", "volume"):
        output[column] = pd.to_numeric(
            frame.loc[close_time.index, column], errors="coerce"
        ).to_numpy()
    return output.dropna()


def _atomic_csv(path: Path, frame: pd.DataFrame, maximum_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        frame.to_csv(handle, index=False)
    try:
        old_size = path.stat().st_size if path.exists() else 0
        total_with_temp = sum(
            item.stat().st_size for item in ROOT.rglob("*") if item.is_file()
        )
        if total_with_temp - old_size > maximum_bytes:
            raise RuntimeError("configured bulk size limit reached during merge")
        if shutil.disk_usage(WORKSPACE).free < 5 * 1024**3:
            raise RuntimeError("disk safety stop during merge")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _verified_jobs_for_groups(
    requested: list[Job], manifest: dict
) -> tuple[list[Job], list[dict]]:
    groups = {
        (job.dataset, job.interval, job.symbol)
        for job in requested
    }
    verified = []
    errors = []
    successful = {"downloaded", "verified_existing"}
    for key, row in manifest.get("jobs", {}).items():
        if row.get("status") not in successful:
            continue
        parts = key.split("/")
        if len(parts) == 3 and parts[0] == "funding":
            dataset, interval, symbol, month = "funding", None, parts[1], parts[2]
        elif len(parts) == 4 and parts[0] == "perp":
            dataset, interval, symbol, month = "perp", parts[1], parts[2], parts[3]
        else:
            continue
        if (dataset, interval, symbol) not in groups:
            continue
        path = Path(row.get("path", ""))
        expected = row.get("sha256")
        if not path.is_file() or not expected or _sha256(path) != expected:
            errors.append(
                {"key": key, "error": "raw_file_missing_or_checksum_mismatch"}
            )
            continue
        verified.append(
            Job(dataset, symbol, month, interval, "", path)
        )
    verified_keys = {job.key for job in verified}
    for job in requested:
        if job.key not in verified_keys:
            errors.append({"key": job.key, "error": "requested_job_not_verified"})
    return verified, errors


def merge_jobs(
    jobs: list[Job],
    manifest_path: Path = MANIFEST_PATH,
    max_gb: float = 20,
) -> dict:
    if not manifest_path.exists():
        return {
            "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "outputs": [],
            "errors": [{"error": "download_manifest_missing"}],
        }
    manifest = json.loads(manifest_path.read_text())
    verified_jobs, verification_errors = _verified_jobs_for_groups(jobs, manifest)
    groups = {}
    for job in verified_jobs:
        group = (job.dataset, job.interval, job.symbol)
        groups.setdefault(group, []).append(job)
    outputs = []
    errors = list(verification_errors)
    maximum_bytes = int(max_gb * 1024**3)
    for (dataset, interval, symbol), group_jobs in sorted(groups.items()):
        try:
            frames = [
                _funding_frame(job) if dataset == "funding" else _kline_frame(job)
                for job in sorted(group_jobs, key=lambda item: item.month)
            ]
            frame = (
                pd.concat(frames, ignore_index=True)
                .drop_duplicates("time", keep="last")
                .sort_values("time")
            )
            key = storage_key(symbol)
            if dataset == "funding":
                path = MERGED / "funding" / f"{key}.csv"
            else:
                path = MERGED / "perp" / str(interval) / f"{key}.csv"
            _atomic_csv(path, frame, maximum_bytes)
            outputs.append(
                {
                    "symbol": symbol,
                    "dataset": dataset,
                    "interval": interval,
                    "rows": len(frame),
                    "from": str(frame["time"].min()),
                    "to": str(frame["time"].max()),
                    "path": str(path),
                    "sha256": _sha256(path),
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "symbol": symbol, "dataset": dataset,
                    "interval": interval, "error": repr(exc),
                }
            )
    report = {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "outputs": outputs,
        "errors": errors,
        "complete_for_requested_jobs": not any(
            error.get("error") == "requested_job_not_verified"
            for error in errors
        ),
    }
    with _file_lock(ROOT / "merge_manifest.json"):
        _atomic_json(ROOT / "merge_manifest.json", report)
    return report


def _default_end() -> str:
    return _previous_complete_month()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m intake.binance_bulk")
    sub = parser.add_subparsers(dest="command", required=True)
    catalog_parser = sub.add_parser("catalog")
    catalog_parser.add_argument("--workers", type=int, default=12)
    catalog_parser.add_argument("--max-symbols", type=int)

    for name in ("plan", "download", "merge"):
        command = sub.add_parser(name)
        command.add_argument("--tier", choices=TIER_DATASETS, default="core")
        command.add_argument("--start", default="2020-01")
        command.add_argument("--end", default=_default_end())
        command.add_argument("--symbols")
        command.add_argument("--max-symbols", type=int)
        if name == "download":
            command.add_argument("--workers", type=int, default=6)
            command.add_argument("--max-gb", type=float, default=20)
            command.add_argument("--confirm-large-download", action="store_true")
        elif name == "merge":
            command.add_argument("--max-gb", type=float, default=20)
    sub.add_parser("status")
    args = parser.parse_args()

    if args.command == "catalog":
        result = build_catalog(workers=args.workers, max_symbols=args.max_symbols)
    elif args.command == "status":
        catalog = load_catalog()
        manifest = (
            json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists()
            else {"jobs": {}}
        )
        result = {
            "catalog_symbols": catalog["symbols_with_history"],
            "historical_or_delisted": catalog["historical_or_delisted"],
            "manifest_jobs": len(manifest["jobs"]),
            "raw_gb": round(
                sum(path.stat().st_size for path in RAW.rglob("*") if path.is_file())
                / 1024**3,
                3,
            ) if RAW.exists() else 0,
            "free_gb": round(shutil.disk_usage(WORKSPACE).free / 1024**3, 2),
        }
    else:
        catalog = load_catalog()
        selected = args.symbols.split(",") if args.symbols else None
        jobs = plan_jobs(
            catalog, args.tier, args.start, args.end,
            selected, args.max_symbols,
        )
        plan = estimate_plan(jobs)
        if args.command == "plan":
            result = plan
        elif args.command == "download":
            if args.tier == "intraday" and not args.confirm_large_download:
                raise SystemExit(
                    "intraday tier requires --confirm-large-download"
                )
            result = {
                "plan": plan,
                "result": download_jobs(jobs, args.max_gb, args.workers),
            }
        else:
            result = merge_jobs(jobs, max_gb=args.max_gb)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
