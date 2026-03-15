"""Data loading and preprocessing helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd


def fetch_us_stock_data(symbol: str, adjust: str = "qfq") -> pd.DataFrame:
    """Fetch a daily US equity series from AkShare."""

    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "AkShare is required for market data downloads. Install the project dependencies first."
        ) from exc

    frame = ak.stock_us_daily(symbol=symbol, adjust=adjust).copy()
    if frame.empty:
        raise ValueError(f"No data was returned for symbol '{symbol}'.")

    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.set_index("date").sort_index()
    return frame


def build_price_frame(
    raw_frames: Mapping[str, pd.DataFrame],
    symbols: Sequence[str],
    start_date: str,
    end_date: str,
    price_column: str = "close",
    frequency: str = "B",
) -> pd.DataFrame:
    """Align price series on a business-day index and forward fill gaps."""

    series_map: dict[str, pd.Series] = {}
    for symbol in symbols:
        frame = raw_frames[symbol].loc[start_date:end_date]
        if frame.empty:
            raise ValueError(f"No data is available for symbol '{symbol}' in the requested date range.")

        series_map[symbol] = frame[price_column].astype(float)

    price_frame = pd.DataFrame(series_map).dropna(how="all")
    business_index = pd.date_range(price_frame.index.min(), price_frame.index.max(), freq=frequency)
    price_frame = price_frame.reindex(business_index).ffill().dropna()
    return price_frame


def load_pair_price_frame(
    dependent_symbol: str,
    independent_symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """Load and align the two price series used in the strategy."""

    symbols = (dependent_symbol, independent_symbol)
    raw_frames = {symbol: fetch_us_stock_data(symbol, adjust=adjust) for symbol in symbols}
    return build_price_frame(raw_frames, symbols, start_date=start_date, end_date=end_date)


def load_benchmark_returns(
    benchmark_symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> pd.Series:
    """Load benchmark prices and convert them to simple daily returns."""

    benchmark_frame = fetch_us_stock_data(benchmark_symbol, adjust=adjust).loc[start_date:end_date]
    if benchmark_frame.empty:
        raise ValueError(
            f"No benchmark data is available for symbol '{benchmark_symbol}' in the requested date range."
        )

    business_index = pd.date_range(benchmark_frame.index.min(), benchmark_frame.index.max(), freq="B")
    benchmark_prices = benchmark_frame["close"].astype(float).reindex(business_index).ffill().dropna()
    benchmark_returns = benchmark_prices.pct_change().rename("benchmark_ret")
    return benchmark_returns.dropna()


def prepare_log_price_frame(price_frame: pd.DataFrame) -> pd.DataFrame:
    """Transform prices into log prices while preserving column names."""

    if (price_frame <= 0).any().any():
        raise ValueError("Prices must be strictly positive before log transformation.")
    return np.log(price_frame)


def split_train_test(price_frame: pd.DataFrame, train_end_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a price frame into training and testing subsets."""

    train_frame = price_frame.loc[:train_end_date].copy()
    test_frame = price_frame.loc[train_end_date:].copy()

    if not test_frame.empty and not train_frame.empty and test_frame.index[0] == train_frame.index[-1]:
        test_frame = test_frame.iloc[1:].copy()

    if train_frame.empty or test_frame.empty:
        raise ValueError("The requested train/test split produced an empty subset.")

    return train_frame, test_frame
