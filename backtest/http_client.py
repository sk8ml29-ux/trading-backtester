from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class HttpGeoBlocked(RuntimeError):
    """HTTP 451 — service unavailable from this egress IP (e.g. Binance eligibility)."""

    def __init__(self, url: str, body: str = "") -> None:
        self.url = url
        self.body = body
        super().__init__(f"HTTP 451 geo-blocked: {url}" + (f" ({body[:160]})" if body else ""))


def _ssl_context() -> ssl.SSLContext:
    """Work around broken CA bundles on some Windows Python installs."""
    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    # Fallback when system certs fail (common on fresh Windows Python 3.14)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_json(url: str, timeout: int = 45):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, context=_ssl_context(), timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError(
                "API rate-limited (HTTP 429). Wait ~15s and retry."
            ) from exc
        if exc.code == 451:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            raise HttpGeoBlocked(url, body) from exc
        raise
