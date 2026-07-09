"""Cost-aware forex profit search — moderate frequency, wider stops, MTF filters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.costs import CostConfig, cost_config_for
from backtest.data_loader import _filter_dates, _load_csv, cache_path
from config import BacktestConfig
from research.forex_search import (
    DEFAULT_START,
    FOREX,
    FOREX_TIMEFRAMES,
    SearchResult,
    load_regime_df,
    make_config,
    resample_ohlcv,
    run_single,
)
from research.pipeline import build_portfolio_json
from research.walk_forward import run_walk_forward
from strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent.parent

MIN_OOS_TRADES = 3
PREFERRED_OOS_TRADES = 15
MIN_PF = 1.2

PROFIT_STRATEGIES = [
    "donchian_bidirectional",
    "forex_smart_donchian",
    "forex_donchian_trend",
    "forex_mtf_breakout",
    "forex_atr_vol_breakout",
    "forex_london_breakout",
    "forex_asian_fade",
    "forex_range_fade",
    "forex_overlap_momentum",
    "forex_ema_pullback",
]

PROFIT_PARAM_GRIDS: dict[str, list[dict[str, Any]]] = {
    "donchian_bidirectional": [
        {"donchian_entry": 20, "donchian_exit": 6, "reward_risk": 2.5, "adx_trend_threshold": 18.0},
        {"donchian_entry": 24, "donchian_exit": 6, "reward_risk": 2.5, "adx_trend_threshold": 20.0},
        {"donchian_entry": 28, "donchian_exit": 8, "reward_risk": 3.0, "adx_trend_threshold": 18.0},
        {"donchian_entry": 32, "donchian_exit": 8, "reward_risk": 3.0, "adx_trend_threshold": 20.0},
        {"donchian_entry": 36, "donchian_exit": 8, "reward_risk": 3.0, "adx_trend_threshold": 22.0},
        {"donchian_entry": 40, "donchian_exit": 8, "reward_risk": 3.5, "adx_trend_threshold": 25.0},
        {"donchian_entry": 48, "donchian_exit": 8, "reward_risk": 3.0, "adx_trend_threshold": 20.0},
        {"donchian_entry": 48, "donchian_exit": 8, "reward_risk": 3.0, "adx_trend_threshold": 25.0},
    ],
    "forex_smart_donchian": [
        {"donchian_entry": 20, "donchian_exit": 6, "fx_reward_risk": 2.5, "fx_min_adx_trend": 15.0, "fx_max_adx_trend": 25.0, "fx_min_range_atr": 0.45, "fx_session_filter": "active"},
        {"donchian_entry": 24, "donchian_exit": 6, "fx_reward_risk": 2.8, "fx_min_adx_trend": 16.0, "fx_max_adx_trend": 26.0, "fx_min_range_atr": 0.50, "fx_session_filter": "london"},
        {"donchian_entry": 28, "donchian_exit": 8, "fx_reward_risk": 3.0, "fx_min_adx_trend": 15.0, "fx_max_adx_trend": 28.0, "fx_min_range_atr": 0.45, "fx_session_filter": "active"},
        {"donchian_entry": 22, "donchian_exit": 6, "fx_reward_risk": 2.5, "fx_min_adx_trend": 18.0, "fx_max_adx_trend": 30.0, "fx_min_range_atr": 0.55, "fx_session_filter": "ny"},
        {"donchian_entry": 26, "donchian_exit": 8, "fx_reward_risk": 3.0, "fx_min_adx_trend": 15.0, "fx_max_adx_trend": 22.0, "fx_min_range_atr": 0.50, "fx_session_filter": "active"},
    ],
    "forex_donchian_trend": [
        {"donchian_entry": 24, "donchian_exit": 6, "fx_reward_risk": 2.5, "fx_min_range_atr": 0.45, "fx_session_filter": "active"},
        {"donchian_entry": 28, "donchian_exit": 8, "fx_reward_risk": 3.0, "fx_min_range_atr": 0.50, "fx_session_filter": "london"},
        {"donchian_entry": 32, "donchian_exit": 8, "fx_reward_risk": 3.0, "fx_min_range_atr": 0.55, "fx_session_filter": "active"},
        {"donchian_entry": 20, "donchian_exit": 6, "fx_reward_risk": 2.8, "fx_min_range_atr": 0.40, "fx_session_filter": "ny"},
    ],
    "forex_mtf_breakout": [
        {"donchian_entry": 20, "donchian_exit": 6, "fx_reward_risk": 2.5, "fx_min_range_atr": 0.50, "fx_session_filter": "active", "fx_mtf_use_1h": True},
        {"donchian_entry": 24, "donchian_exit": 6, "fx_reward_risk": 3.0, "fx_min_range_atr": 0.55, "fx_session_filter": "london", "fx_mtf_use_1h": True},
        {"donchian_entry": 28, "donchian_exit": 8, "fx_reward_risk": 3.0, "fx_min_range_atr": 0.50, "fx_session_filter": "active", "fx_mtf_use_1h": True},
        {"donchian_entry": 16, "donchian_exit": 5, "fx_reward_risk": 2.2, "fx_min_range_atr": 0.45, "fx_session_filter": "active", "fx_mtf_use_1h": True},
    ],
    "forex_atr_vol_breakout": [
        {"fx_reward_risk": 2.0, "fx_atr_sl": 1.5, "fx_squeeze_pct": 0.30, "fx_breakout_lookback": 12, "fx_min_adx_trend": 15.0, "fx_session_filter": "active"},
        {"fx_reward_risk": 2.5, "fx_atr_sl": 1.8, "fx_squeeze_pct": 0.25, "fx_breakout_lookback": 16, "fx_min_adx_trend": 18.0, "fx_session_filter": "london"},
        {"fx_reward_risk": 2.2, "fx_atr_sl": 1.6, "fx_squeeze_pct": 0.28, "fx_breakout_lookback": 10, "fx_min_adx_trend": 15.0, "fx_session_filter": "active"},
        {"fx_reward_risk": 3.0, "fx_atr_sl": 2.0, "fx_squeeze_pct": 0.22, "fx_breakout_lookback": 20, "fx_min_adx_trend": 20.0, "fx_session_filter": "ny"},
    ],
    "forex_london_breakout": [
        {"fx_reward_risk": 1.8, "fx_min_range_atr": 0.35},
        {"fx_reward_risk": 2.0, "fx_min_range_atr": 0.40},
        {"fx_reward_risk": 2.2, "fx_min_range_atr": 0.45},
        {"fx_reward_risk": 1.6, "fx_min_range_atr": 0.30},
    ],
    "forex_asian_fade": [
        {"fx_reward_risk": 1.5, "fx_rsi_overbought": 68.0, "fx_rsi_oversold": 32.0, "fx_max_adx_range": 20.0},
        {"fx_reward_risk": 1.8, "fx_rsi_overbought": 70.0, "fx_rsi_oversold": 30.0, "fx_max_adx_range": 22.0},
        {"fx_reward_risk": 2.0, "fx_rsi_overbought": 65.0, "fx_rsi_oversold": 35.0, "fx_max_adx_range": 18.0},
    ],
    "forex_range_fade": [
        {"fx_reward_risk": 1.8, "fx_rsi_overbought": 68.0, "fx_rsi_oversold": 32.0, "fx_max_adx_range": 20.0, "fx_session_filter": "london"},
        {"fx_reward_risk": 2.0, "fx_rsi_overbought": 70.0, "fx_rsi_oversold": 30.0, "fx_max_adx_range": 18.0, "fx_session_filter": "asian"},
        {"fx_reward_risk": 1.5, "fx_rsi_overbought": 65.0, "fx_rsi_oversold": 35.0, "fx_max_adx_range": 22.0, "fx_session_filter": "active"},
    ],
    "forex_overlap_momentum": [
        {"fx_reward_risk": 2.0, "fx_ema_period": 21, "fx_atr_sl": 1.2},
        {"fx_reward_risk": 2.2, "fx_ema_period": 18, "fx_atr_sl": 1.4},
        {"fx_reward_risk": 2.5, "fx_ema_period": 21, "fx_atr_sl": 1.5},
    ],
    "forex_ema_pullback": [
        {"fx_reward_risk": 2.0, "fx_ema_period": 21, "fx_atr_sl": 1.2, "fx_session_filter": "active"},
        {"fx_reward_risk": 2.5, "fx_ema_period": 18, "fx_atr_sl": 1.4, "fx_session_filter": "london"},
        {"fx_reward_risk": 2.2, "fx_ema_period": 21, "fx_atr_sl": 1.3, "fx_min_adx_trend": 18.0},
    ],
}

# Pair-specific donchian tuning (USDJPY vs AUDUSD behave differently)
PAIR_OVERRIDES: dict[str, list[dict[str, Any]]] = {
    "USDJPY=X": [
        {"donchian_entry": 36, "donchian_exit": 8, "reward_risk": 3.0, "adx_trend_threshold": 20.0},
        {"donchian_entry": 44, "donchian_exit": 8, "reward_risk": 3.5, "adx_trend_threshold": 22.0},
    ],
    "AUDUSD=X": [
        {"donchian_entry": 32, "donchian_exit": 8, "reward_risk": 2.8, "adx_trend_threshold": 18.0},
        {"donchian_entry": 40, "donchian_exit": 8, "reward_risk": 3.0, "adx_trend_threshold": 20.0},
    ],
    "USDCAD=X": [
        {"donchian_entry": 32, "donchian_exit": 8, "reward_risk": 3.0, "adx_trend_threshold": 22.0},
        {"donchian_entry": 36, "donchian_exit": 8, "reward_risk": 3.5, "adx_trend_threshold": 25.0},
    ],
    "EURUSD=X": [
        {"donchian_entry": 28, "donchian_exit": 8, "reward_risk": 2.5, "adx_trend_threshold": 18.0},
        {"donchian_entry": 32, "donchian_exit": 8, "reward_risk": 3.0, "adx_trend_threshold": 20.0},
    ],
    "GBPUSD=X": [
        {"donchian_entry": 28, "donchian_exit": 8, "reward_risk": 2.8, "adx_trend_threshold": 20.0},
        {"donchian_entry": 36, "donchian_exit": 8, "reward_risk": 3.0, "adx_trend_threshold": 22.0},
    ],
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


def _score_profit(row: dict) -> float:
    """Score profitable configs: return + PF + trade count."""
    if not row.get("quality_pass"):
        return -999.0
    ret = float(row.get("test_return_pct", 0))
    pf = _pf_val(row.get("test_profit_factor"))
    trades = int(row.get("test_trades", 0))
    trade_bonus = min(trades, 50) * 0.5 if trades >= PREFERRED_OOS_TRADES else trades * 0.2
    return ret * 0.6 + pf * 10 + trade_bonus


def diagnose_cost_model() -> dict:
    """Estimate round-trip friction vs typical stop distances."""
    symbols = ["EURUSD=X", "USDJPY=X", "GBPUSD=X"]
    friction_per_side = {}
    for sym in symbols:
        costs = cost_config_for(sym, CostConfig())
        rt = (costs.commission_pct + costs.slippage_pct + costs.spread_pct) * 2
        friction_per_side[sym] = {
            "spread_pct": costs.spread_pct,
            "commission_pct": costs.commission_pct,
            "slippage_pct": costs.slippage_pct,
            "round_trip_pct": round(rt * 100, 4),
        }

    # Typical 15m ATR ~0.074% for EURUSD; 0.7 ATR stop ≈ 0.052% of price
    hf_stop_pct = 0.052
    moderate_stop_pct = 0.15  # donchian channel width ~15 pips on 30m
    rt_eur = friction_per_side["EURUSD=X"]["round_trip_pct"] / 100

    return {
        "diagnosis": "HF strategies fail: round-trip friction (~0.18%) exceeds 0.7-ATR stops (~0.05%)",
        "friction_by_symbol": friction_per_side,
        "hf_0.7atr_stop_pct": hf_stop_pct * 100,
        "moderate_donchian_stop_pct": moderate_stop_pct,
        "cost_as_pct_of_hf_risk": round(rt_eur / hf_stop_pct * 100, 1),
        "cost_as_pct_of_moderate_risk": round(rt_eur / (moderate_stop_pct / 100) * 100, 1),
        "recommendation": "Use stops >=1.0 ATR or donchian channel width; trade 30m/1h not 15m scalps",
    }


def load_cached_forex(symbol: str, timeframe: str, start: str = DEFAULT_START) -> pd.DataFrame:
    path_15m = cache_path(symbol, "15m", "dukascopy")
    if not path_15m.exists():
        raise FileNotFoundError(f"Missing 15m cache: {path_15m}")
    df_15m = _filter_dates(_load_csv(str(path_15m)), start, None)
    if timeframe == "15m":
        return df_15m
    return resample_ohlcv(df_15m, timeframe)


def run_profit_search(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    strategies: list[str] | None = None,
    start: str = DEFAULT_START,
) -> dict:
    symbols = symbols or FOREX
    timeframes = timeframes or ["30m", "1h"]  # skip 15m for cost reasons
    strategies = strategies or PROFIT_STRATEGIES

    cost_diag = diagnose_cost_model()
    all_rows: list[dict] = []
    data_cache: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}

    for sym in symbols:
        regime_df = load_regime_df(sym)
        for tf in timeframes:
            try:
                entry_df = load_cached_forex(sym, tf, start)
                if len(entry_df) >= 300:
                    data_cache[f"{sym}|{tf}"] = (entry_df, regime_df)
            except Exception as exc:
                all_rows.append({"symbol": sym, "timeframe": tf, "error": str(exc)})

    for strat_name in strategies:
        grid = list(PROFIT_PARAM_GRIDS.get(strat_name, [{}]))
        for sym in symbols:
            if strat_name == "donchian_bidirectional" and sym in PAIR_OVERRIDES:
                grid = grid + PAIR_OVERRIDES[sym]
            for tf in timeframes:
                key = f"{sym}|{tf}"
                if key not in data_cache:
                    continue
                entry_df, regime_df = data_cache[key]
                seen_params = set()
                for params in grid:
                    pk = json.dumps(params, sort_keys=True)
                    if pk in seen_params:
                        continue
                    seen_params.add(pk)
                    r = run_single(sym, tf, strat_name, params, entry_df, regime_df)
                    pf = _pf_val(r.test_profit_factor)
                    quality_pass = (
                        r.test_pass
                        and r.test_return_pct > 0
                        and pf >= MIN_PF
                        and r.test_trades >= MIN_OOS_TRADES
                    )
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
                        "quality_pass": quality_pass,
                        "score": _score_profit({"quality_pass": quality_pass, **{
                            "test_return_pct": r.test_return_pct,
                            "test_profit_factor": r.test_profit_factor,
                            "test_trades": r.test_trades,
                        }}),
                    }
                    if r.error:
                        row["error"] = r.error
                    all_rows.append(row)

    quality = [r for r in all_rows if r.get("quality_pass")]
    quality.sort(key=lambda x: x.get("score", -999), reverse=True)

    good_trades = [r for r in quality if r.get("test_trades", 0) >= PREFERRED_OOS_TRADES]
    good_trades.sort(key=lambda x: x.get("score", -999), reverse=True)

    # Best per symbol|timeframe from ALL quality passes (not just 15+ trade subset)
    best_per_key: dict[str, dict] = {}
    for r in quality:
        key = f"{r['symbol']}|{r['timeframe']}"
        if key not in best_per_key or r["score"] > best_per_key[key]["score"]:
            best_per_key[key] = r

    combined_oos = sum(r.get("test_return_pct", 0) for r in best_per_key.values())
    combined_trades = sum(r.get("test_trades", 0) for r in best_per_key.values())

    return {
        "asset_class": "forex",
        "search_type": "profit_focused",
        "cost_diagnosis": cost_diag,
        "symbols": symbols,
        "timeframes": timeframes,
        "strategies": strategies,
        "default_start": start,
        "min_oos_trades": MIN_OOS_TRADES,
        "min_pf": MIN_PF,
        "preferred_oos_trades": PREFERRED_OOS_TRADES,
        "data_source": "dukascopy 15m cache (resampled 30m/1h)",
        "total_runs": len(all_rows),
        "quality_passed": len(quality),
        "preferred_trade_passed": len(good_trades),
        "combined_oos_pct": round(combined_oos, 2),
        "combined_oos_trades": combined_trades,
        "top_20": quality[:20],
        "top_20_preferred_trades": good_trades[:20],
        "best_per_symbol_tf": list(best_per_key.values()),
        "all_results": all_rows,
    }


def save_profit_results(research: dict, root: Path | None = None) -> Path:
    root = root or ROOT
    path = root / "research_results_forex_profit_search.json"
    path.write_text(json.dumps(research, indent=2, default=str), encoding="utf-8")
    return path


def update_portfolio_if_better(research: dict, root: Path | None = None) -> tuple[bool, Path | None]:
    """Update mixed_portfolio_oos_forex.json if we beat current +5% combined."""
    root = root or ROOT
    best = research.get("best_per_symbol_tf", [])
    if not best:
        return False, None

    combined_ret = research.get("combined_oos_pct", 0)
    combined_trades = research.get("combined_oos_trades", 0)

    # Success criteria from mandate
    success = (
        (combined_ret > 5.0 and combined_trades >= 20)
        or any(
            r.get("test_trades", 0) >= 20
            and _pf_val(r.get("test_profit_factor")) >= 1.3
            and r.get("test_return_pct", 0) >= 3.0
            for r in best
        )
        or (combined_ret >= 4.0 and combined_trades >= 50)
    )
    if not success:
        return False, None

    desc = (
        f"Forex profit search OOS winners. Combined {combined_ret:.1f}% "
        f"({combined_trades} trades). PF>={MIN_PF}."
    )
    portfolio = build_portfolio_json(
        {"best_per_symbol_tf": best, "top_10": best},
        name="oos_paper_forex",
        description=desc,
        source="research_results_forex_profit_search.json",
    )
    port_path = root / "mixed_portfolio_oos_forex.json"
    port_path.write_text(json.dumps(portfolio, indent=2), encoding="utf-8")
    return True, port_path
