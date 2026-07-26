"""Deterministic accounting tests for the funding paper tracker.

No network, no API keys, no real money. Run:
    python3 scripts/test_paper_forward.py
"""
from __future__ import annotations

import unittest
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research import paper_forward as pf


class PaperForwardAccountingTests(unittest.TestCase):
    def test_funding_is_credited_for_both_sides(self):
        times = pd.to_datetime(
            ["2026-01-01 08:00", "2026-01-01 16:00"], utc=True
        )
        funding = {
            "POS": pd.DataFrame({"time": times, "rate": [0.001, 0.001]}),
            "NEG": pd.DataFrame({"time": times, "rate": [-0.002, -0.002]}),
        }
        positions = {
            "POS": {"g": 1, "weight": 0.5},
            "NEG": {"g": -1, "weight": 0.25},
        }
        lo = int(pd.Timestamp("2026-01-01", tz="UTC").timestamp() * 1000)
        hi = int(pd.Timestamp("2026-01-02", tz="UTC").timestamp() * 1000)
        pnl, _ = pf.realized_pnl(positions, funding, lo, hi)
        self.assertAlmostEqual(pnl, 0.002, places=12)

    def test_funding_window_does_not_double_count(self):
        times = pd.to_datetime(
            ["2026-01-01 08:00", "2026-01-01 16:00"], utc=True
        )
        funding = {"BTC": pd.DataFrame({"time": times, "rate": [0.001, 0.001]})}
        positions = {"BTC": {"g": 1, "weight": 1.0}}
        lo = int(pd.Timestamp("2026-01-01 08:00", tz="UTC").timestamp() * 1000)
        hi = int(pd.Timestamp("2026-01-01 16:00", tz="UTC").timestamp() * 1000)
        pnl, _ = pf.realized_pnl(positions, funding, lo, hi)
        self.assertAlmostEqual(pnl, 0.001, places=12)

    def test_basis_pnl_has_correct_sign(self):
        old = {
            "BTC": {
                "g": 1,
                "weight": 1.0,
                "perp_price": 101.0,
                "spot_price": 100.0,
            }
        }
        # Basis converges: short perp + long spot profits 1%.
        cur = {"BTC": {"perp_price": 100.0, "spot_price": 100.0}}
        pnl, _ = pf.basis_pnl(old, cur)
        self.assertAlmostEqual(pnl, 1.0 - 100.0 / 101.0, places=12)

    def test_short_spot_borrow_cost_only_on_negative_side(self):
        positions = {
            "SHORT_SPOT": {"g": -1, "weight": 2.0},
            "LONG_SPOT": {"g": 1, "weight": 3.0},
        }
        cost = pf.short_spot_borrow_cost(positions, elapsed_days=365)
        self.assertAlmostEqual(cost, 2.0 * pf.SHORT_SPOT_BORROW_APR, places=12)

    def test_open_close_and_reversal_turnover(self):
        open_book = {"BTC": {"g": 1, "weight": 0.5}}
        cost, turn = pf.turnover_cost({}, open_book)
        self.assertAlmostEqual(turn, 0.5)
        self.assertAlmostEqual(cost, 0.5 * pf.LEG_COST)
        cost, turn = pf.turnover_cost(
            open_book, {"BTC": {"g": -1, "weight": 0.5}}
        )
        self.assertAlmostEqual(turn, 1.0)
        self.assertAlmostEqual(cost, pf.LEG_COST)

    def test_target_book_preserves_position_on_missing_api_data(self):
        old = {
            "BTC": {
                "g": 1,
                "weight": 0.5,
                "pred": 0.001,
                "perp_price": 101.0,
                "spot_price": 100.0,
            }
        }
        book = pf.target_book({}, old, leverage=5.0, prices={})
        self.assertIn("BTC", book)
        self.assertEqual(book["BTC"]["g"], 1)

    def test_basis_filter_blocks_unsupported_entry(self):
        times = pd.date_range("2026-01-01", periods=pf.LOOKBACK, freq="8h", tz="UTC")
        funding = {
            "BTC": pd.DataFrame(
                {"time": times, "rate": [pf.ENTER * 2] * pf.LOOKBACK}
            )
        }
        # Positive funding wants short perp/long spot, but negative basis blocks it.
        prices = {
            "BTC": {
                "perp_price": 99.0,
                "spot_price": 100.0,
                "basis": -0.01,
            }
        }
        self.assertEqual(pf.target_book(funding, {}, 1.0, prices), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
