"""Read-only Data Intake Bot.

The bot can:
* initialize a git-ignored private intake workspace;
* collect keyless public market, energy and prediction-market snapshots;
* validate imported CSV/JSON/PDF files without modifying originals;
* detect likely secrets/PII and write SHA-256 manifests;
* report whether optional environment variables exist without exposing values.

It deliberately imports no live broker clients and contains no order, transfer,
withdrawal, wallet-signing, OCPP, Modbus or hardware-control code.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from energy.flexible_load import ZONES, download_prices
from research.binance_vision import fetch_funding as fetch_binance_funding
from research.binance_vision import fetch_klines as fetch_binance_klines
from research.hyperliquid_data import fetch_candles as fetch_hyperliquid_candles
from research.hyperliquid_data import fetch_funding as fetch_hyperliquid_funding
from intake.binance_bulk import (
    CATALOG_PATH as BULK_CATALOG_PATH,
    TIER_DATASETS as BULK_TIERS,
    build_catalog as build_bulk_catalog,
    download_jobs as download_bulk_jobs,
    estimate_plan as estimate_bulk_plan,
    load_catalog as load_bulk_catalog,
    merge_jobs as merge_bulk_jobs,
    plan_jobs as plan_bulk_jobs,
    status_report as bulk_status_report,
)

WORKSPACE = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = WORKSPACE / "data" / "cache" / "private_intake"
PUBLIC_CACHE = WORKSPACE / "data" / "cache"
SCHEMA_VERSION = 1

TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".txt", ".md", ".yaml", ".yml"}
ALLOWED_SUFFIXES = TEXT_SUFFIXES | {".pdf"}
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "common_api_token": re.compile(
        r"\b(?:sk_live_|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{12,}\b"
    ),
    "secret_assignment": re.compile(
        r"(?i)\b(?:api[_-]?secret|secret[_-]?key|private[_-]?key|password)"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9+/=_-]{12,}"
    ),
}
PII_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "swedish_personnummer": re.compile(
        r"\b(?:19|20)?\d{6}[-+]?\d{4}\b"
    ),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
}

CONSTRAINTS_TEMPLATE = {
    "country": "Sweden",
    "tax_residency": "Sweden",
    "capital_sek": None,
    "maximum_drawdown_pct": None,
    "maximum_leverage": None,
    "minimum_cash_reserve_pct": 30,
    "target_return": "6 percent monthly",
    "acceptable_holding_period": "hours_to_months",
    "allowed_markets": [],
    "allowed_venues": [],
    "forbidden_activities": [
        "withdrawals",
        "unlicensed_gambling",
        "geo_block_bypass",
    ],
    "paper_only": True,
}

README_TEXT = """PRIVATE DATA INTAKE (git-ignored)

1. Put secrets only in /workspace/.env, never in this folder.
2. Export original account files into the matching folder without editing them.
3. Remove names, addresses, account numbers and personnummer first.
4. Run: python -m intake.bot validate
5. Read validation_report.json and manifests/latest.json.

Folders:
  legal/       Terms/eligibility PDFs or links
  trading/     Trades, funding, account statements, fee schedules
  prediction/  Market metadata and external exports
  energy/      Meter readings, invoices, tariffs, EV configuration
  marketplace/ Purchases, sales, fees, shipping, returns, inventory
  public/      Public snapshots recorded by this bot

