"""Spread monitoring: per-symbol limits, trade filters, live overrides (MT5-ready)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backtest.universe import category_for

ROOT = Path(__file__).resolve().parent.parent
SPREAD_PATH = ROOT / "spread_config.json"


@dataclass
class SpreadInfo:
    symbol: str
    typical_spread_pct: float
    max_spread_pct: float
    current_spread_pct: float
    source: str  # config | live | category
    tradeable: bool
    reason: str = ""


class SpreadMonitor:
    def __init__(self, path: Path | None = None):
        self.path = path or SPREAD_PATH
        self._data = self._load()
        self._live: dict[str, dict] = dict(self._data.get("live_overrides", {}))

    def _load(self) -> dict:
        if not self.path.exists():
            return {"defaults": {}, "categories": {}, "symbols": {}, "live_overrides": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def reload(self) -> None:
        self._data = self._load()
        self._live = dict(self._data.get("live_overrides", {}))

    def _defaults(self) -> dict:
        return self._data.get("defaults", {})

    def typical_spread_pct(self, symbol: str) -> float:
        info = self._resolve(symbol)
        return info["typical_spread_pct"]

    def max_spread_pct(self, symbol: str) -> float:
        info = self._resolve(symbol)
        return info["max_spread_pct"]

    def current_spread_pct(self, symbol: str) -> float:
        live = self._live.get(symbol)
        if live and "spread_pct" in live:
            return float(live["spread_pct"])
        return self.typical_spread_pct(symbol)

    def _resolve(self, symbol: str) -> dict:
        defaults = self._defaults()
        cat = category_for(symbol)
        cat_cfg = self._data.get("categories", {}).get(cat, {})
        sym_cfg = self._data.get("symbols", {}).get(symbol, {})

        typical = float(
            sym_cfg.get("typical_spread_pct", cat_cfg.get("typical_spread_pct", defaults.get("typical_spread_pct", 0.0002)))
        )
        maximum = float(
            sym_cfg.get("max_spread_pct", cat_cfg.get("max_spread_pct", defaults.get("max_spread_pct", 0.0006)))
        )
        return {"typical_spread_pct": typical, "max_spread_pct": maximum}

    def update_live(self, symbol: str, bid: float, ask: float, persist: bool = False) -> float:
        """Set live spread from bid/ask (e.g. MT5). Returns spread_pct."""
        mid = (bid + ask) / 2
        if mid <= 0:
            return self.typical_spread_pct(symbol)
        spread_pct = (ask - bid) / mid
        self._live[symbol] = {
            "spread_pct": spread_pct,
            "bid": bid,
            "ask": ask,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if persist:
            self._data["live_overrides"] = self._live
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        return spread_pct

    def check_trade(
        self,
        symbol: str,
        price: float,
        stop_distance: float,
        spread_pct: float | None = None,
    ) -> SpreadInfo:
        """Return whether spread is acceptable vs stop distance and max limit."""
        resolved = self._resolve(symbol)
        typical = resolved["typical_spread_pct"]
        maximum = resolved["max_spread_pct"]
        current = spread_pct if spread_pct is not None else self.current_spread_pct(symbol)
        source = "live" if symbol in self._live and spread_pct is None else "config"

        defaults = self._defaults()
        min_ratio = float(defaults.get("min_risk_to_spread_ratio", 4.0))
        round_trip = current * 2 * price

        if current > maximum:
            return SpreadInfo(
                symbol=symbol,
                typical_spread_pct=typical,
                max_spread_pct=maximum,
                current_spread_pct=current,
                source=source,
                tradeable=False,
                reason=f"spread {current*100:.3f}% > max {maximum*100:.3f}%",
            )

        if stop_distance > 0 and round_trip * min_ratio > stop_distance:
            return SpreadInfo(
                symbol=symbol,
                typical_spread_pct=typical,
                max_spread_pct=maximum,
                current_spread_pct=current,
                source=source,
                tradeable=False,
                reason=f"spread cost {round_trip:.4f} too large vs stop {stop_distance:.4f}",
            )

        return SpreadInfo(
            symbol=symbol,
            typical_spread_pct=typical,
            max_spread_pct=maximum,
            current_spread_pct=current,
            source=source,
            tradeable=True,
            reason="ok",
        )

    def mt5_symbol(self, symbol: str) -> str | None:
        sym = self._data.get("symbols", {}).get(symbol, {})
        return sym.get("mt5_symbol")


# Module singleton
_monitor: SpreadMonitor | None = None


def get_spread_monitor() -> SpreadMonitor:
    global _monitor
    if _monitor is None:
        _monitor = SpreadMonitor()
    return _monitor
