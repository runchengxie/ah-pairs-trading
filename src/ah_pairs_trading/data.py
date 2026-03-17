"""Data loading and preprocessing helpers for A/H relative value research."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .cache import atomic_json_dump, atomic_pickle_dump, build_cache_key, load_json, load_pickle
from .config import BenchmarkMarket, DataConfig, PipelineConfig

_DATE_ALIASES = ("date", "日期", "datetime", "时间")
_CLOSE_ALIASES = ("close", "收盘", "最新价", "price", "value")
_OPEN_ALIASES = ("open", "开盘")
_HIGH_ALIASES = ("high", "最高")
_LOW_ALIASES = ("low", "最低")
_VOLUME_ALIASES = ("volume", "成交量", "vol")
_MARKET_DATA_CACHE_VERSION = 2
_MARKET_DATA_ARTIFACT_FORMAT = "pickle"


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


def _empty_standardized_history_frame() -> pd.DataFrame:
    """Return an empty normalized history frame for empty provider responses."""

    return pd.DataFrame(
        {column: pd.Series(dtype=float) for column in ("open", "high", "low", "close", "volume")},
        index=pd.DatetimeIndex([], name="date"),
    )


def _standardize_history_frame_allow_empty(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize market history while treating empty provider responses as no-op windows."""

    try:
        return standardize_history_frame(frame)
    except ValueError:
        if frame.empty:
            return _empty_standardized_history_frame()
        raise


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

    result = _standardize_history_frame_allow_empty(frame)
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

    result = _standardize_history_frame_allow_empty(frame)
    return result.loc[start_date:end_date]


