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
        return normalize_intervals(
            pd.read_csv(path, parse_dates=["time_start", "time_end"])
        )

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
    return normalize_intervals(frame)


def normalize_intervals(prices: pd.DataFrame) -> pd.DataFrame:
    """Repair source DST end-times using the next unique UTC start.

    The source occasionally labels the first repeated autumn hour as a two-hour
    interval while also emitting the second hour separately. Clipping each end
    to the next start produces a gap-free physical timeline without duplicate
    charging capacity.
    """
    frame = normalize_intervals(prices)
    frame = frame.sort_values("time_start").drop_duplicates(
        ["zone", "time_start"], keep="last"
    )
    next_start = frame.groupby("zone")["time_start"].shift(-1)
    frame["time_end"] = pd.concat(
        [frame["time_end"], next_start.rename("next_start")], axis=1
    ).min(axis=1)
    frame = frame[frame["time_end"] > frame["time_start"]]
    return frame.reset_index(drop=True)


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


def allocate_contiguous_cheapest(
    intervals: pd.DataFrame,
    grid_energy_kwh: float,
    charging_kw: float,
) -> pd.Series:
    """Best possible single contiguous charging block (strong benchmark)."""
    best = None
    best_cost = float("inf")
    for start_position in range(len(intervals)):
        candidate = intervals.iloc[start_position:]
        try:
            allocation = allocate_energy(
                candidate, grid_energy_kwh, charging_kw, optimize=False
            )
        except ValueError:
            continue
        used = allocation[allocation > 0]
        if used.empty:
            continue
        # Candidate rows are time-sorted and therefore physically contiguous.
        cost = float((used * candidate.loc[used.index, "all_in_price"]).sum())
        if cost < best_cost:
            best_cost = cost
            best = allocation.reindex(intervals.index, fill_value=0.0)
    if best is None:
        raise ValueError("No contiguous charging block can serve the energy")
    return best


