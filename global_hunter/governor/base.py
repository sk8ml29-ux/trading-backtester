"""The unit of bookkeeping ExecutionGovernor reasons about: one admitted
order, tracked from admission to close, ranked by "velocity" -- expected net
SEK per hour of capital tied up. This is the single number the governor
uses to decide "is this new opportunity worth preempting an existing hold
for?", directly answering the entrepreneur's el-arbitrage-vs-Polymarket
scenario: 5 000 kr/dag stable vs. 15 000 kr/timme fleeting is a
velocity comparison, not a raw-SEK comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from global_hunter.contracts import ActionType, ApprovedOrder

CommitmentStatus = Literal["active", "closing", "closed"]

#: EXECUTE_NOW opportunities are modeled as completing almost instantly for
#: ranking purposes (their capital is released the moment execution
#: finishes regardless -- see ExecutionGovernor.on_execution_result). This
#: constant only affects how they rank against BUY_AND_HOLD commitments
#: when both are competing for capital in the same instant.
EXECUTE_NOW_ASSUMED_HOURS = 1.0 / 60.0  # ~1 minute


@dataclass
class Commitment:
    order: ApprovedOrder
    committed_at: datetime
    status: CommitmentStatus = "active"

    @property
    def velocity_sek_per_hour(self) -> float:
        if self.order.action is ActionType.EXECUTE_NOW:
            hours = EXECUTE_NOW_ASSUMED_HOURS
        else:
            hours = max(self.order.opportunity.horizon_days * 24.0, 0.5)
        return self.order.expected_net_profit_sek / hours
