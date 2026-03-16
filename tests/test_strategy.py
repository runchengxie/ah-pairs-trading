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


def test_return_filter_can_block_entries() -> None:
    """The optional return filter should be able to suppress otherwise valid z-score entries."""

    signal_frame, hedge_ratio = make_ah_signal_frame()
    blocked_signal_frame = signal_frame.copy()
    blocked_signal_frame.loc[blocked_signal_frame["zscore"].notna(), "ret_spread_ema_filter_pass"] = False

    result = backtest_relative_value_strategy(
        signal_frame=blocked_signal_frame,
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
            return_filter_mode="ema",
            return_filter_window=10,
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

    assert result.summary.trade_count == 0
    assert result.summary.time_in_market == 0.0


def test_cointegration_gate_blocks_entries_and_forces_exit() -> None:
    """The rolling cointegration gate should block new entries and flatten broken relationships."""

    index = pd.date_range("2024-01-01", periods=5, freq="B")
    signal_frame = pd.DataFrame(
        {
            "600036": [100.0, 100.0, 100.0, 100.0, 100.0],
            "03968": [50.0, 50.0, 50.0, 50.0, 50.0],
            "intercept": [0.1, 0.1, 0.1, 0.1, 0.1],
            "hedge_ratio": [1.0, 1.0, 1.0, 1.0, 1.0],
            "spread": [0.12, 0.14, 0.13, 0.12, 0.01],
            "zscore": [1.2, 1.4, 1.1, 1.3, 0.1],
            "cheap_leg": ["h", "h", "h", "h", "flat"],
            "cointegration_p_value": [0.01, 0.01, 0.20, 0.20, 0.01],
            "cointegration_significant": pd.Series([True, True, False, False, True], index=index, dtype="boolean"),
            "cointegration_gate_pass": pd.Series([True, True, False, False, True], index=index, dtype="boolean"),
        },
        index=index,
    )

    result = backtest_relative_value_strategy(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=1.0,
        strategy_config=StrategyConfig(
            entry_z_candidates=(1.0,),
            exit_z=0.25,
            stop_z=2.5,
            z_window=20,
            z_min_periods=20,
            max_holding_days=20,
            position_size_fraction=0.8,
            initial_capital=100_000.0,
            execution_mode="paired",
            cointegration_gate_mode="significant",
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

    assert result.summary.trade_count == 1
    assert result.trades.iloc[0]["exit_reason"] == "cointegration_breakdown"
    assert result.equity_curve.loc[index[2], "position"] == "flat"


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


def test_paired_backtest_uses_row_level_hedge_ratio_for_sizing() -> None:
    """Paired sizing should follow the hedge ratio carried by the signal frame."""

    signal_frame, hedge_ratio = make_ah_signal_frame()
    high_hedge_ratio_frame = signal_frame.copy()
    low_hedge_ratio_frame = signal_frame.copy()
    high_hedge_ratio_frame["hedge_ratio"] = 2.0
    low_hedge_ratio_frame["hedge_ratio"] = 0.4
    strategy_config = StrategyConfig(
        entry_z_candidates=(0.8,),
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
    )
    zero_costs = CostConfig(
        a_buy_cost_bps=0.0,
        a_sell_cost_bps=0.0,
        h_buy_cost_bps=0.0,
        h_sell_cost_bps=0.0,
        h_stamp_duty_bps=0.0,
        fx_conversion_bps=0.0,
    )

    high_result = backtest_relative_value_strategy(
        signal_frame=high_hedge_ratio_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=hedge_ratio,
        strategy_config=strategy_config,
        cost_config=zero_costs,
    )
    low_result = backtest_relative_value_strategy(
        signal_frame=low_hedge_ratio_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=hedge_ratio,
        strategy_config=strategy_config,
        cost_config=zero_costs,
    )

    assert not high_result.trades.empty
    assert not low_result.trades.empty
    assert high_result.trades.iloc[0]["h_shares"] > low_result.trades.iloc[0]["h_shares"]


def test_return_spread_entry_signal_mode_is_available() -> None:
    """A standardized return-spread signal should be usable as the primary entry mode."""

    signal_frame, hedge_ratio = make_ah_signal_frame()
    result = backtest_relative_value_strategy(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=hedge_ratio,
        strategy_config=StrategyConfig(
            entry_z_candidates=(0.5,),
            exit_z=0.2,
            stop_z=2.0,
            z_window=20,
            z_min_periods=20,
            max_holding_days=20,
            position_size_fraction=0.8,
            initial_capital=100_000.0,
            execution_mode="paired",
            entry_signal_mode="ret_spread_ema",
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

    assert result.summary.trade_count > 0
    assert "signal_score" in result.equity_curve.columns
