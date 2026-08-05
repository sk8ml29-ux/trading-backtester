"""Loads FeedConfig objects from YAML files.

## How to add a new marketplace in under 5 minutes

1. Copy one of the templates in `global_hunter/ingestion/feeds/` that matches
   your rule type:
   - `threshold`: one endpoint, flag when its value crosses a fixed
     reference (e.g. "this listing is priced 20%+ under book value").
   - `cross_feed_spread`: two endpoints for the SAME underlying at two
     venues, flag when they diverge (classic arbitrage, config-only).
   - `zscore_window`: one endpoint, flag when its current value is a
     statistical outlier vs its own recent history (no manual reference
     value needed -- the module tracks the rolling mean/std itself).
2. Set `url`/`params` to the public JSON endpoint, and `value_path` to the
   dot-path into the response that holds the number you care about (use
   `curl <url> | python -m json.tool` to find it). If the API returns the
   number embedded in a string (e.g. "$42.39"), leave `value_regex` unset --
   the default numeric-extraction regex handles most formats.
3. Drop the file in `global_hunter/ingestion/feeds/` (or any directory you
   pass to `GlobalIngestionEngine(feeds_dir=...)`). It is picked up on the
   next process start -- no core code changes, ever.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from global_hunter.contracts import ActionType, MarketType
from global_hunter.ingestion.schema import (
    CrossFeedSpreadRule,
    FeedConfig,
    FeedEndpoint,
    Rule,
    ThresholdRule,
    ZScoreRule,
)

DEFAULT_FEEDS_DIR = Path(__file__).parent / "feeds"


def _build_rule(raw: dict) -> Rule:
    kind = raw.get("kind", "threshold")
    if kind == "threshold":
        return ThresholdRule(
            reference_value=float(raw["reference_value"]),
            direction=raw.get("direction", "below"),
            min_edge_pct=float(raw.get("min_edge_pct", 1.0)),
        )
    if kind == "cross_feed_spread":
        return CrossFeedSpreadRule(
            endpoint_a=raw["endpoint_a"],
            endpoint_b=raw["endpoint_b"],
            min_edge_pct=float(raw.get("min_edge_pct", 0.5)),
            cost_buffer_pct=float(raw.get("cost_buffer_pct", 0.15)),
        )
    if kind == "zscore_window":
        return ZScoreRule(
            window_size=int(raw.get("window_size", 30)),
            zscore_threshold=float(raw.get("zscore_threshold", 2.0)),
        )
    raise ValueError(f"Unknown rule kind: {kind!r} (expected threshold|cross_feed_spread|zscore_window)")


def _build_endpoint(raw: dict) -> FeedEndpoint:
    return FeedEndpoint(
        id=raw["id"],
        url=raw["url"],
        method=raw.get("method", "GET"),
        params={str(k): str(v) for k, v in (raw.get("params") or {}).items()},
        value_path=raw.get("value_path", ""),
        value_regex=raw.get("value_regex"),
        instrument_path=raw.get("instrument_path"),
        instrument_static=raw.get("instrument_static"),
    )


def parse_feed_config(raw: dict) -> FeedConfig:
    return FeedConfig(
        name=raw["name"],
        market_type=MarketType(raw["market_type"]),
        endpoints=tuple(_build_endpoint(e) for e in raw["endpoints"]),
        rule=_build_rule(raw["rule"]),
        poll_interval_s=float(raw.get("poll_interval_s", 60.0)),
        action=ActionType(raw.get("action", "execute_now")),
        confidence=float(raw.get("confidence", 0.75)),
        horizon_days=float(raw.get("horizon_days", 0.0)),
        notes=raw.get("notes", ""),
    )


def load_feed_config(path: Path) -> FeedConfig:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    try:
        return parse_feed_config(raw)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid feed config {path}: {exc}") from exc


def load_feed_configs(directory: Path = DEFAULT_FEEDS_DIR, *, skip_templates: bool = True) -> list[FeedConfig]:
    """Load every *.yaml/*.yml in `directory`.

    Files named `*_template.yaml` are placeholder examples (unfilled
    placeholder URLs) and are skipped by default so a fresh checkout doesn't
    try to hit example.com; set `skip_templates=False` once you've filled
    one in and dropped the `_template` suffix (or just pass `skip_templates=False`
    if you renamed the file already).
    """
    if not directory.exists():
        return []
    configs = []
    for path in sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml")):
        if skip_templates and "_template" in path.stem:
            continue
        configs.append(load_feed_config(path))
    return configs
