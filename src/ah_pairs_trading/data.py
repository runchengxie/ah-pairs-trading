"""Data loading and preprocessing helpers for A/H relative value research."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .cache import atomic_json_dump, atomic_pickle_dump, build_cache_key, load_pickle
from .config import BenchmarkMarket, DataConfig, PipelineConfig

_DATE_ALIASES = ("date", "日期", "datetime", "时间")
_CLOSE_ALIASES = ("close", "收盘", "最新价", "price", "value")
_OPEN_ALIASES = ("open", "开盘")
_HIGH_ALIASES = ("high", "最高")
_LOW_ALIASES = ("low", "最低")
_VOLUME_ALIASES = ("volume", "成交量", "vol")


@dataclass(slots=True)
class LoadedPairData:
    """Aligned market data required by the A/H pipeline."""

    aligned_prices: pd.DataFrame
    model_prices: pd.DataFrame
    benchmark_returns: pd.Series


def _external_benchmark_label(
    config: PipelineConfig,
    benchmark_frame: pd.DataFrame | None,
) -> str:
    if config.benchmark_symbol is not None:
        return config.benchmark_symbol
    if config.data.benchmark_csv_path is not None:
        return Path(config.data.benchmark_csv_path).stem
    if benchmark_frame is not None:
        return str(benchmark_frame.attrs.get("label", "benchmark"))
    return "benchmark"


def _find_column(frame: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    alias_map = {column.lower(): column for column in frame.columns}
    for alias in aliases:
        match = alias_map.get(alias.lower())
        if match is not None:
            return match
    return None


def _ensure_history_index(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.index, pd.DatetimeIndex):
        result = frame.copy()
        result.index = pd.to_datetime(result.index).tz_localize(None)
        return result.sort_index()

    date_column = _find_column(frame, _DATE_ALIASES)
    if date_column is None:
        raise ValueError("The input frame must include a date column or already use a DatetimeIndex.")

    result = frame.copy()
    result[date_column] = pd.to_datetime(result[date_column])
    result = result.set_index(date_column).sort_index()
    result.index = result.index.tz_localize(None)
    return result


def standardize_history_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw market-history frame into English column names."""

    indexed = _ensure_history_index(frame)
    column_mapping: dict[str, str] = {}

    close_column = _find_column(indexed, _CLOSE_ALIASES)
    if close_column is None:
        raise ValueError("The input frame does not contain a recognizable close-price column.")
    column_mapping[close_column] = "close"

    open_column = _find_column(indexed, _OPEN_ALIASES)
    high_column = _find_column(indexed, _HIGH_ALIASES)
    low_column = _find_column(indexed, _LOW_ALIASES)
    volume_column = _find_column(indexed, _VOLUME_ALIASES)
    if open_column is not None:
        column_mapping[open_column] = "open"
    if high_column is not None:
        column_mapping[high_column] = "high"
    if low_column is not None:
        column_mapping[low_column] = "low"
    if volume_column is not None:
        column_mapping[volume_column] = "volume"

    result = indexed.rename(columns=column_mapping)
    numeric_columns = [column for column in ("open", "high", "low", "close", "volume") if column in result.columns]
    if numeric_columns:
        result = result.astype({column: float for column in numeric_columns})
    return result


