# Högre månadsresultat — två nya mekanismer

## Slutsats

Målet 6 % per månad motsvarar cirka 101 % per år med ränta-på-ränta. Jag fann
ingen robust, laglig marknadsneutral tradingstrategi som stöder det målet.

Jag byggde och falsifierade cross-venue funding mellan Binance och Hyperliquid.
Jag byggde därefter en fysisk svensk kostnadsbot. Den senare sparade mer än 6 %
per månad på den modellerade flexibla EV-laddningen, men resultatet är
**kostnadsbesparing, inte investeringsavkastning**.

## 1. Förkastad: cross-venue funding

Mekanismen håller motsatta perpetualpositioner på Binance och Hyperliquid och
försöker skörda skillnaden i funding utan riktningsexponering.

Data:

- 15 gemensamma perpetualmarknader.
- Publik Binance Vision-historik.
- 20 351 timvisa Hyperliquid-fundingposter per marknad.
- Gemensamt testfönster 2024-04–2026-06.
- Faktiska prisserier på båda venues och full tvåbenskostnad.

Den förregistrerade strategin tog noll affärer: den förväntade spreaden täckte
aldrig en konservativ komplett rundtur.

Ett avsiktligt omöjligt övre tak—perfekt kunskap om nästa realiserade spread,
de fem bästa marknaderna varje settlement, 2× pair-notional, inga avgifter och
ingen venue-basisrisk—gav:

| Mått | Perfekt övre tak |
|---|---:|
| Genomsnitt per månad | 3,01 % |
| Median per månad | 2,60 % |
| Bästa månad | 6,89 % |
| Annualiserat | 36,95 % |

Eftersom även detta omöjliga tak missar 6 % i median förkastas hela familjen för
målet. Högre hävstång skulle inte skapa edge, bara liquidation- och venue-risk.
Maskinresultat: `research_cross_venue.json`.

## 2. Godkänd kostnadsbot: flexibel svensk EV-laddning

Botten använder publicerade dagen-före-priser och flyttar exakt samma
laddenergi inom fönstret 18:00–07:00. Baseline laddar omedelbart vid inkoppling.

Grundscenario:

- 15 kWh till batteriet per dag.
- 90 % laddverkningsgrad.
- 11 kW laddbox.
- Ingen V2G eller nätförsäljning.
- Samma avresetid och levererad energi i baseline och strategi.
- Spot + 0,08 kr/kWh elhandelspåslag + 0,25 kr/kWh rörlig nätavgift +
  0,36 kr/kWh energiskatt, därefter 25 % moms.
- Historik 2022-11–2026-07; 44 kompletta månader i månadsgrinden.

| Zon | Median månadsbesparing | Andel månader ≥6 % | Minsta månad | Sparat i hela samplet |
|---|---:|---:|---:|---:|
| SE1 | 17,80 % | 97,73 % | 5,78 % | 6 460 kr |
| SE2 | 17,10 % | 97,73 % | 5,27 % | 6 287 kr |
| SE3 | 29,25 % | 100 % | 10,53 % | 13 475 kr |
| SE4 | 34,43 % | 100 % | 14,23 % | 17 385 kr |

Procenten gäller **EV-laddningens rörliga kostnad**, inte hela hushållsfakturan
och inte avkastning på investerat kapital.

### Känslighet

Fasta scenarier finns i `research_energy_sensitivity.json`.

- SE3, 15 kWh, långsam 3,7 kW-laddning kl. 18: median 24,26 %.
- SE3, 15 kWh, 7,4 kW och sen inkoppling kl. 21: median 17,45 %.
- SE3, 30 kWh, 7,4 kW kl. 18: median 24,26 %.
- Hårt begränsat fall, 30 kWh, 3,7 kW först kl. 21: median 1,32 % och FAIL.

Resultatet är alltså inte universellt. Det kräver verklig flexibilitet mellan
inkoppling och avresa.

## Bot och säkerhet

`energy.flexible_load`:

- stöder timpris före 2025-10-01 och kvartspris därefter,
- hanterar svensk lokal tid och sommar-/vintertid,
- levererar identisk energi som baseline,
- skapar SHA-256-pinnade paper-scheman,
- skickar inga kommandon till laddbox eller bil.

Exempel:

```bash
python -m energy.flexible_load --zone SE3 \
  --schedule-date 2026-07-27 \
  --schedule-out research_energy_schedule_sample.json
```

`research_energy_schedule_sample.json` visar ett verkligt paper-schema med
16,667 kWh nätenergi, prisintervall och snapshot-hash.

## Vad som krävs för verklig besparing

Den generiska historiken är tillräcklig för mekanismen. För kundspecifik
produktion behövs affärs-/hårdvarudata:

1. priszon och daterat elhandelsavtal,
2. nätägarens rörliga avgift och eventuell effekttariff,
3. bilens verkliga energibehov, inkoppling och avresa,
4. laddboxmodell och tillåtet lokalt API, OCPP eller Modbus,
5. huvudsäkring och annan samtidig fastighetslast.

Utan dessa uppgifter ska botten förbli paper-only. En lokal laddbox/BMS och
huvudsäkringsvakt måste alltid kunna överstyra programvaran.

## Laglighet och datakälla

Spotpriser hämtas från det öppna API:t hos
[Elpriset just nu](https://www.elprisetjustnu.se/elpris-api), som uttryckligen är
fritt att använda. Lastflytt bakom kundens mätare är normal
energikostnadsoptimering. Verklig styrning måste följa elavtal,
nätägarvillkor, laddboxens API-villkor och elsäkerhetskrav.

## Ärlig status

- Cross-venue trading: **FAIL** för 6 % per månad.
- EV-kostnadsbot: **PASS** för >6 % medianbesparing i grundscenariot i alla fyra
  zoner.
- Produktionsklar: **nej**, tills kundens verkliga tariff, last och hårdvara
  har paper-validerats.
