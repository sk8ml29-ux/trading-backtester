# Rapport — Daytrading-uppdrag (lönsamhet efter alla kostnader)

**Datum:** 2026-07-22
**Uppdrag:** Bygg en daytrading-strategi som är lönsam efter ALLA kostnader, med mål
≥ 0,25 % nettoavkastning/handelsdag, ≥ 60 % vinstdagar, max DD ≤ 10 %, daglig maxförlust
≤ 2 %, Sharpe ≥ 1,5 (annualiserat, OOS) och ≥ 200 trades i valideringen.

---

## 1. Kort dom (brutalt ärligt)

**Målet nås INTE.** Efter en bred och rigorös utforskning (5 klassiska TA-familjer på
15m/1h, cross-sectional marknadsneutral momentum/reversion på 4 rebalanserings-bars,
tid-på-dygnet-säsong, samt granskning av repots egen funding-/MACD-forskning) finns
**ingen intraday-strategi med robust edge kvar efter realistiska kostnader** på likvida
kryptomajors.

Den enda ansats som ger **positiv out-of-sample-förväntan efter kostnader** är en
**marknadsneutral cross-sectional momentum-bok** — men den rebalanseras ~var 12:e dag
(alltså *swing*, inte intraday) och **missar ändå alla de skarpa dagsmålen**.

### Kärninsikt
Råa (pre-cost) edger *finns* intraday — t.ex. RSI-2-reversion hade **+75 % brutto**
in-sample. Men edgen per trade (~0,1 %) är **mindre än rundturskostnaden (~0,18 %)**.
På 15m/1h äter spread + courtage + slippage helt upp signalen. Det är därför daytrading
på likvid krypto är så svårt: marknaden är för effektiv relativt friktionen.

---

## 2. Metodik (varför siffrorna går att lita på)

- **Data:** Binance-klines (hög kvalitet, 24/7) direkt ur `data/cache/` — full historik
  2023-01→2026-07 för 7 majors (BTC, ETH, SOL, XRP, ADA, DOGE, LINK); ETH 15m finns från
  2017. (Repots `run_backtest.py` kapade intraday till ~60 dagar; mitt nya labb kringgår
  det och använder hela historiken.)
- **Kostnadsmodell (per sida):** courtage 0,05 % + slippage 0,02 % + halv spread 0,02 %
  → **~0,18 % rundtur**. Konservativt för Binance-majors; separat stresstest möjligt via
  flaggor. **Ingen körning gjordes utan kostnader.**
- **Simulator (`research/daytrade_lab.py`):** event-driven, en position i taget, entry på
  **nästa bars open** (ingen lookahead), stop/TP intrabar där **stop antas träffa först**
  (värsta fall), tidsstopp. Positionsstorlek från ATR-stop och riskfraktion.
- **Anti-overfit:** parametrar delas **över alla symboler** (en robust uppsättning, inte
  en per symbol), tydlig **train/OOS-split** + **rullande walk-forward** (omoptimering per
  veck). OOS-avkastning aggregeras på fast bas (additiv, ingen ränta-på-ränta-illusion).
- **Mått:** dagsavkastning, andel vinstdagar (kalender + aktiva dagar), Sharpe/Sortino på
  dagsavkastning (annualiserat ×365 för krypto), max drawdown, träffsäkerhet,
  snittvinst/-förlust, och **kostnadernas andel av bruttovinsten**.

---

## 3. Resultat per ansats (alla efter kostnader)

### 3a. Intraday TA — 15m, 7 symboler, delade parametrar, train/OOS
| Strategi | IS bästa net | IS bästa BRUTTO (pre-cost) | OOS net | OOS Sharpe | Dom |
|---|---|---|---|---|---|
| mean_reversion_bb | −0,46 % | **+30,6 %** | −0,13 % | −0,07 | FAIL |
| zscore_reversion | −3,73 % | +35,7 % | −3,21 % | −1,22 | FAIL |
| rsi2_pullback | −2,10 % | **+75,2 %** | −12,6 % | −2,06 | FAIL |
| donchian_breakout | −146 % | +8,5 % | −119 % | −5,51 | FAIL |
| ema_pullback_trend | −209 % | +62,4 % | −150 % | −6,70 | FAIL |

**Slutsats:** brutto-edge finns i reversion-familjerna, men kostnaderna (cost share
> 100 % av bruttot) gör allt net-negativt. Breakouts/trend är rena kostnadskvarnar
på 15m.

### 3b. Intraday TA — 1h
In-sample kan finjusteras marginellt positivt (mean_rev +3,3 %, zscore +3,4 %,
rsi2 +2,4 % net IS) men **samtliga är OOS-negativa**. Klassiskt överfit.

### 3c. Cross-sectional marknadsneutral (long topp-k / short botten-k)
Rebalansering testad på 4h/8h/12h/1D, med och utan volatilitetsmål och walk-forward.
- 8h momentum: stark IS (Sharpe 1,8) men **OOS Sharpe −0,93** som fast config; walk-forward
  med omoptimering gav +59…95 % OOS men det drevs helt av en tidig tjurmarknadsvecka
  (2023-09→2024-05). Inte robust.
- **12h momentum (bästa kandidaten, se §4):** positiv OOS men svag.

### 3d. Tid-på-dygnet-säsong (BTC/ETH 1h)
Starkaste timmen (22 UTC) driver ~0,02–0,07 % — **under** rundturskostnaden. Ingen edge.

