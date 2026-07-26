# Rapport — Daytrading-uppdrag (lönsamhet efter alla kostnader)

**Datum:** 2026-07-22 (uppdaterad — se §0 för genombrottet)
**Uppdrag:** Bygg en daytrading-strategi som är lönsam efter ALLA kostnader, med mål
≥ 0,25 % nettoavkastning/handelsdag, ≥ 60 % vinstdagar, max DD ≤ 10 %, daglig maxförlust
≤ 2 %, Sharpe ≥ 1,5 (annualiserat, OOS) och ≥ 200 trades i valideringen.

---

## 0a. REDOVISNINGSAUDIT 2026-07-24 — KORRIGERADE SIFFROR

Ett fullständigt bugtest hittade två modellfel i tidigare v3-tabeller:

1. hävstångssvepet multiplicerade felaktigt även cash-räntan med hävstång,
2. låneräntan för *kort spot* (negativ funding-sida) saknades.

Paper-motorn saknade dessutom första inträdeskostnaden och verklig basis-P&L. Allt
detta är nu rättat. Tidigare v2/v3-tabeller längre ner är forskningshistorik och ska
**inte** användas för kapitalbeslut.

### Korrigerad walk-forward OOS (50 likvida coins, 5 % cash-ränta, 10 % kort-spot-lån)
| Hävstång | Årsavk | net/dag | Max DD | Sämsta dag | Vinstdagar | Sharpe |
|---|---|---|---|---|---|---|
| 1x | +10,9 % | 0,028 % | −0,4 % | −0,4 % | 71 % | 7,1 |
| 5x | +62,3 % | 0,134 % | −1,9 % | −1,9 % | 68 % | 6,7 |
| 7x | +96,2 % | 0,186 % | −2,6 % | −2,6 % | 68 % | 6,7 |
| 8x | +115,6 % | 0,213 % | −3,0 % | −3,0 % | 68 % | 6,6 |
| 10x | +160,2 % | 0,265 % | −3,7 % | −3,7 % | 68 % | 6,6 |

**Korrigerad dom:** funding-idén förblir positiv och riskjusterat stark i historiska
tester, men **ingen testad hävstång klarar samtidigt** 0,25 %/dag och sämsta dag ≥ −2 %.
0,25 %-målet nås först kring 10x, där historisk sämsta dag är cirka −3,7 %. Starta
paper på 1x; 5x är ett forskningsscenario, inte ett live-råd.

Kostnadsstress (taker-only, 0,44 % rundtur) vid 1x: cirka +10,3 %/år, Sharpe 6,8.

### Kör den korrigerade modellen
```bash
python3 -m research.funding_lab --champion --out research_funding_champion.json   # full validering (50 likvida coins)
python3 -m research.funding_lab --signal --leverage 1                             # forskningssignal, låg risk
```

---

## 0c. TESTAT & FÖRKASTAT: conviction / per-trade hävstång (1..10)

Idé: poängsätt varje position och höj hävstången på "säkra" trades. **Förkastad som
strategiändring** (höjde inte risk-justerad vinst) — sparad som forskning
(`research_conviction_test.json`, `alloc="conviction"` i `funding_lab.py`).

- *Per trade* har poängen (stabil carry = funding ÷ funding-volatilitet) äkta signal:
  toppkvintil step-Sharpe **0,254** vs 0,046 i botten, minst svans (−29 vs −655 bps).
- *Men som portfölj-hävstång* koncentreras kapitalet → diversifieringen (källan till
  Sharpe ~10 / −0,3 % DD) offras och turnover-kostnaden steg 19 %→50 %. Walk-forward:
  equal-weight 14,1 %/Sharpe 10,0 vs conviction 13,3 %/Sharpe 5,2 — och sämre svans
  (−0,78 % vs −0,21 %). **Likformig hävstång på den diversifierade boken dominerar.**

---

## 0b. FORSKNINGSHISTORIK FÖRE AUDIT (SIFFRORNA ÄR ERSATTA)

Efter första leveransen kördes 5 experimentloopar (allt validerat OOS + walk-forward,
fulla kostnader). Motor: `research/funding_lab.py` (ärligare turnover-kostnad — debiteras
på faktisk *signerad viktförändring*, inte bara teckenbyten).

| Loop | Idé | Utfall |
|---|---|---|
| 1 | Fast slot-allokering (stabil) vs aktiv rebalans vs koncentration | **Equal-weight + fasta slots vinner.** Koncentration (top-N/funding-viktat) förlorar — diversifiering slår urval (hög funding = hög basis/blowup-risk) |
| 2 | Bredda universum 24 → **~55 perps** | **Största lyftet:** OOS-avk 5,8 % → 8,8 %, fler aktiva ben, lägre risk |
| 3 | Hävstång vs vol-target som avkastningsratt | Statisk hävstång är renast (vol-target sänkte Sharpe); hävstångssvep nedan |
| 4 | Bättre funding-prognos (24-bars mean) + basis-filter | Sharpe upp, mindre whipsaw; ta bara det ben som basisen stödjer |
| 5 | Walk-forward + kostnadsstress | **Robust — samma parametrar valdes i ALLA folds** (ej överfit) |

De fem experimentlooparna gav fortfarande värdefulla designresultat: equal-weight,
likviditetskurering, fast slot-allokering, längre funding-prognos och basisfilter höll
bättre än koncentration och conviction-viktning. **Alla gamla avkastningssiffror i denna
forskningsfas är dock ersatta av den korrigerade tabellen i §0a.**

---

## 0. URSPRUNGLIG FUNDING-FORSKNING (HISTORIK — SIFFROR ERSATTA AV §0a)

