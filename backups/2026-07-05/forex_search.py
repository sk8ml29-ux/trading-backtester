"""Systematic forex strategy search with walk-forward OOS on cached Dukascopy data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.data_loader import _filter_dates, _load_csv, cache_path
from config import BacktestConfig
from research.pipeline import FOREX, build_portfolio_json
from research.walk_forward import run_walk_forward
from strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent.parent

FOREX_TIMEFRAMES = ["15m", "30m", "1h"]
DEFAULT_START = "2023-01-01"
MIN_HF_OOS_TRADES = 20

# High-frequency focused search + legacy winners for comparison
FOREX_SEARCH_STRATEGIES = [
    "forex_harmonic",
    "forex_rsi_reversion",
    "forex_bollinger_fade",
    "forex_short_breakout",
    "forex_session_momentum",
    "forex_london_breakout",
    "forex_range_fade",
    "forex_overlap_momentum",
    "velocity_rejection",
    "rsi_bidirectional",
    "donchian_bidirectional",
]

PARAM_GRIDS: dict[str, list[dict[str, Any]]] = {
    "forex_harmonic": [
        {"fx_reward_risk": 1.8, "fx_harmonic_patterns": "butterfly", "fx_harmonic_d_tol": 0.40, "fx_session_filter": "active"},
        {"fx_reward_risk": 2.0, "fx_harmonic_patterns": "butterfly,gartley", "fx_harmonic_d_tol": 0.35, "fx_session_filter": "london"},
        {"fx_reward_risk": 2.2, "fx_harmonic_patterns": "butterfly,gartley,bat", "fx_harmonic_d_tol": 0.35, "fx_session_filter": "active"},
        {"fx_reward_risk": 1.5, "fx_harmonic_patterns": "butterfly,gartley,bat,crab", "fx_harmonic_d_tol": 0.45, "fx_harmonic_pivot_left": 2, "fx_harmonic_pivot_right": 2, "fx_session_filter": "ny"},
        {"fx_reward_risk": 2.0, "fx_harmonic_patterns": "gartley,bat", "fx_harmonic_d_tol": 0.30, "fx_session_filter": "active"},
        {"fx_reward_risk": 1.8, "fx_harmonic_patterns": "butterfly", "fx_harmonic_d_tol": 0.50, "fx_harmonic_pivot_left": 2, "fx_harmonic_pivot_right": 2, "fx_session_filter": "all"},
    ],
    "forex_rsi_reversion": [
        {"fx_reward_risk": 1.5, "fx_rsi_oversold": 35.0, "fx_rsi_overbought": 65.0, "fx_max_adx_range": 28.0, "fx_atr_sl": 0.8, "fx_session_filter": "active"},
        {"fx_reward_risk": 1.8, "fx_rsi_oversold": 32.0, "fx_rsi_overbought": 68.0, "fx_max_adx_range": 25.0, "fx_atr_sl": 1.0, "fx_session_filter": "london"},
        {"fx_reward_risk": 1.5, "fx_rsi_oversold": 30.0, "fx_rsi_overbought": 70.0, "fx_max_adx_range": 22.0, "fx_atr_sl": 0.7, "fx_session_filter": "active"},
        {"fx_reward_risk": 2.0, "fx_rsi_oversold": 38.0, "fx_rsi_overbought": 62.0, "fx_max_adx_range": 30.0, "fx_atr_sl": 0.9, "fx_session_filter": "ny"},
        {"fx_reward_risk": 1.6, "fx_rsi_oversold": 33.0, "fx_rsi_overbought": 67.0, "fx_max_adx_range": 26.0, "fx_atr_sl": 0.75, "fx_session_filter": "all"},
    ],
    "forex_bollinger_fade": [
        {"fx_reward_risk": 1.5, "fx_bb_period": 20, "fx_rsi_overbought": 65.0, "fx_rsi_oversold": 35.0, "fx_max_adx_range": 28.0, "fx_session_filter": "active"},
        {"fx_reward_risk": 1.8, "fx_bb_period": 16, "fx_rsi_overbought": 68.0, "fx_rsi_oversold": 32.0, "fx_max_adx_range": 25.0, "fx_session_filter": "london"},
        {"fx_reward_risk": 1.5, "fx_bb_period": 20, "fx_bb_std": 2.2, "fx_rsi_overbought": 60.0, "fx_rsi_oversold": 40.0, "fx_max_adx_range": 30.0, "fx_session_filter": "all"},
        {"fx_reward_risk": 2.0, "fx_bb_period": 14, "fx_rsi_overbought": 65.0, "fx_rsi_oversold": 35.0, "fx_max_adx_range": 22.0, "fx_atr_sl": 0.8, "fx_session_filter": "active"},
    ],
    "forex_short_breakout": [
        {"donchian_entry": 8, "donchian_exit": 3, "fx_reward_risk": 1.5, "fx_min_range_atr": 0.20, "fx_session_filter": "active"},
        {"donchian_entry": 10, "donchian_exit": 4, "fx_reward_risk": 1.8, "fx_min_range_atr": 0.25, "fx_session_filter": "london"},
        {"donchian_entry": 12, "donchian_exit": 4, "fx_reward_risk": 2.0, "fx_min_range_atr": 0.25, "fx_session_filter": "active"},
        {"donchian_entry": 8, "donchian_exit": 3, "fx_reward_risk": 1.5, "fx_min_range_atr": 0.15, "fx_session_filter": "all"},
        {"donchian_entry": 14, "donchian_exit": 5, "fx_reward_risk": 2.0, "fx_min_range_atr": 0.30, "fx_session_filter": "ny"},
        {"donchian_entry": 10, "donchian_exit": 3, "fx_reward_risk": 1.6, "fx_min_range_atr": 0.20, "fx_session_filter": "active"},
    ],
    "forex_session_momentum": [
        {"fx_reward_risk": 1.5, "fx_ema_fast": 9, "fx_ema_period": 21, "fx_atr_sl": 0.8, "fx_session_filter": "active", "fx_require_trend": False},
        {"fx_reward_risk": 1.8, "fx_ema_fast": 8, "fx_ema_period": 18, "fx_atr_sl": 0.7, "fx_session_filter": "london", "fx_require_trend": False},
        {"fx_reward_risk": 2.0, "fx_ema_fast": 9, "fx_ema_period": 21, "fx_atr_sl": 0.9, "fx_session_filter": "ny", "fx_require_trend": True},
        {"fx_reward_risk": 1.6, "fx_ema_fast": 7, "fx_ema_period": 15, "fx_atr_sl": 0.65, "fx_session_filter": "active", "fx_require_trend": False},
    ],
    "forex_london_breakout": [
        {"fx_reward_risk": 1.5, "fx_min_range_atr": 0.30},
        {"fx_reward_risk": 1.8, "fx_min_range_atr": 0.35},
        {"fx_reward_risk": 2.0, "fx_min_range_atr": 0.40},
        {"fx_reward_risk": 1.6, "fx_min_range_atr": 0.25},
    ],
    "forex_range_fade": [
        {"fx_reward_risk": 1.5, "fx_rsi_overbought": 65.0, "fx_rsi_oversold": 35.0, "fx_max_adx_range": 22.0, "fx_session_filter": "london"},
        {"fx_reward_risk": 1.8, "fx_rsi_overbought": 68.0, "fx_rsi_oversold": 32.0, "fx_max_adx_range": 20.0, "fx_session_filter": "asian"},
        {"fx_reward_risk": 1.5, "fx_rsi_overbought": 70.0, "fx_rsi_oversold": 30.0, "fx_max_adx_range": 25.0, "fx_session_filter": "active"},
    ],
    "forex_overlap_momentum": [
        {"fx_reward_risk": 1.5, "fx_ema_period": 18, "fx_atr_sl": 0.8},
        {"fx_reward_risk": 1.8, "fx_ema_period": 21, "fx_atr_sl": 0.9},
        {"fx_reward_risk": 2.0, "fx_ema_period": 21, "fx_atr_sl": 1.0},
    ],
    "velocity_rejection": [
        {"vrs_swing_lookback": 6, "vrs_reward_risk": 1.2, "vrs_use_1h_filter": False, "vrs_sweep_atr": 0.05},
        {"vrs_swing_lookback": 8, "vrs_reward_risk": 1.28, "vrs_use_1h_filter": False, "vrs_sweep_atr": 0.07},
        {"vrs_swing_lookback": 6, "vrs_reward_risk": 1.35, "vrs_use_1h_filter": True, "vrs_sweep_atr": 0.06},
        {"vrs_swing_lookback": 10, "vrs_reward_risk": 1.5, "vrs_use_1h_filter": False, "vrs_sweep_atr": 0.08},
    ],
    "rsi_bidirectional": [
        {"rsi_period": 14, "rsi_oversold": 35.0, "rsi_atr_sl": 0.8, "rsi_atr_tp": 1.2, "rsi_reward_risk": 1.5},
        {"rsi_period": 12, "rsi_oversold": 32.0, "rsi_atr_sl": 0.7, "rsi_atr_tp": 1.0, "rsi_reward_risk": 1.4},
        {"rsi_period": 14, "rsi_oversold": 38.0, "rsi_atr_sl": 0.9, "rsi_atr_tp": 1.35, "rsi_reward_risk": 1.5},
    ],
    "donchian_bidirectional": [
        {"donchian_entry": 12, "donchian_exit": 4, "reward_risk": 1.8},
        {"donchian_entry": 16, "donchian_exit": 5, "reward_risk": 2.0},
        {"donchian_entry": 20, "donchian_exit": 5, "reward_risk": 2.2},
        {"donchian_entry": 24, "donchian_exit": 6, "reward_risk": 2.5},
    ],
}


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample OHLCV to target timeframe."""
    if timeframe == "15m":
        return df
    rule = "30min" if timeframe == "30m" else "1h"
    ohlc = df.resample(rule, label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })
    return ohlc.dropna(subset=["open"])


