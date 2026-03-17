"""Integration tests for the A/H research pipeline."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from ah_pairs_trading.analysis import CointegrationResult
from ah_pairs_trading.config import CostConfig, DataConfig, PipelineConfig, RollingConfig, StrategyConfig
from ah_pairs_trading.pipeline import build_pipeline_summary, render_pipeline_scorecard, run_ah_relative_value_pipeline

pytestmark = pytest.mark.integration


def zero_cost_config() -> CostConfig:
    return CostConfig(
        a_buy_cost_bps=0.0,
        a_sell_cost_bps=0.0,
        h_buy_cost_bps=0.0,
        h_sell_cost_bps=0.0,
        h_stamp_duty_bps=0.0,
        fx_conversion_bps=0.0,
        a_slippage_bps=0.0,
        h_slippage_bps=0.0,
        a_impact_bps_per_100pct_adv=0.0,
        h_impact_bps_per_100pct_adv=0.0,
        a_short_borrow_apr_bps=0.0,
        h_short_borrow_apr_bps=0.0,
        a_long_financing_apr_bps=0.0,
        h_long_financing_apr_bps=0.0,
    )


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

    a_frame = pd.DataFrame(
        {
            "date": index,
            "open": np.exp(log_a) * 1.001,
            "close": np.exp(log_a),
            "volume": np.full(length, 4_000_000.0),
        }
    )
    h_frame = pd.DataFrame(
        {
            "date": index,
            "open": np.exp(log_h_cny) / fx_rate * 0.999,
            "close": np.exp(log_h_cny) / fx_rate,
            "volume": np.full(length, 3_500_000.0),
        }
    )
    fx_frame = pd.DataFrame({"date": index, "close": fx_rate})
    return a_frame, h_frame, fx_frame


def test_pipeline_runs_on_supplied_ah_frames() -> None:
    """The full A/H pipeline should run end to end on supplied local frames."""

    a_frame, h_frame, fx_frame = make_raw_ah_frames()
    config = PipelineConfig(
        a_symbol="601857",
        h_symbol="00857",
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
        costs=zero_cost_config(),
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
    assert not result.benchmark_comparison.empty
    assert result.benchmark_comparison.attrs["benchmark_source"] == "internal"
    assert result.benchmark_comparison.attrs["benchmark_label"] == "Internal A/H Basket (hedge_ratio)"


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
        h_symbol="00857",
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
        costs=zero_cost_config(),
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


def test_pipeline_summary_schema_and_scorecard_are_persisted(tmp_path) -> None:
    """Saved artifacts should include a structured JSON summary and a readable scorecard."""

    a_frame, h_frame, fx_frame = make_raw_ah_frames()
    benchmark_frame = pd.DataFrame(
        {
            "date": a_frame["date"],
            "close": np.linspace(100.0, 110.0, len(a_frame)),
        }
    )
    output_dir = tmp_path / "artifacts"

    config = PipelineConfig(
        a_symbol="601857",
        h_symbol="00857",
        benchmark_symbol="CSI300",
        benchmark_market="a",
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
        costs=zero_cost_config(),
        output_dir=output_dir,
    )

    result = run_ah_relative_value_pipeline(
        config,
        a_frame=a_frame,
        h_frame=h_frame,
        fx_frame=fx_frame,
        benchmark_frame=benchmark_frame,
    )
    summary_payload = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    summary_markdown = (output_dir / "summary.md").read_text(encoding="utf-8")
    terminal_scorecard = render_pipeline_scorecard(build_pipeline_summary(config, result))

    assert {
        "run_meta",
        "instrument_meta",
        "strategy_params",
        "cost_assumptions",
        "train_metrics",
        "test_metrics",
        "benchmark_metrics",
        "rolling_metrics",
        "diagnostics",
    } <= set(summary_payload)
    assert summary_payload["instrument_meta"]["benchmark_label"] == "CSI300"
    assert summary_payload["instrument_meta"]["benchmark_source"] == "external"
    assert summary_payload["instrument_meta"]["pair_validation"]["status"] == "matched"
    assert summary_payload["test_metrics"]["annual_return"] is not None
    assert summary_payload["benchmark_metrics"]["benchmark_total_return"] is not None
    assert "Test Scorecard" in summary_markdown
    assert "Benchmark Comparison" in summary_markdown
    assert "Rolling Beta (" in summary_markdown
    assert "Annual volatility" in terminal_scorecard
    assert "Pair validation: matched" in terminal_scorecard
    assert "Rolling Beta (" in terminal_scorecard
    assert "Artifacts:" in terminal_scorecard


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
        costs=zero_cost_config(),
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


def test_pipeline_rejects_known_cross_issuer_pair_before_backtest() -> None:
    """Known registry mismatches should fail fast in strict mode."""

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
        costs=zero_cost_config(),
    )

    with pytest.raises(ValueError) as exc_info:
        run_ah_relative_value_pipeline(
            config,
            a_frame=a_frame,
            h_frame=h_frame,
            fx_frame=fx_frame,
        )

    message = str(exc_info.value)
    assert "same-issuer mismatch" in message
    assert "PetroChina" in message
    assert "CNOOC" in message


def test_paired_mode_without_explicit_benchmark_does_not_auto_generate_internal_reference() -> None:
    """Paired mode should stay benchmark-free in auto mode unless the user opts in."""

    a_frame, h_frame, fx_frame = make_raw_ah_frames()
    config = PipelineConfig(
        a_symbol="601857",
        h_symbol="00857",
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
            execution_mode="paired",
            a_lot_size=100,
            h_lot_size=100,
        ),
        costs=zero_cost_config(),
    )

    result = run_ah_relative_value_pipeline(
        config,
        a_frame=a_frame,
        h_frame=h_frame,
        fx_frame=fx_frame,
    )
    summary_payload = build_pipeline_summary(config, result)
    scorecard = render_pipeline_scorecard(summary_payload)

    assert result.benchmark_comparison.empty
    assert result.rolling_beta.empty
    assert summary_payload["benchmark_metrics"] is None
    assert "Rolling Beta (60d): unavailable because no benchmark was resolved" in scorecard
    assert "mean=n/a" not in scorecard


def test_pipeline_supports_return_spread_entry_signal_mode() -> None:
    """The pipeline should support a standardized return-spread signal as the primary entry mode."""

    a_frame, h_frame, fx_frame = make_raw_ah_frames()
    config = PipelineConfig(
        a_symbol="601857",
        h_symbol="00857",
        start_date="2020-01-01",
        end_date="2020-12-31",
        train_end_date="2020-09-30",
        require_significant_cointegration=False,
        benchmark_mode="internal",
        data=DataConfig(constant_fx_rate=None),
        strategy=StrategyConfig(
            entry_z_candidates=(0.5, 0.8),
            exit_z=0.2,
            stop_z=2.0,
            z_window=20,
            z_min_periods=20,
            max_holding_days=15,
            position_size_fraction=0.75,
            initial_capital=100_000.0,
            execution_mode="paired",
            entry_signal_mode="ret_spread_ema",
            a_lot_size=100,
            h_lot_size=100,
        ),
        costs=zero_cost_config(),
    )

    result = run_ah_relative_value_pipeline(
        config,
        a_frame=a_frame,
        h_frame=h_frame,
        fx_frame=fx_frame,
    )
    summary_payload = build_pipeline_summary(config, result)

    assert result.best_entry_z in {0.5, 0.8}
    assert "ret_spread_ema_zscore" in result.signal_frame.columns
    assert "signal_score" in result.test_backtest.equity_curve.columns
    assert summary_payload["strategy_params"]["entry_signal_mode"] == "ret_spread_ema"


def test_pipeline_supports_dynamic_hedge_ratio_gates_and_half_life_anchor() -> None:
    """The pipeline should expose rolling diagnostics and half-life-derived execution parameters."""

    a_frame, h_frame, fx_frame = make_raw_ah_frames(length=320)
    config = PipelineConfig(
        a_symbol="601857",
        h_symbol="00857",
        start_date="2020-01-01",
        end_date="2021-03-31",
        train_end_date="2020-11-30",
        require_significant_cointegration=False,
        data=DataConfig(constant_fx_rate=None),
        strategy=StrategyConfig(
            entry_z_candidates=(0.8, 1.0),
            exit_z=0.2,
            stop_z=2.0,
            z_window=20,
            z_min_periods=20,
            max_holding_days=15,
            position_size_fraction=0.75,
            initial_capital=100_000.0,
            execution_mode="paired",
            hedge_ratio_mode="rolling",
            cointegration_gate_mode="significant",
            ecm_gate_mode="significant_negative",
            half_life_anchor_mode="training",
            half_life_z_window_multiplier=2.0,
            half_life_max_holding_multiplier=1.5,
            a_lot_size=100,
            h_lot_size=100,
        ),
        costs=zero_cost_config(),
        rolling=RollingConfig(
            cointegration_window=60,
            cointegration_step=5,
            sharpe_window=30,
            beta_window=30,
            var_max_lags=3,
        ),
    )

    result = run_ah_relative_value_pipeline(
        config,
        a_frame=a_frame,
        h_frame=h_frame,
        fx_frame=fx_frame,
    )
    summary_payload = build_pipeline_summary(config, result)

    assert "cointegration_gate_pass" in result.signal_frame.columns
    assert "cointegration_p_value" in result.signal_frame.columns
    assert "ecm_gate_pass" in result.signal_frame.columns
    assert "ecm_p_value" in result.signal_frame.columns
    assert "a_open" in result.signal_frame.columns
    assert "h_open" in result.signal_frame.columns
    assert result.signal_frame["hedge_ratio"].notna().sum() > 0
    assert result.signal_frame["hedge_ratio"].dropna().nunique() > 1
    assert result.signal_frame["cointegration_gate_pass"].dropna().isin([True, False]).all()
    assert result.signal_frame["ecm_gate_pass"].dropna().isin([True, False]).all()
    assert not result.rolling_ecm.empty
    assert summary_payload["strategy_params"]["hedge_ratio_mode"] == "rolling"
    assert summary_payload["strategy_params"]["cointegration_gate_mode"] == "significant"
    assert summary_payload["strategy_params"]["ecm_gate_mode"] == "significant_negative"
    assert summary_payload["strategy_params"]["execution_timing"] == "next_open"
    assert summary_payload["strategy_params"]["z_window"] != config.strategy.z_window
    assert summary_payload["strategy_params"]["max_holding_days"] != config.strategy.max_holding_days
