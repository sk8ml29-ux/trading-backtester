from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from strategies.base import Side


@dataclass
class OpenPosition:
    side: str
    entry_time: str
    entry_price: float
    stop_loss: float
    take_profit: float
    size: float
    reason: str


@dataclass
class BotState:
    symbol: str
    strategy: str
    equity: float
    open_position: OpenPosition | None = None
    last_bar_time: str | None = None
    trade_count: int = 0
    peak_equity: float = 0.0
    initial_capital: float = 0.0
    start_time: str | None = None  # när boten först startade (för hälsovakt)

    @classmethod
    def load(cls, path: Path, default_equity: float, symbol: str, strategy: str) -> BotState:
        if not path.exists():
            return cls(
                symbol=symbol,
                strategy=strategy,
                equity=default_equity,
                peak_equity=default_equity,
                initial_capital=default_equity,
                start_time=datetime.now().isoformat(timespec="seconds"),
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        pos = raw.get("open_position")
        equity = float(raw.get("equity", default_equity))
        return cls(
            symbol=raw.get("symbol", symbol),
            strategy=raw.get("strategy", strategy),
            equity=equity,
            open_position=OpenPosition(**pos) if pos else None,
            last_bar_time=raw.get("last_bar_time"),
            trade_count=int(raw.get("trade_count", 0)),
            peak_equity=float(raw.get("peak_equity", equity)),
            initial_capital=float(raw.get("initial_capital", default_equity)),
            start_time=raw.get("start_time") or datetime.now().isoformat(timespec="seconds"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"[{ts}] {message}\n")
