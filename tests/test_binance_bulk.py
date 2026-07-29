import hashlib
import io
import json
import contextlib
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from intake.binance_bulk import (
    DiskBudget,
    Job,
    _atomic_csv,
    build_catalog,
    discover_symbols,
    download_jobs,
    download_job,
    estimate_plan,
    funding_months,
    merge_jobs,
    plan_jobs,
    storage_key,
    symbols_at,
)


def xml_listing(prefixes=(), keys=(), truncated=False, token=None):
    body = [
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    ]
    body.extend(
        f"<CommonPrefixes><Prefix>{prefix}</Prefix></CommonPrefixes>"
        for prefix in prefixes
    )
    body.extend(f"<Contents><Key>{key}</Key></Contents>" for key in keys)
    body.append(f"<IsTruncated>{str(truncated).lower()}</IsTruncated>")
    if token:
        body.append(f"<NextContinuationToken>{token}</NextContinuationToken>")
    body.append("</ListBucketResult>")
    return "".join(body).encode()


def zip_bytes(filename: str, content: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(filename, content)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        return self.payload.read(size)


class BinanceBulkTests(unittest.TestCase):
    def test_discovers_current_and_delisted_usdt_symbols(self):
        payload = xml_listing(
            prefixes=[
                "data/futures/um/monthly/fundingRate/BTCUSDT/",
                "data/futures/um/monthly/fundingRate/FTMUSDT/",
                "data/futures/um/monthly/fundingRate/BTCUSDC/",
            ]
        )
        with patch("intake.binance_bulk._get", return_value=payload):
            symbols = discover_symbols()

        self.assertEqual(symbols, ["BTCUSDT", "FTMUSDT"])

    def test_extracts_lifecycle_months(self):
        payload = xml_listing(
            keys=[
                "data/futures/um/monthly/fundingRate/FTMUSDT/"
                "FTMUSDT-fundingRate-2021-01.zip",
                "data/futures/um/monthly/fundingRate/FTMUSDT/"
                "FTMUSDT-fundingRate-2024-06.zip",
            ]
        )
        with patch("intake.binance_bulk._get", return_value=payload):
            months = funding_months("FTMUSDT")

        self.assertEqual(months, ["2021-01", "2024-06"])

    def test_s3_discovery_follows_continuation_token(self):
        first = xml_listing(
            prefixes=["data/futures/um/monthly/fundingRate/BTCUSDT/"],
            truncated=True,
            token="next",
        )
        second = xml_listing(
            prefixes=["data/futures/um/monthly/fundingRate/ETHUSDT/"]
        )
        with patch("intake.binance_bulk._get", side_effect=[first, second]):
            symbols = discover_symbols()

        self.assertEqual(symbols, ["BTCUSDT", "ETHUSDT"])

    def test_point_in_time_universe_uses_lifecycle(self):
        catalog = {
            "symbols": {
                "OLDUSDT": {
                    "first_month": "2020-01",
                    "last_month": "2022-06",
                    "funding_months": ["2022-06"],
                },
                "NEWUSDT": {
                    "first_month": "2023-01",
                    "last_month": "2026-06",
                    "funding_months": ["2023-01"],
                },
            }
        }
        self.assertEqual(symbols_at(catalog, "2022-06"), ["OLDUSDT"])
        self.assertEqual(symbols_at(catalog, "2023-01"), ["NEWUSDT"])

    def test_catalog_refresh_preserves_previous_row_after_network_error(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "catalog.json"
            previous_row = {
                "symbol": "BTCUSDT",
                "storage_key": "BTCUSDT",
                "first_month": "2020-01",
                "last_month": "2026-06",
                "funding_months": ["2020-01", "2026-06"],
                "month_count": 2,
                "status_inferred": "recent",
                "source": "official_binance_vision_s3",
            }
            output.write_text(
                json.dumps({"symbols": {"BTCUSDT": previous_row}})
            )
            with patch(
                "intake.binance_bulk.discover_symbols",
                return_value=["BTCUSDT"],
            ), patch(
                "intake.binance_bulk.funding_months",
                side_effect=RuntimeError("temporary failure"),
            ):
                catalog = build_catalog(
                    workers=1, output=output
                )

            self.assertEqual(
                catalog["symbols"]["BTCUSDT"]["first_month"], "2020-01"
            )
            self.assertEqual(
                catalog["symbols"]["BTCUSDT"]["catalog_refresh"],
                "preserved_after_error",
            )

    def test_plan_is_tiered_and_includes_historical_symbols(self):
        catalog = {
            "symbols": {
                "OLDUSDT": {
                    "funding_months": ["2021-01", "2021-02"],
                }
            }
        }
        jobs = plan_jobs(catalog, "swing", "2021-01", "2021-02")
        estimate = estimate_plan(jobs)

        self.assertEqual(len(jobs), 6)
        self.assertEqual(estimate["counts"], {"funding": 2, "8h": 2, "1h": 2})

    def test_download_verifies_checksum_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = zip_bytes("test.csv", "a,b\n1,2\n")
            checksum = hashlib.sha256(payload).hexdigest()
            job = Job(
                "funding", "BTCUSDT", "2024-01", None,
                "https://example.test/file.zip", root / "file.zip",
            )
            budget = DiskBudget(root, max_gb=1)
            with patch(
                "intake.binance_bulk._official_checksum", return_value=checksum
            ), patch(
                "intake.binance_bulk.urllib.request.urlopen",
                return_value=FakeResponse(payload),
            ):
                first = download_job(job, budget)
                second = download_job(job, budget)

            self.assertEqual(first["status"], "downloaded")
            self.assertEqual(second["status"], "verified_existing")
            self.assertEqual(hashlib.sha256(job.path.read_bytes()).hexdigest(), checksum)

    def test_missing_checksum_does_not_hide_existing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = Job(
                "funding", "BTCUSDT", "2024-01", None,
                "https://example.test/file.zip", root / "file.zip",
            )
            error = urllib.error.HTTPError(
                job.url + ".CHECKSUM", 404, "missing", {}, None
            )
            with patch(
                "intake.binance_bulk._official_checksum", side_effect=error
            ), patch("intake.binance_bulk._head_status", return_value=200):
                with self.assertRaises(RuntimeError):
                    download_job(job, DiskBudget(root, 1))

    def test_double_404_is_recorded_as_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = Job(
                "funding", "BTCUSDT", "2019-01", None,
                "https://example.test/file.zip", root / "file.zip",
            )
            error = urllib.error.HTTPError(
                job.url + ".CHECKSUM", 404, "missing", {}, None
            )
            with patch(
                "intake.binance_bulk._official_checksum", side_effect=error
            ), patch("intake.binance_bulk._head_status", return_value=404):
                result = download_job(job, DiskBudget(root, 1))

            self.assertEqual(result["status"], "missing")

    def test_transient_retry_cannot_downgrade_verified_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "file.zip"
            raw.write_bytes(b"verified")
            checksum = hashlib.sha256(raw.read_bytes()).hexdigest()
            job = Job(
                "funding", "BTCUSDT", "2024-01", None, "", raw
            )
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "jobs": {
                            job.key: {
                                "status": "downloaded",
                                "path": str(raw),
                                "sha256": checksum,
                            }
                        },
                    }
                )
            )
            with patch("intake.binance_bulk.ROOT", root), patch(
                "intake.binance_bulk.download_job",
                side_effect=RuntimeError("temporary network failure"),
            ):
                result = download_jobs(
                    [job], max_gb=1, workers=1, manifest_path=manifest
                )
            saved = json.loads(manifest.read_text())["jobs"][job.key]

            self.assertEqual(len(result["errors"]), 1)
            self.assertEqual(saved["status"], "downloaded")
            self.assertEqual(saved["last_retry_status"], "error")

    def test_merge_preserves_causal_kline_close_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            funding_path = root / "funding.zip"
            kline_path = root / "kline.zip"
            funding_path.write_bytes(
                zip_bytes(
                    "funding.csv",
                    "calc_time,funding_interval_hours,last_funding_rate\n"
                    "1704067200000,8,0.0001\n",
                )
            )
            kline_path.write_bytes(
                zip_bytes(
                    "kline.csv",
                    "1704067200000,1,2,0.5,1.5,10,1704095999999,"
                    "0,0,0,0,0\n",
                )
            )
            jobs = [
                Job(
                    "funding", "BTCUSDT", "2024-01", None, "",
                    funding_path,
                ),
                Job(
                    "perp", "BTCUSDT", "2024-01", "8h", "",
                    kline_path,
                ),
            ]
            manifest_path = root / "download_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "jobs": {
                            job.key: {
                                "status": "downloaded",
                                "path": str(job.path),
                                "sha256": hashlib.sha256(
                                    job.path.read_bytes()
                                ).hexdigest(),
                            }
                            for job in jobs
                        }
                    }
                )
            )
            with patch("intake.binance_bulk.MERGED", root / "merged"), patch(
                "intake.binance_bulk.ROOT", root
            ):
                result = merge_jobs(jobs, manifest_path=manifest_path, max_gb=1)

            self.assertEqual(result["errors"], [])
            kline_output = next(
                row for row in result["outputs"] if row["dataset"] == "perp"
            )
            frame = pd.read_csv(kline_output["path"], parse_dates=["time"])
            self.assertEqual(
                frame["time"].iloc[0],
                pd.Timestamp("2024-01-01 08:00:00"),
            )

    def test_incremental_merge_preserves_previously_verified_months(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = []
            for month, timestamp in (
                ("2024-01", "1704067200000"),
                ("2024-02", "1706745600000"),
            ):
                path = root / f"{month}.zip"
                path.write_bytes(
                    zip_bytes(
                        "funding.csv",
                        "calc_time,funding_interval_hours,last_funding_rate\n"
                        f"{timestamp},8,0.0001\n",
                    )
                )
                jobs.append(
                    Job("funding", "BTCUSDT", month, None, "", path)
                )
            manifest_path = root / "download_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "jobs": {
                            job.key: {
                                "status": "downloaded",
                                "path": str(job.path),
                                "sha256": hashlib.sha256(
                                    job.path.read_bytes()
                                ).hexdigest(),
                            }
                            for job in jobs
                        }
                    }
                )
            )
            with patch("intake.binance_bulk.MERGED", root / "merged"), patch(
                "intake.binance_bulk.ROOT", root
            ):
                merge_jobs(jobs, manifest_path=manifest_path, max_gb=1)
                result = merge_jobs(
                    [jobs[1]], manifest_path=manifest_path, max_gb=1
                )

            output = Path(result["outputs"][0]["path"])
            self.assertEqual(len(pd.read_csv(output)), 2)

    def test_merge_rejects_tampered_raw_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "funding.zip"
            path.write_bytes(zip_bytes("funding.csv", "a,b\n1,2\n"))
            job = Job("funding", "BTCUSDT", "2024-01", None, "", path)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "jobs": {
                            job.key: {
                                "status": "downloaded",
                                "path": str(path),
                                "sha256": "0" * 64,
                            }
                        }
                    }
                )
            )
            with patch("intake.binance_bulk.ROOT", root):
                result = merge_jobs([job], manifest_path=manifest, max_gb=1)

            self.assertEqual(result["outputs"], [])
            self.assertTrue(
                any(
                    row.get("error") == "raw_file_missing_or_checksum_mismatch"
                    for row in result["errors"]
                )
            )

    def test_merge_disk_reserve_is_checked_before_temp_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "merged.csv"
            disk_usage = type(
                "DiskUsage", (), {"total": 10, "used": 6, "free": 4 * 1024**3}
            )()
            with patch("intake.binance_bulk.ROOT", root), patch(
                "intake.binance_bulk.shutil.disk_usage",
                return_value=disk_usage,
            ):
                with self.assertRaises(RuntimeError):
                    _atomic_csv(
                        output,
                        pd.DataFrame({"time": ["2024-01-01"], "value": [1]}),
                        maximum_bytes=1024**3,
                    )

            self.assertFalse(output.exists())

    def test_download_and_merge_use_same_operation_lock(self):
        seen = []

        @contextlib.contextmanager
        def fake_lock(path):
            seen.append(path)
            yield

        with patch("intake.binance_bulk._file_lock", side_effect=fake_lock), patch(
            "intake.binance_bulk._download_jobs_locked", return_value={}
        ), patch("intake.binance_bulk._merge_jobs_locked", return_value={}):
            download_jobs([], max_gb=1)
            merge_jobs([])

        self.assertEqual(seen[0], seen[1])
        self.assertEqual(seen[0].name, "bulk_operation")

    def test_unicode_symbol_uses_safe_storage_key(self):
        key = storage_key("币安人生USDT")
        self.assertNotIn("/", key)
        self.assertIn("%", key)


if __name__ == "__main__":
    unittest.main()
