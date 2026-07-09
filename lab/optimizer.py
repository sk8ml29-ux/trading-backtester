"""
Lab Optimizer — parameter mutation engine.

Generates candidate parameter sets by randomly mutating known
BacktestConfig fields within their search spaces.
No external optimizer dependency — pure random search + constraints.
"""

from __future__ import annotations

import logging
import random
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SEARCH SPACES  (keyed by BacktestConfig attribute name)
# ─────────────────────────────────────────────────────────────────────────────

PARAM_SPACES: dict[str, dict[str, Any]] = {
    # ── Donchian ─────────────────────────────────────────────────────────────
    "donchian_period":        {"type": "int",   "low": 10, "high": 60,  "step": 5},
    "donchian_exit_period":   {"type": "int",   "low": 5,  "high": 30,  "step": 5},
    # ── RSI ──────────────────────────────────────────────────────────────────
    "rsi_period":             {"type": "int",   "low": 8,  "high": 25,  "step": 1},
    "rsi_oversold":           {"type": "int",   "low": 20, "high": 38,  "step": 2},
    "rsi_overbought":         {"type": "int",   "low": 62, "high": 80,  "step": 2},
    # ── MACD ─────────────────────────────────────────────────────────────────
    "macd_fast":              {"type": "int",   "low": 8,  "high": 16,  "step": 1},
    "macd_slow":              {"type": "int",   "low": 20, "high": 36,  "step": 1},
    "macd_signal":            {"type": "int",   "low": 6,  "high": 12,  "step": 1},
    # ── EMA ──────────────────────────────────────────────────────────────────
    "ema_fast":               {"type": "int",   "low": 5,  "high": 20,  "step": 1},
    "ema_slow":               {"type": "int",   "low": 30, "high": 100, "step": 5},
    # ── ATR ──────────────────────────────────────────────────────────────────
    "atr_period":             {"type": "int",   "low": 10, "high": 25,  "step": 1},
    "atr_multiplier":         {"type": "float", "low": 1.0, "high": 3.5, "step": 0.25},
    # ── Reward / Risk ────────────────────────────────────────────────────────
    "reward_risk":            {"type": "float", "low": 1.5, "high": 4.0, "step": 0.25},
    "fx_reward_risk":         {"type": "float", "low": 1.2, "high": 3.0, "step": 0.2},
    # ── Bollinger / Squeeze ──────────────────────────────────────────────────
    "bb_period":              {"type": "int",   "low": 15, "high": 30,  "step": 1},
    "bb_std":                 {"type": "float", "low": 1.5, "high": 2.5, "step": 0.25},
    "kc_multiplier":          {"type": "float", "low": 1.0, "high": 2.5, "step": 0.25},
    # ── ADX ──────────────────────────────────────────────────────────────────
    "adx_period":             {"type": "int",   "low": 10, "high": 20,  "step": 2},
    "adx_threshold":          {"type": "int",   "low": 18, "high": 32,  "step": 2},
}

# Logical constraint pairs: (fast_key, slow_key, min_gap)
_ORDERING_CONSTRAINTS = [
    ("macd_fast",  "macd_slow",  4),
    ("ema_fast",   "ema_slow",   10),
]

# Logical value pairs: (low_key, high_key, min_gap)
_BAND_CONSTRAINTS = [
    ("rsi_oversold", "rsi_overbought", 20),
]


def _sample_param(space: dict) -> Any:
    if space["type"] == "int":
        choices = list(range(space["low"], space["high"] + 1, space["step"]))
        return random.choice(choices)
    if space["type"] == "float":
        choices: list[float] = []
        v = space["low"]
        while v <= space["high"] + 1e-9:
            choices.append(round(v, 4))
            v += space["step"]
        return random.choice(choices)
    raise ValueError(f"Unknown param type: {space['type']}")


def _apply_constraints(params: dict) -> dict:
    for fast_k, slow_k, gap in _ORDERING_CONSTRAINTS:
        if fast_k in params and slow_k in params:
            if params[fast_k] >= params[slow_k] - gap:
                params[fast_k] = max(
                    PARAM_SPACES[fast_k]["low"],
                    params[slow_k] - gap,
                )

    for low_k, high_k, gap in _BAND_CONSTRAINTS:
        if low_k in params and high_k in params:
            if params[low_k] >= params[high_k] - gap:
                params[low_k] = max(
                    PARAM_SPACES[low_k]["low"],
                    params[high_k] - gap,
                )

    return params


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def mutate_params(base_params: dict, n_mutations: int = 3) -> dict:
    """
    Apply n random mutations to base_params.
    Only mutates keys that are in PARAM_SPACES.
    Returns a new dict — base_params is not modified.
    """
    candidate = deepcopy(base_params)
    mutable = [k for k in candidate if k in PARAM_SPACES]
    if not mutable:
        logger.debug("[Optimizer] No mutable params found in %s", list(base_params.keys()))
        return candidate

    keys = random.sample(mutable, min(n_mutations, len(mutable)))
    for key in keys:
        candidate[key] = _sample_param(PARAM_SPACES[key])

    return _apply_constraints(candidate)


def generate_candidates(base_params: dict, n: int = 10) -> list[dict]:
    """
    Generate n distinct parameter candidates by mutating base_params.
    Falls back to random number of mutations (1–4) per candidate.
    """
    seen: set[str] = set()
    candidates: list[dict] = []
    attempts = 0

    while len(candidates) < n and attempts < n * 25:
        attempts += 1
        c = mutate_params(base_params, n_mutations=random.randint(1, 4))
        key = str(sorted(c.items()))
        if key not in seen:
            seen.add(key)
            candidates.append(c)

    if len(candidates) < n:
        logger.debug("[Optimizer] Only generated %d/%d candidates", len(candidates), n)

    return candidates