The bot is READ-ONLY with respect to financial accounts and physical hardware.
"""


@dataclass(frozen=True)
class IntakePaths:
    root: Path = DEFAULT_ROOT

    @property
    def manifest_dir(self) -> Path:
        return self.root / "manifests"

    @property
    def audit_log(self) -> Path:
        return self.root / "audit.jsonl"


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, default=str) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
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


def _audit(paths: IntakePaths, event: str, details: dict) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **details,
    }
    with paths.audit_log.open("a") as handle:
        handle.write(json.dumps(row, default=str) + "\n")


def _storage_safety(root: Path) -> str:
    resolved = root.resolve()
    workspace = WORKSPACE.resolve()
    try:
        relative = resolved.relative_to(workspace)
    except ValueError:
        return "outside_repository"
    safe_root = Path("data") / "cache" / "private_intake"
    if relative != safe_root and safe_root not in relative.parents:
        raise ValueError(
            "Private intake root inside the repository must be under "
            "data/cache/private_intake/"
        )
    tracked = subprocess.run(
        ["git", "ls-files", "--", str(relative)],
        cwd=WORKSPACE,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if tracked:
        raise ValueError(
            "Private intake root contains files already tracked by Git: "
            + ", ".join(tracked[:5])
        )
    return "git_ignored_data_cache"


def initialize(paths: IntakePaths, force: bool = False) -> dict:
    storage_safety = _storage_safety(paths.root)
    paths.root.mkdir(parents=True, exist_ok=True)
    for folder in (
        "legal", "trading", "prediction", "energy", "marketplace",
        "public/markets", "public/energy", "public/prediction", "manifests",
    ):
        (paths.root / folder).mkdir(parents=True, exist_ok=True)
    constraints = paths.root / "user_constraints.json"
    readme = paths.root / "README.txt"
    if force or not constraints.exists():
        _atomic_json(constraints, CONSTRAINTS_TEMPLATE)
    if force or not readme.exists():
        readme.write_text(README_TEXT)
    result = {
        "root": str(paths.root),
        "constraints": str(constraints),
        "created": True,
        "storage_safety": storage_safety,
        "git_ignored_by_parent": storage_safety == "git_ignored_data_cache",
    }
    _audit(paths, "init", result)
    return result


def _scan_text(path: Path) -> tuple[list[str], list[str]]:
    """Stream the complete text file with overlap so tokens cannot hide at boundaries."""
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return [], []
    secret_hits = set()
    pii_hits = set()
    overlap = ""
    with path.open(encoding="utf-8", errors="replace") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            text = overlap + chunk
            secret_hits.update(
                name for name, pattern in SECRET_PATTERNS.items()
                if pattern.search(text)
            )
            pii_hits.update(
                name for name, pattern in PII_PATTERNS.items()
                if pattern.search(text)
            )
            overlap = text[-512:]
    return sorted(secret_hits), sorted(pii_hits)


def _pdf_review_error(path: Path) -> str | None:
    sidecar = path.with_suffix(path.suffix + ".reviewed.json")
    if not sidecar.exists():
        return "pdf_requires_manual_review_sidecar"
    try:
        review = json.loads(sidecar.read_text())
    except Exception:
        return "invalid_pdf_review_sidecar"
    if review.get("manual_secret_pii_review") is not True:
        return "pdf_manual_review_not_confirmed"
    if review.get("sha256") != _sha256(path):
        return "pdf_review_hash_mismatch"
    return None


def _csv_metadata(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        rows = sum(1 for _ in reader)
    metadata = {"columns": header, "rows": rows}
    timestamp_columns = [
        column for column in header
        if any(token in column.lower() for token in ("time", "date", "timestamp"))
    ]
    metadata["timestamp_columns"] = timestamp_columns
    return metadata


def _json_metadata(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {"valid_json": False, "error": str(exc)}
    if isinstance(value, list):
        return {"valid_json": True, "root_type": "list", "items": len(value)}
    if isinstance(value, dict):
        return {
            "valid_json": True,
            "root_type": "object",
            "keys": sorted(value.keys()),
        }
    return {"valid_json": True, "root_type": type(value).__name__}


def inspect_file(path: Path, root: Path) -> dict:
    resolved = path.resolve()
    if path.is_symlink():
        return {
            "path": str(path.relative_to(root)),
            "status": "error",
            "errors": ["symlinks_not_allowed"],
        }
    suffix = path.suffix.lower()
    errors = []
    warnings = []
    if suffix not in ALLOWED_SUFFIXES:
        errors.append("unsupported_file_type")
    secret_hits, pii_hits = _scan_text(path)
    errors.extend(f"likely_secret:{name}" for name in secret_hits)
    warnings.extend(f"possible_pii:{name}" for name in pii_hits)
    if suffix == ".pdf":
        pdf_error = _pdf_review_error(path)
        if pdf_error:
            errors.append(pdf_error)
    metadata = {}
    if suffix == ".csv":
        metadata = _csv_metadata(path)
        if not metadata["columns"]:
            errors.append("missing_csv_header")
        if metadata["rows"] == 0:
            warnings.append("empty_csv")
    elif suffix == ".json":
        metadata = _json_metadata(path)
        if metadata.get("valid_json") is False:
            errors.append("invalid_json")
    return {
        "path": str(resolved.relative_to(root.resolve())),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "suffix": suffix,
        "status": "error" if errors else ("warning" if warnings else "ok"),
        "errors": errors,
        "warnings": warnings,
        "metadata": metadata,
    }


def validate(paths: IntakePaths) -> dict:
    initialize(paths)
    excluded = {
        paths.audit_log.resolve(),
        (paths.root / "validation_report.json").resolve(),
    }
    files = []
    for path in sorted(paths.root.rglob("*")):
        if not path.is_file() or path.resolve() in excluded:
            continue
        if paths.manifest_dir in path.parents:
            continue
        files.append(inspect_file(path, paths.root))
    constraints_path = paths.root / "user_constraints.json"
    constraint_errors = []
    try:
        constraints = json.loads(constraints_path.read_text())
        for key in (
            "country",
            "tax_residency",
            "capital_sek",
            "maximum_drawdown_pct",
            "maximum_leverage",
            "paper_only",
        ):
            if key not in constraints:
                constraint_errors.append(f"missing_constraint:{key}")
            elif constraints[key] in (None, "", []):
                constraint_errors.append(f"unfilled_constraint:{key}")
        if constraints.get("paper_only") is not True:
            constraint_errors.append("paper_only_must_be_true_for_intake")
    except Exception as exc:
        constraint_errors.append(f"invalid_constraints:{exc}")
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(paths.root),
        "files": files,
        "summary": {
            "files": len(files),
            "ok": sum(row["status"] == "ok" for row in files),
            "warnings": sum(row["status"] == "warning" for row in files),
            "errors": sum(row["status"] == "error" for row in files),
            "constraint_errors": constraint_errors,
        },
    }
    report["safe_to_analyze"] = not constraint_errors and not any(
        row["errors"] for row in files
    )
    _atomic_json(paths.root / "validation_report.json", report)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    manifest_path = paths.manifest_dir / f"manifest_{stamp}.json"
    _atomic_json(manifest_path, report)
    _atomic_json(paths.manifest_dir / "latest.json", report)
    _audit(
        paths,
        "validate",
        {"safe_to_analyze": report["safe_to_analyze"], **report["summary"]},
    )
    return report


def _get_json(url: str, retries: int = 4):
    request = urllib.request.Request(
        url, headers={"User-Agent": "read-only-intake-bot/1.0"}
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(attempt + 1)
    return None


def collect_prediction(paths: IntakePaths, limit: int = 20) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError("prediction limit must be between 1 and 500")
    query = urllib.parse.urlencode(
        {"active": "true", "closed": "false", "limit": limit}
    )
    markets = _get_json(f"https://gamma-api.polymarket.com/markets?{query}")
    snapshots = []
    for market in markets:
        token_ids = market.get("clobTokenIds")
        outcomes = market.get("outcomes")
        if isinstance(token_ids, str):
            try:
                token_ids = json.loads(token_ids)
            except json.JSONDecodeError:
                token_ids = []
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except json.JSONDecodeError:
                outcomes = []
        books = []
        for token_id in (token_ids or [])[:2]:
            books.append(
                _get_json(
                    "https://clob.polymarket.com/book?"
                    + urllib.parse.urlencode({"token_id": token_id})
                )
            )
        best_asks = []
        best_ask_sizes = []
        for book in books:
            asks = [
                (float(level["price"]), float(level.get("size", 0)))
                for level in (book or {}).get("asks", [])
                if "price" in level
            ]
            best = min(asks, default=None)
            best_asks.append(best[0] if best else None)
            best_ask_sizes.append(best[1] if best else None)
        binary_complements = (
            len(outcomes or []) == 2
            and {str(value).strip().lower() for value in outcomes} == {"yes", "no"}
            and len(token_ids or []) == 2
        )
        complete_set_ask = (
            sum(best_asks)
            if binary_complements and len(best_asks) == 2 and None not in best_asks
            else None
        )
        snapshots.append(
            {
                "id": market.get("id"),
                "slug": market.get("slug"),
                "question": market.get("question"),
                "condition_id": market.get("conditionId"),
                "outcomes": outcomes,
                "token_ids": token_ids,
                "best_asks": best_asks,
                "best_ask_sizes": best_ask_sizes,
                "executable_complete_set_size": (
                    min(best_ask_sizes)
                    if complete_set_ask is not None and None not in best_ask_sizes
                    else None
                ),
                "complete_set_ask_before_fees": complete_set_ask,
                "books": books,
            }
        )
    timestamp = datetime.now(timezone.utc)
    output = {
        "retrieved_at": timestamp.isoformat(),
        "source": "public_polymarket_gamma_and_clob",
        "read_only": True,
        "markets": snapshots,
    }
    path = (
        paths.root
        / "public"
        / "prediction"
        / f"polymarket_{timestamp:%Y%m%dT%H%M%S%fZ}.json"
    )
    _atomic_json(path, output)
    result = {"source": "prediction", "files": [str(path)], "markets": len(snapshots)}
    _audit(paths, "collect", result)
    return result


def collect_energy(
    paths: IntakePaths,
    zones: Iterable[str],
    start: str,
    end: str,
    refresh: bool,
) -> dict:
    files = []
    rows = {}
    for zone in zones:
        frame = download_prices(zone, start, end, refresh)
        path = PUBLIC_CACHE / f"swedish_spot_{zone.lower()}.csv"
        files.append(str(path))
        rows[zone] = len(frame)
    result = {"source": "energy", "files": files, "rows": rows}
    _audit(paths, "collect", result)
    return result


def collect_markets(
    paths: IntakePaths,
    coins: Iterable[str],
    start: str,
    end: str,
    refresh: bool,
) -> dict:
    files = []
    rows = {}
    for coin in coins:
        coin = coin.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,15}", coin):
            raise ValueError(f"Invalid market coin identifier: {coin!r}")
        symbol = f"{coin}USDT"
        binance_funding = fetch_binance_funding(symbol, start, end, refresh)
        binance_perp = fetch_binance_klines(
            symbol, "um", "8h", start, end, refresh
        )
        hyper_funding = fetch_hyperliquid_funding(coin, start, end, refresh)
        hyper_candles = fetch_hyperliquid_candles(
            coin, start, end, "4h", refresh
        )
        expected = [
            PUBLIC_CACHE / f"vision_funding_{symbol.lower()}.csv",
            PUBLIC_CACHE / f"vision_perp_{symbol.lower()}_8h.csv",
            PUBLIC_CACHE / f"hyperliquid_funding_{coin.lower()}.csv",
            PUBLIC_CACHE / f"hyperliquid_perp_{coin.lower()}_4h.csv",
        ]
        files.extend(str(path) for path in expected if path.exists())
        rows[coin] = {
            "binance_funding": len(binance_funding),
            "binance_perp": len(binance_perp),
            "hyperliquid_funding": len(hyper_funding),
            "hyperliquid_perp": len(hyper_candles),
        }
    result = {"source": "markets", "files": files, "rows": rows}
    _audit(paths, "collect", result)
    return result


def collect_binance_bulk(
    paths: IntakePaths,
    tier: str,
    start: str,
    end: str,
    symbols: list[str] | None,
    max_symbols: int | None,
    workers: int,
    max_gb: float,
    refresh_catalog: bool,
    plan_only: bool,
    confirm_large_download: bool,
) -> dict:
    start_month = start[:7]
    end_month = end[:7]
    if refresh_catalog or not BULK_CATALOG_PATH.exists():
        catalog = build_bulk_catalog(workers=max(4, workers))
    else:
        catalog = load_bulk_catalog()
    jobs = plan_bulk_jobs(
        catalog,
        tier,
        start_month,
        end_month,
        symbols=symbols,
        max_symbols=max_symbols,
    )
    plan = estimate_bulk_plan(jobs)
    if plan_only:
        result = {
            "source": "binance-bulk",
            "mode": "plan_only",
            "tier": tier,
            "catalog_symbols": catalog["symbols_with_history"],
            "plan": plan,
        }
    else:
        if tier == "intraday" and not confirm_large_download:
            raise ValueError(
                "intraday bulk requires --confirm-large-download"
            )
        downloaded = download_bulk_jobs(jobs, max_gb=max_gb, workers=workers)
        merged = merge_bulk_jobs(jobs, max_gb=max_gb)
        result = {
            "source": "binance-bulk",
            "mode": "download_and_merge",
            "tier": tier,
            "catalog_symbols": catalog["symbols_with_history"],
            "plan": plan,
            "download": downloaded,
            "merge_outputs": len(merged["outputs"]),
            "merge_errors": merged["errors"],
            "complete": bool(
                not downloaded["errors"]
                and downloaded["missing"] == 0
                and not merged["errors"]
                and merged.get("complete_for_requested_jobs")
            ),
        }
    _audit(paths, "collect", result)
    return result


def env_status() -> dict:
    names = (
        "POLYGON_API_KEY",
        "OKX_API_KEY",
        "OKX_API_SECRET",
        "OKX_PASSPHRASE",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
    )
    return {name: bool(os.environ.get(name)) for name in names}


def status(paths: IntakePaths) -> dict:
    initialize(paths)
    files = [path for path in paths.root.rglob("*") if path.is_file()]
    report_path = paths.root / "validation_report.json"
    previous = json.loads(report_path.read_text()) if report_path.exists() else None
    result = {
        "root": str(paths.root),
        "private_files": len(files),
        "last_validation": previous.get("generated_at") if previous else None,
        "safe_to_analyze": previous.get("safe_to_analyze") if previous else None,
        "environment_variables_present": env_status(),
        "capabilities": {
            "financial_orders": False,
            "withdrawals": False,
            "wallet_signing": False,
            "hardware_control": False,
            "public_read_only_collection": True,
        },
    }
    if BULK_CATALOG_PATH.exists():
        result["binance_bulk"] = bulk_status_report()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m intake.bot")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--force", action="store_true")
    subparsers.add_parser("validate")
    subparsers.add_parser("status")

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument(
        "--source",
        choices=("markets", "energy", "prediction", "binance-bulk", "all"),
        required=True,
    )
    collect_parser.add_argument("--coins")
    collect_parser.add_argument("--zones", default="SE3")
    collect_parser.add_argument("--start", default="2024-04-01")
    collect_parser.add_argument("--end", default=str(date.today()))
    collect_parser.add_argument("--refresh", action="store_true")
    collect_parser.add_argument("--prediction-limit", type=int, default=20)
    collect_parser.add_argument("--bulk-tier", choices=BULK_TIERS, default="core")
    collect_parser.add_argument("--max-symbols", type=int)
    collect_parser.add_argument("--workers", type=int, default=6)
    collect_parser.add_argument("--max-gb", type=float, default=20)
    collect_parser.add_argument("--refresh-catalog", action="store_true")
    collect_parser.add_argument("--plan-only", action="store_true")
    collect_parser.add_argument("--confirm-large-download", action="store_true")
    collect_parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    paths = IntakePaths(args.root)

    if args.command == "init":
        result = initialize(paths, args.force)
    elif args.command == "validate":
        result = validate(paths)
    elif args.command == "status":
        result = status(paths)
    else:
        initialize(paths)
        requested = (
            ("markets", "energy", "prediction")
            if args.source == "all"
            else (args.source,)
        )
        if args.dry_run:
            if args.source == "binance-bulk":
                result = collect_binance_bulk(
                    paths,
                    args.bulk_tier,
                    args.start,
                    args.end,
                    [
                        coin.strip().upper()
                        if coin.strip().upper().endswith("USDT")
                        else coin.strip().upper() + "USDT"
                        for coin in args.coins.split(",")
                    ]
                    if args.coins
                    else None,
                    args.max_symbols,
                    args.workers,
                    args.max_gb,
                    args.refresh_catalog,
                    plan_only=True,
                    confirm_large_download=args.confirm_large_download,
                )
                result["dry_run"] = True
            else:
                result = {
                    "dry_run": True,
                    "sources": requested,
                    "coins": args.coins.split(",") if args.coins else ["BTC", "ETH"],
                    "zones": args.zones.split(","),
                    "start": args.start,
                    "end": args.end,
                }
        else:
            collected = []
            if "markets" in requested:
                collected.append(
                    collect_markets(
                        paths,
                        args.coins.split(",") if args.coins else ["BTC", "ETH"],
                        args.start,
                        args.end,
                        args.refresh,
                    )
                )
            if "energy" in requested:
                collected.append(
                    collect_energy(
                        paths,
                        [zone.upper() for zone in args.zones.split(",")],
                        args.start,
                        args.end,
                        args.refresh,
                    )
                )
            if "prediction" in requested:
                collected.append(
                    collect_prediction(paths, args.prediction_limit)
                )
            if "binance-bulk" in requested:
                collected.append(
                    collect_binance_bulk(
                        paths,
                        args.bulk_tier,
                        args.start,
                        args.end,
                        [
                            coin.strip().upper()
                            if coin.strip().upper().endswith("USDT")
                            else coin.strip().upper() + "USDT"
                            for coin in args.coins.split(",")
                        ]
                        if args.coins
                        else None,
                        args.max_symbols,
                        args.workers,
                        args.max_gb,
                        args.refresh_catalog,
                        args.plan_only,
                        args.confirm_large_download,
                    )
                )
            result = {"collected": collected, "validation": validate(paths)["summary"]}
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
