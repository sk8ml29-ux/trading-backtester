# Jakten på en bättre strategi — resultat

**Uppdrag:** hitta en ny strategi med högre avkastning och utan döda år.

**Kort svar:** jag byggde och testade fyra nya strategifamiljer. Ingen slog den
befintliga. Men jag hittade orsaken till det döda året — och den går att fixa.
Resultatet är **CAGR 34,8 % istället för 18,8 %, och 2023 gick från +5 % till
+19 %**.

---

## 1. Vad som testades och föll

Fyra oberoende idéer, alla byggda på ny data: minutupplöst orderflöde
(uppdelning av aggressiv köp- och säljvolym) och terminspremien mot spotindex,
för 150 symboler över fyra år. Cirka 14 GB rådata.

### 1.1 Krasch-studsen — föll på kausalitetstestet

Hypotes: tvingade likvidationer pressar priset förbi rimlig nivå, och det
studsar tillbaka.

Först såg det lysande ut: efter fall på över 12 sigma studsade priset **+183
punkter** på en timme med 68 % träffsäkerhet.

Sedan mätte jag en **kausal** ingång — vid den första minut som faktiskt
utlöser signalen, istället för att medelvärdesbilda över hela kaskaden. Då blev
samma korg **−434 punkter** out-of-sample. Den första siffran förutsatte att man
visste när kraschen tog slut. Att köpa fallande knivar fungerar inte, och
mediandraget mot en innan någon studs kommer är −169 punkter.

### 1.2 Residualhandel med likviditetsfilter — dödades av kostnaderna

Hypotes: storbolagsmynt är i praktiken en tillgång med många tickers. Rensa bort
marknadsrörelsen och handla residualen — men bara när orderflödet och premien
visar att rörelsen var likviditetsdriven, inte informationsdriven.

Bruttosignalen fanns (Sharpe upp till 1,1), men omsättningen blev 1,3 gånger
portföljen per ombalansering. Vid retail-avgifter blir det **över 100 % i
kostnad per år**. Varje variant gav negativ nettoavkastning.

**Slutsats som gäller generellt:** frekvensvägen är stängd. Vid 8 punkter per
sida finns ingen intradagsstrategi som bär sina kostnader.

### 1.3 Mikrostruktursignaler på dagsbasis — föll på kontrolltestet

Tre signaler såg starka ut i både in-sample och out-of-sample:

| Signal | IC in-sample | IC out-of-sample |
|---|---|---|
| Genomsnittlig affärsstorlek | +0,040 | +0,071 |
| Terminspremiens nivå | −0,044 | −0,038 |
| Premiens instabilitet | −0,052 | −0,059 |

Sedan rensade jag bort allt som volatilitet, momentum, volym och prisnivå kan
förklara. **Ingen överlevde.** Affärsstorlek visade sig korrelera 0,84 med
handelsvolym — den var storleksfaktorn förklädd. Ärligt negativt resultat.

### 1.4 Kombination av flera motorer — spädde ut istället för att förstärka

De nya benen var inbördes korrelerade 0,6–0,75; de var samma
volatilitetsfaktor i olika förklädnad. Varje blandning sänkte resultatet:

| Portfölj | Sharpe |
|---|---|
| Enbart originalstrategin | **1,37** |
| + storleksben | 0,99 |
| + lågvolatilitetsben | 0,54 |
| + alla fyra | 0,32 |

---

## 2. Vad som faktiskt fungerade

Under sökandet hittade jag orsaken till det döda året, och den var inte att
kanten försvann.

**En strategi med fast positionsstorlek kör inte med fast risk.** Strategins
egen volatilitet varierar med en faktor tre mellan lugna och våldsamma
marknadsregimer. 2023 var ett lugnt år — kanten fanns kvar, men portföljen körde
på en tredjedel av sin normala risk och tjänade därefter.

Lösningen är ett **risktyrningslager**: skala exponeringen så att den
realiserade volatiliteten hålls nära ett mål. Det kräver ingen prognos, bara
strategins egen historik, och multiplikatorn för dag *t* räknas fram ur
avkastning till och med *t−1*.

### Resultat

| | Utan risktyrning | **Med risktyrning** |
|---|---|---|
| CAGR | 18,8 % | **34,8 %** |
| Sharpe | 1,37 | **1,46** |
| Största ras | −16,4 % | −22,8 % |
| Sämsta dag | −4,9 % | −7,5 % |
| Sämsta månad | −11 % | −16,1 % |

**Per år:**

| År | Utan | Med |
|---|---|---|
| 2022 (från juli) | +11 % | **+21 %** |
| 2023 | +5 % | **+19 %** |
| 2024 | +25 % | **+42 %** |
| 2025 | +40 % | **+65 %** |
| 2026 (till juni) | −2 % | −3 % |

Det döda året är borta. 8 av 9 halvår positiva, sämsta halvår −3,2 %.

Belåningen är måttlig: median 1,63x, 90:e percentilen 2,58x, tak 3x.

---

## 3. Vad du bör räkna med i verkligheten

Backtestsiffror är inte löften. Halvera dem som utgångspunkt:

- **Realistiskt: 15–20 % per år** efter kostnader, före skatt.
- **Efter svensk skatt: 11–14 %.**
- **Räkna med ras på 25–35 %** live. Med tre gångers belåning kan en dålig vecka
  kosta 15 %.
- **2026 är fortfarande negativt** i backtestet. Strategin är inte immun.

Risktyrning är ingen gratislunch: den ökar avkastningen genom att ta mer risk
när risken framstår som låg. Om volatiliteten hoppar upp snabbare än
45-dagarsfönstret hinner registrera, är portföljen för stor precis när det gör
mest ont. Det är den verkliga faran, och den syns i att sämsta månad går från
−11 % till −16 %.

---

## 4. Juridiken är oförändrad

Strategin är marknadsneutral och kräver kortbenet. Den behöver också ett
heterogent universum, vilket det reglerade EES-utbudet i dag inte erbjuder —
testad på just den korgen ger den Sharpe −0,08. Det hindret står kvar och löses
inte av risktyrning.

**Belåning gör dessutom instrumentfrågan skarpare, inte mildare.** Tre gångers
hävstång på ett oreglerat konto utan skydd mot negativt saldo är en helt annan
risk än samma strategi hos en reglerad motpart.

---

## 5. Kod

| Fil | Innehåll |
|---|---|
| `research/backdraft/data.py`, `vision.py` | minutdata: orderflöde, premieindex, spot |
| `research/backdraft/features.py`, `events.py` | krasch-studsstudien (1.1) |
| `research/backdraft/conditional.py` | betingad analys och kausalitetstestet |
| `research/backdraft/panel.py`, `residual.py` | residualhandeln (1.2) |
| `research/backdraft/daily.py` | mikrostruktursignalerna (1.3) |
| `research/backdraft/backtest.py` | tvärsnittsmotor för matrispanelen |
| **`research/backdraft/risk.py`** | **risktyrningslagret — det som fungerade** |

De misslyckade grenarna är kvar med flit. De visar vad som redan är uteslutet,
så att ingen behöver testa samma saker igen.

Resultat: `strategy_results.json`.
