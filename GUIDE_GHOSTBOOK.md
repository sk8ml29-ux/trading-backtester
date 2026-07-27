# Ghost Book — Skuggboken

En ny strategi byggd på en variabel som ingen börs publicerar och ingen
dataleverantör säljer: **till vilka priser de öppna hävstångspositionerna
faktiskt köptes.**

---

## 1. Idén på tre meningar

En kryptobörs berättar hur *många* terminskontrakt som är öppna (open interest),
men aldrig till vilket *pris* de öppnades. Eftersom open interest är en sluten
pool som bara ändras genom öppningar och stängningar går det att pussla ihop
den saknade prisfördelningen ur femminutersflödet — och då ser man exakt vilka
positioner som ligger back och därmed vilka som snart tvingas ur marknaden.

Det som visade sig gå att tjäna pengar på är **överhänget**: hur långt över det
vanliga priset som mängden faktiskt lastade på sig sina positioner.

---

## 2. Hur rekonstruktionen går till

Varje femte minut observeras open interest `OI` och märkespriset `P`:

| Observation | Tolkning | Uppdatering av boken |
|---|---|---|
| `ΔOI > 0` | nya kontrakt öppnades | lägg till `ΔOI` i prisfacket vid `P` |
| `ΔOI < 0` | kontrakt stängdes | skala ned hela boken proportionellt |
| `ΔOI = 0` | inget nettoflöde | oförändrad |

Kör man det över fyra år får man `h[p]` — en skattning av fördelningen av
ingångspriser för alla just nu öppna kontrakt. On-chain-analys gör motsvarande
för spotinnehav och kallar det *realised price* eller *MVRV*. **Ingen publicerar
det för derivatens open interest**, av ett mycket praktiskt skäl: indata måste
sys ihop ur cirka 400 000 dagliga dumpfiler. Det finns ingen feed att köpa.

Ur boken faller sedan:

```
kostnadsbas = notionalviktat genomsnittligt ingångspris
överhäng(n) = ln(kostnadsbas) − ln(glidande medelvärde av priset över n timmar)
signal(n)   = −överhäng(n) / (volatilitet · √n)
```

Ett glidande medelvärde säger vad priset *brukade vara*. Kostnadsbasen säger vad
mängden *faktiskt betalade*, eftersom varje historiskt pris vägs med det
positionsflöde som gick igenom det — inte med hur lång tid som förflutit.

**Positivt överhäng = positionerna lastades på under uppgångsspikar och boken
bär nu en klump av långa positioner under vatten.** Sälj dem. Köp spegelbilden.

---

## 3. Varför edgen finns kvar

Kanten kommer av **tvingat flöde, inte av en prognos**. Positioner som öppnats
ovanför marknaden avvecklas för att marginalkravet kräver det, inte för att
någon valt det. Sådant flöde är okänsligt för pris och överdriver rörelsen.

Tre skäl till att det inte redan är bortarbitrerat:

1. **Indata är obekvämt.** Ingen feed finns; det krävs en bulkpipeline mot
   historiska dumpfiler.
2. **Signalen är långsam.** Den ger inget till högfrekvenshandlare, vars
   infrastruktur är byggd för millisekunder.
3. **Den ser inte ut som något känt.** Korrelationen mot standardfaktorer är
   nära noll (se nedan), så den fastnar inte i vanliga faktormodeller.

---

## 4. Resultat

Universum: 440 USDT-terminer på Binance, urvalet gjort på **rullande likviditet
vid varje tidpunkt** ur en pool som även innehåller avlistade mynt, så ingen
överlevnadsbias. Kostnader: 5 bps taker + 3 bps spread per sida, plus
storleksberoende marknadspåverkan och verklig funding. Handel sker på *öppning
nästa timme* efter signalen.

