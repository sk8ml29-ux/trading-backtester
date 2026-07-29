import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from intake.bot import (
    IntakePaths,
    collect_prediction,
    initialize,
    inspect_file,
    status,
    validate,
)


class IntakeBotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "private_intake"
        self.paths = IntakePaths(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_init_creates_private_structure_and_paper_constraints(self):
        result = initialize(self.paths)
        constraints = json.loads(
            (self.root / "user_constraints.json").read_text()
        )

        self.assertTrue(result["git_ignored_by_parent"])
        self.assertTrue(constraints["paper_only"])
        self.assertTrue((self.root / "trading").is_dir())
        self.assertTrue((self.root / "public" / "prediction").is_dir())

    def test_concurrent_init_keeps_valid_constraints(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _: initialize(self.paths), range(20)))
        constraints = json.loads(
            (self.root / "user_constraints.json").read_text()
        )

        self.assertTrue(constraints["paper_only"])

    def test_validation_hashes_csv_and_writes_manifest(self):
        initialize(self.paths)
        csv_path = self.root / "trading" / "trades_test.csv"
        csv_path.write_text("timestamp,symbol,price\n2026-01-01T00:00:00Z,BTC,1\n")
        report = validate(self.paths)

        row = next(item for item in report["files"] if item["path"].endswith(".csv"))
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["metadata"]["rows"], 1)
        self.assertEqual(len(row["sha256"]), 64)
        self.assertTrue((self.paths.manifest_dir / "latest.json").exists())
        self.assertTrue(report["safe_to_analyze"])

    def test_likely_secret_blocks_analysis(self):
        initialize(self.paths)
        bad = self.root / "trading" / "bad.txt"
        bad.write_text("api_secret=abcdefghijklmnop123456")
        report = validate(self.paths)

        self.assertFalse(report["safe_to_analyze"])
        row = next(item for item in report["files"] if item["path"] == "trading/bad.txt")
        self.assertIn("likely_secret:secret_assignment", row["errors"])

    def test_pii_is_warning_not_silently_ignored(self):
        initialize(self.paths)
        path = self.root / "energy" / "invoice.txt"
        path.write_text("customer@example.com")
        row = inspect_file(path, self.root)

        self.assertEqual(row["status"], "warning")
        self.assertIn("possible_pii:email", row["warnings"])

    @patch("intake.bot._get_json")
    def test_prediction_collection_is_public_snapshot_only(self, get_json):
        get_json.side_effect = [
            [
                {
                    "id": "1",
                    "slug": "test",
                    "question": "Test?",
                    "conditionId": "0x1",
                    "clobTokenIds": '["yes","no"]',
                }
            ],
            {"asks": [{"price": "0.45", "size": "10"}], "bids": []},
            {"asks": [{"price": "0.50", "size": "10"}], "bids": []},
        ]
        initialize(self.paths)
        result = collect_prediction(self.paths, limit=1)
        snapshot = json.loads(Path(result["files"][0]).read_text())

        self.assertTrue(snapshot["read_only"])
        self.assertEqual(
            snapshot["markets"][0]["complete_set_ask_before_fees"], 0.95
        )

    def test_status_explicitly_disables_mutating_capabilities(self):
        initialize(self.paths)
        capabilities = status(self.paths)["capabilities"]

        self.assertFalse(capabilities["financial_orders"])
        self.assertFalse(capabilities["withdrawals"])
        self.assertFalse(capabilities["wallet_signing"])
        self.assertFalse(capabilities["hardware_control"])


if __name__ == "__main__":
    unittest.main()
