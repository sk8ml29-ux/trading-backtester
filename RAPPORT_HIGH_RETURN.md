# Högre månadsavkastning — ärlig slutrapport

## Dom

Målet 6 % per månad motsvarar cirka 101 % per år med ränta-på-ränta. Två nya
lagliga, botkörbara mekanismer byggdes och testades. Båda förkastas för målet:

1. cross-venue perpetual funding saknade tillräcklig spread vid högst 2×,
2. flexibel EV-laddning gav stor besparing mot omedelbar laddning, men mindre än
   1 % inkrementellt mot en enkel billigaste-block-timer.

Ingen robust strategi i denna leverans stöder 6 % per månad. Att rapportera
annat skulle kräva efterhandsoptimering, svag benchmark eller hög hävstång.

## 1. Cross-venue funding: FAIL vid 2×

Mekanismen håller lika många coin-enheter i motsatta perpetualpositioner på
Binance och Hyperliquid.

Data:

- 15 gemensamma perpetualmarknader,
- 20 351 timvisa Hyperliquid-fundingposter per marknad,
- Binance Vision funding och perpetualpriser,
- gemensamt testfönster 2024-04–2026-06,
- full tvåbenskostnad och venue-basis-P&L.

Den förregistrerade 2×-strategin tog noll affärer: förväntad kvarvarande spread
täckte aldrig den konservativa rundturskostnaden.

Ett avsiktligt omöjligt övre tak—perfekt kunskap om nästa spread, de fem bästa
marknaderna varje settlement, inga avgifter och ingen basisrisk—gav:

| Mått | Perfekt övre tak vid 2× |
|---|---:|
| Genomsnitt per månad | 3,01 % |
| Median per månad | 2,60 % |
| Bästa månad | 6,89 % |
| Linjärt annualiserat | 36,95 % |

Det förkastar den förregistrerade 2×-versionen. Taket skalar matematiskt till
cirka 6 % median först runt 4,6×, men då fortfarande med perfekt förutseende,
noll kostnad och noll venue-risk. Det är inte en bevisad strategi och strider
mot robusthetsmålet. Resultat: `research_cross_venue.json`.

## 2. Flexibel svensk EV-laddning: FAIL mot stark benchmark

Botten:

- använder dagen-före-priser från Elpriset just nu,
- stöder timpris och kvartspris,
- normaliserar överlappande intervall vid vintertidsomställningen,
- levererar samma energi och avresetid,
- använder ingen V2G eller nätförsäljning,
- skapar endast paper-JSON och kan inte styra hårdvara.

Grundscenario: 15 kWh batterienergi, 90 % verkningsgrad, 11 kW, inkoppling
18:00 och avresa 07:00.

### Svag benchmark: ladda omedelbart

Mot omedelbar laddning sparade prisoptimeringen historiskt cirka:

- SE1: 19,35 % totalt,
- SE2: 18,93 %,
- SE3: 30,82 %,
- SE4: 34,24 %.

Detta visar värdet av lastflytt, men inte värdet av en avancerad bot.

### Stark benchmark

Primär benchmark är nu den billigaste av:

1. ladda omedelbart,
2. fast midnattstimer med säkerhetsfallback,
3. bästa sammanhängande laddblock med full priskännedom.

Mot denna benchmark gav den avancerade icke-sammanhängande optimeringen:

| Zon | Median månadsbesparing | Total inkrementell besparing |
|---|---:|---:|
| SE1 | 0,26 % | 154 kr |
| SE2 | 0,29 % | 151 kr |
| SE3 | 0,32 % | 164 kr |
| SE4 | 0,50 % | 262 kr |

Ingen av 44 kompletta månader nådde 6 %. Produkten klarar därför inte målet och
motiverar normalt inte extra komplexitet jämfört med en bra timer.

Resultat: `research_energy_se1.json` … `research_energy_se4.json`.

## Vad mer data skulle ändra

Mer generell pris- eller fundinghistorik löser inte de två mekanismernas
ekonomi:

- cross-venue-edgen är för liten relativt kostnad vid robust hävstång,
- EV-botens extra värde utöver bästa sammanhängande block är för litet.

Följande data kan däremot öppna **nya** mekanismer:

1. kundens verkliga effekttariff, baslast och laddsessioner—kan skapa värde genom
   effekttoppskontroll, inte bara spotpris,
2. synkrona orderboks- och filltapes för prediction-market complete-set
   arbitrage,
3. faktisk tillgång, kapitalgränser och jurisdiktion för tillåtna venues,
4. kommersiella data för laglig marketplace-/upphandlingsarbitrage.

Dessa är affärs- och åtkomstdata, inte något som kan ersättas med fler vanliga
OHLC-priser.

## Laglighet och säkerhet

- Marknadsmodulen använder bara publika API:er och skickar inga order.
- Energimodulen skriver bara paper-scheman och saknar OCPP/Modbus/device-adapter.
- Ingen geoblockering kringgicks.
- Riktig derivathandel kräver tillåten venue, KYC, skatt och jurisdiktionskontroll.
- Riktig laststyrning kräver kundens tariff, laddboxvillkor, huvudsäkring och
  lokal säkerhetsöverstyrning.

## Reproduktion

```bash
python -m research.cross_venue_funding --out research_cross_venue.json
python -m energy.flexible_load --zone SE3 --out research_energy_se3.json
python -m energy.sensitivity --out research_energy_sensitivity.json
```

Maskindomen är `production_ready: false` eller `research_gate_pass: false` för
6 %-målet.
