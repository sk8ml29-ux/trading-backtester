"""Hard guardrails for the Global Value Hunter.

These constants encode the entrepreneur's business mandate directly as code
(per .cursorrules rule #7: "Kapitalgrunser ar kod, inte kommentarer"). They
are read from the environment so ops can tune them without touching code,
but the defaults are safe, conservative values inside the agreed range.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Svensk bolagsskatt (2026). Parametrized, not hardcoded inline in tax.py,
#: so a future rate change is a one-line update.
CORP_TAX_RATE_SE = float(os.environ.get("GH_CORP_TAX_RATE_SE", "0.206"))

#: Entrepreneur's hard capital fence. NO leverage, NO loans -- ever.
MIN_CAPITAL_SEK = float(os.environ.get("GH_MIN_CAPITAL_SEK", "100000"))
MAX_CAPITAL_SEK = float(os.environ.get("GH_MAX_CAPITAL_SEK", "800000"))

#: Business target the whole system is calibrated against.
TARGET_NET_SEK_PER_MONTH = float(os.environ.get("GH_TARGET_NET_SEK_PER_MONTH", "30000"))

#: Never risk more than this fraction of *available* capital on one single
#: opportunity, no matter how confident a module is. Diversification guard.
MAX_POSITION_PCT_OF_CAPITAL = float(os.environ.get("GH_MAX_POSITION_PCT", "0.25"))


@dataclass
class CapitalLedger:
    """Tracks how much of the entrepreneur's cash is currently deployed.

    Thread-/task-safe enough for a single asyncio event loop (no real
    concurrency across threads); if you ever run this across processes,
    swap this for a DB-backed ledger with row locking.
    """

    total_capital_sek: float

    def __post_init__(self) -> None:
        if not (MIN_CAPITAL_SEK - 1 <= self.total_capital_sek <= MAX_CAPITAL_SEK + 1):
            raise ValueError(
                f"total_capital_sek={self.total_capital_sek} is outside the mandated "
                f"range [{MIN_CAPITAL_SEK}, {MAX_CAPITAL_SEK}] SEK. No loans, no margin "
                f"beyond this fence -- see .cursorrules."
            )
        self.allocated_sek: float = 0.0

    @property
    def available_sek(self) -> float:
        return max(0.0, self.total_capital_sek - self.allocated_sek)

    def reserve(self, amount_sek: float) -> bool:
        if amount_sek <= 0:
            return False
        if amount_sek > self.available_sek + 1e-6:
            return False
        self.allocated_sek += amount_sek
        return True

    def release(self, amount_sek: float) -> None:
        self.allocated_sek = max(0.0, self.allocated_sek - amount_sek)
