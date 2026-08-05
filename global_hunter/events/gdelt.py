"""Minimal async client for GDELT's free, public, no-key DOC 2.0 API
(https://api.gdeltproject.org/api/v2/doc/doc) -- global news coverage
volume + tone timelines, used as the "globala nyhetsflöden" signal for
AlphaEventScanner.

GDELT asks for max one request every 5 seconds per IP ("Please limit
requests to one every 5 seconds..."). This client enforces that itself via
an internal lock + minimum-interval sleep, so callers never need to worry
about it -- just don't spin up multiple GdeltClient instances hammering the
same IP in parallel.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from global_hunter.scanner.http_venue import HttpVenueMixin

logger = logging.getLogger("global_hunter.events.gdelt")

BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_REQUEST_INTERVAL_S = 5.5  # GDELT's stated courtesy limit is 5s; add a small safety margin


class GdeltClient(HttpVenueMixin):
    def __init__(self) -> None:
        HttpVenueMixin.__init__(self)
        self._last_call_monotonic = 0.0
        self._throttle_lock = asyncio.Lock()

    async def _throttled_get(self, params: dict) -> dict:
        async with self._throttle_lock:
            wait_s = MIN_REQUEST_INTERVAL_S - (time.monotonic() - self._last_call_monotonic)
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            self._last_call_monotonic = time.monotonic()
            return await self.get_json(BASE_URL, params=params)

    async def _timeline(self, query: str, mode: str, timespan: str) -> list[tuple[datetime, float]]:
        data = await self._throttled_get({"query": query, "mode": mode, "format": "json", "timespan": timespan})
        series = (data.get("timeline") or [{}])[0].get("data") or []
        points: list[tuple[datetime, float]] = []
        for p in series:
            try:
                ts = datetime.strptime(p["date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                points.append((ts, float(p["value"])))
            except (KeyError, ValueError):
                continue
        return points

    async def volume_timeline(self, query: str, timespan: str = "3d") -> list[tuple[datetime, float]]:
        """Normalized article-volume-% time series for `query` (news volume spike detector)."""
        return await self._timeline(query, "timelinevol", timespan)

    async def tone_timeline(self, query: str, timespan: str = "1d") -> list[tuple[datetime, float]]:
        """Average sentiment tone time series for `query` (roughly -10..+10; negative = crisis-toned)."""
        return await self._timeline(query, "timelinetone", timespan)
