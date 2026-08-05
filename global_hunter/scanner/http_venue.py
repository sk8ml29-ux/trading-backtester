"""Shared aiohttp plumbing for venue adapters (rule #1: real async I/O in the
hot path, no blocking `requests` calls inside the scan loop).
"""

from __future__ import annotations

import aiohttp

DEFAULT_TIMEOUT_S = 10.0
USER_AGENT = "global-value-hunter/1.0 (+https://github.com/)"


class HttpVenueMixin:
    """Mixin providing a lazily-created, reused aiohttp session per adapter instance."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _session_ref(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_S),
                headers={"User-Agent": USER_AGENT},
            )
        return self._session

    async def get_json(self, url: str, params: dict | None = None):
        session = await self._session_ref()
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
