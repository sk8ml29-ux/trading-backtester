"""Second, REAL execution adapter -- proof that DynamicExecutionEngine can
target "vilket API som helst" without touching its own code. Wraps the
repo's existing `live/okx_client.py` OKXDemoClient, which is hard-locked to
OKX's demo-trading environment (`x-simulated-trading: 1`). Swap in the live
endpoint only after paper-forward validation AND with the entrepreneur's
explicit go-ahead (API keys are a business decision, never inferred).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from global_hunter.contracts import ApprovedOrder, ExecutionResult
from global_hunter.engine.base import ExecutionAdapter

logger = logging.getLogger("global_hunter.engine.adapters.okx_demo")


class OKXDemoExecutionAdapter(ExecutionAdapter):
    name = "okx_demo"

    def __init__(self, inst_map: dict[str, str], usd_sek_rate: float, td_mode: str = "cash") -> None:
        """`inst_map` maps an Opportunity.instrument label (e.g. "BTC") to an
        OKX instrument id (e.g. "BTC-USDT"). `usd_sek_rate` converts the
        SEK position size into USDT notional -- refresh this periodically
        from a real FX feed; it is intentionally injected, not fetched here,
        to keep this adapter's only job "place the order".
        """
        from live.okx_client import OKXDemoClient  # local import: optional dependency, only needed if this adapter is used

        self._client = OKXDemoClient(demo=True)
        self.inst_map = inst_map
        self.usd_sek_rate = usd_sek_rate
        self.td_mode = td_mode
        # Tracks BUY_AND_HOLD fills so close_position() knows the opposite
        # side + size to unwind (ExecutionGovernor preemption path).
        self._held: dict[str, dict] = {}

    async def place_order(self, order: ApprovedOrder) -> ExecutionResult:
        inst_id = self.inst_map[order.opportunity.instrument]
        side = "buy" if order.opportunity.raw.get("direction", "long") != "short" else "sell"
        if order.opportunity.raw.get("buy_venue") == "okx_spot":
            side = "buy"
        elif order.opportunity.raw.get("sell_venue") == "okx_spot":
            side = "sell"

        try:
            ticker = await asyncio.to_thread(self._client.ticker, inst_id)
            last_price = float(ticker["last"])
            size_usdt = order.size_sek / self.usd_sek_rate
            size_units = round(size_usdt / last_price, 6)
            data = await asyncio.to_thread(
                self._client.place_order, inst_id, side, str(size_units), self.td_mode, "market"
            )
            fill = data[0] if data else {}
            status = "filled" if order.action.value == "execute_now" else "held"
            if status == "held":
                self._held[order.opportunity.instrument] = {
                    "opportunity_id": order.opportunity.id, "inst_id": inst_id,
                    "side": side, "size_units": size_units,
                    "expected_net_profit_sek": order.expected_net_profit_sek,
                }
            return ExecutionResult(
                order_id=fill.get("ordId", f"okx-demo-{order.opportunity.id}"),
                opportunity_id=order.opportunity.id,
                status=status,
                realized_or_expected_sek=order.expected_net_profit_sek,
                executed_at=datetime.now(timezone.utc),
                adapter=self.name,
                raw=fill,
            )
        except Exception as exc:  # noqa: BLE001 - OKXError or network failure -> reject, never crash the engine
            return ExecutionResult(
                order_id=f"okx-demo-failed-{order.opportunity.id}",
                opportunity_id=order.opportunity.id,
                status="rejected",
                realized_or_expected_sek=0.0,
                executed_at=datetime.now(timezone.utc),
                adapter=self.name,
                raw={"error": str(exc)},
            )

    async def get_position(self, instrument: str) -> dict | None:
        positions = await asyncio.to_thread(self._client.positions)
        for pos in positions:
            if pos.get("instId") == self.inst_map.get(instrument):
                return pos
        return None

    async def close_position(self, instrument: str) -> ExecutionResult | None:
        held = self._held.pop(instrument, None)
        if held is None:
            return None
        opposite_side = "sell" if held["side"] == "buy" else "buy"
        try:
            data = await asyncio.to_thread(
                self._client.place_order, held["inst_id"], opposite_side, str(held["size_units"]), self.td_mode, "market"
            )
            fill = data[0] if data else {}
            return ExecutionResult(
                order_id=fill.get("ordId", f"okx-demo-close-{held['opportunity_id']}"),
                opportunity_id=held["opportunity_id"],
                status="closed",
                realized_or_expected_sek=held["expected_net_profit_sek"],
                executed_at=datetime.now(timezone.utc),
                adapter=self.name,
                raw=fill,
            )
        except Exception:  # noqa: BLE001
            self._held[instrument] = held  # restore -- we failed to actually close it
            logger.exception("Failed to close OKX demo position for %s", instrument)
            raise

    async def cancel_order(self, order_id: str) -> bool:
        return False  # OKXDemoClient does not expose cancel yet; extend when needed

    async def healthcheck(self) -> bool:
        try:
            result = await asyncio.to_thread(self._client.get_time)
            return bool(result)
        except Exception:  # noqa: BLE001
            return False
