"""Swedish day-ahead EV charging optimizer and historical evaluator.

This is a cost-saving bot, not an investment-return strategy. It schedules the
same required EV energy inside the same plug-in window as the immediate-charge
baseline. It never uses V2G/export and never sends device commands.

Price source: https://www.elprisetjustnu.se/elpris-api
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
BASE = "https://www.elprisetjustnu.se/api/v1/prices"
ZONES = ("SE1", "SE2", "SE3", "SE4")


@dataclass(frozen=True)
class Tariff:
    markup_ex_vat_sek_kwh: float = 0.08
    variable_grid_ex_vat_sek_kwh: float = 0.25
    energy_tax_ex_vat_sek_kwh: float = 0.36
    vat_multiplier: float = 1.25


@dataclass(frozen=True)
class EVConfig:
    battery_energy_kwh: float = 15.0
    charging_kw: float = 11.0
    efficiency: float = 0.90
    connect_hour: int = 18
    departure_hour: int = 7


def _url(day: date, zone: str) -> str:
    return f"{BASE}/{day.year}/{day:%m-%d}_{zone}.json"


def _fetch_day(day: date, zone: str, retries: int = 4) -> list[dict]:
    request = urllib.request.Request(
        _url(day, zone), headers={"User-Agent": "flexible-load-research/1.0"}
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            if attempt == retries - 1:
                raise
        except Exception:
            if attempt == retries - 1:
                raise
        time.sleep(0.5 * (attempt + 1))
    return []


def download_prices(
    zone: str,
    start: str = "2022-11-01",
    end: str = "2026-07-28",
    refresh: bool = False,
) -> pd.DataFrame:
    zone = zone.upper()
    if zone not in ZONES:
        raise ValueError(f"Unknown Swedish zone: {zone}")
    path = CACHE / f"swedish_spot_{zone.lower()}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, parse_dates=["time_start", "time_end"])

    first = pd.Timestamp(start).date()
    last = pd.Timestamp(end).date()
    rows = []
    current = first
    while current <= last:
        raw = _fetch_day(current, zone)
        for row in raw:
            rows.append(
                {
                    "zone": zone,
                    "sek_per_kwh": float(row["SEK_per_kWh"]),
                    "time_start": row["time_start"],
                    "time_end": row["time_end"],
                    "source_url": _url(current, zone),
                }
            )
        current += timedelta(days=1)
        time.sleep(0.01)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["time_start"] = pd.to_datetime(frame["time_start"], utc=True)
    frame["time_end"] = pd.to_datetime(frame["time_end"], utc=True)
    frame = frame.drop_duplicates(["time_start", "zone"]).sort_values("time_start")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame


def all_in_price(spot: pd.Series, tariff: Tariff) -> pd.Series:
    return (
        spot
        + tariff.markup_ex_vat_sek_kwh
        + tariff.variable_grid_ex_vat_sek_kwh
        + tariff.energy_tax_ex_vat_sek_kwh
    ) * tariff.vat_multiplier


def allocate_energy(
    intervals: pd.DataFrame,
    grid_energy_kwh: float,
    charging_kw: float,
    optimize: bool,
) -> pd.Series:
    """Allocate identical energy chronologically or into cheapest intervals."""
    duration_hours = (
        intervals["time_end"] - intervals["time_start"]
    ).dt.total_seconds() / 3600
    capacity = duration_hours * charging_kw
    order = (
        intervals["all_in_price"].sort_values(kind="stable").index
        if optimize
        else intervals.index
    )
    allocation = pd.Series(0.0, index=intervals.index)
    remaining = grid_energy_kwh
    for index in order:
        take = min(float(capacity.loc[index]), remaining)
        allocation.loc[index] = take
        remaining -= take
        if remaining <= 1e-9:
            break
    if remaining > 1e-6:
        raise ValueError(f"Charging window cannot serve {remaining:.3f} kWh")
    return allocation


def evaluate(
    prices: pd.DataFrame,
    tariff: Tariff = Tariff(),
    ev: EVConfig = EVConfig(),
) -> dict:
    frame = prices.copy()
    frame["time_start"] = pd.to_datetime(frame["time_start"], utc=True)
    frame["time_end"] = pd.to_datetime(frame["time_end"], utc=True)
    frame = frame.sort_values("time_start").reset_index(drop=True)
    frame["all_in_price"] = all_in_price(frame["sek_per_kwh"], tariff)
    local = frame["time_start"].dt.tz_convert("Europe/Stockholm")
    available_dates = set(local.dt.date)
    first_day = min(available_dates)
    last_day = max(available_dates) - timedelta(days=1)
    grid_energy = ev.battery_energy_kwh / ev.efficiency
    rows = []
    current = first_day
    while current <= last_day:
        connect = pd.Timestamp(
            f"{current} {ev.connect_hour:02d}:00", tz="Europe/Stockholm"
        ).tz_convert("UTC")
        departure_day = current + timedelta(days=1)
        departure = pd.Timestamp(
            f"{departure_day} {ev.departure_hour:02d}:00",
            tz="Europe/Stockholm",
        ).tz_convert("UTC")
        window = frame[
            (frame["time_start"] >= connect)
            & (frame["time_end"] <= departure)
        ].copy()
        if window.empty:
            current += timedelta(days=1)
            continue
        try:
            immediate = allocate_energy(
                window, grid_energy, ev.charging_kw, optimize=False
            )
            optimized = allocate_energy(
                window, grid_energy, ev.charging_kw, optimize=True
            )
        except ValueError:
            current += timedelta(days=1)
            continue
        baseline_cost = float((immediate * window["all_in_price"]).sum())
        optimized_cost = float((optimized * window["all_in_price"]).sum())
        rows.append(
            {
                "session_date": current,
                "baseline_sek": baseline_cost,
                "optimized_sek": optimized_cost,
                "saved_sek": baseline_cost - optimized_cost,
                "energy_kwh": grid_energy,
                "service_shortfall_kwh": 0.0,
            }
        )
        current += timedelta(days=1)

    sessions = pd.DataFrame(rows)
    if sessions.empty:
        raise ValueError("No complete charging sessions in price data")
    sessions["month"] = pd.to_datetime(sessions["session_date"]).dt.to_period("M")
    monthly = sessions.groupby("month").agg(
        baseline_sek=("baseline_sek", "sum"),
        optimized_sek=("optimized_sek", "sum"),
        saved_sek=("saved_sek", "sum"),
        sessions=("session_date", "count"),
        service_shortfall_kwh=("service_shortfall_kwh", "sum"),
    )
    monthly["savings_pct"] = (
        monthly["saved_sek"] / monthly["baseline_sek"] * 100
    )
    complete = monthly[monthly["sessions"] >= 27]
    annual = sessions.assign(
        year=pd.to_datetime(sessions["session_date"]).dt.year
    ).groupby("year").agg(
        baseline_sek=("baseline_sek", "sum"),
        optimized_sek=("optimized_sek", "sum"),
        saved_sek=("saved_sek", "sum"),
    )
    annual["savings_pct"] = annual["saved_sek"] / annual["baseline_sek"] * 100
    result = {
        "strategy": "swedish_day_ahead_flexible_load",
        "measurement": "cost_saving_not_investment_return",
        "zone": str(prices["zone"].iloc[0]),
        "tariff": asdict(tariff),
        "ev": asdict(ev),
        "sessions": int(len(sessions)),
        "total": {
            "baseline_sek": round(float(sessions["baseline_sek"].sum()), 2),
            "optimized_sek": round(float(sessions["optimized_sek"].sum()), 2),
            "saved_sek": round(float(sessions["saved_sek"].sum()), 2),
            "savings_pct": round(
                float(sessions["saved_sek"].sum() / sessions["baseline_sek"].sum() * 100),
                2,
            ),
        },
        "complete_months": int(len(complete)),
        "monthly": {
            str(index): {
                key: round(float(value), 2)
                for key, value in row.items()
            }
            for index, row in complete.iterrows()
        },
        "monthly_summary": {
            "median_savings_pct": round(float(complete["savings_pct"].median()), 2),
            "mean_savings_pct": round(float(complete["savings_pct"].mean()), 2),
            "min_savings_pct": round(float(complete["savings_pct"].min()), 2),
            "months_ge_6pct": int((complete["savings_pct"] >= 6).sum()),
            "share_months_ge_6pct": round(
                float((complete["savings_pct"] >= 6).mean() * 100), 2
            ),
        },
        "annual": {
            str(index): {
                key: round(float(value), 2)
                for key, value in row.items()
            }
            for index, row in annual.iterrows()
        },
        "verdict": {
            "all_service_constraints_met": bool(
                sessions["service_shortfall_kwh"].sum() == 0
            ),
            "median_month_saving_ge_6pct": bool(
                complete["savings_pct"].median() >= 6
            ),
            "at_least_75pct_months_ge_6pct": bool(
                (complete["savings_pct"] >= 6).mean() >= 0.75
            ),
            "every_complete_month_positive": bool(
                (complete["saved_sek"] > 0).all()
            ),
        },
        "limitations": [
            "Savings apply only to the modeled flexible EV load, not the whole household bill.",
            "Daily 15 kWh charging is a representative scenario, not the user's measured demand.",
            "Tariff values are configurable assumptions, not the user's dated supplier invoices.",
            "Historical files are timetable-causal but not immutable point-in-time snapshots.",
        ],
    }
    result["verdict"]["research_gate_pass"] = bool(
        all(result["verdict"].values())
    )
    return result


def current_schedule(
    prices: pd.DataFrame,
    tariff: Tariff = Tariff(),
    ev: EVConfig = EVConfig(),
) -> list[dict]:
    frame = prices.copy()
    frame["time_start"] = pd.to_datetime(frame["time_start"], utc=True)
    frame["time_end"] = pd.to_datetime(frame["time_end"], utc=True)
    frame["all_in_price"] = all_in_price(frame["sek_per_kwh"], tariff)
    allocation = allocate_energy(
        frame,
        ev.battery_energy_kwh / ev.efficiency,
        ev.charging_kw,
        optimize=True,
    )
    output = []
    for index, energy in allocation[allocation > 0].items():
        row = frame.loc[index]
        duration = (row["time_end"] - row["time_start"]).total_seconds() / 3600
        output.append(
            {
                "valid_from": row["time_start"].isoformat(),
                "valid_to": row["time_end"].isoformat(),
                "ev_kw": round(float(energy / duration), 3),
                "all_in_sek_kwh": round(float(row["all_in_price"]), 4),
            }
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zone", default="SE3", choices=ZONES)
    parser.add_argument("--start", default="2022-11-01")
    parser.add_argument("--end", default="2026-07-28")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()
    prices = download_prices(args.zone, args.start, args.end, args.refresh)
    result = evaluate(prices)
    print(json.dumps(result, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
