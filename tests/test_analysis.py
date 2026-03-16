"""Tests for the statistical analysis module."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ah_pairs_trading.analysis import (
    estimate_mean_reversion,
    fit_error_correction_model,
    fit_var_diagnostics,
    matrix_ols,
    run_cointegration_analysis,
    run_rolling_cointegration,
)


def make_cointegrated_log_prices(length: int = 420) -> pd.DataFrame:
    """Create a strongly cointegrated pair for deterministic tests."""

    rng = np.random.default_rng(7)
    index = pd.date_range("2020-01-01", periods=length, freq="B")

    random_walk = np.cumsum(rng.normal(0.0, 0.01, size=length))
    log_independent = np.log(100.0) + random_walk

    residual = np.zeros(length)
    for row_number in range(1, length):
        residual[row_number] = 0.82 * residual[row_number - 1] + rng.normal(0.0, 0.008)

    log_dependent = 0.15 + 1.2 * log_independent + residual
    return pd.DataFrame({"KO": log_dependent, "PEP": log_independent}, index=index)


def test_cointegration_workflow_detects_expected_relationship() -> None:
    """The full analysis stack should recover the synthetic relationship."""

    log_prices = make_cointegrated_log_prices()

    result = run_cointegration_analysis(log_prices, dependent_symbol="KO", independent_symbol="PEP")
    assert result.p_value < 0.05
    assert result.hedge_ratio == pytest.approx(1.2, rel=0.06)

    ecm = fit_error_correction_model(log_prices, result)
    assert ecm.error_correction_speed < 0

    mean_reversion = estimate_mean_reversion(result.residuals)
    assert mean_reversion.theta is not None
    assert mean_reversion.half_life is not None
    assert mean_reversion.half_life > 0

    manual = matrix_ols(log_prices["KO"], log_prices["PEP"])
    assert manual.intercept == pytest.approx(result.intercept, rel=1e-6)
    assert manual.hedge_ratio == pytest.approx(result.hedge_ratio, rel=1e-6)


def test_rolling_cointegration_and_var_outputs_are_non_empty() -> None:
    """Rolling statistics and VAR diagnostics should produce usable outputs."""

    log_prices = make_cointegrated_log_prices()
    rolling_frame = run_rolling_cointegration(
        log_prices,
        dependent_symbol="KO",
        independent_symbol="PEP",
        window_size=120,
        step_size=20,
    )
    assert not rolling_frame.empty
    assert {"p_value", "significant", "intercept", "hedge_ratio", "residual_std"} <= set(rolling_frame.columns)

    diagnostics = fit_var_diagnostics(log_prices, symbols=("KO", "PEP"), max_lags=3)
    assert diagnostics.lag_order >= 1
    assert isinstance(diagnostics.is_stable, bool)
    assert diagnostics.inverse_root_magnitudes.size == diagnostics.roots.size