Efter att TA/cross-sectional visat sig sakna robust edge (§1–§5 nedan) hittade jag
den verkliga, kostnadståliga edgen i **carry, inte prisriktning**: att skörda
**funding-rate** på perpetual-kontrakt marknadsneutralt.

**Mekanik (marknadsneutral):** för att håva in POSITIV funding håller man
*kort perp + lång spot*; för NEGATIV funding *lång perp + kort spot*. Positionen är
delta-neutral → prisrörelser tar ut varandra (bara den lilla perp-vs-spot-basisen kvar,
~5 bps std för majors). P&L domineras av funding, som Binance betalar var 8:e timme och
som är **positiv ~85 % av tiden för BTC**. Nyckeln som fick det att funka: en
**hysteres-logik** — gå in bara när funding tydligt överstiger kostnadströskeln och håll
tills den avtar → låg turnover, så rundturskostnaden (~0,30 %) amorteras över långa hålltider.

**Data:** Binance Vision (publika dumpar, ej geoblockat — live-API gav HTTP 451).
Funding + perp- och spot-klines (8h) för **24 likvida coins**, 2023-01 → 2026-06.
Full ärlig kostnadsmodell (perp-leg 0,06 % + spot-leg 0,09 % per sida).

Denna fas visade att funding-carry var betydligt mer lovande än kortsiktig TA och
ledde till hysteres, delta-neutral spot/perp och full Binance Vision-data. De
ursprungliga avkastningstabellerna är borttagna eftersom de saknade kort-spot-låneränta.
Använd endast de korrigerade resultaten i §0a.

### Kör den (paper)
```bash
# Ladda/uppdatera funding + priser (Binance Vision, inga nycklar)
python3 -m research.binance_vision --start 2023-01-01 --end 2026-06-30 --interval 8h
# Dagens marknadsneutrala målbok (vilka coins: kort/lång perp + motsatt spot)
python3 -m research.funding_harvest --signal --lookback 12 --enter 0.0001 --mode both
# Full OOS-validering + hävstångssvep
python3 -m research.funding_harvest --fixed --lookback 12 --enter 0.0001 --mode both --leverage 1.0
```

### Viktiga varningar (ärligt)
- **Kräver perp-handel** (kort perp + spot) på en börs där du har tillgång; funding
  betalas var 8:e timme. Inte en ren spot-"daytrade".
- **Basisrisk & likvidation** vid hög hävstång: håll dig till 3–5x, håll spot som collateral.
- **Regimberoende nivå:** funding var rikare 2023–24 (bull) än 2025–26; avkastningen sjunker
  i lågfunding-regim men blev aldrig en förlustregim i testet.
- **Exekvering:** använd limit/maker där möjligt för att sänka kostnaden ytterligare.

---

## 1. Kort dom om TA/prisbaserade strategier (brutalt ärligt)

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

**Nya filer (funding-skörd — primär leverans):**
- `research/binance_vision.py` — nedladdare för Binance Vision (funding + perp/spot klines)
- `research/okx_data.py` — OKX-nedladdare (reserv; funding-historik ~90 d)
- `research/funding_harvest.py` — delta-neutral funding-skörd: simulator, hysteres-signal,
  train/OOS, walk-forward, hävstångssvep, `--signal` paper-läge
- `research/funding_harvest.json` (`research_funding_harvest.json`) — validerings-output
- `data/cache/vision_*` — nedladdad funding + priser (24 coins, gitignorerad cache)

**Nya filer (TA/cross-sectional-utforskning):**
- `research/daytrade_lab.py` — snabb numpy-simulator + dagsmått + kostnadsmodell
- `research/daytrade_strategies.py` — 5 TA-strategifamiljer + parametergrids
- `research/run_daytrade.py` — delade-parametrar train/OOS + walk-forward driver
- `research/cross_sectional.py` — marknadsneutral cross-sectional motor (+ signal/fixed/WF)
- `research/daytrade_best_params.json` — valda parametrar (nu funding-skörd som primär)
- `RAPPORT_DAYTRADING.md` — denna rapport
- Resultat-JSON: `research_daytrade_15m.json`, `research_daytrade_1h.json`,
  `research_xsect_8h.json`, `research_xsect_12h.json`, `research_xsect_1D.json`

**Filer säkerhetskopierade före omskrivning** (till `backups/2026-07-22/`):
`daytrade_best_params.json.bak`, `RAPPORT_DAYTRADING.md.bak`.

---

## 7. Rekommenderat nästa steg

1. **Prova funding-skörden i paper först** (§0): kör `--signal` var 8:e timme, bokför
   funding + basis + avgifter i minst **4–8 veckor** och jämför mot backtestets ~65 %
   vinstdagar och Sharpe ~5. Börja på **1x hävstång**, höj mot 3x först när paper-resultatet
   stämmer.
2. **Behåll prisstrategierna (TA/cross-sectional) på hyllan** — de saknar robust OOS-edge
   efter kostnader (§1–§5). Handla dem inte.
3. **Sänk friktionen** (maker/limit-orders, avgiftsnivå, BNB-rabatt) — varje sänkt bps går
   rakt in i funding-nettot och höjer träffsäkerheten ytterligare.
4. **Riskkontroll för live:** spot som collateral, håll hävstång ≤ 3–5x, övervaka
   likvidationsnivå och perp-vs-spot-basis; ha en kill-switch om basisen divergerar.
5. **Utöka edgen:** fler perps (30–50), vikta mot högst funding, och lägg till basis-/
   likvidations-signaler. Behåll train/OOS + walk-forward + fulla kostnader (finns i
   `research/`-labbet) för allt framtida arbete.
6. **Riktiga pengar** beslutar du — efter godkänd paper-forward. Merga inget innan du sagt OK.
