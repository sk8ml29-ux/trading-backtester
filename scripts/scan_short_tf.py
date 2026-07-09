#!/usr/bin/env python3
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.scan_multi_tf import scan_timeframe

summaries = [scan_timeframe(tf) for tf in ("15m", "5m")]
out = {"timeframes": ["15m", "5m"], "summaries": summaries}
Path("universe_scan_short_tf.json").write_text(json.dumps(out, indent=2, default=str))
print("=== KORTA TIMEFRAMES ===")
for s in summaries:
    top = s["top_5"][0] if s["top_5"] else {}
    print(
        f"{s['timeframe']:4}  {s['profitable_symbols']:2}/{s['symbols_scanned']} lonsamma  "
        f"{s['total_best_trades']:4} trades  WR {s['avg_win_rate_pct']}%  "
        f"bäst: {top.get('symbol', '?')} {float(top.get('total_return_pct', 0)):+.1f}%"
    )
