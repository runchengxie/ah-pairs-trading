"""Statistical analysis tools for the pairs trading workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.api import VAR
from statsmodels.tsa.stattools import coint

from .config import SegmentWindow


@dataclass(slots=True)
class CointegrationResult:
    """The output of an Engle-Granger style cointegration analysis."""

    dependent_symbol: str
    independent_symbol: str
    alpha: float
    score: float
    p_value: float
    critical_values: tuple[float, ...]
    significant: bool
    intercept: float
    hedge_ratio: float
    residual_mean: float
    residual_std: float
    residuals: pd.Series
    regression_summary_text: str
    regression_params: pd.Series


@dataclass(slots=True)
class ECMResult:
    """The output of a simple error-correction model."""

    error_correction_speed: float
    error_correction_p_value: float
    coefficients: pd.Series
    summary_text: str


@dataclass(slots=True)
class MeanReversionResult:
    """Mean-reversion characteristics estimated from the residual series."""

    ar_coefficient: float
    theta: float | None
    half_life: float | None


@dataclass(slots=True)
class MatrixOLSResult:
    """Manual matrix OLS estimates for the cointegration regression."""

    intercept: float
    hedge_ratio: float


@dataclass(slots=True)
class VARDiagnostics:
    """Lag selection and stability diagnostics for a VAR model."""

    lag_order: int
    selected_orders: dict[str, int | None]
    is_stable: bool
    roots: np.ndarray
    inverse_root_magnitudes: np.ndarray
    selection_summary_text: str
    model_summary_text: str


def run_cointegration_analysis(
    log_prices: pd.DataFrame,
    dependent_symbol: str,
    independent_symbol: str,
    alpha: float = 0.05,
) -> CointegrationResult:
    """Run the Engle-Granger test and the hedge-ratio regression."""

    dependent_series = log_prices[dependent_symbol]
    independent_series = log_prices[independent_symbol]
    score, p_value, critical_values = coint(dependent_series, independent_series)

    regression_x = sm.add_constant(independent_series)
    regression = sm.OLS(dependent_series, regression_x).fit()
    intercept = float(regression.params["const"])
    hedge_ratio = float(regression.params[independent_symbol])
    residuals = regression.resid.rename("residual")

    return CointegrationResult(
        dependent_symbol=dependent_symbol,
        independent_symbol=independent_symbol,
        alpha=alpha,
        score=float(score),
        p_value=float(p_value),
        critical_values=tuple(float(value) for value in critical_values),
        significant=bool(p_value < alpha),
        intercept=intercept,
        hedge_ratio=hedge_ratio,
        residual_mean=float(residuals.mean()),
        residual_std=float(residuals.std()),
        residuals=residuals,
        regression_summary_text=regression.summary().as_text(),
        regression_params=regression.params,
    )


def fit_error_correction_model(log_prices: pd.DataFrame, coint_result: CointegrationResult) -> ECMResult:
    """Fit a one-lag error-correction model from a cointegration result."""

    ecm_frame = pd.DataFrame(
        {
            "dependent_diff": log_prices[coint_result.dependent_symbol].diff(),
            "independent_diff": log_prices[coint_result.independent_symbol].diff(),
            "error_correction_lag1": coint_result.residuals.shift(1),
        }
    ).dropna()

    regression_x = sm.add_constant(ecm_frame[["error_correction_lag1", "independent_diff"]])
    regression = sm.OLS(ecm_frame["dependent_diff"], regression_x).fit()

    return ECMResult(
        error_correction_speed=float(regression.params["error_correction_lag1"]),
        error_correction_p_value=float(regression.pvalues["error_correction_lag1"]),
        coefficients=regression.params,
        summary_text=regression.summary().as_text(),
    )


def estimate_mean_reversion(residuals: pd.Series) -> MeanReversionResult:
    """Estimate the AR(1) coefficient, theta, and half-life for residuals."""

    frame = pd.DataFrame({"residual": residuals, "residual_lag1": residuals.shift(1)}).dropna()
    regression_x = sm.add_constant(frame["residual_lag1"])
    regression = sm.OLS(frame["residual"], regression_x).fit()
    ar_coefficient = float(regression.params["residual_lag1"])

    theta: float | None = None
    half_life: float | None = None
    if 0 < abs(ar_coefficient) < 1:
        theta = float(-np.log(abs(ar_coefficient)))
        half_life = float(np.log(2) / theta)

    return MeanReversionResult(
        ar_coefficient=ar_coefficient,
        theta=theta,
        half_life=half_life,
    )


def run_segment_analysis(
    log_prices: pd.DataFrame,
    dependent_symbol: str,
    independent_symbol: str,
    segments: tuple[SegmentWindow, ...],
    alpha: float = 0.05,
    min_samples: int = 30,
) -> pd.DataFrame:
    """Run cointegration analysis on a sequence of fixed windows."""

    rows: list[dict[str, Any]] = []
    for segment in segments:
        segment_frame = log_prices.loc[segment.start : segment.end]
        if len(segment_frame) < min_samples:
            continue

        result = run_cointegration_analysis(
            segment_frame,
            dependent_symbol=dependent_symbol,
            independent_symbol=independent_symbol,
            alpha=alpha,
        )
        rows.append(
            {
                "segment": segment.resolved_label(),
                "sample_size": len(segment_frame),
                "p_value": result.p_value,
                "significant": result.significant,
                "intercept": result.intercept,
                "hedge_ratio": result.hedge_ratio,
                "residual_mean": result.residual_mean,
                "residual_std": result.residual_std,
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "sample_size",
                "p_value",
                "significant",
                "intercept",
                "hedge_ratio",
                "residual_mean",
                "residual_std",
            ]
        )

    return pd.DataFrame(rows).set_index("segment")


def run_rolling_cointegration(
    log_prices: pd.DataFrame,
    dependent_symbol: str,
    independent_symbol: str,
    window_size: int,
    step_size: int,
    alpha: float = 0.05,
) -> pd.DataFrame:
    """Run a rolling cointegration diagnostic across the full sample."""

    rows: list[dict[str, Any]] = []
    total_rows = len(log_prices)
    for start_index in range(0, total_rows - window_size + 1, step_size):
        window_frame = log_prices.iloc[start_index : start_index + window_size]
        result = run_cointegration_analysis(
            window_frame,
            dependent_symbol=dependent_symbol,
            independent_symbol=independent_symbol,
            alpha=alpha,
        )
        rows.append(
            {
                "window_end": window_frame.index[-1],
                "p_value": result.p_value,
                "hedge_ratio": result.hedge_ratio,
                "residual_std": result.residual_std,
            }
        )

    if not rows:
        return pd.DataFrame(columns=["p_value", "hedge_ratio", "residual_std"])

    return pd.DataFrame(rows).set_index("window_end")


def matrix_ols(
    dependent_series: pd.Series,
    independent_series: pd.Series,
) -> MatrixOLSResult:
    """Estimate the cointegration regression with the matrix OLS formula."""

    y = dependent_series.to_numpy().reshape(-1, 1)
    x = np.column_stack([np.ones(len(independent_series)), independent_series.to_numpy()])
    coefficients = np.linalg.solve(x.T @ x, x.T @ y).ravel()
    return MatrixOLSResult(intercept=float(coefficients[0]), hedge_ratio=float(coefficients[1]))


def fit_var_diagnostics(
    log_prices: pd.DataFrame,
    symbols: tuple[str, str],
    max_lags: int = 5,
    criterion: str = "bic",
) -> VARDiagnostics:
    """Fit a VAR on differenced log prices and report stability diagnostics."""

    diff_frame = log_prices.loc[:, list(symbols)].diff().dropna()
    if diff_frame.empty:
        raise ValueError("The differenced log-price frame is empty and cannot be used for VAR diagnostics.")

    model = VAR(diff_frame)
    order_selection = model.select_order(maxlags=max_lags)
    selected_orders = {
        key: (int(value) if value is not None else None)
        for key, value in order_selection.selected_orders.items()
    }
    lag_order = selected_orders.get(criterion) or 1

    result = model.fit(lag_order)
    roots = np.asarray(result.roots)
    inverse_root_magnitudes = np.array([], dtype=float)
    if roots.size > 0:
        inverse_root_magnitudes = 1.0 / np.abs(roots)

    return VARDiagnostics(
        lag_order=lag_order,
        selected_orders=selected_orders,
        is_stable=bool(result.is_stable()),
        roots=roots,
        inverse_root_magnitudes=inverse_root_magnitudes,
        selection_summary_text=order_selection.summary().as_text(),
        model_summary_text=str(result.summary()),
    )
