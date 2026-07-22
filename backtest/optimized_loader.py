"""Load optimized 30m parameters."""

from __future__ import annotations

import json
import re
from pathlib import Path

from config import BacktestConfig, LiveConfig

ROOT = Path(__file__).resolve().parent.parent
OPT_PATH = ROOT / "optimized_30m.json"
OPT_BY_SYMBOL_PATH = ROOT / "optimized_30m_by_symbol.json"
SCAN_PATH = ROOT / "universe_scan_30m.json"
SQUEEZE_PATH = ROOT / "optimized_squeeze.json"

# Legacy fallback (commodities/crypto only)
RECOMMENDED_30M: dict[str, str] = {
    "GC=F": "rsi_mean_reversion",
    "BTC-USD": "donchian_breakout",
    "ETH-USD": "donchian_breakout",
    "SI=F": "macd_pullback",
}


def load_universe_recommendations() -> dict[str, str]:
    """Best profitable strategy per symbol from full universe scan."""
    if not SCAN_PATH.exists():
        return dict(RECOMMENDED_30M)
    data = json.loads(SCAN_PATH.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for sym, info in data.get("best_per_symbol", {}).items():
        ret = float(info.get("total_return_pct", 0))
        pf = info.get("profit_factor", 0)
        if ret > 0 and pf not in (0, "0") and float(pf) >= 1.0:
            out[sym] = info["strategy"]
    return out if out else dict(RECOMMENDED_30M)


def _filtered_scan_pairs() -> list[tuple[str, str]]:
    if not SCAN_PATH.exists():
        return list(RECOMMENDED_30M.items())
    data = json.loads(SCAN_PATH.read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    for sym, info in data.get("best_per_symbol", {}).items():
        ret = float(info.get("total_return_pct", 0))
        pf = info.get("profit_factor", 0)
        wr = float(info.get("win_rate_pct", 0))
        if ret <= 0 or pf in (0, "0") or float(pf) < 1.35:
            continue
        if wr < 52 and float(pf) < 1.6:
            continue
        pairs.append((sym, info["strategy"]))
    return pairs


def profitable_universe_pairs() -> list[tuple[str, str]]:
    """Profitable symbol/strategy pairs from universe scan."""
    return list(load_universe_recommendations().items())


def mixed_portfolio_path(timeframe: str = "30m") -> Path:
    if timeframe in ("30m", ""):
        return ROOT / "mixed_portfolio.json"
    return ROOT / f"mixed_portfolio_{timeframe}.json"


def mixed_portfolio_pairs(timeframe: str = "30m") -> list[tuple[str, str]]:
    """Curated mixed portfolio for the given entry timeframe."""
    mixed_path = mixed_portfolio_path(timeframe)
    if mixed_path.exists():
        data = json.loads(mixed_path.read_text(encoding="utf-8"))
        return [(p["symbol"], p["strategy"]) for p in data.get("pairs", [])]
    if timeframe == "30m":
        return _filtered_scan_pairs()
    scan = ROOT / f"universe_scan_{timeframe}.json"
    if not scan.exists():
        return []
    data = json.loads(scan.read_text(encoding="utf-8"))
    return [(sym, info["strategy"]) for sym, info in data.get("best_per_symbol", {}).items()
            if float(info.get("total_return_pct", 0)) > 0]


def triple_portfolio_pairs() -> list[tuple[str, str]]:
    """Same symbols + triple_tf_confluence on all three bots."""
    path = ROOT / "mixed_portfolio_triple.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return [(p["symbol"], p["strategy"]) for p in data.get("pairs", [])]
    strategy = "triple_tf_confluence"
    return [(s, strategy) for s in ["QQQ", "BTC-USD", "ETH-USD", "GLD", "SOL-USD"]]


def scalp_portfolio_pairs() -> list[tuple[str, str]]:
    """15m Velocity Rejection Scalp portfolio."""
    path = ROOT / "mixed_portfolio_scalp.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return [(p["symbol"], p["strategy"]) for p in data.get("pairs", [])]
    return [(s, "velocity_rejection") for s in ["AMZN", "TSLA", "NFLX", "USO"]]


def oos_portfolio_pairs(timeframe: str = "15m") -> list[tuple[str, str]]:
    """Walk-forward validated crypto pairs for one bot timeframe."""
    return [(e["symbol"], e["strategy"]) for e in oos_portfolio_entries(timeframe)]


def oos_portfolio_entries(timeframe: str = "15m") -> list[dict]:
    """
    OOS-validerade par med optimerade parametrar per symbol.
    Returnerar lista av dicts med symbol, strategy och params.
    """
    path = ROOT / "mixed_portfolio_oos.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))

    # Försök hitta par i "pairs"-listan filtrerat på timeframe
    raw_pairs = [p for p in data.get("pairs", []) if p.get("timeframe") == timeframe]

    # Fallback: hämta från bots-sektionen
    if not raw_pairs:
        raw_pairs = data.get("bots", {}).get(timeframe, [])

    entries = []
    for p in raw_pairs:
        # Extrahera optimerade parametrar direkt från par-objektet
        PARAM_KEYS = ("reward_risk", "swing_lookback", "macd_signal_mode",
                      "squeeze_bb_period", "squeeze_width_pct_max")
        params = {k: p[k] for k in PARAM_KEYS if k in p}

        # Slå ihop med eventuell "optimized_params"-sektion från bots
        bots_entry = next(
            (b for b in data.get("bots", {}).get(timeframe, [])
             if b.get("symbol") == p.get("symbol") and b.get("strategy") == p.get("strategy")),
            {},
        )
        params.update(bots_entry.get("optimized_params", {}))

        entries.append({
            "symbol":   p["symbol"],
            "strategy": p["strategy"],
            "params":   params,
        })
    return entries


def forex_oos_portfolio_path() -> Path:
    return ROOT / "mixed_portfolio_oos_forex.json"


def forex_oos_portfolio_entries(timeframe: str | None = None) -> list[dict]:
    """Forex OOS pairs with per-pair optimized params."""
    path = forex_oos_portfolio_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if timeframe and timeframe in data.get("bots", {}):
        return list(data["bots"][timeframe])
    pairs = data.get("pairs", [])
    if timeframe:
        pairs = [p for p in pairs if p.get("timeframe") == timeframe]
    return pairs


def forex_oos_portfolio_pairs(timeframe: str) -> list[tuple[str, str]]:
    return [(p["symbol"], p["strategy"]) for p in forex_oos_portfolio_entries(timeframe)]


def stocks_oos_portfolio_path() -> Path:
    return ROOT / "mixed_portfolio_oos_stocks.json"


def stocks_oos_portfolio_entries() -> list[dict]:
    """1d stocks/commodities OOS pairs."""
    path = stocks_oos_portfolio_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("pairs", [])


def meanrev_oos_portfolio_path() -> Path:
    return ROOT / "mixed_portfolio_oos_meanrev.json"


def meanrev_oos_portfolio_entries() -> list[dict]:
    """RSI(2) mean-reversion 1d pairs with per-pair params."""
    path = meanrev_oos_portfolio_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    param_keys = ("rsi2_oversold", "rsi2_atr_sl", "rsi2_exit_sma",
                  "rsi2_period", "rsi2_trend_sma", "rsi2_max_rr")
    for p in data.get("pairs", []):
        params = {k: p[k] for k in param_keys if k in p}
        entries.append({
            "symbol": p["symbol"],
            "strategy": p["strategy"],
            "timeframe": p.get("timeframe", "1d"),
            "params": params,
        })
    return entries


def spicy_oos_portfolio_path() -> Path:
    return ROOT / "mixed_portfolio_oos_spicy.json"


def spicy_oos_portfolio_entries() -> list[dict]:
    """ConvictionStack 1d pairs (aggressiv 'krydda') with per-pair params."""
    path = spicy_oos_portfolio_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    param_keys = ("reward_risk", "conviction_min_checks", "conviction_high_checks",
                  "conviction_risk_mult", "conviction_adx_min",
                  "conviction_rsi_recover", "conviction_overext_atr")
    for p in data.get("pairs", []):
        params = {k: p[k] for k in param_keys if k in p}
        entries.append({
            "symbol": p["symbol"],
            "strategy": p["strategy"],
            "timeframe": p.get("timeframe", "1d"),
            "params": params,
        })
    return entries


def apply_params_to_config(config: BacktestConfig | LiveConfig, params: dict) -> None:
    for key, value in params.items():
        if hasattr(config, key):
            setattr(config, key, value)


def load_optimized_params() -> dict[str, dict]:
    if not OPT_PATH.exists():
        return {}
    data = json.loads(OPT_PATH.read_text(encoding="utf-8"))
    return {item["strategy"]: item["best_params"] for item in data}


def load_per_symbol_params() -> dict[str, dict[str, dict]]:
    if not OPT_BY_SYMBOL_PATH.exists():
        return {}
    data = json.loads(OPT_BY_SYMBOL_PATH.read_text(encoding="utf-8"))
    out: dict[str, dict[str, dict]] = {}
    for strategy, by_sym in data.items():
        out[strategy] = {sym: entry["best_params"] for sym, entry in by_sym.items()}
    return out


def resolve_strategy_for_symbol(symbol: str) -> str | None:
    return load_universe_recommendations().get(symbol)


def params_for(symbol: str, strategy: str) -> dict:
    if strategy in ("squeeze_breakout", "squeeze_bidirectional") and SQUEEZE_PATH.exists():
        squeeze = json.loads(SQUEEZE_PATH.read_text(encoding="utf-8"))
        if symbol in squeeze and "params" in squeeze[symbol]:
            return squeeze[symbol]["params"]
    per_symbol = load_per_symbol_params().get(strategy, {}).get(symbol)
    if per_symbol:
        return per_symbol
    return load_optimized_params().get(strategy, {})


def _apply_params(config: BacktestConfig | LiveConfig, params: dict):
    for key, value in params.items():
        if hasattr(config, key):
            setattr(config, key, value)


def apply_to_config(config: BacktestConfig, strategy: str) -> BacktestConfig:
    _apply_params(config, params_for(config.symbol, strategy))
    return config


def apply_optimized_to_live(
    config: LiveConfig,
    auto_strategy: bool = True,
    extra_params: dict | None = None,
) -> LiveConfig:
    if auto_strategy:
        recommended = resolve_strategy_for_symbol(config.symbol)
        if recommended:
            config.strategy = recommended
    params = dict(params_for(config.symbol, config.strategy))
    if extra_params:
        params.update(extra_params)
    _apply_params(config, params)
    state_file, log_file = live_paths(config.symbol, config.strategy, config.timeframe)
    config.state_file = state_file
    config.log_file = log_file
    return config


def live_paths(symbol: str, strategy: str, timeframe: str = "") -> tuple[str, str]:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", symbol).strip("_").lower()
    tf = f"_{timeframe}" if timeframe else ""
    return (
        f"data/live/{safe}_{strategy}{tf}_state.json",
        f"data/live/{safe}_{strategy}{tf}.log",
    )
