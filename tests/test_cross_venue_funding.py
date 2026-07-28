import unittest

import numpy as np
import pandas as pd

from research.cross_venue_funding import (
    Params,
    _funding_on_binance_grid,
    simulate,
)


class CrossVenueFundingTests(unittest.TestCase):
    def test_hourly_settlements_are_aggregated_into_binance_interval(self):
        binance_index = pd.date_range("2024-01-01 08:00", periods=3, freq="8h")
        other_index = pd.date_range("2024-01-01 04:00", periods=6, freq="4h")
        result = _funding_on_binance_grid(
            pd.Series([0.003, 0.003, 0.003], index=binance_index),
            pd.Series([0.001] * 6, index=other_index),
        )

        self.assertEqual(
            result["hyperliquid_funding"].tolist(), [0.002, 0.002, 0.002]
        )
        self.assertTrue(
            np.allclose(
                result["binance_funding"] - result["hyperliquid_funding"], 0.001
            )
        )

    def test_entry_does_not_receive_same_settlement_spread(self):
        index = pd.date_range("2024-01-01", periods=8, freq="8h")
        frame = pd.DataFrame(
            {
                "reserve": 0.01,
                "forecast": 0.001,
                "side": 1.0,
                "basis": 0.0,
                "liquidity": 1_000_000.0,
                "age": np.arange(8),
                "binance_price": 100.0,
                "hyperliquid_price": 100.0,
                "binance_funding": 0.001,
                "hyperliquid_funding": 0.0,
            },
            index=index,
        )
        params = Params(
            min_history_steps=0,
            min_spread=0.0,
            min_reserve=0.0,
            slots=1,
            leverage=1.0,
            max_hold_steps=100,
        )
        step, _, diagnostics = simulate(
            {"BTC": frame}, params, cost_multiplier=0.0
        )

        # Entry is after t0, so only t1..t7 can earn seven settlements.
        self.assertAlmostEqual(diagnostics["funding_pnl"], 0.007)
        self.assertAlmostEqual(step.sum(), 0.007)

    def test_venue_basis_pnl_has_correct_direction(self):
        index = pd.date_range("2024-01-01", periods=4, freq="8h")
        frame = pd.DataFrame(
            {
                "reserve": 0.01,
                "forecast": 0.001,
                "side": 1.0,
                "basis": [0.0, 0.0, -0.05, -0.05],
                "liquidity": 1_000_000.0,
                "age": np.arange(4),
                "binance_price": 100.0,
                "hyperliquid_price": [100.0, 100.0, 105.0, 105.0],
                "binance_funding": 0.0,
                "hyperliquid_funding": 0.0,
            },
            index=index,
        )
        params = Params(
            min_history_steps=0,
            min_spread=0.0,
            min_reserve=0.0,
            slots=1,
            leverage=1.0,
            max_exit_basis=1.0,
            max_hold_steps=100,
        )
        _, _, diagnostics = simulate({"BTC": frame}, params, cost_multiplier=0.0)

        # side=+1 is short Binance / long OKX, so OKX +5% earns +5%.
        self.assertAlmostEqual(diagnostics["venue_basis_pnl"], 0.05)


if __name__ == "__main__":
    unittest.main()
