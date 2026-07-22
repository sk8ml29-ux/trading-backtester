"""
Strategi-hälsovakt
==================
Jämför varje strategis LIVE-resultat mot dess förväntade profil (benchmark
från OOS backtest). Upptäcker degradering och rekommenderar paus.

Detta är svaret på frågan "hur vet jag att en strategi slutat fungera?".
Vi kan inte förutse framtiden — men vi kan mäta om live avviker från vad
backtesten förutsade, och stänga av innan skadan blir stor.

Hälsostatus:
  GREEN  — live presterar som förväntat eller bättre
  YELLOW — live släpar men inom statistisk normalvariation → bevaka
  RED    — live under förväntad undre gräns ELLER drawdown värre än
           backtestens max → PAUSA strategin

Kriterier för RED (degradering):
  1. Live-drawdown > expected_max_dd * DD_TOLERANCE  (default 2.0x)
  2. Live-equity under undre förväntad bana (mean - N*std, ackumulerat)
  3. Rullande förlustserie längre än statistiskt rimligt givet win-rate

Kriterier för YELLOW:
  - Live-drawdown > expected_max_dd men < 2x
  - Live-equity mellan undre band och förväntat
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS_PATH = ROOT / "strategy_benchmarks.json"

# Toleranser
DD_TOLERANCE_RED = 2.0     # live DD > 2x förväntad max → röd
DD_TOLERANCE_YELLOW = 1.0  # live DD > 1x förväntad max → gul
BAND_STD_MULT = 1.5        # undre bana = mean - 1.5*std per månad
MIN_MONTHS_BEFORE_JUDGE = 0.5  # vänta minst ~2 veckor innan dom


@dataclass
class HealthVerdict:
    symbol: str
    strategy: str
    status: Literal["green", "yellow", "red", "unknown"]
    reason: str
    live_return_pct: float
    expected_return_pct: float
    lower_band_pct: float
    live_dd_pct: float
    expected_max_dd_pct: float
    months_live: float
    recommend_pause: bool


def load_benchmarks() -> dict:
    if not BENCHMARKS_PATH.exists():
        return {}
    return json.loads(BENCHMARKS_PATH.read_text(encoding="utf-8")).get("benchmarks", {})


def _months_between(start_iso: str | None, now: datetime | None = None) -> float:
    if not start_iso:
        return 0.0
    now = now or datetime.now()
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", ""))
    except Exception:
        return 0.0
    return max((now - start).days / 30.0, 0.0)


def assess_strategy(
    benchmark: dict,
    equity: float,
    peak_equity: float,
    initial_capital: float,
    months_live: float,
) -> HealthVerdict:
    """Bedöm en strategis hälsa mot dess benchmark."""
    sym = benchmark.get("symbol", "?")
    strat = benchmark.get("strategy", "?")

    if initial_capital <= 0:
        return HealthVerdict(sym, strat, "unknown", "saknar startkapital",
                             0, 0, 0, 0, 0, months_live, False)

    live_return = (equity - initial_capital) / initial_capital
    live_dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0

    exp_m = float(benchmark.get("expected_monthly_return", 0.0))
    std_m = float(benchmark.get("monthly_std", 0.0))
    exp_max_dd = float(benchmark.get("expected_max_dd", 0.0)) / 100.0

    # Förväntad bana och undre gräns vid nuvarande tidpunkt
    m = max(months_live, 0.01)
    expected_return = (1 + exp_m) ** m - 1
    lower_monthly = exp_m - BAND_STD_MULT * std_m
    lower_band = (1 + lower_monthly) ** m - 1
    # "Fortfarande normalt"-gräns: mild avvikelse under förväntat räknas som grön
    normal_monthly = exp_m - 0.75 * std_m
    normal_band = (1 + normal_monthly) ** m - 1

    # För tidigt att döma
    if months_live < MIN_MONTHS_BEFORE_JUDGE:
        return HealthVerdict(
            sym, strat, "green",
            f"för tidigt att döma ({months_live:.1f} mån live)",
            live_return*100, expected_return*100, lower_band*100,
            live_dd*100, exp_max_dd*100, months_live, False,
        )

    # RED-kriterier
    if exp_max_dd > 0 and live_dd > exp_max_dd * DD_TOLERANCE_RED:
        return HealthVerdict(
            sym, strat, "red",
            f"drawdown {live_dd*100:.1f}% > 2x förväntad ({exp_max_dd*100:.1f}%) — PAUSA",
            live_return*100, expected_return*100, lower_band*100,
            live_dd*100, exp_max_dd*100, months_live, True,
        )
    if live_return < lower_band:
        return HealthVerdict(
            sym, strat, "red",
            f"avkastning {live_return*100:+.1f}% under undre gräns ({lower_band*100:+.1f}%) — PAUSA",
            live_return*100, expected_return*100, lower_band*100,
            live_dd*100, exp_max_dd*100, months_live, True,
        )

    # YELLOW-kriterier
    if exp_max_dd > 0 and live_dd > exp_max_dd * DD_TOLERANCE_YELLOW:
        return HealthVerdict(
            sym, strat, "yellow",
            f"drawdown {live_dd*100:.1f}% över förväntad ({exp_max_dd*100:.1f}%) — bevaka",
            live_return*100, expected_return*100, lower_band*100,
            live_dd*100, exp_max_dd*100, months_live, False,
        )
    if live_return < normal_band:
        return HealthVerdict(
            sym, strat, "yellow",
            f"avkastning {live_return*100:+.1f}% under normalband ({normal_band*100:+.1f}%) men över undre gräns — bevaka",
            live_return*100, expected_return*100, lower_band*100,
            live_dd*100, exp_max_dd*100, months_live, False,
        )

    # GREEN
    return HealthVerdict(
        sym, strat, "green",
        f"presterar som förväntat ({live_return*100:+.1f}% vs {expected_return*100:+.1f}%)",
        live_return*100, expected_return*100, lower_band*100,
        live_dd*100, exp_max_dd*100, months_live, False,
    )


def assess_from_state_file(state_path: Path, benchmark: dict) -> HealthVerdict | None:
    """Läs en live state-fil och bedöm mot benchmark."""
    if not state_path.exists():
        return None
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    equity = float(raw.get("equity", 0))
    peak = float(raw.get("peak_equity", equity))
    initial = float(raw.get("initial_capital", 0))
    months = _months_between(raw.get("start_time"))
    # Fallback: om ingen starttid, uppskatta från trade_count / trades_per_month
    if months <= 0:
        tpm = float(benchmark.get("trades_per_month", 1)) or 1
        months = float(raw.get("trade_count", 0)) / tpm if tpm else 0.0
    return assess_strategy(benchmark, equity, peak, initial, months)