| Period | Längd | CAGR | Sharpe | Sortino | Max DD | Calmar |
|---|---|---|---|---|---|---|
| In-sample (2022-07 → 2025-04) | 2,76 år | **+16,9 %** | 1,32 | 1,81 | −14,4 % | 1,17 |
| **Out-of-sample (2025-04 → 2026-06)** | 1,24 år | **+24,7 %** | **1,74** | 2,63 | −11,8 % | 2,08 |
| Hela perioden | 4,01 år | **+18,8 %** | 1,37 | 1,87 | −16,4 % | 1,15 |

**Per halvår — samtliga åtta perioder positiva:**

| Halvår | Avkastning | Sharpe |
|---|---|---|
| 2022 H2 | +12,8 % | 2,18 |
| 2023 H1 | +3,2 % | 0,64 |
| 2023 H2 | +0,3 % | 0,10 |
| 2024 H1 | +4,9 % | 1,08 |
| 2024 H2 | +13,8 % | 1,97 |
| 2025 H1 | +4,8 % | 0,72 |
| 2025 H2 | +32,8 % | 4,33 |
| 2026 H1 | +1,7 % | 0,34 |

**Per år (netto):** 2022 +10,9 %, 2023 +5,3 %, 2024 +25,3 %, 2025 +39,6 %,
**2026 t.o.m. juni −2,2 %.**

**Kostnadsuppdelning (årstakt):**

| Post | Bidrag |
|---|---|
| Bruttoavkastning | +29,6 % |
| Avgifter och spread | −8,9 % |
| Marknadspåverkan | −0,0 % |
| Funding (netto intäkt) | +0,3 % |
| **Netto** | **+18,8 %** |

### Robusthetstester

| Test | Utfall |
|---|---|
| **Nolltest** (signalen slumpblandad mellan mynt, 40 omgångar) | verklig Sharpe 1,74 mot noll­fördelning medel −4,07, max −2,20, **p = 0,000** |
| **Kostnadströskel** | lönsam upp till **~31 bps per sida** — nära fyra gånger antagandet |
| **Extra exekveringsfördröjning** | 0h: 1,74 · 4h: 1,60 · 8h: 1,50 · **24h: 1,44** |
| **Marknadskorrelation** | −0,22 |
| **Vinstkoncentration** | 292 mynt bidrar; utan de fem bästa dagarna kvarstår +16 % av +32 % |
| **Kausalitet** | verifierat att exekveringspriset är öppningen på nästa timme |

Att en hel extra dags fördröjning bara kostar 0,3 Sharpe är praktiskt viktigt:
strategin kan köras som ett dagligt batchjobb, inte som en latenskänslig tjänst.

### Att signalen faktiskt är ny

Korrelation mot etablerade faktorer:

| Faktor | Korrelation |
|---|---|
| Realiserad volatilitet | **0,03** |
| Glidande medelvärde-oscillator | −0,19 |
| Momentum | −0,08 |

Det avgörande testet: när överhänget renas från allt som glidande medelvärden,
momentum **och** realiserad volatilitet kan förklara, behåller residualen
prediktiv kraft med samma tecken både in-sample (t = 2,97) och out-of-sample
(t = 2,13). Det som blir kvar går inte att bygga ur en prisserie.

---

## 5. Vad som inte fungerade

Det här är minst lika viktigt som det som fungerade.

- **Min ursprungliga hypotes föll.** Jag byggde en tillståndsmaskin kring
  "instängda positioner": kombinera vem som är trängd med hur snabbt open
  interest kollapsar, och handla med det tvingade flödet först och emot det
  sedan. Den förlorade pengar i båda perioderna (Sharpe −3,3). Det som
  överlevde var den enkla överhängsvarianten.
- **Bokens spridning var förklädd volatilitet.** Den såg ut som den starkaste
  enskilda variabeln (IC −0,049) tills den renades mot realiserad volatilitet —
  då försvann allt (t = −1,49 in-sample, 0,08 out-of-sample).
