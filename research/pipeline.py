"""
Research pipeline — scan strategies with walk-forward OOS validation.
Uses best data provider per symbol (Binance crypto, Dukascopy forex, Polygon stocks).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.data_loader import fetch_ohlcv
from backtest.providers.registry import provider_for_symbol
from backtest.universe import category_for
from config import BacktestConfig
from research.walk_forward import run_walk_forward
from strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent.parent

# Focus where we have long intraday history + edge potential
CRYPTO = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "LINK-USD",
    "AVAX-USD", "BNB-USD", "MATIC-USD", "LTC-USD", "DOT-USD",
    "ATOM-USD", "NEAR-USD",
]
FOREX = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X"]
FOREX_EXTENDED = FOREX + ["EURGBP=X", "EURJPY=X", "NZDUSD=X", "USDCHF=X"]
STOCKS = ["AAPL", "NVDA", "TSLA", "AMZN", "QQQ", "SPY"]
TIMEFRAMES = ["15m", "30m", "1h"]
# Strategies worth testing (skip ultra-niche for speed)
CANDIDATE_STRATEGIES = [
    "squeeze_bidirectional",
    "donchian_bidirectional",
    "velocity_rejection",
    "active_pulse",
    "triple_tf_confluence",
    "macd_pullback",
    "rsi_bidirectional",
]
ALL_STRATEGIES = sorted(STRATEGIES.keys())

ASSET_DEFAULTS: dict[str, dict] = {
    "crypto": {"symbols": CRYPTO, "default_start": "2023-01-01"},
    "forex": {"symbols": FOREX, "default_start": "2024-01-01"},
    "stocks": {"symbols": STOCKS, "default_start": "2023-01-01"},
}


def polygon_available() -> bool:
    return bool(os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY"))


def stocks_provider_note() -> str | None:
    if polygon_available():
        return None
    return (
        "POLYGON_API_KEY not set — stocks use Yahoo (limited intraday history). "
        "Set POLYGON_API_KEY for full 15m/30m/1h coverage."
    )


def _stock_provider_name() -> str | None:
    return "polygon" if polygon_available() else None


def clamp_start(symbol: str, timeframe: str, start: str = "2023-01-01") -> str:
    if symbol in STOCKS and _stock_provider_name():
        from backtest.providers.registry import get_provider
        prov = get_provider("polygon")
    else:
        prov = provider_for_symbol(symbol, timeframe)
    limit = prov.intraday_limit_days(timeframe)
    if limit is None:
        return start
    earliest = (pd.Timestamp.now() - pd.Timedelta(days=limit - 1)).strftime("%Y-%m-%d")
    if pd.Timestamp(start) < pd.Timestamp(earliest):
        return earliest
    return start


def load_frames(
    symbol: str,
    entry_tf: str,
    regime_tf: str = "1d",
    default_start: str | None = None,
):
    start = default_start or clamp_start(symbol, entry_tf)
    stock_prov = _stock_provider_name() if symbol in STOCKS else None
    if stock_prov:
        entry_df = fetch_ohlcv(symbol, entry_tf, start=start, refresh=False, provider=stock_prov)
        regime_df = fetch_ohlcv(
            symbol, regime_tf, start="2020-01-01", refresh=False, provider=stock_prov
        )
    elif regime_tf == "1d" and (symbol in FOREX or symbol.endswith("=X")):
        entry_df = fetch_ohlcv(symbol, entry_tf, start=start, refresh=False)
        regime_df = fetch_ohlcv(symbol, regime_tf, start="2020-01-01", refresh=False, provider="yahoo")
    else:
        entry_df = fetch_ohlcv(symbol, entry_tf, start=start, refresh=False)
        regime_df = fetch_ohlcv(symbol, regime_tf, start="2020-01-01", refresh=False)
    cfg = BacktestConfig(
        symbol=symbol,
        timeframe=entry_tf,
        entry_timeframe=entry_tf,
        regime_timeframe=regime_tf,
    )
    return entry_df, regime_df, cfg


def scan_symbol(
    symbol: str,
    entry_tf: str,
    default_start: str | None = None,
    strategies: list[str] | None = None,
) -> list[dict]:
    results = []
    strat_list = strategies or CANDIDATE_STRATEGIES
    try:
        entry_df, regime_df, cfg = load_frames(symbol, entry_tf, default_start=default_start)
    except Exception as exc:
        return [{"symbol": symbol, "timeframe": entry_tf, "error": str(exc)}]

    if len(entry_df) < 300:
        return [{"symbol": symbol, "timeframe": entry_tf, "error": f"too few bars: {len(entry_df)}"}]

    for strat_name in strat_list:
        strategy = STRATEGIES[strat_name](cfg)
        try:
            wf = run_walk_forward(entry_df, regime_df, cfg, strategy)
        except Exception as exc:
            results.append({
                "symbol": symbol, "strategy": strat_name, "timeframe": entry_tf,
                "error": str(exc),
            })
            continue

        tm = wf.test_metrics
        tr = wf.train_metrics
        results.append({
            "symbol": symbol,
            "strategy": strat_name,
            "timeframe": entry_tf,
            "category": category_for(symbol),
            "provider": provider_for_symbol(symbol, entry_tf).name,
            "bars": len(entry_df),
            "split_date": wf.split_date,
            "train_return_pct":    float(tr.get("total_return_pct", 0)),
            "test_return_pct":     float(tm.get("total_return_pct", 0)),
            "test_trades":         int(tm.get("total_trades", 0)),
            "test_win_rate_pct":   float(tm.get("win_rate_pct", 0)),
            "test_profit_factor":  tm.get("profit_factor", 0),
            "test_sharpe":         float(tm.get("sharpe", 0.0)),
            "test_sortino":        float(tm.get("sortino", 0.0)),
            "test_calmar":         float(tm.get("calmar", 0.0)),
            "test_max_drawdown_pct": float(tm.get("max_drawdown_pct", 0.0)),
            "test_cagr_pct":       float(tm.get("cagr_pct", 0.0)),
            "test_pass": wf.test_pass,
            "score": _score(wf),
        })
    return results


def _score(wf) -> float:
    """
    Composite OOS score — higher is better.
    Balances risk-adjusted return (Sharpe), consistency (PF), activity (trades)
    and raw return. Sharpe replaces raw return as primary driver because
    a high-PF strategy with 5 trades is not deployable.

    Formula (all OOS test metrics):
      Sharpe  × 10   (primary driver — risk-adjusted)
      + log(PF) × 5  (profitability, log-scaled to prevent inf domination)
      + Sortino × 3  (downside protection bonus)
      + trades_weight (saturates at 40, prevents low-N gaming)
      + return × 0.3 (raw return tiebreaker)
    """
    tm = wf.test_metrics
    if not wf.test_pass:
        return -999.0

    sharpe  = float(tm.get("sharpe", 0.0))
    sortino = float(tm.get("sortino", 0.0))
    ret     = float(tm.get("total_return_pct", 0))
    trades  = int(tm.get("total_trades", 0))

    pf = tm.get("profit_factor", 0)
    if pf == "inf":
        pf_val = 2.5   # cap inf — likely just lucky with few trades
    elif pf in (0, "0", None):
        pf_val = 0.0
    else:
        pf_val = float(pf)

    log_pf = float(np.log(max(pf_val, 0.01)))
    trade_weight = min(trades, 40) * 0.15

    return sharpe * 10 + log_pf * 5 + sortino * 3 + trade_weight + ret * 0.3


def run_research(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    asset_class: str = "crypto",
    default_start: str | None = None,
    strategies: list[str] | None = None,
) -> dict:
    defaults = ASSET_DEFAULTS.get(asset_class, ASSET_DEFAULTS["crypto"])
    symbols = symbols or defaults["symbols"]
    timeframes = timeframes or TIMEFRAMES
    default_start = default_start or defaults.get("default_start")
    strat_list = strategies or CANDIDATE_STRATEGIES
    all_rows: list[dict] = []

    for sym in symbols:
        for tf in timeframes:
            all_rows.extend(scan_symbol(sym, tf, default_start=default_start, strategies=strat_list))

    valid = [r for r in all_rows if r.get("test_pass") and "error" not in r]
    valid.sort(key=lambda x: x.get("score", -999), reverse=True)

    best_per_symbol: dict[str, dict] = {}
    for r in valid:
        key = f"{r['symbol']}|{r['timeframe']}"
        if key not in best_per_symbol or r["score"] > best_per_symbol[key]["score"]:
            best_per_symbol[key] = r

    out: dict = {
        "asset_class": asset_class,
        "symbols": symbols,
        "timeframes": timeframes,
        "strategies": strat_list,
        "default_start": default_start,
        "total_runs": len(all_rows),
        "oos_passed": len(valid),
        "top_10": valid[:10],
        "best_per_symbol_tf": list(best_per_symbol.values()),
        "all_results": all_rows,
    }
    if asset_class == "stocks":
        note = stocks_provider_note()
        if note:
            out["provider_note"] = note
    return out


def run_research_forex(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    default_start: str | None = None,
    strategies: list[str] | None = None,
) -> dict:
    return run_research(
        symbols=symbols or FOREX,
        timeframes=timeframes,
        asset_class="forex",
        default_start=default_start or "2024-01-01",
        strategies=strategies,
    )


def run_research_forex_extended(
    default_start: str = "2023-01-01",
) -> dict:
    """9 pairs × 3 TF × 14 strategies — uses Dukascopy cache when available."""
    return run_research(
        symbols=FOREX_EXTENDED,
        timeframes=TIMEFRAMES,
        asset_class="forex",
        default_start=default_start,
        strategies=ALL_STRATEGIES,
    )


def prefetch_stock_data(
    symbols: list[str] | None = None,
    entry_timeframes: list[str] | None = None,
) -> None:
    """Download/cache Polygon OHLCV sequentially (avoids rate-limit bursts)."""
    if not polygon_available():
        raise RuntimeError("POLYGON_API_KEY not set — stocks require Polygon.")
    symbols = symbols or STOCKS
    entry_tfs = entry_timeframes or ["1h"]
    start = ASSET_DEFAULTS["stocks"]["default_start"]
    for sym in symbols:
        fetch_ohlcv(sym, "1d", start="2020-01-01", refresh=False, provider="polygon")
        for tf in entry_tfs:
            fetch_ohlcv(sym, tf, start=start, refresh=False, provider="polygon")


def run_research_stocks(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
) -> dict:
    if not polygon_available():
        return {
            "asset_class": "stocks",
            "symbols": symbols or STOCKS,
            "timeframes": timeframes or TIMEFRAMES,
            "total_runs": 0,
            "oos_passed": 0,
            "top_10": [],
            "best_per_symbol_tf": [],
            "all_results": [],
            "provider_note": stocks_provider_note(),
            "error": "POLYGON_API_KEY not set",
        }
    prefetch_stock_data(symbols=symbols, entry_timeframes=timeframes or ["1h"])
    return run_research(
        symbols=symbols or STOCKS,
        timeframes=timeframes,
        asset_class="stocks",
    )


def build_portfolio_json(
    research: dict,
    *,
    name: str,
    description: str,
    source: str,
) -> dict:
    """Build mixed_portfolio_oos-style JSON from OOS-passing pairs."""
    winners = [r for r in research.get("best_per_symbol_tf", []) if r.get("test_pass")]
    winners.sort(key=lambda x: x.get("score", -999), reverse=True)

    by_tf: dict[str, list] = {}
    for w in winners:
        by_tf.setdefault(w["timeframe"], []).append(w)

    bots: dict[str, list] = {}
    for tf, rows in sorted(by_tf.items()):
        bots[tf] = [
            {
                "symbol": r["symbol"],
                "strategy": r["strategy"],
                "oos_test_pct": round(float(r["test_return_pct"]), 1),
                "note": f"PF {r['test_profit_factor']}  {r['test_trades']} OOS trades",
                **({"params": r["params"]} if r.get("params") else {}),
            }
            for r in rows
        ]

    pairs = [
        {
            "symbol": r["symbol"],
            "strategy": r["strategy"],
            "timeframe": r["timeframe"],
            **({"params": r["params"]} if r.get("params") else {}),
        }
        for r in winners
    ]

    out: dict = {
        "name": name,
        "description": description,
        "source": source,
        "capital_per_bot_sek": 20000,
        "risk_per_trade": 0.0075,
        "validation": "70/30 walk-forward, min 3 OOS trades, PF>=1.0",
        "bots": bots,
        "pairs": pairs,
    }
    if research.get("provider_note"):
        out["provider_note"] = research["provider_note"]
    return out


def save_research(out: dict, path: Path | None = None) -> Path:
    path = path or ROOT / "research_results.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return path