def standardize_fx_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize an FX history frame to a single `fx_rate` column."""

    if "fx_rate" in frame.columns:
        result = _ensure_history_index(frame)
        result["fx_rate"] = result["fx_rate"].astype(float)
        return result[["fx_rate"]]
    result = standardize_history_frame(frame)
    return result.rename(columns={"close": "fx_rate"})[["fx_rate"]]


def load_history_csv(path: str | Path) -> pd.DataFrame:
    """Load a local CSV file and normalize it with the shared parser."""

    frame = pd.read_csv(path)
    return standardize_history_frame(frame)


def load_fx_csv(path: str | Path) -> pd.DataFrame:
    """Load a local FX CSV file and normalize it."""

    frame = pd.read_csv(path)
    return standardize_fx_frame(frame)


def _missing_csv_error(path: str | Path, *, argument_name: str, hint: str) -> FileNotFoundError:
    return FileNotFoundError(
        f"Local CSV passed via `{argument_name}` was not found: {Path(path)}. "
        "The repository does not ship sample CSV files at that path. "
        f"{hint}"
    )


def _compact_date(date_str: str) -> str:
    return pd.Timestamp(date_str).strftime("%Y%m%d")


def _normalize_a_symbol_for_daily(symbol: str) -> str:
    if symbol.startswith(("sh", "sz", "bj")):
        return symbol
    if symbol.startswith(("6", "9")):
        return f"sh{symbol}"
    if symbol.startswith(("0", "2", "3")):
        return f"sz{symbol}"
    return symbol


def fetch_a_share_history(symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
    """Fetch A-share history via AkShare and return a normalized frame."""

    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "AkShare is required for online A/H market data downloads. Install project dependencies first."
        ) from exc

    try:
        frame = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=_compact_date(start_date),
            end_date=_compact_date(end_date),
            adjust=adjust,
        )
    except Exception:
        frame = ak.stock_zh_a_daily(symbol=_normalize_a_symbol_for_daily(symbol), adjust=adjust)

    result = standardize_history_frame(frame)
    return result.loc[start_date:end_date]


def fetch_h_share_history(symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
    """Fetch H-share history via AkShare and return a normalized frame."""

    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "AkShare is required for online A/H market data downloads. Install project dependencies first."
        ) from exc

    try:
        frame = ak.stock_hk_hist(
            symbol=symbol,
            period="daily",
            start_date=_compact_date(start_date),
            end_date=_compact_date(end_date),
            adjust=adjust,
        )
    except Exception:
        frame = ak.stock_hk_daily(symbol=symbol, adjust=adjust)

    result = standardize_history_frame(frame)
    return result.loc[start_date:end_date]


def _resolve_frame(
    supplied_frame: pd.DataFrame | None,
    csv_path: Path | None,
    fetcher,
    *fetch_args,
    csv_argument_name: str = "--*-csv",
    missing_file_hint: str = (
        "Point the argument at a real file, or remove the local CSV option to use automatic loading instead."
    ),
    cache_dir: Path | None = None,
    cache_namespace: str | None = None,
    cache_payload: dict[str, str | float | None] | None = None,
    refresh_cache: bool = False,
) -> pd.DataFrame:
    if supplied_frame is not None:
        return standardize_history_frame(supplied_frame)
    if csv_path is not None:
        if not Path(csv_path).exists():
            raise _missing_csv_error(csv_path, argument_name=csv_argument_name, hint=missing_file_hint)
        return load_history_csv(csv_path)

    if cache_dir is not None and cache_namespace is not None and cache_payload is not None:
        cache_key = build_cache_key(cache_payload)
        cache_root = Path(cache_dir) / "data" / cache_namespace
        artifact_path = cache_root / f"{cache_key}.pkl"
        metadata_path = cache_root / f"{cache_key}.json"
        if artifact_path.exists() and not refresh_cache:
            return load_pickle(artifact_path)

        frame = fetcher(*fetch_args)
        atomic_pickle_dump(frame, artifact_path)
        atomic_json_dump(cache_payload, metadata_path)
        return frame

    return fetcher(*fetch_args)


def _resolve_fx_frame(
    supplied_frame: pd.DataFrame | None,
    data_config: DataConfig,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    if supplied_frame is not None:
        return standardize_fx_frame(supplied_frame)
    if data_config.fx_csv_path is not None:
        if not Path(data_config.fx_csv_path).exists():
            raise _missing_csv_error(
                data_config.fx_csv_path,
                argument_name="--fx-csv",
                hint=(
                    "Point `--fx-csv` at a real HKD/CNY history file, or remove it and use "
                    "`--constant-fx-rate` for quick prototyping."
                ),
            )
        return load_fx_csv(data_config.fx_csv_path)
    if data_config.constant_fx_rate is not None:
        index = pd.date_range(start_date, end_date, freq="B")
        return pd.DataFrame({"fx_rate": float(data_config.constant_fx_rate)}, index=index)
    raise ValueError(
        "An FX series is required for A/H normalization. Provide `fx_frame`, `fx_csv_path`, or `constant_fx_rate`."
    )


def build_ah_price_frame(
    a_frame: pd.DataFrame,
    h_frame: pd.DataFrame,
    fx_frame: pd.DataFrame,
    start_date: str,
    end_date: str,
    share_ratio: float = 1.0,
) -> pd.DataFrame:
    """Align A, H, and FX histories on their common tradable dates."""

    a_history = standardize_history_frame(a_frame).loc[start_date:end_date]
    h_history = standardize_history_frame(h_frame).loc[start_date:end_date]
    fx_history = standardize_fx_frame(fx_frame).loc[start_date:end_date]

    if a_history.empty or h_history.empty:
        raise ValueError("The requested A/H date range produced an empty history frame.")

    aligned = pd.DataFrame(
        {
            "a_close": a_history["close"],
            "h_close_hkd": h_history["close"],
        }
    ).dropna()
    aligned = aligned.loc[aligned.index.intersection(a_history.index).intersection(h_history.index)]
    aligned = aligned.sort_index()

    fx_series = fx_history["fx_rate"].reindex(aligned.index).ffill()
    aligned = aligned.join(fx_series.rename("fx_rate"), how="left").dropna(subset=["fx_rate"])
    aligned["h_close_cny"] = aligned["h_close_hkd"] * aligned["fx_rate"] * float(share_ratio)
    aligned["ah_premium_pct"] = aligned["a_close"] / aligned["h_close_cny"] - 1.0
    return aligned


def prepare_model_price_frame(aligned_prices: pd.DataFrame, a_symbol: str, h_symbol: str) -> pd.DataFrame:
    """Extract the price series used in the cointegration model."""

    result = pd.DataFrame(
        {
            a_symbol: aligned_prices["a_close"].astype(float),
            h_symbol: aligned_prices["h_close_cny"].astype(float),
        }
    )
    return result.dropna()


def prepare_log_price_frame(price_frame: pd.DataFrame) -> pd.DataFrame:
    """Transform prices into log prices while preserving column names."""

    if (price_frame <= 0).any().any():
        raise ValueError("Prices must be strictly positive before log transformation.")
    return np.log(price_frame)


def prepare_signal_frame(
    model_prices: pd.DataFrame,
    a_symbol: str,
    h_symbol: str,
    intercept: float,
    hedge_ratio: float,
    z_window: int = 120,
    min_periods: int | None = None,
) -> pd.DataFrame:
    """Build rolling spread and z-score signals from A/H model prices."""

    if z_window < 2:
        raise ValueError("The rolling z-score window must be at least 2 observations.")

    minimum_periods = min_periods or z_window
    log_prices = prepare_log_price_frame(model_prices[[a_symbol, h_symbol]])
    spread = log_prices[a_symbol] - intercept - hedge_ratio * log_prices[h_symbol]
    rolling_mean = spread.rolling(z_window, min_periods=minimum_periods).mean()
    rolling_std = spread.rolling(z_window, min_periods=minimum_periods).std()
    zscore = ((spread - rolling_mean) / rolling_std).replace([np.inf, -np.inf], np.nan)

    signal_frame = model_prices[[a_symbol, h_symbol]].copy()
    signal_frame["spread"] = spread
    signal_frame["rolling_mean"] = rolling_mean
    signal_frame["rolling_std"] = rolling_std
    signal_frame["zscore"] = zscore
    signal_frame["cheap_leg"] = np.where(zscore > 0, "h", np.where(zscore < 0, "a", "flat"))
    signal_frame["pair_direction"] = np.where(
        zscore > 0,
        "short_a_long_h",
        np.where(zscore < 0, "long_a_short_h", "flat"),
    )
    return signal_frame


def convert_prices_to_returns(price_series: pd.Series, name: str = "benchmark_ret") -> pd.Series:
    """Convert a price series into simple daily returns."""

    returns = price_series.astype(float).pct_change().rename(name)
    return returns.dropna()


def split_train_test(price_frame: pd.DataFrame, train_end_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a frame into training and testing subsets."""

    train_frame = price_frame.loc[:train_end_date].copy()
    test_frame = price_frame.loc[train_end_date:].copy()

    if not test_frame.empty and not train_frame.empty and test_frame.index[0] == train_frame.index[-1]:
        test_frame = test_frame.iloc[1:].copy()

    if train_frame.empty or test_frame.empty:
        raise ValueError("The requested train/test split produced an empty subset.")

    return train_frame, test_frame


