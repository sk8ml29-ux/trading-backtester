# Data Intake Bot — steg för steg

Botten är strikt read-only mot finansiella konton och fysisk hårdvara. Den kan
samla publika data, kontrollera privata exporter, varna för hemligheter/PII och
skapa SHA-256-manifest. Den kan inte lägga order, flytta pengar, signera
wallet-transaktioner eller styra laddboxar.

## 1. Starta

```bash
cd /workspace
. .venv/bin/activate
python -m intake.bot init
```

Det skapar:

```text
data/cache/private_intake/
  user_constraints.json
  legal/
  trading/
  prediction/
  energy/
  marketplace/
  public/
  manifests/
```

Hela `data/cache/` är git-ignorerad.

## 2. Fyll i affärsgränser

Öppna:

```text
data/cache/private_intake/user_constraints.json
```

Fyll minst i:

- kapital,
- maximal drawdown,
- maximal hävstång,
- lagligt tillgängliga marknader och venues,
- förbjudna aktiviteter.

Låt `"paper_only": true` vara kvar.

## 3. Lägg in privata exporter

Placera redigerade kopior här:

```text
legal/       villkor och behörighetsunderlag
trading/     trades, funding, statements och avgifter
energy/      mätvärden, tariff och laddsessioner
marketplace/ köp, försäljning, frakt, returer och lager
prediction/  externa marknads-/trade-exporter
```

Ta bort namn, adress, personnummer, kontonummer och anläggnings-ID först.
Originalen bör sparas separat utanför repot.

## 4. API-nycklar

API-nycklar läggs endast i `/workspace/.env`.

Krav:

- read-only,
- inga withdrawals,
- inga transfers,
- ingen orderläggning,
- IP-whitelist om plattformen stöder det.

Botten visar bara om en miljövariabel finns; värdet skrivs aldrig ut.

## 5. Validera privata filer

```bash
python -m intake.bot validate
```

Resultat:

```text
data/cache/private_intake/validation_report.json
data/cache/private_intake/manifests/latest.json
```

Kontrollen omfattar:

- filtyp,
- CSV-rubriker och radantal,
- giltig JSON,
- SHA-256,
- sannolika API-hemligheter/private keys,
- e-post, personnummer och IBAN-varningar,
- symlänkar,
- obligatoriska affärsgränser.

`safe_to_analyze` måste vara `true`.

## 6. Hämta publik marknadsdata

Förhandsgranska utan nätverk:

```bash
python -m intake.bot collect --source markets \
  --coins BTC,ETH \
  --start 2024-04-01 --end 2026-06-30 \
  --dry-run
```

Hämta/läs cache:

```bash
python -m intake.bot collect --source markets \
  --coins BTC,ETH \
  --start 2024-04-01 --end 2026-06-30
```

Källor:

- Binance Vision,
- Hyperliquid public API.

Använd `--refresh` endast med hela önskade historikintervallet. En kort refresh
kan annars ersätta en längre lokal cache med ett kortare fönster.

## 7. Hämta svenska elpriser

```bash
python -m intake.bot collect --source energy \
  --zones SE1,SE2,SE3,SE4 \
  --start 2022-11-01 --end 2026-07-28
```

Data hämtas från Elpriset just nu och DST-normaliseras av energimodulen.

## 8. Spela in prediction-market-orderböcker

```bash
python -m intake.bot collect --source prediction \
  --prediction-limit 20
```

Det sparar:

- aktiv marknadsmetadata från Polymarket Gamma,
- publika YES/NO-orderböcker från CLOB,
- bästa asks,
- YES + NO före avgifter,
- fulla böcker och hämtningstid.

Ingen wallet eller API-nyckel används.

För historisk fill-realism måste kommandot köras återkommande; gamla samtidiga
orderböcker går inte att återskapa i efterhand.

Exempel med cron varje timme:

```cron
5 * * * * cd /workspace && .venv/bin/python -m intake.bot collect --source prediction --prediction-limit 100 >> data/cache/private_intake/cron.log 2>&1
```

## 9. Hämta allt

Förhandsgranskning:

```bash
python -m intake.bot collect --source all \
  --coins BTC,ETH \
  --zones SE3 \
  --start 2024-04-01 --end 2026-07-28 \
  --dry-run
```

Körning:

```bash
python -m intake.bot collect --source all \
  --coins BTC,ETH \
  --zones SE3 \
  --start 2024-04-01 --end 2026-07-28
```

## 10. Status

```bash
python -m intake.bot status
```

Status visar:

- senaste validering,
- om datan är säker att analysera,
- vilka miljövariabler som finns, aldrig deras värden,
- att order, withdrawals, wallet-signering och hårdvarustyrning är avstängda.

## Rekommenderad rutin

1. Exportera privata data månadsvis.
2. Kör `validate`.
3. Åtgärda alla `errors`.
4. Granska alla PII-`warnings`.
5. Samla prediction-orderböcker återkommande.
6. Kör marknads-/energiinsamling enligt datakällans publiceringstakt.
7. Börja strategiutvärdering först när `safe_to_analyze` är `true`.
