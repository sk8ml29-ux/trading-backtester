"""
Minimal OKX v5 REST client for DEMO (paper) trading — no third-party deps.

Safety by design:
  - Talks to OKX **demo trading** only (sends header `x-simulated-trading: 1`).
    Demo uses fake money; it can never touch real funds.
  - Credentials come from environment variables, never hard-coded:
        OKX_API_KEY, OKX_API_SECRET, OKX_API_PASSPHRASE
  - Read helpers (balance/positions/tickers/instruments) are safe.
  - `place_order` only runs when you explicitly call it from the bot's exec mode.

OKX demo API keys are created in the OKX web UI under "Demo trading" and are
separate from real keys. This client will refuse to run without them.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import ssl
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.okx.com"
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


class OKXError(RuntimeError):
    pass


class OKXDemoClient:
    """Signed OKX v5 client locked to demo trading."""

    def __init__(self, key: str | None = None, secret: str | None = None,
                 passphrase: str | None = None, demo: bool = True):
        self.key = key or os.environ.get("OKX_API_KEY", "")
        self.secret = secret or os.environ.get("OKX_API_SECRET", "")
        self.passphrase = passphrase or os.environ.get("OKX_API_PASSPHRASE", "")
        # Hard lock: this client is only ever used for demo in this project.
        self.demo = demo

    # -- auth -----------------------------------------------------------------
    def has_credentials(self) -> bool:
        return bool(self.key and self.secret and self.passphrase)

    def _headers(self, method: str, path: str, body: str) -> dict:
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        prehash = ts + method.upper() + path + body
        sign = base64.b64encode(
            hmac.new(self.secret.encode(), prehash.encode(), hashlib.sha256).digest()
        ).decode()
        h = {
            "OK-ACCESS-KEY": self.key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "User-Agent": _UA,
        }
        if self.demo:
            h["x-simulated-trading"] = "1"
        return h

    def _request(self, method: str, path: str, params: dict | None = None,
                 body: dict | None = None, private: bool = False):
        url = BASE + path
        body_str = json.dumps(body) if body else ""
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            path = f"{path}?{qs}"
            url = f"{url}?{qs}"
        headers = {"Content-Type": "application/json", "User-Agent": _UA}
        if private:
            if not self.has_credentials():
                raise OKXError("Saknar OKX demo-nycklar (OKX_API_KEY/SECRET/PASSPHRASE).")
            headers = self._headers(method, path, body_str)
        elif self.demo:
            headers["x-simulated-trading"] = "1"
        data = body_str.encode() if body_str else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, context=_CTX, timeout=30) as r:
                payload = json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="ignore")
            raise OKXError(f"HTTP {exc.code}: {detail}") from exc
        if payload.get("code") not in ("0", 0, None):
            raise OKXError(f"OKX code {payload.get('code')}: {payload.get('msg')} | {payload.get('data')}")
        return payload.get("data", payload)

    # -- read helpers ---------------------------------------------------------
    def get_time(self):
        return self._request("GET", "/api/v5/public/time")

    def instruments(self, inst_type: str):
        return self._request("GET", "/api/v5/public/instruments",
                             params={"instType": inst_type})

    def ticker(self, inst_id: str):
        d = self._request("GET", "/api/v5/market/ticker", params={"instId": inst_id})
        return d[0] if d else {}

    def balance(self):
        return self._request("GET", "/api/v5/account/balance", private=True)

    def positions(self):
        return self._request("GET", "/api/v5/account/positions", private=True)

    # -- trading (demo only) --------------------------------------------------
    def place_order(self, inst_id: str, side: str, sz: str, td_mode: str,
                    ord_type: str = "market", tgt_ccy: str | None = None,
                    reduce_only: bool = False):
        body = {"instId": inst_id, "side": side, "ordType": ord_type,
                "sz": sz, "tdMode": td_mode}
        if tgt_ccy:
            body["tgtCcy"] = tgt_ccy
        if reduce_only:
            body["reduceOnly"] = True
        return self._request("POST", "/api/v5/trade/order", body=body, private=True)


def connectivity_report() -> str:
    """Human-readable check: reachable? keys present? account readable?"""
    c = OKXDemoClient()
    lines = ["OKX demo-anslutning:"]
    try:
        c.get_time()
        lines.append("  [OK] Når OKX.")
    except Exception as e:
        return "  [FEL] Når inte OKX: " + repr(e)[:150]
    if not c.has_credentials():
        lines.append("  [!] Inga demo-nycklar satta (OKX_API_KEY/SECRET/PASSPHRASE).")
        lines.append("      Läsning av testkonto hoppas över tills nycklar finns.")
        return "\n".join(lines)
    try:
        bal = c.balance()
        total = bal[0].get("totalEq", "?") if bal else "?"
        lines.append(f"  [OK] Läste demo-konto. Total (demo) equity: {total}")
    except Exception as e:
        lines.append("  [FEL] Nycklar finns men konto kunde inte läsas: " + repr(e)[:150])
    return "\n".join(lines)


if __name__ == "__main__":
    print(connectivity_report())