### 3e. Repots egen forskning (bekräftar bilden)
`funding_confluence_oos.json`: BTC −11,8 %, ETH −15,9 % OOS (FAIL); endast NEAR/ATOM
"passerar" på 26–36 trades (statistiskt brus). `mbd_oos.json` (macd_bidirectional):
FAIL OOS på alla majors. Detta stämmer med mina resultat.

---

## 4. Bästa levererbara kandidat (ärligt: FAIL mot skarpa mål, men positiv OOS-edge)

**Cross-sectional momentum, marknadsneutral.** Motor: `research/cross_sectional.py`.
Parametrar i `research/daytrade_best_params.json`.

- Universe: BTC, ETH, SOL, XRP, ADA, DOGE, LINK (Binance 15m → resamplad till 12h)
- Signal: rankad trailing-avkastning 12 bars (6 dygn); **long topp-3, short botten-3**,
  dollar-neutral, hävstång 1,0 (brutto), rebalans var 24:e bar (~12 dygn)
- Kostnader: 0,18 % rundtur (samma modell som ovan)

### Nyckeltal, out-of-sample (senaste ~45 % av historiken, ~2024-09 → 2026-07)
| Mått | Värde | Mål | Dom |
|---|---|---|---|
| Nettoavkastning/dag | **+0,017 %** | ≥ 0,25 % | ❌ FAIL |
| Andel vinstdagar | **50,5 %** | ≥ 60 % (golv 50 %) | ❌ FAIL (⚠️ klarar golvet) |
| Sharpe (annualiserad) | **0,37** | ≥ 1,5 | ❌ FAIL |
| Max drawdown (OOS) | **−12,6 %** | ≤ 10 % | ❌ FAIL (nära) |
| Sämsta dag | −4,2 % | ≥ −2 % | ❌ FAIL |
| Kostnadsandel av brutto | 31,6 % | — | ok |
| Total OOS-avkastning | +8,0 % | — | positiv |

**Årsvis OOS:** 2024 −1,2 % · 2025 +9,6 % (Sharpe 0,58) · 2026 −0,3 %.
In-sample (för transparens, ej att lita på): +102,7 %, Sharpe 1,71 — gapet IS→OOS visar
hur mycket som är överfit/regim.

**Ärlig tolkning:** en verklig men svag, regimberoende marknadsneutral edge. Positiv OOS,
kontrollerad OOS-drawdown, men långt under dagsvinstmålet och **inte intraday**.

### Kör i paper-läge (signal)
```bash
python3 -m research.cross_sectional --bar 12h --signal --lb 12 --hold 24 --k 3 --leverage 1.0
```
Skriver dagens målbok (vilka 3 att gå long / 3 att short). Reproducera valideringen:
```bash
python3 -m research.cross_sectional --bar 12h --fixed --lb 12 --hold 24 --k 3 --leverage 1.0
```

---

## 5. Uppnås "vinst varje/varannan dag"?

**Nej.** Ingen testad strategi ger ≥ 60 % vinstdagar OOS efter kostnader; bästa kandidaten
ligger på ~50 % (dvs rena slantsinglingen på dagsnivå) med Sharpe 0,37. Att pålitligt tjäna
pengar *varje/varannan dag* efter kostnader på likvid krypto-intraday **stöds inte av datan**.

---

## 6. Backuper och ändrade filer

**Inga befintliga huvudfiler skrevs om** — allt arbete är *additivt* (nya filer). Därför
krävdes inga backuper enligt regeln, men backup-katalogen skapades enligt protokoll:
`backups/2026-07-22/`.

**Nya filer:**
- `research/daytrade_lab.py` — snabb numpy-simulator + dagsmått + kostnadsmodell
- `research/daytrade_strategies.py` — 5 TA-strategifamiljer + parametergrids
- `research/run_daytrade.py` — delade-parametrar train/OOS + walk-forward driver
- `research/cross_sectional.py` — marknadsneutral cross-sectional motor (+ signal/fixed/WF)
- `research/daytrade_best_params.json` — valda parametrar för bästa kandidaten
- `RAPPORT_DAYTRADING.md` — denna rapport
- Resultat-JSON: `research_daytrade_15m.json`, `research_daytrade_1h.json`,
  `research_xsect_8h.json`, `research_xsect_12h.json`, `research_xsect_1D.json`

---

## 7. Rekommenderat nästa steg

1. **Handla inte detta med riktiga pengar.** Ingen strategi klarar målen OOS.
2. Om cross-sectional-boken ska provas: **veckor–månader av paper-forward** (den föreslagna
   12h-momentum-boken) och mät om 2025-mönstret håller i realtid. Förvänta Sharpe < 0,5.
3. **Sänk friktionen** där den verkliga hävstången finns: förhandla maker-rebates/lägre
   fees, använd limit-orders (maker) istället för market, och exekvera på perps med
   djup orderbok. Om rundturskostnaden kan pressas mot ~0,05 % blir flera av de
   brutto-positiva reversion-signalerna intressanta igen — det är den mest lovande vägen
   till en *äkta* intraday-edge.
4. **Datakällor för nästa edge:** funding-rate/basis och orderflöde/likvidationer på perps
   (inte ren OHLC-TA), samt bredare universe (20–40 coins) för starkare cross-section.
5. Behåll den ärliga valideringsdisciplinen: train/OOS + walk-forward + fulla kostnader är
   redan inbyggt i `research/`-labbet och bör återanvändas för allt framtida arbete.
