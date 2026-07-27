import unittest

import numpy as np
import pandas as pd

from research.settlement_memory_carry import (
    Params,
    _survival_reserve,
    build_features,
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
        )
        _, diag = simulate(build_features({"X": raw}, params), params, turn_cost=0)

        # One spot unit was bought at 100; the 100 -> 110 move earns 0.10 of
        # starting capital. No implicit per-settlement rehedge is performed.
        self.assertAlmostEqual(diag["basis_pnl"], 0.10, places=10)


if __name__ == "__main__":
    unittest.main()
