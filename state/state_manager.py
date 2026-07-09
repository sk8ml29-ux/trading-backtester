"""
State Manager — unified view of all live bot state.

Reads every *_state.json in data/live/ and aggregates into a single
snapshot.  Used by the health monitor, the Gatekeeper, and the lab
to understand current equity / drawdown / open positions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
LIVE_DATA = ROOT / "data" / "live"


class StateManager:
    """Aggregates all live-bot state files into a unified snapshot."""

    # ─────────────────────────────────────────────────────────────────────────
    # SNAPSHOTS
    # ─────────────────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Read all *_state.json files and return a combined dict."""
        bots: dict[str, dict] = {}
        if LIVE_DATA.exists():
            for f in LIVE_DATA.glob("*_state.json"):
                try:
                    bots[f.stem] = json.loads(f.read_text())
                except Exception as e:
                    logger.warning("[StateManager] Could not read %s: %s", f, e)
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "bots": bots,
            "total_bots": len(bots),
        }

    def save_snapshot(self, path: Path) -> None:
        snap = self.snapshot()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snap, indent=2))
        logger.info("[StateManager] Snapshot saved → %s", path)

    # ─────────────────────────────────────────────────────────────────────────
    # DERIVED VIEWS
    # ─────────────────────────────────────────────────────────────────────────

    def equity_map(self) -> dict[str, float]:
        """{ bot_id: current_equity } for every active bot."""
        result: dict[str, float] = {}
        for bot_id, state in self.snapshot()["bots"].items():
            result[bot_id] = float(
                state.get("equity", state.get("balance", state.get("capital", 0.0)))
            )
        return result

    def total_equity(self) -> float:
        return sum(self.equity_map().values())

    def open_positions(self) -> list[dict]:
        """All open positions across every bot, each tagged with bot_id."""
        positions: list[dict] = []
        for bot_id, state in self.snapshot()["bots"].items():
            pos = state.get("position") or state.get("positions", [])
            if isinstance(pos, dict) and pos:
                positions.append({"bot": bot_id, **pos})
            elif isinstance(pos, list):
                for p in pos:
                    positions.append({"bot": bot_id, **p})
        return positions

    def peak_equity_map(self) -> dict[str, float]:
        """Best recorded equity per bot (for drawdown calculation)."""
        result: dict[str, float] = {}
        for bot_id, state in self.snapshot()["bots"].items():
            result[bot_id] = float(
                state.get("peak_equity", state.get("equity", state.get("balance", 0.0)))
            )
        return result

    def daily_start_equity_map(self) -> dict[str, float]:
        """Equity at day open per bot (for daily-loss circuit breaker)."""
        result: dict[str, float] = {}
        for bot_id, state in self.snapshot()["bots"].items():
            result[bot_id] = float(
                state.get(
                    "daily_start_equity",
                    state.get("equity", state.get("balance", 0.0)),
                )
            )
        return result
