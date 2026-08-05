"""Config schema + generic value extraction for GlobalIngestionEngine.

This is the piece that makes "ny marknadsplats pa under 5 minuter" literally
true: a new source is a YAML file describing one or more HTTP endpoints and
one evaluation rule, not a new Python class. See global_hunter/ingestion/feeds/
for real, tested templates plus placeholder templates for auctions and
logistics/energy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Union

from global_hunter.contracts import ActionType, MarketType

_DEFAULT_NUMERIC_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def resolve_path(obj: Any, path: str) -> Any:
    """Dot-path resolver into decoded JSON: "data.0.last" -> obj["data"][0]["last"].

    Empty path returns `obj` unchanged (useful when the endpoint's JSON body
    IS the scalar/number you want).
    """
    if not path:
        return obj
    current = obj
    for part in path.split("."):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(f"Cannot resolve path segment {part!r} into {type(current).__name__}")
    return current


def extract_numeric(value: Any, value_regex: str | None = None) -> float:
    """Turn whatever `resolve_path` returned into a float.

    Handles raw numbers directly, and strings like "$42.39", "36,93 EUR",
    "1 234.56" via an optional custom regex or a sane default numeric regex.
    """
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    pattern = re.compile(value_regex) if value_regex else _DEFAULT_NUMERIC_RE
    match = pattern.search(text)
    if not match:
        raise ValueError(f"Could not extract a number from {text!r} (regex={pattern.pattern!r})")
    cleaned = match.group(0).replace(",", "").replace(" ", "")
    return float(cleaned)


@dataclass(frozen=True)
class FeedEndpoint:
    id: str
    url: str
    method: str = "GET"
    params: dict[str, str] = field(default_factory=dict)
    value_path: str = ""
    value_regex: str | None = None
    instrument_path: str | None = None
    instrument_static: str | None = None


@dataclass(frozen=True)
class ThresholdRule:
    reference_value: float
    direction: str = "below"  # "below" -> opportunity when value < reference (undervalued);
    min_edge_pct: float = 1.0  # "above" -> opportunity when value > reference (overvalued/short)
    kind: str = "threshold"


@dataclass(frozen=True)
class CrossFeedSpreadRule:
    endpoint_a: str
    endpoint_b: str
    min_edge_pct: float = 0.5
    cost_buffer_pct: float = 0.15
    kind: str = "cross_feed_spread"


@dataclass(frozen=True)
class ZScoreRule:
    window_size: int = 30
    zscore_threshold: float = 2.0
    kind: str = "zscore_window"


Rule = Union[ThresholdRule, CrossFeedSpreadRule, ZScoreRule]


@dataclass(frozen=True)
class FeedConfig:
    name: str
    market_type: MarketType
    endpoints: tuple[FeedEndpoint, ...]
    rule: Rule
    poll_interval_s: float = 60.0
    action: ActionType = ActionType.EXECUTE_NOW
    confidence: float = 0.75
    horizon_days: float = 0.0
    notes: str = ""
