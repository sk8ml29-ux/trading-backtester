#!/usr/bin/env bash
# =============================================================================
#  run_funding.sh — ett enda kommando för funding-skörden (för icke-kodare)
#
#  Användning (kör FRÅN projektmappen):
#     bash run_funding.sh signal        # visar dagens bok (vad man ska köpa/sälja)
#     bash run_funding.sh signal 5      # samma, med hävstång 5
#     bash run_funding.sh validera      # kör hela backtestet (siffrorna)
#
#  Skriptet gör automatiskt:
#    1) installerar det som behövs (pandas/numpy)
#    2) laddar ner marknadsdatan EN gång om den saknas (kan ta 20-40 min)
#    3) kör strategin
# =============================================================================
set -e
cd "$(dirname "$0")"   # gå till projektmappen där skriptet ligger

echo "================================================================"
echo "  FUNDING-SKÖRD — startar"
echo "================================================================"

# --- 1. Beroenden -------------------------------------------------------------
if ! python3 -c "import pandas, numpy" 2>/dev/null; then
  echo "[1/3] Installerar pandas + numpy ..."
  pip3 install --break-system-packages -q pandas numpy || pip3 install -q pandas numpy
else
  echo "[1/3] Beroenden finns redan. OK."
fi

# --- 2. Marknadsdata (laddas ner en gång; hoppar över det som redan finns) ----
COUNT=$(ls data/cache/vision_funding_*.csv 2>/dev/null | wc -l | tr -d ' ')
if [ "${COUNT:-0}" -lt 50 ]; then
  echo "[2/3] Laddar ner marknadsdata (en gång — kan ta 20-40 min, ha tålamod) ..."
  # Grunduniversum
  python3 -m research.binance_vision --start 2023-01-01 --end 2026-06-30 --interval 8h
  # Bredare universum (samma som i v3-rapporten) — hoppar över filer som redan finns
  EXTRA="SEIUSDT,TIAUSDT,WIFUSDT,ORDIUSDT,RUNEUSDT,GALAUSDT,SANDUSDT,AXSUSDT,EOSUSDT,THETAUSDT,ALGOUSDT,XLMUSDT,ICPUSDT,IMXUSDT,GRTUSDT,ENAUSDT,WLDUSDT,PENDLEUSDT,DYDXUSDT,CRVUSDT,LDOUSDT,COMPUSDT,MKRUSDT,SNXUSDT,HBARUSDT,VETUSDT,MANAUSDT,CHZUSDT,FTMUSDT,STXUSDT,TRBUSDT,APEUSDT,GMTUSDT,FETUSDT,ENSUSDT,BLURUSDT,GMXUSDT,MASKUSDT,LRCUSDT,1INCHUSDT,ZRXUSDT,BATUSDT,KAVAUSDT,ROSEUSDT,CELOUSDT,QTUMUSDT,IOTAUSDT,WAVESUSDT,KSMUSDT,EGLDUSDT,FLOWUSDT,SKLUSDT,STORJUSDT,BANDUSDT,YFIUSDT,SUSHIUSDT,BALUSDT,RSRUSDT,DASHUSDT,XTZUSDT,NEOUSDT,IOSTUSDT,ENJUSDT,ZENUSDT,ANKRUSDT,CHRUSDT"
  python3 -m research.binance_vision --coins "$EXTRA" --start 2023-01-01 --end 2026-06-30 --interval 8h || true
else
  echo "[2/3] Marknadsdata finns redan ($COUNT coins). OK."
fi

# --- 3. Kör strategin ---------------------------------------------------------
MODE="${1:-signal}"
echo "[3/3] Kör: $MODE"
echo "----------------------------------------------------------------"
if [ "$MODE" = "validera" ] || [ "$MODE" = "champion" ]; then
  python3 -m research.funding_lab --champion --out research_funding_champion.json
else
  LEV="${2:-5}"
  python3 -m research.funding_lab --signal --leverage "$LEV"
fi
echo "----------------------------------------------------------------"
echo "KLART."