- **Utjämning och handelströsklar förstörde strategin.** Båda höjde in-sample
  (Sharpe upp till 1,81) och sänkte out-of-sample till noll eller under. Klassisk
  överanpassning. Ingen av dem används.
- **336-timmarsfönstret failade.** Bra in-sample, kraftigt negativt
  out-of-sample. Uteslutet — men notera att det var dåligt in-sample också, så
  beslutet vilar inte på testperioden.
- **Long-only fungerar inte.** Korrelationen mot marknaden blir 0,97 och
  resultatet −11,5 % per år. Strategin *kräver* kortbenet. Den slår visserligen
  ett likaviktat index (−33,2 %), men det är klen tröst.

---

## 6. Det viktigaste förbehållet: universumets sammansättning

Strategin är **tvärsnittslig**. Den säger inte "det här myntet ska upp" utan
"den här bokens överhäng är värre än den där bokens". Den jämförelsen kräver ett
**heterogent** universum.

| Korg | Antal namn | Sharpe hela perioden | CAGR |
|---|---|---|---|
| Hela universumet | ~102 | **1,39** | +19,1 % |
| Topp 20 efter rullande likviditet | 20 | **1,34** | +31,1 % |
| Endast likvida och mogna (>1 år) | 41 | 0,79 | +12,1 % |
| Endast likvida och unga (<1 år) | 29 | 0,13 | −1,3 % |
| **Endast EES-reglerade instrument** | **~20** | **−0,08** | **−3,1 %** |

Två saker faller ut:

1. **Bredd är inte problemet.** Topp 20 efter rullande likviditet fungerar lika
   bra som hela universumet. Tjugo namn räcker.
2. **Sammansättningen är problemet.** När universumet delas efter ålder
   presterar *ingen* halva i närheten av blandningen. Kanten sitter i
   jämförelsen mellan olikartade böcker — dela upp den och den försvinner.

**Konsekvensen är obekväm:** det reglerade EES-utbudet består av just mogna
storbolagsmynt, alltså precis den homogena sammansättning där strategin inte
fungerar. Testad på den korgen ger den Sharpe −0,08 över fyra år.

**Strategin som den är validerad går därför inte att köra på det reglerade
EES-utbudet i dag.** Det utbudet växer snabbt — från 5 till 19 par på några
månader — så det kan ändras. Men just nu är det ärliga svaret nej.

---

## 7. Risker och begränsningar

- **2026 är svagt.** Första halvåret 2026 gav −2,2 %. Antingen normal variation
  eller början på en försämring — det går inte att avgöra ännu.
- **Kanten satt i de mest likvida namnen out-of-sample** (topp 40: Sharpe 2,46;
  namn 41–120: −0,62). Men *in-sample* var hela universumet bäst (1,32 mot
  0,94). Att smalna av till topp 40 vore att anpassa mot testperioden, så jag
  har låtit bli. Det är en öppen fråga och en verklig risk.
- **Sammansättningsberoendet i avsnitt 6 är den allvarligaste begränsningen.**
  Strategin behöver ett blandat universum och tappar hela kanten på en homogen
  storbolagskorg.
- **En vald parameter kvarstår.** Tidsfönstren {72h, 168h} valdes på in-sample-
  data. Ensemblen över två fönster gör valet mindre skört än ett enda, men det
  är inte parameterfritt.
- **Ett databeroende.** Allt vilar på att Binance fortsätter publicera
  femminuters open interest. Slutar de finns ingen ersättare.
- **Överfullt handelsfall.** Om många börjar handla samma sak försvinner kanten.
- **Backtest är inte live.** Se avsnitt 8.

---

## 8. Juridik för dig som bor i Sverige

Det här är en regulatorisk genomgång, inte individuell juridisk rådgivning.

