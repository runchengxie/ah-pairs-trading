"""Performance metric helpers used by the backtests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm


def compute_max_drawdown(capital_series: pd.Series) -> float:
    """Return the worst drawdown observed in a capital series."""

    running_max = capital_series.cummax()
    drawdown = capital_series / running_max - 1.0
    return float(drawdown.min())


def add_drawdown_columns(equity_curve: pd.DataFrame) -> pd.DataFrame:
    """Attach running maximum and drawdown columns to an equity curve."""

    result = equity_curve.copy()
    result["running_max"] = result["capital"].cummax()
    result["drawdown"] = result["capital"] / result["running_max"] - 1.0
    return result


def rolling_sharpe(returns: pd.Series, window: int = 60) -> pd.Series:
    """Compute a rolling annualized Sharpe ratio from daily returns."""

    rolling_mean = returns.rolling(window).mean()
    rolling_std = returns.rolling(window).std()
    sharpe = np.sqrt(252) * rolling_mean / rolling_std
    return sharpe.replace([np.inf, -np.inf], np.nan).rename("rolling_sharpe")


def prepare_comparison_frame(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    benchmark_label: str = "benchmark",
) -> pd.DataFrame:
    """Align strategy and benchmark returns into one reporting frame."""

    comparison = pd.DataFrame(
        {
            "strategy_ret": strategy_returns,
            "benchmark_ret": benchmark_returns,
        }
    ).dropna()

    comparison["cum_strategy"] = (1.0 + comparison["strategy_ret"]).cumprod()
    comparison["cum_benchmark"] = (1.0 + comparison["benchmark_ret"]).cumprod()
    comparison["excess_ret"] = comparison["strategy_ret"] - comparison["benchmark_ret"]
    comparison["cum_excess_ret"] = (1.0 + comparison["excess_ret"]).cumprod()
    comparison.attrs["benchmark_label"] = benchmark_label
    return comparison


def rolling_beta(comparison: pd.DataFrame, window: int = 60) -> pd.Series:
    """Estimate a rolling beta of strategy returns against benchmark returns."""

    if window >= len(comparison):
        return pd.Series(dtype=float, name="rolling_beta")

    betas: list[float] = []
    index_values: list[pd.Timestamp] = []
    for row_index in range(window, len(comparison) + 1):
        sample = comparison.iloc[row_index - window : row_index]
        regression_x = sm.add_constant(sample["benchmark_ret"])
        regression = sm.OLS(sample["strategy_ret"], regression_x).fit()
        betas.append(float(regression.params["benchmark_ret"]))
        index_values.append(sample.index[-1])

    return pd.Series(betas, index=index_values, name="rolling_beta")
