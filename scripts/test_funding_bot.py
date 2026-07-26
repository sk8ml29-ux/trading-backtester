"""Deterministic tests for the OKX demo funding bot's planning/sizing.

No network, no keys, no orders. Run:
    python3 scripts/test_funding_bot.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live import funding_bot as fb


PRICES = {
    "BTC": {"spot_price": 100.0, "perp_price": 101.0, "basis": 0.01},
}
INSTR = {
    "BTC": {"ctVal": 0.01, "swap_lot": 0.1, "swap_min": 0.1,
            "spot_lot": 0.001, "spot_min": 0.001},
}


class SizingTests(unittest.TestCase):
    def test_floor_to_lot(self):
        self.assertAlmostEqual(fb._floor_to_lot(4950.49, 0.1), 4950.4, places=6)
        self.assertAlmostEqual(fb._floor_to_lot(50.0, 0.001), 50.0, places=6)

    def test_positive_funding_is_short_perp_long_spot(self):
        book = {"BTC": {"g": 1, "weight": 0.5}}
        d = fb.desired_targets(book, PRICES, INSTR, 10000.0)["BTC"]
        self.assertEqual(d["spot_side"], "buy")
        self.assertEqual(d["perp_side"], "sell")
        self.assertAlmostEqual(d["spot_base"], 50.0, places=6)
        self.assertAlmostEqual(d["perp_ct"], 4950.4, places=1)

    def test_negative_funding_is_long_perp_short_spot(self):
        book = {"BTC": {"g": -1, "weight": 0.5}}
        d = fb.desired_targets(book, PRICES, INSTR, 10000.0)["BTC"]
        self.assertEqual(d["spot_side"], "sell")
        self.assertEqual(d["perp_side"], "buy")

    def test_below_minimum_is_skipped(self):
        book = {"BTC": {"g": 1, "weight": 0.5}}
        tiny = {"BTC": {"ctVal": 0.01, "swap_lot": 0.1, "swap_min": 1e9,
                        "spot_lot": 0.001, "spot_min": 0.001}}
        self.assertEqual(fb.desired_targets(book, PRICES, tiny, 10000.0), {})

    def test_missing_price_or_instrument_skipped(self):
        book = {"BTC": {"g": 1, "weight": 0.5}}
        self.assertEqual(fb.desired_targets(book, {}, INSTR, 10000.0), {})
        self.assertEqual(fb.desired_targets(book, PRICES, {}, 10000.0), {})


class PlanTests(unittest.TestCase):
    def _pos(self, g=1):
        return {"g": g, "spot_base": 50.0, "spot_side": "buy" if g == 1 else "sell",
                "perp_ct": 4950.4, "perp_side": "sell" if g == 1 else "buy"}

    def test_open_from_flat(self):
        plan = fb.diff_plan({"BTC": self._pos(1)}, {})
        self.assertEqual(len(plan), 2)
        self.assertTrue(all(o["action"] == "open" for o in plan))
        spot = next(o for o in plan if o["leg"] == "spot")
        perp = next(o for o in plan if o["leg"] == "perp")
        self.assertEqual(spot["side"], "buy")
        self.assertEqual(perp["side"], "sell")

    def test_no_change_no_orders(self):
        held = {"BTC": self._pos(1)}
        self.assertEqual(fb.diff_plan({"BTC": self._pos(1)}, held), [])

    def test_close_dropped_coin(self):
        plan = fb.diff_plan({}, {"BTC": self._pos(1)})
        self.assertEqual(len(plan), 2)
        self.assertTrue(all(o["action"] == "close" for o in plan))
        perp = next(o for o in plan if o["leg"] == "perp")
        self.assertTrue(perp["reduce_only"])
        self.assertEqual(perp["side"], "buy")  # opposite of the short

    def test_flip_side_closes_then_opens(self):
        plan = fb.diff_plan({"BTC": self._pos(-1)}, {"BTC": self._pos(1)})
        self.assertEqual(len(plan), 4)
        self.assertEqual(sum(o["action"] == "close" for o in plan), 2)
        self.assertEqual(sum(o["action"] == "open" for o in plan), 2)

    def test_reconcile_all_failed_keeps_prev(self):
        # all orders failed -> tracked book unchanged (no phantom positions)
        desired = {"BTC": self._pos(1)}
        held = fb.reconcile_held({}, desired, failed_coins={"BTC"})
        self.assertEqual(held, {})

    def test_reconcile_success_opens(self):
        desired = {"BTC": self._pos(1)}
        held = fb.reconcile_held({}, desired, failed_coins=set())
        self.assertIn("BTC", held)

    def test_reconcile_successful_close_removes(self):
        prev = {"BTC": self._pos(1)}
        held = fb.reconcile_held(prev, {}, failed_coins=set())
        self.assertEqual(held, {})

    def test_reconcile_failed_close_keeps_position(self):
        prev = {"BTC": self._pos(1)}
        held = fb.reconcile_held(prev, {}, failed_coins={"BTC"})
        self.assertIn("BTC", held)

    def test_caps_flag_oversized_leg(self):
        desired = {"BTC": {"g": 1, "notional": 9000.0, "spot_base": 1, "spot_side": "buy",
                           "perp_ct": 1, "perp_side": "sell"}}
        problems = fb.check_caps(desired, 10000.0)  # 90% > 40% cap
        self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main(verbosity=2)
