from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Signal:
  side: Side
  stop_loss: float
  take_profit: float
  reason: str = ""


class Strategy:
  name: str = "base"

  def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
    """Add indicator columns. Called once before the simulation loop."""
    return df

  def generate_signal(self, row: pd.Series, prev: pd.Series) -> Signal | None:
    """Return an entry signal for the current bar, or None."""
    raise NotImplementedError

  def allows_regime(self, regime: str) -> bool:
    """Whether this strategy trades in the given regime label."""
    return True
