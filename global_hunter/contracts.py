"""Shared data contracts between scanner / legal / execution.

These are the ONLY objects the three core modules are allowed to exchange.
No module may import another module's internals -- only these types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal


class MarketType(str, Enum):
    CRYPTO = "crypto"
    FX = "fx"
    EQUITY = "equity"
    COMMODITY = "commodity"
    ENERGY = "energy"
    PREDICTION_MARKET = "prediction_market"
    MARKETPLACE = "marketplace"
    DIGITAL_CONTRACT = "digital_contract"


class ActionType(str, Enum):
    EXECUTE_NOW = "execute_now"   # edge closes immediately (arbitrage-style)
    BUY_AND_HOLD = "buy_and_hold"  # value expected to accrue over a horizon


@dataclass(frozen=True)
class Opportunity:
    """Emitted by a scanner module. Pure detection -- no legal/tax/sizing opinion."""

    id: str
    source: str                # name of the module that found it
    instrument: str
    market_type: MarketType
    edge_pct: float            # estimated edge in percent, net of the module's own cost estimate
    confidence: float          # empirical probability [0, 1] that the edge is real / will realize
    action: ActionType
    detected_at: datetime
    horizon_days: float = 0.0  # only meaningful for BUY_AND_HOLD
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RejectedOpportunity:
    opportunity: Opportunity
    reason: str
    rejected_at: datetime


@dataclass(frozen=True)
class ApprovedOrder:
    """Emitted by LegalAndTaxFilter. Ready for DynamicExecutionEngine."""

    opportunity: Opportunity
    action: ActionType
    size_sek: float
    expected_gross_profit_sek: float
    expected_cost_sek: float
    expected_tax_sek: float
    expected_net_profit_sek: float
    legal_basis: str


@dataclass(frozen=True)
class ExecutionResult:
    order_id: str
    opportunity_id: str
    status: Literal["filled", "partial", "rejected", "held", "closed"]
    realized_or_expected_sek: float
    executed_at: datetime
    adapter: str
    raw: dict[str, Any] = field(default_factory=dict)
