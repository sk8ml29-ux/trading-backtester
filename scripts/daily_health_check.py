#!/usr/bin/env python3
"""
Daglig hälsokontroll — körs av systemd-timer en gång/dag.

1. Läser alla live-botars state och jämför mot benchmarks.
2. Sparar full rapport till data/live/health_report.json (dashboarden läser den).
3. Vid RÖD flagg: skriver larm till data/live/health_alerts.log OCH skickar
   Telegram-notis om TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID är satta.

Notifiering är valfri — utan Telegram-credentials skrivs larmet ändå till fil
och syns i dashboarden. Sätt så här för Telegram (i deploy/bot.env):
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=987654321
"""
from __future__ import annotations
import sys, os, json, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from risk.strategy_health import load_benchmarks, assess_from_state_file

LIVE_DIR = ROOT / "data" / "live"
REPORT_PATH = LIVE_DIR / "health_report.json"
ALERTS_LOG = LIVE_DIR / "health_alerts.log"


def find_state_for(benchmark: dict) -> Path | None:
    import re
    sym = benchmark["symbol"]
    strat = benchmark["strategy"]
    tf = benchmark.get("timeframe", "")
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", sym).strip("_").lower()
    for c in (LIVE_DIR / f"{safe}_{strat}_{tf}_state.json",
              LIVE_DIR / f"{safe}_{strat}_state.json"):
        if c.exists():
            return c
    return None


def send_telegram(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id, "text": message, "parse_mode": "HTML",
        }).encode()
        with urllib.request.urlopen(url, data=data, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"Telegram-fel: {e}")
        return False


def main() -> int:
    benchmarks = load_benchmarks()
    if not benchmarks:
        print("Inga benchmarks. Kör generate_strategy_benchmarks.py först.")
        return 1

    verdicts = []
    for key, bm in benchmarks.items():
        sp = find_state_for(bm)
        if not sp:
            continue
        v = assess_from_state_file(sp, bm)
        if v:
            verdicts.append(v)

    reds = [v for v in verdicts if v.status == "red"]
    yellows = [v for v in verdicts if v.status == "yellow"]
    greens = [v for v in verdicts if v.status == "green"]

    # Spara full rapport (dashboarden läser denna)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {"green": len(greens), "yellow": len(yellows), "red": len(reds)},
        "verdicts": [v.__dict__ for v in verdicts],
    }, indent=2))

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{stamp}] Hälsokoll: 🟢{len(greens)} 🟡{len(yellows)} 🔴{len(reds)}")

    if reds:
        lines = [f"⚠️ TRADING-LARM {stamp}",
                 f"{len(reds)} strategi(er) har degraderat och pausats:"]
        for v in reds:
            lines.append(f"🔴 {v.symbol}/{v.strategy}: {v.reason}")
        msg = "\n".join(lines)

        with ALERTS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
        print(msg)

        if send_telegram(msg):
            print("Telegram-notis skickad.")
        else:
            print("(Ingen Telegram konfigurerad — larm sparat i health_alerts.log)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
