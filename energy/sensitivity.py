"""Fixed scenario matrix for the Swedish flexible-load optimizer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from energy.flexible_load import EVConfig, ZONES, download_prices, evaluate

SCENARIOS = {
    "default_15kwh_11kw_18": EVConfig(
        battery_energy_kwh=15, charging_kw=11, connect_hour=18
    ),
    "slow_15kwh_3_7kw_18": EVConfig(
        battery_energy_kwh=15, charging_kw=3.7, connect_hour=18
    ),
    "late_15kwh_7_4kw_21": EVConfig(
        battery_energy_kwh=15, charging_kw=7.4, connect_hour=21
    ),
    "heavy_30kwh_7_4kw_18": EVConfig(
        battery_energy_kwh=30, charging_kw=7.4, connect_hour=18
    ),
    "constraint_stress_30kwh_3_7kw_21": EVConfig(
        battery_energy_kwh=30, charging_kw=3.7, connect_hour=21
    ),
}


def run() -> dict:
    output = {}
    for zone in ZONES:
        prices = download_prices(zone)
        output[zone] = {}
        for name, config in SCENARIOS.items():
            result = evaluate(prices, ev=config)
            output[zone][name] = {
                "ev": result["ev"],
                "total_saved_sek": result["total"]["saved_sek"],
                **result["monthly_summary"],
                "research_gate_pass": result["verdict"]["research_gate_pass"],
            }
    return {
        "measurement": "flexible_ev_cost_saving_not_investment_return",
        "scenarios": output,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="research_energy_sensitivity.json")
    args = parser.parse_args()
    result = run()
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
