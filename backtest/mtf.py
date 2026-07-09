from __future__ import annotations

import pandas as pd

from backtest.indicators import add_regime_columns
from config import BacktestConfig
from ta.trend import EMAIndicator


# Yahoo intraday history limits (approximate)
INTRADAY_LOOKBACK_DAYS = {
    "30m": 60,
    "1h": 729,
    "15m": 60,
    "5m": 7,
}


def load_entry_regime(
    symbol: str,
    entry_tf: str = "30m",
    regime_tf: str = "1d",
) -> tuple[pd.DataFrame, pd.DataFrame, BacktestConfig]:
    """Load entry + regime OHLCV and a base config for the pair."""
    from backtest.data_loader import fetch_ohlcv

    start = clamp_start_for_timeframe("2015-01-01", entry_tf, symbol)
    entry_df = fetch_ohlcv(symbol, entry_tf, start=start, refresh=False)
    if entry_tf == regime_tf:
        regime_df = entry_df
        effective_regime = entry_tf
    else:
        regime_df = fetch_ohlcv(symbol, regime_tf, start="2015-01-01", refresh=False)
        effective_regime = regime_tf
    cfg = BacktestConfig(
        symbol=symbol,
        timeframe=entry_tf,
        entry_timeframe=entry_tf,
        regime_timeframe=effective_regime,
    )
    return prepare_entry_frame(entry_df, cfg), regime_df, cfg


def clamp_start_for_timeframe(start: str, timeframe: str, symbol: str | None = None) -> str:
    """Clamp start to provider limits (Binance crypto has years; Yahoo ~60d on 15m)."""
    max_days = INTRADAY_LOOKBACK_DAYS.get(timeframe)
    if symbol:
        from backtest.providers.registry import provider_for_symbol
        prov_limit = provider_for_symbol(symbol, timeframe).intraday_limit_days(timeframe)
        if prov_limit is None:
            return start
        max_days = prov_limit
    if not max_days:
        return start
    earliest = (pd.Timestamp.now() - pd.Timedelta(days=max_days - 1)).strftime("%Y-%m-%d")
    if pd.Timestamp(start) < pd.Timestamp(earliest):
        print(f"Note: {timeframe} data limited to ~{max_days} days. Start adjusted to {earliest}.")
        return earliest
    return start


def prepare_entry_frame(entry_df: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    """Entry OHLCV with local fast/slow EMAs (no regime merge yet)."""
    entry = entry_df.copy().sort_index()
    entry["ema_slow"] = EMAIndicator(
        close=entry["close"], window=config.regime_ema_slow
    ).ema_indicator()
    entry["ema_fast"] = EMAIndicator(
        close=entry["close"], window=config.regime_ema_fast
    ).ema_indicator()
    return entry


def build_mtf_dataset(
    entry_df: pd.DataFrame,
    regime_df: pd.DataFrame,
    config: BacktestConfig,
) -> pd.DataFrame:
    """
    Trading Rush style multi-timeframe setup:
    - Regime from higher TF (daily 9/200 EMA + ADX)
    - Entry signals on lower TF with local 200 EMA trend filter
    """
    entry = prepare_entry_frame(entry_df, config)
    return apply_regime_to_entry(entry, regime_df, config)


def apply_regime_to_entry(
    entry: pd.DataFrame,
    regime_df: pd.DataFrame,
    config: BacktestConfig,
) -> pd.DataFrame:
    """Merge daily regime onto entry bars using current ADX/trend settings."""
    daily = add_regime_columns(
        regime_df,
        ema_fast=config.regime_ema_fast,
        ema_slow=config.regime_ema_slow,
        adx_period=config.adx_period,
        adx_trend_threshold=config.adx_trend_threshold,
    )

    regime_lookup = daily[["regime"]].copy()
    regime_lookup.index = pd.to_datetime(regime_lookup.index)
    regime_lookup = regime_lookup.sort_index()

    entry_ts = entry.reset_index(names="timestamp")
    regime_ts = regime_lookup.reset_index(names="timestamp")
    if regime_ts.columns[0] != "timestamp":
        regime_ts = regime_ts.rename(columns={regime_ts.columns[0]: "timestamp"})

    entry_ts["timestamp"] = pd.to_datetime(entry_ts["timestamp"]).astype("datetime64[ns]")
    regime_ts["timestamp"] = pd.to_datetime(regime_ts["timestamp"]).astype("datetime64[ns]")

    merged = pd.merge_asof(
        entry_ts,
        regime_ts,
        on="timestamp",
        direction="backward",
    ).set_index("timestamp")

    for col in ("ema_slow", "ema_fast"):
        if col in entry.columns:
            merged[col] = entry[col].values

    merged["regime"] = merged["regime"].fillna("range")
    return merged
