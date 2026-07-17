# AGENTS.md

## Cursor Cloud specific instructions

Python trading backtester + paper-trading bot + read-only Flask dashboard. Python 3.12, deps in `requirements.txt`. The startup update script creates a virtualenv at `.venv` and installs `requirements.txt`, so activate it before running anything:

```bash
. .venv/bin/activate
```

### Services / entry points
- **Backtester CLI** — `python run_backtest.py --symbol GC=F --entry-tf 1d --regime-tf 1d --strategy all` (see `README.md` / `INSTRUKTIONER.md` for all flags). Default `--entry-tf` is `30m`.
- **Paper-trading bot** — `python run_live.py --once --strategy macd_pullback --symbol GC=F` (paper only, no broker). Writes state/logs to `data/live/`.
- **Dashboard (web app)** — `python dashboard/app.py`, serves on `0.0.0.0:8080` (read-only view of `data/live/` state). Override port with `DASHBOARD_PORT`; set `DASHBOARD_PASSWORD` to require basic auth (open if unset).

### Non-obvious notes
- **Market data cache** lives in `data/cache/` (gitignored). It is pre-populated in the VM snapshot and also shipped as `data_cache_10y.tar.gz`. If a symbol/timeframe has <300 cached bars, the loader re-downloads live from Yahoo/Binance/Dukascopy (network egress works in this environment). No API keys are required for the default flows; `POLYGON_API_KEY` (in `.env`) is optional and only upgrades stocks intraday data (falls back to Yahoo without it).
- `run_live.py` uses `--timeframe` (not `--entry-tf`). Running the paper bot with `--timeframe 1d` (entry == regime timeframe) hits a pre-existing `KeyError: 'ema_slow'`; use the default `30m` entry / `1d` regime documented flow instead.
- There is **no formal lint config or pytest suite** (`scripts/test_*.py` are ad-hoc research scripts, not unit tests). Use `python -m compileall backtest strategies live dashboard config.py run_backtest.py run_live.py` as a quick syntax check.
- Dependency versions float (`pandas>=2.0`, `numpy>=1.24`); the code runs on current pandas 3.x / numpy 2.x.
- Per repo rule (`.cursor/rules/profitability-mandate.mdc`): back up any file you rewrite into `backups/YYYY-MM-DD/` before editing.
