"""Trading cost model: commission, slippage, spread."""

from __future__ import annotations

from dataclasses import dataclass

from strategies.base import Side


@dataclass
class CostConfig:
    commission_pct: float = 0.0005   # 0.05% per side (broker)
    slippage_pct: float = 0.0003     # 0.03% adverse fill per side
    spread_pct: float = 0.0002       # 0.02% half-spread per side


def fill_price(price: float, side: Side, is_entry: bool, costs: CostConfig) -> float:
    """Worse fill for trader: long pays more on entry, receives less on exit."""
    friction = costs.slippage_pct + costs.spread_pct
    if side == Side.LONG:
        return price * (1 + friction) if is_entry else price * (1 - friction)
    return price * (1 - friction) if is_entry else price * (1 + friction)


def cost_config_for(symbol: str, base: CostConfig | None = None) -> CostConfig:
    """Build cost config using per-symbol spread from spread_config.json."""
    base = base or CostConfig()
    try:
        from backtest.spread import get_spread_monitor

        spread = get_spread_monitor().typical_spread_pct(symbol)
    except Exception:
        spread = base.spread_pct
    return CostConfig(
        commission_pct=base.commission_pct,
        slippage_pct=base.slippage_pct,
        spread_pct=spread,
    )


def trade_pnl(
    side: Side,
    entry_price: float,
    exit_price: float,
    size: float,
    costs: CostConfig,
) -> tuple[float, float]:
    """Return (net_pnl, total_costs)."""
    entry_fill = fill_price(entry_price, side, True, costs)
    exit_fill = fill_price(exit_price, side, False, costs)
    if side == Side.LONG:
        gross = (exit_fill - entry_fill) * size
    else:
        gross = (entry_fill - exit_fill) * size
    commission = (entry_fill * size + exit_fill * size) * costs.commission_pct
    friction_cost = abs(entry_price - entry_fill) * size + abs(exit_price - exit_fill) * size
    total_costs = commission + friction_cost
    return gross - commission, total_costs
