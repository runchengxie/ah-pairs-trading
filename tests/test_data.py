"""Tests for A/H data preparation helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ah_pairs_trading.config import DataConfig, PipelineConfig
from ah_pairs_trading.data import build_ah_price_frame, load_ah_pair_data, prepare_signal_frame, standardize_history_frame


def test_standardize_history_frame_casts_integer_volume_to_float() -> None:
    """Normalization should upcast integer OHLCV columns without pandas setitem errors."""

    frame = pd.DataFrame(
        {
            "日期": ["2024-01-02", "2024-01-03"],
            "收盘": [10, 11],
            "成交量": [100, 125],
        }
    )

    result = standardize_history_frame(frame)

    assert result["close"].dtype == np.dtype("float64")
    assert result["volume"].dtype == np.dtype("float64")
    assert result.loc["2024-01-02", "volume"] == 100.0


def test_build_ah_price_frame_uses_joint_calendar_and_fx_conversion() -> None:
    """A/H alignment should keep only joint trading dates and convert H prices to CNY."""

    a_index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
    h_index = pd.to_datetime(["2024-01-02", "2024-01-04", "2024-01-05"])
    fx_index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])

    a_frame = pd.DataFrame({"date": a_index, "close": [10.0, 10.2, 10.5, 10.4]})
    h_frame = pd.DataFrame({"date": h_index, "close": [9.0, 9.4, 9.2]})
    fx_frame = pd.DataFrame({"date": fx_index, "close": [0.91, 0.92, 0.93, 0.94]})

    aligned = build_ah_price_frame(
        a_frame,
        h_frame,
        fx_frame,
        start_date="2024-01-02",
        end_date="2024-01-05",
        share_ratio=1.0,
    )

    expected_index = pd.to_datetime(["2024-01-02", "2024-01-04", "2024-01-05"])
    assert aligned.index.equals(expected_index)
    assert aligned.loc["2024-01-04", "h_close_cny"] == 9.4 * 0.93
    assert aligned.loc["2024-01-05", "ah_premium_pct"] == 10.4 / (9.2 * 0.94) - 1.0


def test_prepare_signal_frame_creates_zscores_and_direction_labels() -> None:
    """Signal preparation should expose z-scores and relative-value directions."""

    index = pd.date_range("2024-01-01", periods=80, freq="B")
    log_h = np.log(40.0) + 0.001 * np.arange(len(index))
    residual = 0.04 * np.sin(np.linspace(0.0, 8.0 * np.pi, len(index)))
    intercept = 0.1
    hedge_ratio = 1.03
    log_a = intercept + hedge_ratio * log_h + residual
    model_prices = pd.DataFrame({"600000": np.exp(log_a), "00386": np.exp(log_h)}, index=index)

    signal_frame = prepare_signal_frame(
        model_prices,
        a_symbol="600000",
        h_symbol="00386",
        intercept=intercept,
        hedge_ratio=hedge_ratio,
        z_window=15,
        min_periods=15,
    )

    assert signal_frame["zscore"].notna().sum() > 0
    assert signal_frame["ret_spread"].notna().sum() > 0
    assert signal_frame["ret_spread_ema_zscore"].notna().sum() > 0
    assert signal_frame["ret_spread_sma_zscore"].notna().sum() > 0
    assert set(signal_frame["cheap_leg"].dropna().unique()) <= {"a", "h", "flat"}
    assert set(signal_frame["pair_direction"].dropna().unique()) <= {
        "flat",
        "short_a_long_h",
        "long_a_short_h",
    }
    assert set(signal_frame["ret_spread_ema_cheap_leg"].dropna().unique()) <= {"a", "h", "flat"}
    assert set(signal_frame["ret_spread_sma_cheap_leg"].dropna().unique()) <= {"a", "h", "flat"}
    assert signal_frame["ret_spread_ema_filter_pass"].dropna().isin([True, False]).all()
    assert signal_frame["ret_spread_sma_filter_pass"].dropna().isin([True, False]).all()


def test_load_ah_pair_data_reuses_cached_remote_history(tmp_path, monkeypatch) -> None:
    """Remote A/H histories should be fetched once and then served from disk cache."""

    index = pd.date_range("2024-01-02", periods=6, freq="B")
    a_history = pd.DataFrame({"date": index, "close": [10.0, 10.2, 10.1, 10.4, 10.6, 10.5]})
    h_history = pd.DataFrame({"date": index, "close": [8.8, 8.9, 9.0, 9.2, 9.1, 9.3]})
    fetch_calls = {"a": 0, "h": 0}

    def fake_fetch_a(symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
        fetch_calls["a"] += 1
        return a_history

    def fake_fetch_h(symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
        fetch_calls["h"] += 1
        return h_history

    monkeypatch.setattr("ah_pairs_trading.data.fetch_a_share_history", fake_fetch_a)
    monkeypatch.setattr("ah_pairs_trading.data.fetch_h_share_history", fake_fetch_h)

    config = PipelineConfig(
        a_symbol="600036",
        h_symbol="03968",
        start_date="2024-01-02",
        end_date="2024-01-10",
        train_end_date="2024-01-08",
        data=DataConfig(constant_fx_rate=0.91),
        cache_dir=tmp_path / "cache",
    )

    first = load_ah_pair_data(config)
    second = load_ah_pair_data(config)

    assert fetch_calls == {"a": 1, "h": 1}
    pd.testing.assert_frame_equal(first.aligned_prices, second.aligned_prices)
    pd.testing.assert_frame_equal(first.model_prices, second.model_prices)


def test_load_ah_pair_data_missing_local_history_reports_recovery_hint(tmp_path) -> None:
    """Missing local CSV paths should explain how to switch back to automatic loading."""

    config = PipelineConfig(
        a_symbol="600036",
        h_symbol="03968",
        start_date="2024-01-02",
        end_date="2024-01-10",
        train_end_date="2024-01-08",
        data=DataConfig(
            a_csv_path=tmp_path / "missing_a.csv",
            constant_fx_rate=0.91,
        ),
        cache_dir=tmp_path / "cache",
    )

    with pytest.raises(FileNotFoundError) as exc_info:
        load_ah_pair_data(config)

    message = str(exc_info.value)
    assert "--a-csv" in message
    assert "AkShare" in message
    assert "cache" in message
