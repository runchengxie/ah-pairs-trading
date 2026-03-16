"""Tests for the A/H strategy and metric modules."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ah_pairs_trading.config import CostConfig, StrategyConfig
from ah_pairs_trading.data import prepare_signal_frame
from ah_pairs_trading.metrics import prepare_comparison_frame, rolling_beta, rolling_sharpe
from ah_pairs_trading.strategy import backtest_relative_value_strategy, grid_search_entry_z


def make_ah_signal_frame(length: int = 220) -> tuple[pd.DataFrame, float]:
    """Create a deterministic A/H signal frame with repeated mean reversion."""

    index = pd.date_range("2021-01-01", periods=length, freq="B")
    log_h = np.log(80.0) + 0.0012 * np.arange(length)
    residual = 0.06 * np.sin(np.linspace(0.0, 14.0 * np.pi, length))
    intercept = 0.18
    hedge_ratio = 1.08
    log_a = intercept + hedge_ratio * log_h + residual

    model_prices = pd.DataFrame(
        {
            "600036": np.exp(log_a),
            "03968": np.exp(log_h),
        },
        index=index,
    )
    signal_frame = prepare_signal_frame(
        model_prices,
        a_symbol="600036",
        h_symbol="03968",
        intercept=intercept,
        hedge_ratio=hedge_ratio,
        z_window=20,
        min_periods=20,
    )
    return signal_frame, hedge_ratio


def test_long_cheaper_leg_backtest_generates_trades_and_positive_pnl() -> None:
    """A clean A/H mean-reverting spread should produce profitable long-only trades."""

    signal_frame, hedge_ratio = make_ah_signal_frame()
    result = backtest_relative_value_strategy(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=hedge_ratio,
        strategy_config=StrategyConfig(
            entry_z_candidates=(0.9,),
            exit_z=0.25,
            stop_z=2.5,
            z_window=20,
            z_min_periods=20,
            max_holding_days=20,
            position_size_fraction=0.8,
            initial_capital=100_000.0,
            execution_mode="long_cheaper_leg_only",
            a_lot_size=100,
            h_lot_size=100,
        ),
        cost_config=CostConfig(
            a_buy_cost_bps=0.0,
            a_sell_cost_bps=0.0,
            h_buy_cost_bps=0.0,
            h_sell_cost_bps=0.0,
            h_stamp_duty_bps=0.0,
            fx_conversion_bps=0.0,
        ),
    )

    assert result.summary.trade_count >= 4
    assert result.summary.final_capital > 100_000.0
    assert not result.trades.empty
    assert set(result.trades["direction"].unique()) <= {"long_a_only", "long_h_only"}
    assert result.summary.annual_volatility >= 0.0
    assert result.summary.time_in_market > 0.0
    assert {"capital", "returns", "spread", "zscore", "position", "gross_exposure"} <= set(result.equity_curve.columns)


def test_paired_backtest_and_grid_search_are_available() -> None:
    """Paired-mode execution and grid search should both be usable."""

    signal_frame, hedge_ratio = make_ah_signal_frame()
    strategy_config = StrategyConfig(
        entry_z_candidates=(0.8, 1.0, 1.2),
        exit_z=0.25,
        stop_z=2.5,
        z_window=20,
        z_min_periods=20,
        max_holding_days=20,
        position_size_fraction=0.8,
        initial_capital=100_000.0,
        objective="sharpe_ratio",
        execution_mode="paired",
        a_lot_size=100,
        h_lot_size=100,
    )
    zero_costs = CostConfig(
        a_buy_cost_bps=0.0,
        a_sell_cost_bps=0.0,
        h_buy_cost_bps=0.0,
        h_sell_cost_bps=0.0,
        h_stamp_duty_bps=0.0,
        fx_conversion_bps=0.0,
    )

    best_entry_z, grid = grid_search_entry_z(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=hedge_ratio,
        strategy_config=strategy_config,
        cost_config=zero_costs,
    )
    assert best_entry_z in {0.8, 1.0, 1.2}
    assert {
        "final_capital",
        "annual_return",
        "annual_volatility",
        "sharpe_ratio",
        "max_drawdown",
        "calmar_ratio",
        "trade_count",
        "win_rate",
        "time_in_market",
        "total_costs",
    } <= set(grid.columns)

    backtest = backtest_relative_value_strategy(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=hedge_ratio,
        strategy_config=StrategyConfig(
            entry_z_candidates=(best_entry_z,),
            exit_z=0.25,
            stop_z=2.5,
            z_window=20,
            z_min_periods=20,
            max_holding_days=20,
            position_size_fraction=0.8,
            initial_capital=100_000.0,
            execution_mode="paired",
            a_lot_size=100,
            h_lot_size=100,
        ),
        cost_config=zero_costs,
    )
    benchmark_returns = signal_frame["600036"].pct_change().rename("benchmark_ret").dropna()
    comparison = prepare_comparison_frame(backtest.equity_curve["returns"], benchmark_returns, benchmark_label="A-share")
    beta_series = rolling_beta(comparison, window=20)
    sharpe_series = rolling_sharpe(backtest.equity_curve["returns"], window=20)

    assert backtest.summary.trade_count >= 4
    assert set(backtest.trades["direction"].unique()) <= {"short_a_long_h", "long_a_short_h"}
    assert set(backtest.trades["a_side"].unique()) <= {-1, 1}
    assert set(backtest.trades["h_side"].unique()) <= {-1, 1}
    assert backtest.summary.total_costs == 0.0
    assert not comparison.empty
    assert not beta_series.empty
    assert sharpe_series.notna().sum() > 0