def _complete_window(window: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> bool:
    ordered = window.sort_values("time_start")
    if ordered.empty:
        return False
    if ordered["time_start"].iloc[0] != start or ordered["time_end"].iloc[-1] != end:
        return False
    return bool(
        (
            ordered["time_start"].iloc[1:].reset_index(drop=True)
            == ordered["time_end"].iloc[:-1].reset_index(drop=True)
        ).all()
    )


def evaluate(
    prices: pd.DataFrame,
    tariff: Tariff = Tariff(),
    ev: EVConfig = EVConfig(),
) -> dict:
    frame = normalize_intervals(prices)
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
        if not _complete_window(window, connect, departure):
            current += timedelta(days=1)
            continue
        try:
            immediate = allocate_energy(
                window, grid_energy, ev.charging_kw, optimize=False
            )
            optimized = allocate_energy(
                window, grid_energy, ev.charging_kw, optimize=True
            )
            contiguous = allocate_contiguous_cheapest(
                window, grid_energy, ev.charging_kw
            )
            timer_start = pd.Timestamp(
                f"{departure_day} 00:00", tz="Europe/Stockholm"
            ).tz_convert("UTC")
            timer_window = window[window["time_start"] >= timer_start]
        except ValueError:
            current += timedelta(days=1)
            continue
        immediate_cost = float((immediate * window["all_in_price"]).sum())
        try:
            timer = allocate_energy(
                timer_window, grid_energy, ev.charging_kw, optimize=False
            ).reindex(window.index, fill_value=0.0)
            timer_cost = float((timer * window["all_in_price"]).sum())
        except ValueError:
            # A safety-aware fixed timer must fall back to immediate charging
            # when midnight leaves too little time.
            timer_cost = immediate_cost
        contiguous_cost = float((contiguous * window["all_in_price"]).sum())
        benchmark_cost = min(immediate_cost, timer_cost, contiguous_cost)
        optimized_cost = float((optimized * window["all_in_price"]).sum())
        rows.append(
            {
                "session_date": current,
                "immediate_sek": immediate_cost,
                "timer_sek": timer_cost,
                "contiguous_sek": contiguous_cost,
                "benchmark_sek": benchmark_cost,
                "optimized_sek": optimized_cost,
                "saved_sek": benchmark_cost - optimized_cost,
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
        immediate_sek=("immediate_sek", "sum"),
        timer_sek=("timer_sek", "sum"),
        contiguous_sek=("contiguous_sek", "sum"),
        benchmark_sek=("benchmark_sek", "sum"),
        optimized_sek=("optimized_sek", "sum"),
        saved_sek=("saved_sek", "sum"),
        sessions=("session_date", "count"),
        service_shortfall_kwh=("service_shortfall_kwh", "sum"),
    )
    monthly["savings_pct"] = (
        monthly["saved_sek"] / monthly["benchmark_sek"] * 100
    )
    monthly["expected_sessions"] = monthly.index.days_in_month
    complete = monthly[monthly["sessions"] >= monthly["expected_sessions"]]
    annual = sessions.assign(
        year=pd.to_datetime(sessions["session_date"]).dt.year
    ).groupby("year").agg(
        benchmark_sek=("benchmark_sek", "sum"),
        optimized_sek=("optimized_sek", "sum"),
        saved_sek=("saved_sek", "sum"),
    )
    annual["savings_pct"] = annual["saved_sek"] / annual["benchmark_sek"] * 100
    result = {
        "strategy": "swedish_day_ahead_flexible_load",
        "measurement": "cost_saving_not_investment_return",
        "zone": str(prices["zone"].iloc[0]),
        "tariff": asdict(tariff),
        "ev": asdict(ev),
        "sessions": int(len(sessions)),
        "total": {
            "immediate_sek": round(float(sessions["immediate_sek"].sum()), 2),
            "timer_sek": round(float(sessions["timer_sek"].sum()), 2),
            "contiguous_sek": round(float(sessions["contiguous_sek"].sum()), 2),
            "benchmark_sek": round(float(sessions["benchmark_sek"].sum()), 2),
            "optimized_sek": round(float(sessions["optimized_sek"].sum()), 2),
            "saved_sek": round(float(sessions["saved_sek"].sum()), 2),
            "savings_pct": round(
                float(
                    sessions["saved_sek"].sum()
                    / sessions["benchmark_sek"].sum()
                    * 100
                ),
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
            "Savings are incremental to the best of immediate, midnight-timer, and cheapest contiguous-block benchmarks.",
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


def schedule_for_session(
    prices: pd.DataFrame,
    session_date: str,
    tariff: Tariff = Tariff(),
    ev: EVConfig = EVConfig(),
) -> dict:
    """Create a paper-only charging schedule for one local session date."""
    day = pd.Timestamp(session_date).date()
    connect = pd.Timestamp(
        f"{day} {ev.connect_hour:02d}:00", tz="Europe/Stockholm"
    ).tz_convert("UTC")
    departure = pd.Timestamp(
        f"{day + timedelta(days=1)} {ev.departure_hour:02d}:00",
        tz="Europe/Stockholm",
    ).tz_convert("UTC")
    frame = prices.copy()
    frame["time_start"] = pd.to_datetime(frame["time_start"], utc=True)
    frame["time_end"] = pd.to_datetime(frame["time_end"], utc=True)
    frame = frame[
        (frame["time_start"] >= connect) & (frame["time_end"] <= departure)
    ].copy()
    if not _complete_window(frame, connect, departure):
        raise ValueError("Incomplete day-ahead prices for the charging window")
    raw_snapshot = frame[
        ["zone", "sek_per_kwh", "time_start", "time_end"]
    ].to_json(date_format="iso", orient="records")
    schedule = current_schedule(frame, tariff, ev)
    return {
        "mode": "paper_only_no_device_commands",
        "zone": str(frame["zone"].iloc[0]),
        "session_date": str(day),
        "connect": connect.isoformat(),
        "departure": departure.isoformat(),
        "required_battery_energy_kwh": ev.battery_energy_kwh,
        "required_grid_energy_kwh": round(
            ev.battery_energy_kwh / ev.efficiency, 3
        ),
        "price_snapshot_sha256": hashlib.sha256(
            raw_snapshot.encode()
        ).hexdigest(),
        "schedule": schedule,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zone", default="SE3", choices=ZONES)
    parser.add_argument("--start", default="2022-11-01")
    parser.add_argument("--end", default="2026-07-28")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--schedule-date")
    parser.add_argument("--schedule-out")
    parser.add_argument("--out")
    args = parser.parse_args()
    prices = download_prices(args.zone, args.start, args.end, args.refresh)
    if args.schedule_date:
        result = schedule_for_session(prices, args.schedule_date)
        print(json.dumps(result, indent=2))
        if args.schedule_out:
            Path(args.schedule_out).write_text(json.dumps(result, indent=2) + "\n")
        return
    result = evaluate(prices)
    print(json.dumps(result, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
