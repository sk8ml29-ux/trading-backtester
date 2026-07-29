import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from intake.binance_bulk import (
    DiskBudget,
    Job,
    build_catalog,
    discover_symbols,
    download_job,
    estimate_plan,
    funding_months,
    merge_jobs,
    plan_jobs,
    storage_key,
    symbols_at,
)


def xml_listing(prefixes=(), keys=()):
    body = [
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    ]
    body.extend(
        f"<CommonPrefixes><Prefix>{prefix}</Prefix></CommonPrefixes>"
        for prefix in prefixes
    )
    body.extend(f"<Contents><Key>{key}</Key></Contents>" for key in keys)
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
            with patch("intake.binance_bulk.MERGED", root / "merged"), patch(
                "intake.binance_bulk.ROOT", root
            ):
                result = merge_jobs(jobs)

            self.assertEqual(result["errors"], [])
            kline_output = next(
                row for row in result["outputs"] if row["dataset"] == "perp"
            )
            frame = pd.read_csv(kline_output["path"], parse_dates=["time"])
            self.assertEqual(
                frame["time"].iloc[0],
                pd.Timestamp("2024-01-01 08:00:00"),
            )

    def test_unicode_symbol_uses_safe_storage_key(self):
        key = storage_key("币安人生USDT")
        self.assertNotIn("/", key)
        self.assertIn("%", key)


if __name__ == "__main__":
    unittest.main()
