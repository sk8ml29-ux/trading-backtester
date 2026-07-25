# Guide: OKX DEMO-boten (låtsaspengar, steg för steg)

Den här boten lägger funding-ordrar **automatiskt på OKX demo trading** — alltså
med **fejkpengar**. Den kan aldrig röra riktiga pengar. Följ stegen lugnt.

---

## Vad boten gör
För varje coin i "boken" lägger den två ordrar som tar ut prisrörelsen:
- Positiv funding → **köp spot + shorta perp**
- Negativ funding → **köp perp + shorta spot**

Sedan håvar demo-kontot in funding var 8:e timme. Allt loggas.

## Säkerhet (inbyggt)
- Standardläge är **dry-run**: boten *visar* ordrarna men skickar inget.
- Riktiga demo-ordrar skickas bara i `exec` med ordet `yes`.
- Den pratar bara med OKX **demo** (skickar en flagga som gör allt till fejk).
- Hårda gränser: max antal ben och max storlek per order, annars stopp.

---

## Steg 1 — Skapa OKX demo-nycklar (engång)
1. Logga in på OKX i webbläsaren.
2. Gå till **Demo trading** (ibland kallat "Demo" högst upp).
3. Skapa **API-nycklar för demo** (inte riktiga nycklar!). Du får tre saker:
   - API key
   - Secret key
   - Passphrase (ett lösenord du väljer)
4. Ge nyckeln rätt att **handla** (Trade), men **inte** ta ut pengar.

## Steg 2 — Lägg in nycklarna på servern (engång)
På din server, skriv (byt ut texten mot dina egna värden):
```bash
export OKX_API_KEY="din_demo_api_key"
export OKX_API_SECRET="din_demo_secret"
export OKX_API_PASSPHRASE="din_demo_passphrase"
```
Tips: vill du slippa skriva det varje gång, lägg de tre raderna sist i filen
`~/.bashrc` så laddas de automatiskt när du loggar in.

## Steg 3 — Testa anslutningen (säkert)
```bash
cd ~/trading-backtester
bash run_funding.sh bot test
```
Ska visa "Når OKX" och att kontot kunde läsas (om nycklarna är rätt).

## Steg 4 — Se orderplanen utan att skicka något (säkert)
```bash
bash run_funding.sh bot dry 10000 1
```
- `10000` = demo-kapital att räkna på, `1` = hävstång.
- Boten visar exakt vilka ordrar den *skulle* lägga. Inget skickas.

## Steg 5 — Lägg ordrarna på demo (fejkpengar)
När planen ser vettig ut:
```bash
bash run_funding.sh bot exec 10000 1 yes
```
Ordet `yes` på slutet krävs för att faktiskt skicka. Nu läggs ordrarna på OKX demo.

## Steg 6 — Följ upp
```bash
bash run_funding.sh bot status      # visar demo-positioner + botens bok
```
Kör `bot exec ... yes` igen var 8:e timme (eller dagligen) så håller boten boken
uppdaterad. Vill du stänga allt på demo:
```bash
bash run_funding.sh bot close yes
```

## (Valfritt) Kör automatiskt var 8:e timme
Lägg till en rad i crontab (`crontab -e`):
```
0 */8 * * * cd ~/trading-backtester && OKX_API_KEY=... OKX_API_SECRET=... OKX_API_PASSPHRASE=... bash run_funding.sh bot exec 10000 1 yes >> ~/bot.log 2>&1
```

---

## Ärliga påminnelser
- Detta är **demo** — inga riktiga pengar, ingen riktig vinst. Syftet är att se att
  boten lägger rätt ordrar och att strategin beter sig som väntat, i veckor.
- Kontot i OKX demo bör stå i **one-way/net-läge** för positioner (standard oftast).
- Börja på hävstång **1**. Höj först långt senare, och aldrig utan att förstå risken.
- Riktiga pengar är ett separat, senare beslut som bara du fattar.