def load_cached_forex(
    symbol: str,
    timeframe: str,
    start: str = DEFAULT_START,
) -> pd.DataFrame:
    """Load forex OHLCV from 15m Dukascopy cache; resample to 30m/1h for full history."""
    path_15m = cache_path(symbol, "15m", "dukascopy")
    if not path_15m.exists():
        raise FileNotFoundError(f"Missing 15m cache: {path_15m}")

    df_15m = _filter_dates(_load_csv(str(path_15m)), start, None)
    if timeframe == "15m":
        return df_15m
    return resample_ohlcv(df_15m, timeframe)


def load_regime_df(symbol: str, start: str = "2020-01-01") -> pd.DataFrame:
    """Daily regime from Yahoo cache (no download)."""
    from backtest.data_loader import fetch_ohlcv

    return fetch_ohlcv(symbol, "1d", start=start, refresh=False, provider="yahoo")


def make_config(symbol: str, timeframe: str, params: dict[str, Any]) -> BacktestConfig:
    cfg = BacktestConfig(
        symbol=symbol,
        timeframe=timeframe,
        entry_timeframe=timeframe,
        regime_timeframe="1d",
        risk_per_trade=0.0075,
    )
    for key, val in params.items():
        if hasattr(cfg, key):
            setattr(cfg, key, val)
    return cfg


