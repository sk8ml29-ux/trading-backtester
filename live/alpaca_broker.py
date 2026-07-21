"""
Alpaca-mäklarintegration (aktier/ETF/crypto)
=============================================
Kopplar systemet till en RIKTIG mäklare (Alpaca) för verklig orderläggning.

Två lägen (styrs av env ALPACA_ENV):
  paper  → https://paper-api.alpaca.markets  (riktig ordermekanik, INGA riktiga pengar)
  live   → https://api.alpaca.markets         (RIKTIGA PENGAR)

Använder bara Python-standardbibliotek (urllib) — inga nya beroenden.

Order läggs som BRACKET-order: entry + stop-loss + take-profit i ETT paket.
Mäklaren hanterar exit server-side — vi behöver inte simulera stängning.
Det är robustare än att polla och stänga själv.

Säkerhet:
  - Default är PAPER. Live kräver ALPACA_ENV=live explicit.
  - Gatekeepern kapar risk per trade (2%) innan order läggs.
  - Utan API-nycklar är klienten inaktiv (returnerar tydligt fel).

API-nycklar (skapa gratis konto på alpaca.markets):
  ALPACA_API_KEY_ID=...
  ALPACA_API_SECRET_KEY=...
  ALPACA_ENV=paper           # eller 'live' för riktiga pengar
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"


@dataclass
class BrokerOrderResult:
    ok: bool
    message: str
    order_id: str | None = None
    raw: dict | None = None


class AlpacaBroker:
    """Tunn REST-klient mot Alpaca. Endast standardbibliotek."""

    def __init__(self, env: str | None = None):
        self.key = os.environ.get("ALPACA_API_KEY_ID")
        self.secret = os.environ.get("ALPACA_API_SECRET_KEY")
        self.env = (env or os.environ.get("ALPACA_ENV", "paper")).lower()
        self.base = LIVE_BASE if self.env == "live" else PAPER_BASE

    # ── låg nivå ────────────────────────────────────────────────────────────
    @property
    def configured(self) -> bool:
        return bool(self.key and self.secret)

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key or "",
            "APCA-API-SECRET-KEY": self.secret or "",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, body: dict | None = None,
                 timeout: int = 15) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"message": raw}
            return e.code, parsed
        except Exception as e:
            return 0, {"message": str(e)}

    # ── konto & positioner ────────────────────────────────────────────────
    def get_account(self) -> dict | None:
        if not self.configured:
            return None
        status, data = self._request("GET", f"{self.base}/v2/account")
        return data if status == 200 else None

    def get_positions(self) -> list[dict]:
        if not self.configured:
            return []
        status, data = self._request("GET", f"{self.base}/v2/positions")
        return data if status == 200 and isinstance(data, list) else []

    def get_position(self, symbol: str) -> dict | None:
        if not self.configured:
            return None
        sym = self._alpaca_symbol(symbol)
        status, data = self._request("GET", f"{self.base}/v2/positions/{sym}")
        return data if status == 200 else None

    def latest_price(self, symbol: str) -> float | None:
        """Senaste pris via data-API (aktier). Faller tillbaka på None."""
        if not self.configured:
            return None
        sym = self._alpaca_symbol(symbol)
        url = f"{DATA_BASE}/v2/stocks/{sym}/trades/latest"
        status, data = self._request("GET", url)
        if status == 200 and "trade" in data:
            return float(data["trade"]["p"])
        return None

    # ── orderläggning ────────────────────────────────────────────────────
    @staticmethod
    def _alpaca_symbol(symbol: str) -> str:
        # Yahoo-tickers → Alpaca (ETF/aktier oftast identiska; ta bort suffix)
        return symbol.replace("=F", "").replace("=X", "").split(".")[0]

    def submit_bracket(
        self,
        symbol: str,
        qty: float,
        side: str,           # "buy" | "sell"
        stop_loss: float,
        take_profit: float,
        client_order_id: str | None = None,
    ) -> BrokerOrderResult:
        """
        Lägg en bracket-order: marknads-entry + server-side stop + take-profit.
        Mäklaren stänger automatiskt vid SL eller TP.
        """
        if not self.configured:
            return BrokerOrderResult(False, "Alpaca ej konfigurerad (saknar API-nycklar)")
        if qty <= 0:
            return BrokerOrderResult(False, "qty <= 0")

        sym = self._alpaca_symbol(symbol)
        # Aktier handlas i heltal; avrunda ner
        qty_str = str(int(qty)) if qty >= 1 else f"{qty:.6f}"
        body = {
            "symbol": sym,
            "qty": qty_str,
            "side": side,
            "type": "market",
            "time_in_force": "gtc",
            "order_class": "bracket",
            "take_profit": {"limit_price": round(take_profit, 2)},
            "stop_loss": {"stop_price": round(stop_loss, 2)},
        }
        if client_order_id:
            body["client_order_id"] = client_order_id

        status, data = self._request("POST", f"{self.base}/v2/orders", body=body)
        if status in (200, 201):
            return BrokerOrderResult(True, "order lagd", order_id=data.get("id"), raw=data)
        return BrokerOrderResult(False, f"HTTP {status}: {data.get('message', data)}", raw=data)

    def close_position(self, symbol: str) -> BrokerOrderResult:
        if not self.configured:
            return BrokerOrderResult(False, "Alpaca ej konfigurerad")
        sym = self._alpaca_symbol(symbol)
        status, data = self._request("DELETE", f"{self.base}/v2/positions/{sym}")
        if status in (200, 204):
            return BrokerOrderResult(True, "position stängd", raw=data)
        return BrokerOrderResult(False, f"HTTP {status}: {data.get('message', data)}", raw=data)

    def is_market_open(self) -> bool:
        if not self.configured:
            return False
        status, data = self._request("GET", f"{self.base}/v2/clock")
        return bool(status == 200 and data.get("is_open"))
