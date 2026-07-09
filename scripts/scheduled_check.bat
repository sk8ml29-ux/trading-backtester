@echo off
REM Run once every 15 minutes via Windows Task Scheduler (no window needed)
cd /d "%~dp0.."
py run_live.py --optimized --mixed --portfolio --timeframe 30m --capital 20000 --risk 0.0075 --once >> data\live\scheduled_run.log 2>&1
