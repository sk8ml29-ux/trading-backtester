# Alla historiska Binance-symboler — bulkguide

Massflödet använder endast officiella Binance Vision-arkiv. Det hittar även
avnoterade kontrakt och bygger ett point-in-time-register, vilket minskar
survivorship bias.

## Live-katalog 2026-07-29

- 791 historiska USDT-perpetualsymboler.
- 142 klassade som historiska eller avnoterade.
- Core 2020-01–2026-06: 37 908 månadsjobb.
- Grov lagring raw + merged: 2,03 GB.
- Ledigt på aktuell maskin: cirka 234 GB.

Katalogen kan förändras när Binance publicerar nya eller äldre arkiv.

## Genomförd core-körning

Core 2020-01–2026-06 kördes färdigt i denna miljö:

- 37 811 filer nedladdade och verifierade,
- 2 redan existerande filer omverifierade,
- 95 officiellt saknade 8h-månader,
- 791 merged fundingserier,
- 790 merged 8h-perpserier,
- 4 108 069 merged rader,
- cirka 296 MB faktisk raw + merged lagring,
- endast `GAIBUSDT` saknar helt merged 8h-serie.

`complete_for_requested_jobs=false` är avsiktligt eftersom de 95 saknade
arkivmånaderna redovisas öppet. De verifierade serierna är användbara, men en
analys måste respektera luckorna.

## Datanivåer

| Tier | Data | Syfte | Storlek |
|---|---|---|---|
| `core` | funding + 8h perpetual | carry, basis, regimer | minst |
| `swing` | core + 1h | swing, trend, reversion | medel |
| `intraday` | swing + 15m | intraday/microstructure-proxy | störst |

`intraday` kräver explicit `--confirm-large-download`.

Aktuell plan efter färdig core:

- `swing`: 56 862 totaljobb, 19 049 återstår, grovt 6,62 GB totalt.
- `intraday`: 75 816 totaljobb, 38 003 återstår, grovt 22,51 GB totalt.

Storlekarna är konservativa schabloner; core blev betydligt mindre i praktiken.

## 1. Bygg symbolkatalog

```bash
. .venv/bin/activate
python -m intake.binance_bulk catalog --workers 12
```

Output:

```text
data/cache/binance_bulk/usdt_perp_lifecycle.json
```

Varje symbol får:

- första fundingmånad,
- sista fundingmånad,
- alla observerade månader,
- lagringsnyckel,
- `recent` eller `historical_or_delisted`.

## 2. Planera utan nedladdning

```bash
python -m intake.binance_bulk plan \
  --tier core --start 2020-01 --end 2026-06
```

Eller via huvudboten:

```bash
python -m intake.bot collect \
  --source binance-bulk \
  --bulk-tier core \
  --start 2020-01 --end 2026-06 \
  --plan-only
```

Planen visar jobb, datatyper, redan verifierade filer, återstående filer och
grov total lagring.

## 3. Hämta core-data för alla symboler

```bash
python -m intake.binance_bulk download \
  --tier core \
  --start 2020-01 --end 2026-06 \
  --workers 12 \
  --max-gb 20
```

Eller:

```bash
python -m intake.bot collect \
  --source binance-bulk \
  --bulk-tier core \
  --start 2020-01 --end 2026-06 \
  --workers 12 \
  --max-gb 20
```

Flödet:

1. hämtar officiell `.CHECKSUM`,
2. verifierar SHA-256,
3. skriver ZIP atomiskt,
4. sparar manifest,
5. hoppar över redan verifierade filer vid omstart,
6. stoppar om diskgränsen eller 5 GB säkerhetsreserv hotas.

## 4. Merge

Download-kommandot via `intake.bot` merge:ar automatiskt. Separat:

```bash
python -m intake.binance_bulk merge \
  --tier core --start 2020-01 --end 2026-06
```

Output:

```text
data/cache/binance_bulk/merged/funding/
data/cache/binance_bulk/merged/perp/8h/
data/cache/binance_bulk/merge_manifest.json
```

Klines tidsstämplas vid `close_time + 1 ms`, aldrig open time.

## 5. Lägg till 1h

Planera först:

```bash
python -m intake.binance_bulk plan \
  --tier swing --start 2020-01 --end 2026-06
```

Hämta:

```bash
python -m intake.binance_bulk download \
  --tier swing --start 2020-01 --end 2026-06 \
  --workers 12 --max-gb 40
```

Core-filerna verifieras och hoppas över.

## 6. Lägg till 15m

```bash
python -m intake.binance_bulk plan \
  --tier intraday --start 2020-01 --end 2026-06

python -m intake.binance_bulk download \
  --tier intraday --start 2020-01 --end 2026-06 \
  --workers 12 --max-gb 80 \
  --confirm-large-download
```

15m ger inte historisk orderbok eller riktiga fills. Det är fortfarande OHLCV.

## 7. Begränsade testkörningar

För fem symboler:

```bash
python -m intake.binance_bulk download \
  --tier core --start 2024-01 --end 2024-03 \
  --max-symbols 5 --workers 4 --max-gb 1
```

Specifika symboler:

```bash
python -m intake.binance_bulk download \
  --tier core --start 2020-01 --end 2026-06 \
  --symbols BTCUSDT,ETHUSDT,FTMUSDT
```

Avnoterade symboler behålls.

## 8. Status och resume

```bash
python -m intake.binance_bulk status
```

Manifest:

```text
data/cache/binance_bulk/download_manifest.json
```

Avbryt med Ctrl+C och kör samma kommando igen. En redan existerande ZIP används
bara om dess SHA-256 fortfarande matchar Binance checksum.

## 9. Point-in-time-universum

Researchkod kan använda:

```python
from intake.binance_bulk import load_catalog, symbols_at

catalog = load_catalog()
tradable_in_june_2022 = symbols_at(catalog, "2022-06")
```

Använd alltid `symbols_at(...)` för historiska universum. Att globba dagens
cachefiler återinför survivorship bias.

## Begränsningar

- Katalogstatus infereras från arkivets sista fundingmånad, inte ett livekonto.
- Nya namnbyten är separata kontrakt om ingen explicit aliasanalys görs.
- Monthly archives släpar den pågående månaden.
- OHLCV kan inte ersätta orderbok, partial fills eller verklig slippage.
- Alla filer ligger i git-ignorerad cache och måste säkerhetskopieras separat.
