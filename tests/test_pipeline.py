"""Integration tests for the A/H research pipeline."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ah_pairs_trading.analysis import CointegrationResult
from ah_pairs_trading.config import CostConfig, DataConfig, PipelineConfig, StrategyConfig
from ah_pairs_trading.pipeline import run_ah_relative_value_pipeline


def make_raw_ah_frames(length: int = 260) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Create synthetic A/H/FX histories that mimic a cointegrated pair."""

    index = pd.date_range("2020-01-01", periods=length, freq="B")
    fx_rate = 0.92
    log_h_cny = np.log(50.0) + 0.001 * np.arange(length)
    residual = np.zeros(length)
    for row_number in range(1, length):
        residual[row_number] = 0.78 * residual[row_number - 1] + 0.01 * np.sin(row_number / 6.0)
    intercept = 0.12
    hedge_ratio = 1.06
    log_a = intercept + hedge_ratio * log_h_cny + residual

    a_frame = pd.DataFrame({"date": index, "close": np.exp(log_a)})
    h_frame = pd.DataFrame({"date": index, "close": np.exp(log_h_cny) / fx_rate})
    fx_frame = pd.DataFrame({"date": index, "close": fx_rate})
    return a_frame, h_frame, fx_frame


def test_pipeline_runs_on_supplied_ah_frames() -> None:
    """The full A/H pipeline should run end to end on supplied local frames."""

    a_frame, h_frame, fx_frame = make_raw_ah_frames()
    config = PipelineConfig(
        a_symbol="601857",
        h_symbol="00883",
        start_date="2020-01-01",
        end_date="2020-12-31",
        train_end_date="2020-09-30",
        require_significant_cointegration=False,
        data=DataConfig(constant_fx_rate=None),
        strategy=StrategyConfig(
            entry_z_candidates=(0.8, 1.0),
            exit_z=0.2,
            stop_z=2.5,
            z_window=20,
            z_min_periods=20,
            max_holding_days=15,
            position_size_fraction=0.75,
            initial_capital=100_000.0,
            execution_mode="long_cheaper_leg_only",
            a_lot_size=100,
            h_lot_size=100,
        ),
        costs=CostConfig(
            a_buy_cost_bps=0.0,
            a_sell_cost_bps=0.0,
            h_buy_cost_bps=0.0,
            h_sell_cost_bps=0.0,
            h_stamp_duty_bps=0.0,
            fx_conversion_bps=0.0,
        ),
    )

    result = run_ah_relative_value_pipeline(
        config,
        a_frame=a_frame,
        h_frame=h_frame,
        fx_frame=fx_frame,
    )

    assert not result.aligned_prices.empty
    assert not result.signal_frame.empty
    assert result.best_entry_z in {0.8, 1.0}
    assert np.isfinite(result.training_cointegration.p_value)
    assert result.train_backtest.summary.trade_count > 0
    assert result.test_backtest.summary.trade_count > 0


def test_pipeline_resume_from_cache_reuses_stage_outputs(tmp_path, monkeypatch) -> None:
    """A resumed run should reuse cached stages instead of recomputing them."""

    a_frame, h_frame, fx_frame = make_raw_ah_frames()
    a_csv = tmp_path / "600036.csv"
    h_csv = tmp_path / "03968.csv"
    fx_csv = tmp_path / "hkdcny.csv"
    a_frame.to_csv(a_csv, index=False)
    h_frame.to_csv(h_csv, index=False)
    fx_frame.to_csv(fx_csv, index=False)

    config = PipelineConfig(
        a_symbol="601857",
        h_symbol="00883",
        start_date="2020-01-01",
        end_date="2020-12-31",
        train_end_date="2020-09-30",
        require_significant_cointegration=False,
        data=DataConfig(
            a_csv_path=a_csv,
            h_csv_path=h_csv,
            fx_csv_path=fx_csv,
        ),
        strategy=StrategyConfig(
            entry_z_candidates=(0.8, 1.0),
            exit_z=0.2,
            stop_z=2.5,
            z_window=20,
            z_min_periods=20,
            max_holding_days=15,
            position_size_fraction=0.75,
            initial_capital=100_000.0,
            execution_mode="long_cheaper_leg_only",
            a_lot_size=100,
            h_lot_size=100,
        ),
        costs=CostConfig(
            a_buy_cost_bps=0.0,
            a_sell_cost_bps=0.0,
            h_buy_cost_bps=0.0,
            h_sell_cost_bps=0.0,
            h_stamp_duty_bps=0.0,
            fx_conversion_bps=0.0,
        ),
        cache_dir=tmp_path / "cache",
        resume_from_cache=True,
    )

    first = run_ah_relative_value_pipeline(config)

    def fail_if_recomputed(*args, **kwargs):
        raise AssertionError("rolling cointegration should have been loaded from the stage cache")

    monkeypatch.setattr("ah_pairs_trading.pipeline.run_rolling_cointegration", fail_if_recomputed)

    second = run_ah_relative_value_pipeline(config)

    assert first.best_entry_z == second.best_entry_z
    pd.testing.assert_frame_equal(first.rolling_cointegration, second.rolling_cointegration)


def test_pipeline_cointegration_error_surfaces_context(monkeypatch) -> None:
    """The cointegration guardrail should report the failing p-value and recovery hint."""

    a_frame, h_frame, fx_frame = make_raw_ah_frames()
    residual_index = pd.DatetimeIndex(a_frame["date"])
    fake_result = CointegrationResult(
        dependent_symbol="601857",
        independent_symbol="00857",
        alpha=0.05,
        score=-2.7,
        p_value=0.19848,
        critical_values=(-3.9, -3.34, -3.05),
        significant=False,
        intercept=0.03,
        hedge_ratio=0.82,
        residual_mean=0.0,
        residual_std=0.14,
        residuals=pd.Series(np.zeros(len(residual_index)), index=residual_index, name="residual"),
        regression_summary_text="",
        regression_params=pd.Series({"const": 0.03, "00857": 0.82}),
    )

    monkeypatch.setattr("ah_pairs_trading.pipeline.run_cointegration_analysis", lambda *args, **kwargs: fake_result)

    config = PipelineConfig(
        a_symbol="601857",
        h_symbol="00857",
        start_date="2020-01-01",
        end_date="2020-12-31",
        train_end_date="2020-09-30",
        require_significant_cointegration=True,
        data=DataConfig(constant_fx_rate=None),
        strategy=StrategyConfig(
            entry_z_candidates=(0.8, 1.0),
            exit_z=0.2,
            stop_z=2.5,
            z_window=20,
            z_min_periods=20,
            max_holding_days=15,
            position_size_fraction=0.75,
            initial_capital=100_000.0,
            execution_mode="long_cheaper_leg_only",
            a_lot_size=100,
            h_lot_size=100,
        ),
        costs=CostConfig(
            a_buy_cost_bps=0.0,
            a_sell_cost_bps=0.0,
            h_buy_cost_bps=0.0,
            h_sell_cost_bps=0.0,
            h_stamp_duty_bps=0.0,
            fx_conversion_bps=0.0,
        ),
        cache_dir=None,
    )

    with pytest.raises(ValueError) as exc_info:
        run_ah_relative_value_pipeline(
            config,
            a_frame=a_frame,
            h_frame=h_frame,
            fx_frame=fx_frame,
        )

    message = str(exc_info.value)
    assert "p-value=0.198480" in message
    assert "alpha=0.05" in message
    assert "601857/00857" in message
    assert "--allow-non-coint" in message
    assert "--resume-from-cache" in message
    assert "same issuer" in message