- **Strategin är marknadsneutral och kräver blankning.** Utan kortbenet
  försvinner hela värdet — long-only ger korrelation 0,97 mot marknaden och
  −11,5 % per år. Det betyder derivat eller lånad spot; ren spothandel går inte.
- **Handel för egen räkning kräver normalt inget tillstånd** från
  Finansinspektionen så länge du inte förvaltar andras pengar, ger råd, agerar
  market maker eller bedriver verklig högfrekvenshandel. Den här strategin
  handlar en gång per dygn, vilket är långt från högfrekvens.
- **Men instrumenten är blockeringen, inte tillståndet.** Enligt avsnitt 6
  fungerar strategin inte på det utbud som i dag erbjuds reglerat inom EES.
  Att i stället nå globala perpetuals — särskilt via VPN — bryter mot börsens
  egna villkor och ställer dig utanför EES-regleringens skydd. Gör inte det.
- **Beskattning:** vinster är normalt kapitalinkomst med 30 % skatt. Förluster
  på kryptotillgångar är typiskt bara avdragsgilla till 70 %, vilket är en
  verklig kostnad för en strategi som handlar ofta. Dokumentera varje avslut
  löpande — cirka 100 positioner som omsätts dagligen blir en betydande
  deklarationsbörda. **Räkna på skatten innan du skalar upp.**

**Sammanfattat:** forskningen och paper-forward är oproblematiska att köra nu.
Riktiga pengar förutsätter att det reglerade EES-utbudet breddas till ett mer
heterogent instrumenturval — och att strategin då valideras om på just den
korgen.

---

## 9. Så kör du det

```bash
# dagens målportfölj
python run_ghostbook.py signal --capital 50000

# den frysta strategidefinitionen
python run_ghostbook.py spec

# hela valideringsbatteriet
python run_ghostbook.py validate --col gb_ohv_ens2 --rebal 24

# informationsanalys av alla variabler, med kontrollvariabler
python run_ghostbook.py study --split 2025-04-01

# svep över ombalanseringstakt och kostnadsnivå
python run_ghostbook.py sweep --score-col gb_ohv_ens2

# uppdatera datacachen (första körningen tar ungefär en och en halv timme)
python run_ghostbook.py fetch --what klines
python -m research.ghostbook.fetch_pool 64 metrics
```

**Paper-forward — gör detta innan riktiga pengar:**

```bash
python -m research.ghostbook.paper update --capital 50000   # dagligen
python -m research.ghostbook.paper report
```

`update` bokför gårdagens portfölj mot priser som blev kända först efteråt och
sätter sedan en ny målportfölj. Kurvan den bygger består alltså av äkta
framåtblickande observationer. Kör den **minst 6–8 veckor** och jämför utfallet
mot backtestets Sharpe innan du överväger riktigt kapital.

---

## 10. Kodkarta

| Fil | Ansvar |
|---|---|
| `research/ghostbook/vision_bulk.py` | trådad nedladdning av publika dumpar, parquet-cache |
| `research/ghostbook/positionmap.py` | **rekonstruktionen** och dess tillståndsvariabler |
| `research/ghostbook/panel.py` | likviditetsurval vid varje tidpunkt, kausal koppling signal→pris |
| `research/ghostbook/signals.py` | överhänget och kontrollvariablerna som kan falsifiera det |
| `research/ghostbook/ic.py` | informationskoefficienter med överlappskorrigerade t-värden |
| `research/ghostbook/backtest.py` | tvärsnittsmotor med avgifter, spread, påverkan och funding |
| `research/ghostbook/validate.py` | nolltest, block, koncentration, kostnad, fördröjning |
| `research/ghostbook/strategy.py` | den frysta strategidefinitionen |
| `research/ghostbook/live.py` | daglig signalgenerering ur färsk publik data |
| `research/ghostbook/paper.py` | paper-forward-bokföring |
| `run_ghostbook.py` | CLI |

Resultatfiler: `ghostbook_results.json`, `ghostbook_validation.json`.
