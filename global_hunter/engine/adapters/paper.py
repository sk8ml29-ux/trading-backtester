"""PaperExecutionAdapter: the safe default. Simulates instant fills and
appends a structured JSON-lines audit trail to data/live/global_hunter/ --
same convention as the existing run_live.py paper bot. No API keys, no
real orders, ever.

This is the adapter wired in by default everywhere in this package. Swap in
a real adapter (see okx_demo.py for the pattern) only once you've walked the
opportunity type through paper-forward validation (rule #9 in .cursorrules).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from global_hunter.contracts import ApprovedOrder, ExecutionResult
from global_hunter.engine.base import ExecutionAdapter

DEFAULT_LOG_PATH = Path("data/live/global_hunter/paper_fills.jsonl")


class PaperExecutionAdapter(ExecutionAdapter):
    name = "paper"

    def __init__(self, log_path: Path = DEFAULT_LOG_PATH) -> None:
        self.log_path = log_path
        self._positions: dict[str, dict] = {}

    async def place_order(self, order: ApprovedOrder) -> ExecutionResult:
        order_id = f"paper-{order.opportunity.id}"
        status = "filled" if order.action.value == "execute_now" else "held"
        result = ExecutionResult(
            order_id=order_id,
            opportunity_id=order.opportunity.id,
            status=status,
            realized_or_expected_sek=order.expected_net_profit_sek,
            executed_at=datetime.now(timezone.utc),
            adapter=self.name,
            raw={"size_sek": order.size_sek},
            source=order.opportunity.source,
        )
        if status == "held":
            self._positions[order.opportunity.instrument] = {
                "opportunity_id": order.opportunity.id,
                "size_sek": order.size_sek,
                "opened_at": result.executed_at.isoformat(),
                "expected_net_profit_sek": order.expected_net_profit_sek,
                "source": order.opportunity.source,
            }
        await asyncio.to_thread(self._append_log, "open", order, result)
        return result

    async def get_position(self, instrument: str) -> dict | None:
        return self._positions.get(instrument)

    async def close_position(self, instrument: str) -> ExecutionResult | None:
        position = self._positions.pop(instrument, None)
        if position is None:
            return None
        result = ExecutionResult(
            order_id=f"paper-close-{instrument}-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
            opportunity_id=position["opportunity_id"],
            status="closed",
            realized_or_expected_sek=position["expected_net_profit_sek"],
            executed_at=datetime.now(timezone.utc),
            adapter=self.name,
            raw={"size_sek": position["size_sek"], "closed_early": True},
            source=position.get("source", ""),
        )
        await asyncio.to_thread(self._append_log, "close", None, result)
        return result

    async def cancel_order(self, order_id: str) -> bool:
        return True

    def _append_log(self, event: str, order: "ApprovedOrder | None", result: ExecutionResult) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event": event,
            "order": _to_jsonable(order) if order is not None else None,
            "result": _to_jsonable(result),
        }
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


def _to_jsonable(obj) -> dict:
    d = asdict(obj)
    return d
