"""Tradeable symbols grouped by asset class (Yahoo Finance tickers)."""

from __future__ import annotations

UNIVERSE: dict[str, list[str]] = {
    "commodities": [
        "GC=F", "SI=F", "CL=F", "NG=F", "HG=F",
        "PL=F", "PA=F", "ZC=F", "ZS=F",
    ],
    "forex": [
        "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X",
        "USDCHF=X", "NZDUSD=X", "EURGBP=X", "EURJPY=X",
    ],
    "indices": [
        "^GSPC", "^NDX", "^DJI", "^RUT", "^IXIC",
    ],
    "etfs": [
        "SPY", "QQQ", "TLT", "GLD", "IWM", "DIA", "SLV", "USO", "XLE", "EEM",
        "SMH", "XBI", "MDY", "XLI", "XLK", "XLF", "XLV", "XLY", "XLP", "XLU",
        "VTI", "VOO", "EFA", "VWO", "HYG", "LQD", "VNQ",
    ],
    "stocks": [
        "AAPL", "MSFT", "NVDA", "TSLA", "AMZN",
        "GOOGL", "META", "AMD", "NFLX", "JPM", "COST", "BA", "DIS",
        "AVGO", "WMT", "CAT", "V", "MA", "HD", "UNH", "JNJ", "PG", "KO",
        "PEP", "MCD", "XOM", "CVX", "AMAT", "QCOM", "INTC",
    ],
    "crypto": [
        "BTC-USD", "ETH-USD", "SOL-USD",
        "XRP-USD", "ADA-USD", "DOGE-USD", "LINK-USD",
    ],
}


def all_symbols() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for symbols in UNIVERSE.values():
        for s in symbols:
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


def category_for(symbol: str) -> str:
    for cat, symbols in UNIVERSE.items():
        if symbol in symbols:
            return cat
    return "other"


def symbol_count() -> int:
    return len(all_symbols())
