import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.data_loader import fetch_ohlcv
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics
from backtest.mtf import build_mtf_dataset
from config import BacktestConfig
from strategies import STRATEGIES

for sym in ["GC=F", "BTC-USD"]:
    e = fetch_ohlcv(sym, "30m", refresh=False)
    r = fetch_ohlcv(sym, "1d", refresh=False)
    df = build_mtf_dataset(e, r, BacktestConfig(symbol=sym))
    print(sym, "bars", len(df), df["regime"].value_counts().to_dict())
    for name in STRATEGIES:
        cfg = BacktestConfig(symbol=sym, macd_strict_trend=False, adx_trend_threshold=22)
        m = compute_metrics(BacktestEngine(cfg).run(df, STRATEGIES[name](cfg)))
        print(f"  {name}: trades={m['total_trades']} ret={m['total_return_pct']} pf={m['profit_factor']}")
