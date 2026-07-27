# SMRC — originell carrystrategi

## Resultat

Settlement Memory Reserve Carry (SMRC) blev historiskt lönsam efter konservativa
tvåbenskostnader i post-training-perioden 2023-07-01–2026-06-30. Perioden
inspekterades under utvecklingen och är därför **inte strikt orörd OOS**.

| Mått | Normal kostnad | Dubbel kostnad |
|---|---:|---:|
| Nettoavkastning | +19,44 % | +8,45 % |
| Annualiserad avkastning | +6,48 % | +2,82 % |
| Sharpe | 4,97 | 2,13 |
| Max drawdown | −0,32 % | −1,04 % |
| Sämsta dag | −0,17 % | −0,60 % |
| BTC-beta | 0,0019 | 0,0019 |
| Största coins andel av positiva vinster | 16,2 % | 24,6 % |

Normalmodellen klarar den adaptiva historiska grinden. Dubbelkostnadstestet är
fortfarande positivt men klarar varken kravet 5 % per år eller fyra aktiva
segment.

Strategin är **inte produktionsklar**. De sista två halvårssegmenten gjorde inga
affärer eftersom finansieringsreserven var för låg. Det är avsiktlig
kapitalbevaring, men innebär att strategin för närvarande inte ger löpande
inkomst.

## Den nya mekanismen

Vanlig funding carry tittar på senaste eller genomsnittlig funding. SMRC beräknar
i stället en punkt-i-tid-överlevnadstabell för positiva fundingstreaks:

1. Efter varje settlement skattas sannolikheten att streaken överlever nästa
   settlement, med Beta-krympning för små stickprov.
2. Sannolikheterna multipliceras till ett förväntat antal återstående
   betalningar.
3. Förväntad kvarvarande funding och högst 50 % av positiv basis-konvergens
   jämförs med en komplett rundtursreserv på 0,30 %.
4. Bara positiv carry används: lång spot och kort perpetual. Strategin behöver
   aldrig låna och blanka spot.
5. Högst sex positioner hålls. Nya positioner rangordnas på förväntad reserv
   efter ett kausalt 30-dagars likviditetsfilter.
6. Portföljen kör 1,5× pair-notional. Två ben ger cirka 3× nominell
   bruttoexponering när alla slots är fyllda.

Det går inte att bevisa att ingen annan person någonsin tänkt samma idé.
Nyhetskontrollen hittade forskning om fundingpersistens och vanlig
cross-sectional carry, men inte denna kombination av en expanderande
streak-hazard, återstående-carry-reserv, basis-kredit och hård tvåbensgrind.

## Validering

- Data: publika Binance Vision-dumpar, 24 USDT-marknader.
- Parameterurvalets cutoff var 2023-06-30. Post-training började 2023-07-01,
  men perioden är inte orörd OOS eftersom den inspekterades under utvecklingen.
- Sex fasta halvårssegment; ingen parameterändring mellan segment.
- Funding vid settlement `t` kan inte tjänas av en position öppnad efter `t`.
- Klines indexeras när de är stängda, inte när de öppnas.
- Ledgern håller faktiska spotenheter och perp-kontrakt utan gratis
  återhedgning.
- Kostnad: 0,15 % per öppning och 0,15 % per stängning för de två benen.
- Verklig basis-P&L, driftande fundingnotional och terminal stängningskostnad
  bokförs.
- Resultat: `research_smrc_oos.json` och `research_smrc_cost2x.json`.

## Begränsningar

1. Post-training-resultatet är adaptiv historisk evidens, inte strikt OOS.
2. Den statiska symbolkorgen innehåller överlevande kontrakt och saknar
   historiskt avnoterade marknader. Det lämnar survivorshiprisk.
3. Kline-close är inte en orderbok. Samtidig fill av två ben, latency,
   marginalregler, liquidation och börsdefault kan ge sämre liveutfall.
4. Vinsten var regimberoende och koncentrerad till 2023–2024. De två sista
   halvårssegmenten var inaktiva.
5. 1,5× är hävstång. Den låga historiska drawdownen eliminerar inte tailrisk.
6. Paper-forward på OKX är en annan venue än Binance-backtestet. Det är ett
   avsiktligt portabilitetstest, inte exakt replikering.

## Laglighet och drift

Paper-boten använder endast publika API:er och låtsaspengar och skickar aldrig
order. Strategin manipulerar inte marknaden, front-runnar inte och använder
ingen insiderinformation.

Riktig perpetual-handel är bara tillåten om användarens jurisdiktion, vald börs
och kontotyp tillåter derivatet. Skatt, KYC, rapportering och börsens villkor
måste följas. Ingen livekoppling ingår och inga riktiga pengar ska användas före:

1. minst 12 veckor paper-forward,
2. positivt netto efter observerade spreadar/fills,
3. verifierad legal tillgång till en lämplig reglerad venue,
4. separat beslut om kapital och risk.

## Körning

```bash
. .venv/bin/activate

python -m research.settlement_memory_carry --out research_smrc_oos.json
python -m research.settlement_memory_carry \
  --cost-multiplier 2 --out research_smrc_cost2x.json

python -m research.smrc_paper --init --capital 100000
python -m research.smrc_paper --update
python -m research.smrc_paper --show
```