def _normalize_cache_date(value: str) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _previous_day(value: str) -> str:
    return (pd.Timestamp(value) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def _next_day(value: str) -> str:
    return (pd.Timestamp(value) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def _history_cache_paths(
    cache_dir: Path,
    cache_namespace: str,
    cache_identity: dict[str, str | float | None],
) -> tuple[Path, Path]:
    cache_root = Path(cache_dir) / "data" / cache_namespace
    cache_key = build_cache_key(
        {
            "cache_kind": "incremental_history",
            "version": _MARKET_DATA_CACHE_VERSION,
            "identity": cache_identity,
        }
    )
    artifact_path = cache_root / f"{cache_key}.pkl"
    metadata_path = cache_root / f"{cache_key}.json"
    return artifact_path, metadata_path


def _load_history_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = load_json(path)
    return payload if isinstance(payload, dict) else None


def _history_frame_coverage(frame: pd.DataFrame | None) -> tuple[str | None, str | None]:
    if frame is None or frame.empty:
        return None, None
    index = pd.to_datetime(frame.index).tz_localize(None)
    return index.min().strftime("%Y-%m-%d"), index.max().strftime("%Y-%m-%d")


def _merge_history_frames(*frames: pd.DataFrame | None) -> pd.DataFrame:
    normalized_frames: list[pd.DataFrame] = []
    empty_template: pd.DataFrame | None = None
    for frame in frames:
        if frame is None:
            continue
        normalized = _standardize_history_frame_allow_empty(frame)
        if empty_template is None:
            empty_template = normalized.iloc[0:0].copy()
        if normalized.empty:
            continue
        normalized_frames.append(normalized)

    if not normalized_frames:
        return pd.DataFrame() if empty_template is None else empty_template

    merged = pd.concat(normalized_frames).sort_index()
    return merged.loc[~merged.index.duplicated(keep="last")]


def _build_history_manifest(
    frame: pd.DataFrame,
    *,
    cache_identity: dict[str, str | float | None],
    requested_start: str,
    requested_end: str,
) -> dict[str, Any]:
    coverage_start, coverage_end = _history_frame_coverage(frame)
    return {
        "cache_kind": "incremental_history",
        "version": _MARKET_DATA_CACHE_VERSION,
        "artifact_format": _MARKET_DATA_ARTIFACT_FORMAT,
        "identity": cache_identity,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "observation_count": int(len(frame)),
        "last_requested_start_date": _normalize_cache_date(requested_start),
        "last_requested_end_date": _normalize_cache_date(requested_end),
        "updated_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }


def _resolve_incremental_history(
    fetch_window: Callable[[str, str], pd.DataFrame],
    request_start: str,
    request_end: str,
    *,
    cache_dir: Path,
    cache_namespace: str,
    cache_identity: dict[str, str | float | None],
    refresh_cache: bool,
) -> pd.DataFrame:
    artifact_path, metadata_path = _history_cache_paths(cache_dir, cache_namespace, cache_identity)
    cached_frame = None
    if artifact_path.exists():
        cached_frame = _standardize_history_frame_allow_empty(load_pickle(artifact_path))
    manifest = _load_history_manifest(metadata_path)
    requested_start = _normalize_cache_date(request_start)
    requested_end = _normalize_cache_date(request_end)
    cached_start, cached_end = _history_frame_coverage(cached_frame)

    if refresh_cache:
        rebuild_start = min(value for value in (requested_start, cached_start) if value is not None)
        rebuild_end = max(value for value in (requested_end, cached_end) if value is not None)
        refreshed_frame = _standardize_history_frame_allow_empty(fetch_window(rebuild_start, rebuild_end))
        atomic_pickle_dump(refreshed_frame, artifact_path)
        atomic_json_dump(
            _build_history_manifest(
                refreshed_frame,
                cache_identity=cache_identity,
                requested_start=requested_start,
                requested_end=requested_end,
            ),
            metadata_path,
        )
        return refreshed_frame.loc[requested_start:requested_end]

    if cached_frame is None or cached_frame.empty:
        fetched_frame = _standardize_history_frame_allow_empty(fetch_window(requested_start, requested_end))
        atomic_pickle_dump(fetched_frame, artifact_path)
        atomic_json_dump(
            _build_history_manifest(
                fetched_frame,
                cache_identity=cache_identity,
                requested_start=requested_start,
                requested_end=requested_end,
            ),
            metadata_path,
        )
        return fetched_frame.loc[requested_start:requested_end]

    missing_segments: list[pd.DataFrame] = []
    if cached_start is not None and requested_start < cached_start:
        missing_segments.append(fetch_window(requested_start, _previous_day(cached_start)))
    if cached_end is not None and requested_end > cached_end:
        missing_segments.append(fetch_window(_next_day(cached_end), requested_end))

    if missing_segments:
        merged_frame = _merge_history_frames(cached_frame, *missing_segments)
        atomic_pickle_dump(merged_frame, artifact_path)
        atomic_json_dump(
            _build_history_manifest(
                merged_frame,
                cache_identity=cache_identity,
                requested_start=requested_start,
                requested_end=requested_end,
            ),
            metadata_path,
        )
        return merged_frame.loc[requested_start:requested_end]

    if manifest is None or manifest.get("version") != _MARKET_DATA_CACHE_VERSION:
        atomic_json_dump(
            _build_history_manifest(
                cached_frame,
                cache_identity=cache_identity,
                requested_start=requested_start,
                requested_end=requested_end,
            ),
            metadata_path,
        )
    return cached_frame.loc[requested_start:requested_end]


def _resolve_frame(
    supplied_frame: pd.DataFrame | None,
    csv_path: Path | None,
    fetch_window: Callable[[str, str], pd.DataFrame],
    csv_argument_name: str = "--*-csv",
    missing_file_hint: str = (
        "Point the argument at a real file, or remove the local CSV option to use automatic loading instead."
    ),
    request_start: str | None = None,
    request_end: str | None = None,
    cache_dir: Path | None = None,
    cache_namespace: str | None = None,
    cache_identity: dict[str, str | float | None] | None = None,
    refresh_cache: bool = False,
) -> pd.DataFrame:
    if supplied_frame is not None:
        return standardize_history_frame(supplied_frame)
    if csv_path is not None:
        if not Path(csv_path).exists():
            raise _missing_csv_error(csv_path, argument_name=csv_argument_name, hint=missing_file_hint)
        return load_history_csv(csv_path)

    if cache_dir is not None and cache_namespace is not None and cache_identity is not None:
        if request_start is None or request_end is None:
            raise ValueError("Incremental history caching requires `request_start` and `request_end`.")
        return _resolve_incremental_history(
            fetch_window,
            request_start,
            request_end,
            cache_dir=cache_dir,
            cache_namespace=cache_namespace,
            cache_identity=cache_identity,
            refresh_cache=refresh_cache,
        )

    if request_start is None or request_end is None:
        raise ValueError("Automatic history loading requires `request_start` and `request_end`.")
    return _standardize_history_frame_allow_empty(fetch_window(request_start, request_end))


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

    a_open = a_history["open"].astype(float) if "open" in a_history.columns else a_history["close"].astype(float)
    h_open_hkd = h_history["open"].astype(float) if "open" in h_history.columns else h_history["close"].astype(float)
    a_volume = a_history["volume"].astype(float) if "volume" in a_history.columns else pd.Series(np.nan, index=a_history.index)
    h_volume = h_history["volume"].astype(float) if "volume" in h_history.columns else pd.Series(np.nan, index=h_history.index)

    aligned = pd.DataFrame(
        {
            "a_close": a_history["close"],
            "a_open": a_open,
            "a_volume": a_volume,
            "h_close_hkd": h_history["close"],
            "h_open_hkd": h_open_hkd,
            "h_volume": h_volume,
        }
    ).dropna(subset=["a_close", "a_open", "h_close_hkd", "h_open_hkd"])
    aligned = aligned.loc[aligned.index.intersection(a_history.index).intersection(h_history.index)]
    aligned = aligned.sort_index()

    fx_series = fx_history["fx_rate"].reindex(aligned.index).ffill()
    aligned = aligned.join(fx_series.rename("fx_rate"), how="left").dropna(subset=["fx_rate"])
    aligned["h_close_cny"] = aligned["h_close_hkd"] * aligned["fx_rate"] * float(share_ratio)
    aligned["h_open_cny"] = aligned["h_open_hkd"] * aligned["fx_rate"] * float(share_ratio)
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
    if "a_open" in aligned_prices.columns:
        result["a_open"] = aligned_prices["a_open"].astype(float)
    if "h_open_cny" in aligned_prices.columns:
        result["h_open"] = aligned_prices["h_open_cny"].astype(float)
    if "a_volume" in aligned_prices.columns:
        result["a_volume"] = aligned_prices["a_volume"].astype(float)
    if "h_volume" in aligned_prices.columns:
        result["h_volume"] = aligned_prices["h_volume"].astype(float)
    return result.dropna(subset=[a_symbol, h_symbol])


def prepare_log_price_frame(price_frame: pd.DataFrame) -> pd.DataFrame:
    """Transform prices into log prices while preserving column names."""

    if (price_frame <= 0).any().any():
        raise ValueError("Prices must be strictly positive before log transformation.")
    return np.log(price_frame)


def _align_float_input(values: float | pd.Series, index: pd.Index, *, name: str) -> pd.Series:
    if isinstance(values, pd.Series):
        aligned = values.copy()
        if isinstance(aligned.index, pd.DatetimeIndex):
            aligned.index = pd.to_datetime(aligned.index).tz_localize(None)
        return aligned.astype(float).reindex(index).rename(name)
    return pd.Series(float(values), index=index, dtype=float, name=name)


def _align_boolean_input(values: pd.Series, index: pd.Index, *, name: str) -> pd.Series:
    aligned = values.copy()
    if isinstance(aligned.index, pd.DatetimeIndex):
        aligned.index = pd.to_datetime(aligned.index).tz_localize(None)
    return aligned.astype("boolean").reindex(index).rename(name)


def prepare_signal_frame(
    model_prices: pd.DataFrame,
    a_symbol: str,
    h_symbol: str,
    intercept: float | pd.Series,
    hedge_ratio: float | pd.Series,
    z_window: int = 120,
    min_periods: int | None = None,
    return_filter_window: int = 10,
    return_filter_min_periods: int | None = None,
    adv_window: int = 20,
    adv_min_periods: int | None = None,
    cointegration_p_value: pd.Series | None = None,
    cointegration_significant: pd.Series | None = None,
    ecm_speed: pd.Series | None = None,
    ecm_p_value: pd.Series | None = None,
    ecm_gate_pass: pd.Series | None = None,
) -> pd.DataFrame:
    """Build rolling spread and z-score signals from A/H model prices."""

    if z_window < 2:
        raise ValueError("The rolling z-score window must be at least 2 observations.")
    if return_filter_window < 1:
        raise ValueError("The return-filter window must be at least 1 observation.")
    if adv_window < 1:
        raise ValueError("The ADV window must be at least 1 observation.")

    minimum_periods = min_periods or z_window
    return_minimum_periods = return_filter_min_periods or return_filter_window
    adv_minimum_periods = adv_min_periods or adv_window
    signal_frame = model_prices[[a_symbol, h_symbol]].copy()
    for optional_column in ("a_open", "h_open", "a_volume", "h_volume"):
        if optional_column in model_prices.columns:
            signal_frame[optional_column] = model_prices[optional_column].astype(float)
    intercept_series = _align_float_input(intercept, signal_frame.index, name="intercept")
    hedge_ratio_series = _align_float_input(hedge_ratio, signal_frame.index, name="hedge_ratio")
    log_prices = prepare_log_price_frame(signal_frame[[a_symbol, h_symbol]])
    log_returns = log_prices.diff()
    spread = log_prices[a_symbol] - intercept_series - hedge_ratio_series * log_prices[h_symbol]
    rolling_mean = spread.rolling(z_window, min_periods=minimum_periods).mean()
    rolling_std = spread.rolling(z_window, min_periods=minimum_periods).std()
    zscore = ((spread - rolling_mean) / rolling_std).replace([np.inf, -np.inf], np.nan)
    ret_spread = spread.diff()
    ret_spread_ema = ret_spread.ewm(
        span=return_filter_window,
        adjust=False,
        min_periods=return_minimum_periods,
    ).mean()
    ret_spread_sma = ret_spread.rolling(return_filter_window, min_periods=return_minimum_periods).mean()
    ret_spread_ema_rolling_mean = ret_spread_ema.rolling(z_window, min_periods=minimum_periods).mean()
    ret_spread_ema_rolling_std = ret_spread_ema.rolling(z_window, min_periods=minimum_periods).std()
    ret_spread_ema_zscore = ((ret_spread_ema - ret_spread_ema_rolling_mean) / ret_spread_ema_rolling_std).replace(
        [np.inf, -np.inf],
        np.nan,
    )
    ret_spread_sma_rolling_mean = ret_spread_sma.rolling(z_window, min_periods=minimum_periods).mean()
    ret_spread_sma_rolling_std = ret_spread_sma.rolling(z_window, min_periods=minimum_periods).std()
    ret_spread_sma_zscore = ((ret_spread_sma - ret_spread_sma_rolling_mean) / ret_spread_sma_rolling_std).replace(
        [np.inf, -np.inf],
        np.nan,
    )

    signal_frame["intercept"] = intercept_series
    signal_frame["hedge_ratio"] = hedge_ratio_series
    signal_frame["spread"] = spread
    signal_frame["rolling_mean"] = rolling_mean
    signal_frame["rolling_std"] = rolling_std
    signal_frame["zscore"] = zscore
    signal_frame["a_log_return"] = log_returns[a_symbol]
    signal_frame["h_log_return"] = log_returns[h_symbol]
    signal_frame["ret_spread"] = ret_spread
    signal_frame["ret_spread_ema"] = ret_spread_ema
    signal_frame["ret_spread_ema_rolling_mean"] = ret_spread_ema_rolling_mean
    signal_frame["ret_spread_ema_rolling_std"] = ret_spread_ema_rolling_std
    signal_frame["ret_spread_ema_zscore"] = ret_spread_ema_zscore
    signal_frame["ret_spread_sma"] = ret_spread_sma
    signal_frame["ret_spread_sma_rolling_mean"] = ret_spread_sma_rolling_mean
    signal_frame["ret_spread_sma_rolling_std"] = ret_spread_sma_rolling_std
    signal_frame["ret_spread_sma_zscore"] = ret_spread_sma_zscore
    if "a_open" not in signal_frame.columns:
        signal_frame["a_open"] = signal_frame[a_symbol]
    if "h_open" not in signal_frame.columns:
        signal_frame["h_open"] = signal_frame[h_symbol]
    if "a_volume" in signal_frame.columns:
        signal_frame["a_adv"] = signal_frame["a_volume"].rolling(adv_window, min_periods=adv_minimum_periods).mean().shift(1)
    else:
        signal_frame["a_adv"] = np.nan
    if "h_volume" in signal_frame.columns:
        signal_frame["h_adv"] = signal_frame["h_volume"].rolling(adv_window, min_periods=adv_minimum_periods).mean().shift(1)
    else:
        signal_frame["h_adv"] = np.nan
    ema_filter_pass = pd.Series(pd.NA, index=signal_frame.index, dtype="boolean")
    sma_filter_pass = pd.Series(pd.NA, index=signal_frame.index, dtype="boolean")
    zscore_positive = zscore > 0
    zscore_negative = zscore < 0
    ema_filter_pass.loc[zscore_positive] = ret_spread_ema.loc[zscore_positive] <= 0.0
    ema_filter_pass.loc[zscore_negative] = ret_spread_ema.loc[zscore_negative] >= 0.0
    sma_filter_pass.loc[zscore_positive] = ret_spread_sma.loc[zscore_positive] <= 0.0
    sma_filter_pass.loc[zscore_negative] = ret_spread_sma.loc[zscore_negative] >= 0.0
    signal_frame["ret_spread_ema_filter_pass"] = ema_filter_pass
    signal_frame["ret_spread_sma_filter_pass"] = sma_filter_pass
    signal_frame["cheap_leg"] = np.where(zscore > 0, "h", np.where(zscore < 0, "a", "flat"))
    signal_frame["pair_direction"] = np.where(
        zscore > 0,
        "short_a_long_h",
        np.where(zscore < 0, "long_a_short_h", "flat"),
    )
    signal_frame["ret_spread_ema_cheap_leg"] = np.where(
        ret_spread_ema_zscore > 0,
        "h",
        np.where(ret_spread_ema_zscore < 0, "a", "flat"),
    )
    signal_frame["ret_spread_ema_pair_direction"] = np.where(
        ret_spread_ema_zscore > 0,
        "short_a_long_h",
        np.where(ret_spread_ema_zscore < 0, "long_a_short_h", "flat"),
    )
    signal_frame["ret_spread_sma_cheap_leg"] = np.where(
        ret_spread_sma_zscore > 0,
        "h",
        np.where(ret_spread_sma_zscore < 0, "a", "flat"),
    )
    signal_frame["ret_spread_sma_pair_direction"] = np.where(
        ret_spread_sma_zscore > 0,
        "short_a_long_h",
        np.where(ret_spread_sma_zscore < 0, "long_a_short_h", "flat"),
    )
    if cointegration_p_value is not None:
        signal_frame["cointegration_p_value"] = _align_float_input(
            cointegration_p_value,
            signal_frame.index,
            name="cointegration_p_value",
        )
    if cointegration_significant is not None:
        significant_series = _align_boolean_input(
            cointegration_significant,
            signal_frame.index,
            name="cointegration_significant",
        )
        signal_frame["cointegration_significant"] = significant_series
        signal_frame["cointegration_gate_pass"] = significant_series
    if ecm_speed is not None:
        signal_frame["ecm_speed"] = _align_float_input(ecm_speed, signal_frame.index, name="ecm_speed")
    if ecm_p_value is not None:
        signal_frame["ecm_p_value"] = _align_float_input(ecm_p_value, signal_frame.index, name="ecm_p_value")
    if ecm_gate_pass is not None:
        signal_frame["ecm_gate_pass"] = _align_boolean_input(ecm_gate_pass, signal_frame.index, name="ecm_gate_pass")
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
        lambda start_date, end_date: fetch_a_share_history(
            config.a_symbol,
            start_date,
            end_date,
            config.data.a_adjust,
        ),
        csv_argument_name="--a-csv",
        missing_file_hint=(
            "Remove `--a-csv` to let the CLI load the A-share history from AkShare and the local data cache "
            "instead."
        ),
        request_start=config.start_date,
        request_end=config.end_date,
        cache_dir=config.cache_dir,
        cache_namespace="a_share_history",
        cache_identity={
            "provider": config.data.data_provider,
            "symbol": config.a_symbol,
            "adjust": config.data.a_adjust,
        },
        refresh_cache=config.refresh_cache,
    )
    resolved_h = _resolve_frame(
        h_frame,
        config.data.h_csv_path,
        lambda start_date, end_date: fetch_h_share_history(
            config.h_symbol,
            start_date,
            end_date,
            config.data.h_adjust,
        ),
        csv_argument_name="--h-csv",
        missing_file_hint=(
            "Remove `--h-csv` to let the CLI load the H-share history from AkShare and the local data cache "
            "instead."
        ),
        request_start=config.start_date,
        request_end=config.end_date,
        cache_dir=config.cache_dir,
        cache_namespace="h_share_history",
        cache_identity={
            "provider": config.data.data_provider,
            "symbol": config.h_symbol,
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
            lambda start_date, end_date: _fetch_benchmark_history(
                config.benchmark_symbol,
                config.benchmark_market,
                start_date,
                end_date,
                config.data,
            ),
            csv_argument_name="--benchmark-csv",
            missing_file_hint=(
                "Remove `--benchmark-csv` to let the CLI fetch the benchmark online, or point it at a real file."
            ),
            request_start=config.start_date,
            request_end=config.end_date,
            cache_dir=config.cache_dir,
            cache_namespace="benchmark_history",
            cache_identity={
                "provider": config.data.data_provider,
                "symbol": config.benchmark_symbol,
                "benchmark_market": config.benchmark_market,
                "adjust": config.data.a_adjust if config.benchmark_market == "a" else config.data.h_adjust,
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
