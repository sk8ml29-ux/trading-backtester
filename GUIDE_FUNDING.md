# Guide: Funding-skörden — förklarad för dig som lär dig

Den här guiden förklarar **utan kodspråk** vad strategin gör, vad "boken" betyder,
och hur du (senare, på en riktig börs) skulle lägga ordrarna. Läs lugnt — inget
måste göras idag.

---

## 1. Vad går strategin ut på? (den enkla versionen)

På krypto-börser finns två sätt att äga en coin:

- **Spot** = du äger den riktiga coinen (som att köpa en aktie).
- **Perp** (perpetual future) = ett kontrakt som följer priset, där man kan
  satsa både upp (lång) och ner (kort).

Var 8:e timme betalas en avgift mellan de som är långa och korta i perp — den
kallas **funding**. Ofta betalar de långa till de korta.

**Idén:** äg coinen i spot OCH var kort samma coin i perp, lika mycket.
Då spelar det ingen roll om priset går upp eller ner (de tar ut varandra —
"marknadsneutralt"). Kvar blir bara **funding-avgiften du håvar in var 8:e timme.**
Det är en lugn, jämn inkomst — inte en chansning på priset.

> Tänk: du hyr ut din position och får betalt varje 8:e timme, oavsett väder.

---

## 2. Vad betyder "boken"?

När du kör verktyget får du en lista, t.ex.:

```
DOGEUSDT   SHORT perp / LÅNG spot   vikt=0.11
ATOMUSDT   LÅNG perp / SHORT spot   vikt=0.11
```

Rad för rad betyder det:

- **SHORT perp / LÅNG spot** = köp coinen i spot, och blanka (short) lika mycket i perp.
  (Detta gör man när funding är **positiv** — då får man betalt för att vara kort perp.)
- **LÅNG perp / SHORT spot** = tvärtom. (När funding är **negativ**.)
- **vikt** = hur stor del av kapitalet den positionen ska ha (se räkneexemplet nedan).

Om en coin **inte** står i listan → du ska inte ha någon position i den just nu.

---

## 3. Vad behöver du för att göra det på riktigt? (senare)

- Ett konto på en krypto-börs som har **både spot och perp** (t.ex. en av de större).
- Möjlighet att blanka perp (det är standard på perp-marknaden).
- **Börja i demo/paper-läge** på börsen om det finns, eller använd vårt paper-verktyg
  (se avsnitt 6). Rör inte riktiga pengar förrän du sett att det fungerar i veckor.

Jag (assistenten) rör aldrig dina pengar, nycklar eller börskonton — det bestämmer du.

---

## 4. Räkneexempel — en position, steg för steg (utan hävstång, 1x)

Säg att du har **100 000** och boken har **9 coins**, var och en med vikt ≈ 0,11.

Storlek per coin = vikt × kapital = 0,11 × 100 000 = **11 000** per coin.

För raden `DOGEUSDT SHORT perp / LÅNG spot`:

1. **Köp DOGE i spot för 11 000.** (Du äger nu DOGE.)
2. **Blanka (short) DOGE i perp för 11 000.** (Lika stort belopp.)

Klart. Nu är du marknadsneutral i DOGE och håvar in funding var 8:e timme.
Gör samma sak för varje rad i boken (köp spot + short perp, eller tvärtom).

> Summan av alla spot-köp ≈ ditt kapital vid 1x. Enkelt och säkert att börja med.

---

## 5. Hur ofta gör man något?

- Funding betalas **var 8:e timme** (00:00, 08:00, 16:00 UTC).
- Kör verktyget en gång om dagen (eller var 8:e timme). Om boken är **oförändrad**
  gör du **ingenting** — du bara låter positionerna ligga och samla funding.
- Byter en coin sida, eller åker ut/in ur listan → justera just den positionen.
  (Strategin är byggd för att byta **sällan**, så kostnaderna hålls nere.)

---

## 6. Testa live UTAN pengar (gör detta först!)

Vårt paper-verktyg följer strategin i realtid med låtsaspengar och riktig funding:

```bash
cd ~/trading-backtester
bash run_funding.sh paper 100000 5     # startar ett paper-konto: 100 000, hävstång 5x
```

Kör sedan samma rad **var 8:e timme eller en gång om dagen**. Verktyget:
- hämtar den funding som faktiskt betalats,
- räknar löpande vinst/förlust,
- visar en kurva som växer fram.

Låt det gå i **minst 4–8 veckor**. Om kurvan pekar uppåt ungefär som backtestet
(lugnt uppåt, små hack) → då först kan man börja fundera på riktiga pengar.

Tips: vill du att det ska fortsätta även när du stänger datorn:
```bash
cd ~/trading-backtester
nohup bash run_funding.sh paper > paper.log 2>&1 &
```

---

## 7. Hävstång (leverage) — kraftfullt men farligt

Hävstång 5x betyder att alla positioner blir 5× större → 5× vinsten, men också
5× risken. Eftersom strategin är marknadsneutral tål den ganska mycket, men:

- **Börja alltid på 1x** i paper. Höj mot 3–5x först när du förstår det och
  paper-testet stämmer.
- Vid hävstång kan en position **likvideras** (tvångsstängas) om något går fel —
  håll koll och överdriv inte.
- Jaga inte max hävstång för att nå en viss dagssiffra; det extra du vinner kommer
  med oproportionerligt mycket risk.

---

## 8. Ärliga risker (läs detta)

- **Backtest ≠ verklighet.** Räkna med lägre avkastning live än i siffrorna.
- **Funding varierar** med marknadsläget — vissa perioder är magrare.
- **Börsrisk:** dina pengar ligger på en börs; det finns en liten risk för
  frysning/konkurs. Sprid inte allt på ett ställe.
- **Två ben måste hållas ihop:** om du bara har spot eller bara perp är du INTE
  neutral längre — då spelar priset roll igen. Håll alltid båda.

---

## 9. Sammanfattning (spara denna)

1. Kör `bash run_funding.sh paper 100000 5` var 8:e timme i några veckor.
2. Titta att kurvan växer lugnt uppåt.
3. Lär dig lägga en delta-neutral position (köp spot + short perp) i demo.
4. Först därefter, och bara om du vill: små riktiga pengar på 1x.
5. Fråga mig när du kör fast — jag förklarar om och om igen, i din takt.