def _score_hf(row: dict) -> float:
    """Score OOS passes; boost configs with 20+ trades."""
    if not row.get("test_pass"):
        return -999.0
    ret = float(row.get("test_return_pct", 0))
    pf = row.get("test_profit_factor", 0)
    pf_val = 3.0 if pf == "inf" else float(pf) if pf not in (0, "0") else 0
    trades = int(row.get("test_trades", 0))
    trade_bonus = 25.0 if trades >= MIN_HF_OOS_TRADES else min(trades, 40) * 0.3
    return ret * 0.5 + pf_val * 8 + trade_bonus


@dataclass
class SearchResult:
    symbol: str
    strategy: str
    timeframe: str
    params: dict
    bars: int
    split_date: str
    train_return_pct: float
    test_return_pct: float
    test_trades: int
    test_win_rate_pct: float
    test_profit_factor: Any
    test_pass: bool
    score: float
    error: str | None = None


def run_single(
    symbol: str,
    timeframe: str,
    strategy_name: str,
    params: dict[str, Any],
    entry_df: pd.DataFrame | None = None,
    regime_df: pd.DataFrame | None = None,
) -> SearchResult:
    try:
        entry = entry_df if entry_df is not None else load_cached_forex(symbol, timeframe)
        regime = regime_df if regime_df is not None else load_regime_df(symbol)
        cfg = make_config(symbol, timeframe, params)
        strategy = STRATEGIES[strategy_name](cfg)
        wf = run_walk_forward(entry, regime, cfg, strategy)
        tm = wf.test_metrics
        tr = wf.train_metrics
        row = {
            "test_pass": wf.test_pass,
            "test_return_pct": float(tm.get("total_return_pct", 0)),
            "test_trades": int(tm.get("total_trades", 0)),
            "test_profit_factor": tm.get("profit_factor", 0),
        }
        return SearchResult(
            symbol=symbol,
            strategy=strategy_name,
            timeframe=timeframe,
            params=params,
            bars=len(entry),
            split_date=wf.split_date,
            train_return_pct=float(tr.get("total_return_pct", 0)),
            test_return_pct=row["test_return_pct"],
            test_trades=row["test_trades"],
            test_win_rate_pct=float(tm.get("win_rate_pct", 0)),
            test_profit_factor=row["test_profit_factor"],
            test_pass=wf.test_pass,
            score=_score_hf(row),
        )
    except Exception as exc:
        return SearchResult(
            symbol=symbol,
            strategy=strategy_name,
            timeframe=timeframe,
            params=params,
            bars=0,
            split_date="",
            train_return_pct=0.0,
            test_return_pct=0.0,
            test_trades=0,
            test_win_rate_pct=0.0,
            test_profit_factor=0,
            test_pass=False,
            score=-999.0,
            error=str(exc),
        )


