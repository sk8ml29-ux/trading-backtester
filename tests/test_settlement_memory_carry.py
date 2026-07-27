import unittest

import numpy as np
import pandas as pd

from research.smrc_paper import mark_held_book, reconcile_book, target_book
from research.settlement_memory_carry import (
    Params,
    _survival_reserve,
    build_features,
    evaluate,
    simulate,
)


class SettlementMemoryCarryTests(unittest.TestCase):
    def test_survival_estimate_does_not_change_when_future_is_appended(self):
        idx = pd.date_range("2024-01-01", periods=80, freq="8h")
        prefix = pd.Series(
            np.where(np.arange(80) % 9 == 0, -0.0001, 0.0002), index=idx
        )
        params = Params(funding_lookback=5, survival_horizon=10)
        expected_a, forecast_a = _survival_reserve(prefix, params)

        future_idx = pd.date_range(idx[-1] + pd.Timedelta(hours=8), periods=20, freq="8h")
        extended = pd.concat([prefix, pd.Series(-0.001, index=future_idx)])
        expected_b, forecast_b = _survival_reserve(extended, params)

        np.testing.assert_allclose(expected_a, expected_b[: len(prefix)])
        np.testing.assert_allclose(
            forecast_a, forecast_b[: len(prefix)], equal_nan=True
        )

    def test_entry_cannot_receive_same_settlement_funding(self):
        idx = pd.date_range("2024-01-01", periods=16, freq="8h")
        raw = pd.DataFrame(
            {
                "funding": np.full(len(idx), 0.001),
                "perp": np.full(len(idx), 100.0),
                "spot": np.full(len(idx), 100.0),
                "dollar_volume": np.full(len(idx), 10_000_000.0),
            },
            index=idx,
        )
        params = Params(
            funding_lookback=2,
            min_funding=0.0,
            min_reserve=-1.0,
            round_trip_cost=0.0,
            min_history_steps=0,
            liquidity_lookback=2,
            top_liquid=1,
            slots=1,
            survival_horizon=3,
            leverage=1.0,
        )
        daily, diag = simulate(build_features({"X": raw}, params), params, turn_cost=0)

        # The first eligible decision is after the second observation. With 16
        # settlements, at most 14 can be earned; crediting entry settlement
        # would produce 15 * 0.001.
        self.assertAlmostEqual(diag["funding_pnl"], 0.014, places=10)
        self.assertGreater(daily.sum(), 0)

    def test_cost_multiplier_changes_realized_trading_cost(self):
        idx = pd.date_range("2024-01-01", periods=16, freq="8h")
        raw = pd.DataFrame(
            {
                "funding": np.full(len(idx), 0.001),
                "perp": np.full(len(idx), 100.0),
                "spot": np.full(len(idx), 100.0),
                "dollar_volume": np.full(len(idx), 10_000_000.0),
            },
            index=idx,
        )
        common = dict(
            funding_lookback=2,
            min_funding=0.0,
            min_reserve=-1.0,
            min_history_steps=0,
            liquidity_lookback=2,
            top_liquid=1,
            slots=1,
            survival_horizon=3,
            leverage=1.0,
        )
        p1 = Params(round_trip_cost=0.003, **common)
        p2 = Params(round_trip_cost=0.006, **common)
        _, d1 = simulate(build_features({"X": raw}, p1), p1)
        _, d2 = simulate(build_features({"X": raw}, p2), p2)

        self.assertAlmostEqual(d2["trading_cost"], 2 * d1["trading_cost"])

    def test_fixed_unit_ledger_captures_basis_move_without_free_rehedge(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="8h")
        raw = pd.DataFrame(
            {
                "funding": np.full(len(idx), 0.001),
                "perp": [100, 100, 100, 100, 100],
                "spot": [100, 100, 110, 110, 110],
                "dollar_volume": np.full(len(idx), 10_000_000.0),
            },
            index=idx,
        )
        params = Params(
            funding_lookback=2,
            min_funding=0.0,
            min_reserve=-1.0,
            round_trip_cost=0.0,
            min_history_steps=0,
            liquidity_lookback=2,
            top_liquid=1,
            slots=1,
            survival_horizon=3,
            leverage=1.0,
        )
        _, diag = simulate(build_features({"X": raw}, params), params, turn_cost=0)

        # One spot unit was bought at 100; the 100 -> 110 move earns 0.10 of
        # starting capital. No implicit per-settlement rehedge is performed.
        self.assertAlmostEqual(diag["basis_pnl"], 0.10, places=10)

    def test_paper_target_is_positive_carry_only_and_slot_limited(self):
        params = Params(
            funding_lookback=3,
            min_history_steps=4,
            min_funding=0.00005,
            min_reserve=-1.0,
            slots=2,
            leverage=1.0,
            survival_horizon=3,
        )
        times = pd.date_range("2024-01-01", periods=8, freq="8h", tz="UTC")
        funding = {
            coin: pd.DataFrame(
                {"time": times, "rate": np.full(len(times), rate)}
            )
            for coin, rate in {"A": 0.001, "B": 0.0008, "C": -0.001}.items()
        }
        prices = {
            coin: {"basis": 0.001, "perp_price": 100.1, "spot_price": 100.0}
            for coin in funding
        }
        book = target_book(funding, prices, {}, params)

        self.assertEqual(set(book), {"A", "B"})
        self.assertTrue(all(position["g"] == 1 for position in book.values()))
        self.assertAlmostEqual(sum(p["weight"] for p in book.values()), 1.0)

    def test_paper_keeps_fixed_units_without_free_rehedge(self):
        old = {
            "A": {
                "weight": 0.5,
                "spot_units": 5.0,
                "perp_units": 5.0,
                "spot_price": 100.0,
                "perp_price": 100.0,
                "held_settlements": 3,
                "reserve": 0.01,
            }
        }
        target = {"A": {**old["A"], "spot_price": 110.0, "perp_price": 105.0}}
        prices = {"A": {"spot_price": 110.0, "perp_price": 105.0}}
        book, cost, turnover = reconcile_book(old, target, prices, equity=1_000)

        self.assertEqual(book["A"]["spot_units"], 5.0)
        self.assertEqual(book["A"]["perp_units"], 5.0)
        self.assertEqual(cost, 0.0)
        self.assertEqual(turnover, 0.0)

    def test_paper_max_hold_forces_real_exit_before_reentry(self):
        params = Params(
            funding_lookback=3,
            min_history_steps=4,
            min_funding=0.00005,
            min_reserve=-1.0,
            slots=1,
            leverage=1.0,
            survival_horizon=3,
            max_hold_steps=5,
        )
        times = pd.date_range("2024-01-01", periods=8, freq="8h", tz="UTC")
        funding = {
            "A": pd.DataFrame(
                {"time": times, "rate": np.full(len(times), 0.001)}
            )
        }
        prices = {
            "A": {"basis": 0.001, "perp_price": 100.1, "spot_price": 100.0}
        }
        previous = {
            "A": {
                "g": 1,
                "weight": 1.0,
                "pred": 0.001,
                "reserve": 0.01,
                "perp_price": 100.1,
                "spot_price": 100.0,
                "perp_units": 10.0,
                "spot_units": 10.0,
                "held_settlements": 5,
            }
        }

        self.assertEqual(target_book(funding, prices, previous, params), {})

    def test_paper_marks_fixed_units_and_counts_settlements(self):
        now = pd.Timestamp("2024-01-02", tz="UTC")
        old = {
            "A": {
                "spot_units": 2.0,
                "perp_units": 2.0,
                "spot_price": 100.0,
                "perp_price": 100.0,
                "held_settlements": 0,
            }
        }
        funding = {
            "A": pd.DataFrame(
                {
                    "time": [now - pd.Timedelta(hours=8), now],
                    "rate": [0.001, 0.001],
                }
            )
        }
        prices = {"A": {"spot_price": 110.0, "perp_price": 105.0}}
        pnl, settlements = mark_held_book(
            old,
            funding,
            prices,
            int((now - pd.Timedelta(hours=16)).timestamp() * 1000),
            int(now.timestamp() * 1000),
        )

        self.assertAlmostEqual(pnl, 10.42)
        self.assertEqual(settlements, 2)
        self.assertEqual(old["A"]["held_settlements"], 2)

    def test_validation_excludes_parameter_training_period(self):
        idx = pd.date_range("2023-01-01", periods=1_200, freq="8h")
        raw = pd.DataFrame(
            {
                "funding": np.full(len(idx), 0.0002),
                "perp": np.full(len(idx), 100.0),
                "spot": np.full(len(idx), 100.0),
                "dollar_volume": np.full(len(idx), 10_000_000.0),
            },
            index=idx,
        )
        params = Params(
            funding_lookback=3,
            min_history_steps=3,
            liquidity_lookback=3,
            slots=1,
            top_liquid=1,
            leverage=1.0,
        )
        result = evaluate(
            build_features({"BTCUSDT": raw}, params),
            params,
            folds=2,
            validation_start="2023-07-01",
        )

        self.assertEqual(result["post_training_start"], "2023-07-01 00:00:00")
        self.assertTrue(
            all(pd.Timestamp(fold["start"]) >= pd.Timestamp("2023-07-01")
                for fold in result["folds"])
        )


if __name__ == "__main__":
    unittest.main()
