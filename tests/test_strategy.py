"""Tests for the backtesting and metric modules."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ah_pairs_trading.metrics import prepare_comparison_frame, rolling_beta, rolling_sharpe
from ah_pairs_trading.strategy import backtest_fixed_beta, grid_search_entry_z


def make_mean_reverting_price_frame(length: int = 180) -> tuple[pd.DataFrame, float, float, float, float]:
    """Create a deterministic spread that crosses entry and exit thresholds."""

    index = pd.date_range("2021-01-01", periods=length, freq="B")
    log_independent = np.log(90.0) + 0.0015 * np.arange(length)
    residual = 0.06 * np.sin(np.linspace(0.0, 12.0 * np.pi, length))
    intercept = 0.12
    hedge_ratio = 1.1
    log_dependent = intercept + hedge_ratio * log_independent + residual

    price_frame = pd.DataFrame(
        {
            "KO": np.exp(log_dependent),
            "PEP": np.exp(log_independent),
        },
        index=index,
    )
    return price_frame, intercept, hedge_ratio, float(residual.mean()), float(residual.std())


def test_backtest_generates_trades_and_positive_pnl() -> None:
    """A clean mean-reverting spread should produce trades and gains."""

    price_frame, intercept, hedge_ratio, residual_mean, residual_std = make_mean_reverting_price_frame()
    result = backtest_fixed_beta(
        price_frame=price_frame,
        dependent_symbol="KO",
        independent_symbol="PEP",
        intercept=intercept,
        hedge_ratio=hedge_ratio,
        residual_mean=residual_mean,
        residual_std=residual_std,
        entry_z=0.8,
        exit_z=0.1,
        initial_capital=100_000.0,
    )

    assert result.summary.trade_count >= 4
    assert result.summary.final_capital > 100_000.0
    assert not result.trades.empty
    assert {"capital", "returns", "spread", "zscore", "position"} <= set(result.equity_curve.columns)


def test_grid_search_and_rolling_metrics_are_available() -> None:
    """Grid search should pick a candidate and metrics should be computable."""

    price_frame, intercept, hedge_ratio, residual_mean, residual_std = make_mean_reverting_price_frame()
    best_entry_z, grid = grid_search_entry_z(
        price_frame=price_frame,
        dependent_symbol="KO",
        independent_symbol="PEP",
        intercept=intercept,
        hedge_ratio=hedge_ratio,
        residual_mean=residual_mean,
        residual_std=residual_std,
        entry_z_candidates=(0.6, 0.8, 1.0),
        exit_z=0.1,
        initial_capital=100_000.0,
    )
    assert best_entry_z in {0.6, 0.8, 1.0}
    assert {"final_capital", "annual_return", "sharpe_ratio", "max_drawdown", "trade_count"} <= set(grid.columns)

    backtest = backtest_fixed_beta(
        price_frame=price_frame,
        dependent_symbol="KO",
        independent_symbol="PEP",
        intercept=intercept,
        hedge_ratio=hedge_ratio,
        residual_mean=residual_mean,
        residual_std=residual_std,
        entry_z=best_entry_z,
        exit_z=0.1,
        initial_capital=100_000.0,
    )
    benchmark_returns = price_frame["PEP"].pct_change().rename("benchmark_ret").dropna()
    comparison = prepare_comparison_frame(backtest.equity_curve["returns"], benchmark_returns, benchmark_label="PEP")
    beta_series = rolling_beta(comparison, window=20)
    sharpe_series = rolling_sharpe(backtest.equity_curve["returns"], window=20)

    assert not comparison.empty
    assert not beta_series.empty
    assert sharpe_series.notna().sum() > 0