def _fetch_benchmark_history(
    benchmark_symbol: str,
    benchmark_market: BenchmarkMarket,
    start_date: str,
    end_date: str,
    data_config: DataConfig,
) -> pd.DataFrame:
    if benchmark_market == "a":
        return fetch_a_share_history(
            symbol=benchmark_symbol,
            start_date=start_date,
            end_date=end_date,
            adjust=data_config.a_adjust,
        )
    return fetch_h_share_history(
        symbol=benchmark_symbol,
        start_date=start_date,
        end_date=end_date,
        adjust=data_config.h_adjust,
    )


def load_ah_pair_data(
    config: PipelineConfig,
    *,
    a_frame: pd.DataFrame | None = None,
    h_frame: pd.DataFrame | None = None,
    fx_frame: pd.DataFrame | None = None,
    benchmark_frame: pd.DataFrame | None = None,
) -> LoadedPairData:
    """Resolve A/H/FX inputs from frames, CSVs, or AkShare."""

    if config.data.data_provider != "akshare":
        raise ValueError(f"Unsupported data provider '{config.data.data_provider}'.")

    resolved_a = _resolve_frame(
        a_frame,
        config.data.a_csv_path,
        fetch_a_share_history,
        config.a_symbol,
        config.start_date,
        config.end_date,
        config.data.a_adjust,
        csv_argument_name="--a-csv",
        missing_file_hint=(
            "Remove `--a-csv` to let the CLI load the A-share history from AkShare and the local data cache "
            "instead."
        ),
        cache_dir=config.cache_dir,
        cache_namespace="a_share_history",
        cache_payload={
            "provider": config.data.data_provider,
            "symbol": config.a_symbol,
            "start_date": config.start_date,
            "end_date": config.end_date,
            "adjust": config.data.a_adjust,
        },
        refresh_cache=config.refresh_cache,
    )
    resolved_h = _resolve_frame(
        h_frame,
        config.data.h_csv_path,
        fetch_h_share_history,
        config.h_symbol,
        config.start_date,
        config.end_date,
        config.data.h_adjust,
        csv_argument_name="--h-csv",
        missing_file_hint=(
            "Remove `--h-csv` to let the CLI load the H-share history from AkShare and the local data cache "
            "instead."
        ),
        cache_dir=config.cache_dir,
        cache_namespace="h_share_history",
        cache_payload={
            "provider": config.data.data_provider,
            "symbol": config.h_symbol,
            "start_date": config.start_date,
            "end_date": config.end_date,
            "adjust": config.data.h_adjust,
        },
        refresh_cache=config.refresh_cache,
    )
    resolved_fx = _resolve_fx_frame(fx_frame, config.data, config.start_date, config.end_date)

    aligned_prices = build_ah_price_frame(
        resolved_a,
        resolved_h,
        resolved_fx,
        start_date=config.start_date,
        end_date=config.end_date,
        share_ratio=config.data.share_ratio,
    )
    model_prices = prepare_model_price_frame(aligned_prices, config.a_symbol, config.h_symbol)

    benchmark_returns = pd.Series(dtype=float, name="benchmark_ret")
    if config.benchmark_symbol is not None or benchmark_frame is not None or config.data.benchmark_csv_path is not None:
        resolved_benchmark = _resolve_frame(
            benchmark_frame,
            config.data.benchmark_csv_path,
            _fetch_benchmark_history,
            config.benchmark_symbol,
            config.benchmark_market,
            config.start_date,
            config.end_date,
            config.data,
            csv_argument_name="--benchmark-csv",
            missing_file_hint=(
                "Remove `--benchmark-csv` to let the CLI fetch the benchmark online, or point it at a real file."
            ),
            cache_dir=config.cache_dir,
            cache_namespace="benchmark_history",
            cache_payload={
                "provider": config.data.data_provider,
                "symbol": config.benchmark_symbol,
                "benchmark_market": config.benchmark_market,
                "start_date": config.start_date,
                "end_date": config.end_date,
                "a_adjust": config.data.a_adjust,
                "h_adjust": config.data.h_adjust,
            },
            refresh_cache=config.refresh_cache,
        )
        benchmark_close = resolved_benchmark["close"].astype(float).reindex(model_prices.index).ffill().dropna()
        benchmark_returns = convert_prices_to_returns(benchmark_close)
        benchmark_returns.attrs["benchmark_label"] = _external_benchmark_label(config, benchmark_frame)
        benchmark_returns.attrs["benchmark_source"] = "external"

    return LoadedPairData(
        aligned_prices=aligned_prices,
        model_prices=model_prices,
        benchmark_returns=benchmark_returns,
    )
