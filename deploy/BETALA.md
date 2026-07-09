# Vad du betalar för — och i vilken ordning

Beslut taget utifrån **maximal lönsamhet**, inte billigast möjligt.

## Nu (gratis) — redan på plats

| Sak | Kostnad | Varför |
|-----|---------|--------|
| Binance marknadsdata (crypto) | 0 kr | År av 15m/1h — bättre än Yahoo |
| Walk-forward / OOS-validering | 0 kr | Enda sättet att lita på backtest |
| 14 egna strategier + paper-bot | 0 kr | Ingen köpt "bot" |
| Cursor (du har redan) | ditt abonnemang | Utveckling med mig |

**Aktiv strategi:** OOS crypto (`mixed_portfolio_oos.json`)  
**Betala INTE för:** legacy mixed-portfolios, färdiga signal-tjänster, dyra backtest-SaaS.

---

## Steg 1 — Betala här (~50 kr/mån) ⭐ PRIORITET

### Hetzner VPS (molnservrar)

- **Pris:** ~4 EUR/mån (~45–50 SEK)
- **Varför:** Paper måste köra **4–8 veckor dygnet runt** för att bevisa edge. Din PC är dålig lösning (måste vara på, ström, nät).
- **Guide:** `deploy/README_VPS.md`
- **Registrera:** https://www.hetzner.com/cloud — välj CX22, Ubuntu 24.04, Falkenstein/Frankfurt

**Alternativ tills VPS finns:** Kör lokalt (PC måste vara på):
```powershell
cd C:\Users\Alexa\Projects\trading-backtester
.\scripts\start_oos_paper.ps1
py scripts\paper_report.py   # veckovis
```

---

## Steg 2 — Efter bra paper (4–8 veckor)

### Binance live (crypto)

- **Kostnad:** courtage per trade (~0,1%), inget månadsavgift för API
- **Startkapital:** litet test (t.ex. 2 000–5 000 kr), inte 60 000 direkt
- **Varför:** OOS-setup är crypto på Binance — samma data live som backtest

---

## Steg 3 — Bara om vi expanderar till aktier/scalp

### Polygon.io (stocks/ETFs — Week 1+)

- **Pris:** ~$29/mån (Polygon Starter) eller free tier begränsad
- **Varför:** AAPL/NVDA/SPY behöver lång 15m-historik — Yahoo räcker inte för seriös OOS
- **Setup:** Sätt `POLYGON_API_KEY` i `.env` (se `.env.example`). Utan nyckel → Yahoo fallback med begränsad historik.
- **Registrera:** https://polygon.io/

## Sammanfattning — min order

1. **Idag:** Prefetch data + start paper (gratis)
2. **Denna vecka:** Skaffa **Hetzner VPS** (~50 kr/mn) → `vps_setup_all_tf.sh`
3. **4–8 veckor:** `paper_report.py` varje vecka — jämför med OOS
4. **Om paper OK:** Binance live, litet kapital
5. **Senare:** Polygon om vi lägger till aktier

**Du behöver inte betala för något annat just nu.**

---

## Snabbstart Windows (utan VPS)

```powershell
cd C:\Users\Alexa\Projects\trading-backtester
python scripts\prefetch_oos_data.py
.\scripts\start_oos_paper.ps1
python scripts\paper_report.py
```
