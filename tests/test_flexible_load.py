import unittest

import numpy as np
import pandas as pd

from energy.flexible_load import (
    EVConfig,
    Tariff,
    allocate_energy,
    evaluate,
    schedule_for_session,
)


class FlexibleLoadTests(unittest.TestCase):
    def test_optimizer_preserves_energy_and_uses_cheapest_intervals(self):
        start = pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC")
        frame = pd.DataFrame(
            {
                "time_start": start,
                "time_end": start + pd.Timedelta(hours=1),
                "all_in_price": [2.0, 1.0, 4.0, 3.0],
            }
        )
        allocation = allocate_energy(frame, 2.0, 1.0, optimize=True)

        self.assertEqual(allocation.sum(), 2.0)
        self.assertEqual(allocation.tolist(), [1.0, 1.0, 0.0, 0.0])

    def test_same_service_costs_less_than_immediate_charging(self):
        intervals = pd.date_range(
            "2024-01-01 00:00", "2024-02-02 00:00", freq="1h", tz="UTC"
        )
        local_hours = intervals.tz_convert("Europe/Stockholm").hour
        # Evening is expensive, night is cheap.
        spot = np.where((local_hours >= 18) & (local_hours < 22), 2.0, 0.2)
        prices = pd.DataFrame(
            {
                "zone": "SE3",
                "sek_per_kwh": spot,
                "time_start": intervals,
                "time_end": intervals + pd.Timedelta(hours=1),
            }
        )
        result = evaluate(
            prices,
            Tariff(
                markup_ex_vat_sek_kwh=0.0,
                variable_grid_ex_vat_sek_kwh=0.0,
                energy_tax_ex_vat_sek_kwh=0.0,
                vat_multiplier=1.0,
            ),
            EVConfig(
                battery_energy_kwh=2.0,
                charging_kw=1.0,
                efficiency=1.0,
            ),
        )

        self.assertTrue(result["verdict"]["all_service_constraints_met"])
        self.assertTrue(result["verdict"]["every_complete_month_positive"])
        self.assertGreater(result["monthly_summary"]["median_savings_pct"], 6)
        self.assertEqual(result["complete_months"], 1)

    def test_capacity_shortfall_is_rejected(self):
        start = pd.date_range("2024-01-01", periods=2, freq="1h", tz="UTC")
        frame = pd.DataFrame(
            {
                "time_start": start,
                "time_end": start + pd.Timedelta(hours=1),
                "all_in_price": [1.0, 2.0],
            }
        )
        with self.assertRaises(ValueError):
            allocate_energy(frame, 3.0, 1.0, optimize=True)

    def test_paper_schedule_contains_hash_and_required_energy(self):
        intervals = pd.date_range(
            "2024-01-01 17:00", "2024-01-02 06:00", freq="1h", tz="UTC"
        )
        prices = pd.DataFrame(
            {
                "zone": "SE3",
                "sek_per_kwh": np.linspace(1.0, 0.1, len(intervals)),
                "time_start": intervals,
                "time_end": intervals + pd.Timedelta(hours=1),
            }
        )
        ev = EVConfig(
            battery_energy_kwh=2.0,
            charging_kw=1.0,
            efficiency=1.0,
        )
        result = schedule_for_session(prices, "2024-01-01", ev=ev)

        scheduled = sum(
            row["ev_kw"]
            * (
                pd.Timestamp(row["valid_to"]) - pd.Timestamp(row["valid_from"])
            ).total_seconds()
            / 3600
            for row in result["schedule"]
        )
        self.assertEqual(result["mode"], "paper_only_no_device_commands")
        self.assertEqual(len(result["price_snapshot_sha256"]), 64)
        self.assertAlmostEqual(scheduled, 2.0)


if __name__ == "__main__":
    unittest.main()