def run_forex_search(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    strategies: list[str] | None = None,
    start: str = DEFAULT_START,
) -> dict:
    symbols = symbols or FOREX
    timeframes = timeframes or FOREX_TIMEFRAMES
    strategies = strategies or FOREX_SEARCH_STRATEGIES

    all_rows: list[dict] = []
    data_cache: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}

    for sym in symbols:
        regime_df = load_regime_df(sym)
        for tf in timeframes:
            try:
                entry_df = load_cached_forex(sym, tf, start)
                if len(entry_df) < 300:
                    all_rows.append({
                        "symbol": sym, "timeframe": tf,
                        "error": f"too few bars: {len(entry_df)}",
                    })
                    continue
                data_cache[f"{sym}|{tf}"] = (entry_df, regime_df)
            except Exception as exc:
                all_rows.append({"symbol": sym, "timeframe": tf, "error": str(exc)})
                continue

    for strat_name in strategies:
        grid = PARAM_GRIDS.get(strat_name, [{}])
        for sym in symbols:
            for tf in timeframes:
                key = f"{sym}|{tf}"
                if key not in data_cache:
                    continue
                if strat_name == "forex_harmonic" and tf == "15m":
                    continue
                entry_df, regime_df = data_cache[key]
                for params in grid:
                    r = run_single(sym, tf, strat_name, params, entry_df, regime_df)
                    row = {
                        "symbol": r.symbol,
                        "strategy": r.strategy,
                        "timeframe": r.timeframe,
                        "params": r.params,
                        "category": "forex",
                        "provider": "dukascopy_resampled" if tf != "15m" else "dukascopy",
                        "bars": r.bars,
                        "split_date": r.split_date,
                        "train_return_pct": r.train_return_pct,
                        "test_return_pct": r.test_return_pct,
                        "test_trades": r.test_trades,
                        "test_win_rate_pct": r.test_win_rate_pct,
                        "test_profit_factor": r.test_profit_factor,
                        "test_pass": r.test_pass,
                        "score": r.score,
                    }
                    if r.error:
                        row["error"] = r.error
                    all_rows.append(row)

    valid = [r for r in all_rows if r.get("test_pass") and "error" not in r]
    valid.sort(key=lambda x: x.get("score", -999), reverse=True)

    hf_valid = [r for r in valid if r.get("test_trades", 0) >= MIN_HF_OOS_TRADES]
    hf_valid.sort(key=lambda x: x.get("score", -999), reverse=True)

    best_per_key: dict[str, dict] = {}
    pool = hf_valid if hf_valid else valid
    for r in pool:
        key = f"{r['symbol']}|{r['timeframe']}"
        if key not in best_per_key or r["score"] > best_per_key[key]["score"]:
            best_per_key[key] = r

    near_misses = [
        r for r in all_rows
        if not r.get("test_pass") and "error" not in r
        and r.get("test_return_pct", 0) > 0
        and r.get("test_trades", 0) >= 5
        and _pf_val(r.get("test_profit_factor")) >= 0.9
    ]
    near_misses.sort(
        key=lambda x: (x.get("test_trades", 0), x.get("test_return_pct", 0)),
        reverse=True,
    )

    return {
        "asset_class": "forex",
        "symbols": symbols,
        "timeframes": timeframes,
        "strategies": strategies,
        "default_start": start,
        "min_hf_oos_trades": MIN_HF_OOS_TRADES,
        "data_source": "dukascopy 15m cache (native 15m + resampled 30m/1h)",
        "total_runs": len(all_rows),
        "oos_passed": len(valid),
        "hf_oos_passed": len(hf_valid),
        "top_10": valid[:10],
        "top_10_hf": hf_valid[:10],
        "best_per_symbol_tf": list(best_per_key.values()),
        "near_misses": near_misses[:25],
        "all_results": all_rows,
    }


def _pf_val(pf) -> float:
    if pf in (0, "0", None):
        return 0.0
    if pf == "inf":
        return 99.0
    try:
        return float(pf)
    except (TypeError, ValueError):
        return 0.0


def save_winners(research: dict, root: Path | None = None) -> tuple[Path, Path]:
    root = root or ROOT
    results_path = root / "research_results_forex_winners.json"
    results_path.write_text(json.dumps(research, indent=2, default=str), encoding="utf-8")

    desc = (
        f"Forex HF OOS winners (min {MIN_HF_OOS_TRADES} trades preferred). "
        "Dukascopy 15m + resampled."
    )
    portfolio = build_portfolio_json(
        research,
        name="oos_paper_forex",
        description=desc,
        source="research_results_forex_winners.json",
    )
    port_path = root / "mixed_portfolio_oos_forex.json"
    port_path.write_text(json.dumps(portfolio, indent=2), encoding="utf-8")
    return results_path, port_path
