"""Tests for the A/H strategy and metric modules."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ah_pairs_trading.config import CostConfig, StrategyConfig
from ah_pairs_trading.data import prepare_signal_frame
from ah_pairs_trading.metrics import prepare_comparison_frame, rolling_beta, rolling_sharpe
from ah_pairs_trading.strategy import backtest_relative_value_strategy, grid_search_entry_z


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
            "a_open": np.exp(log_a) * 1.001,
            "h_open": np.exp(log_h) * 0.999,
            "a_volume": np.full(length, 4_000_000.0),
            "h_volume": np.full(length, 3_500_000.0),
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
        adv_window=20,
        adv_min_periods=10,
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
        cost_config=zero_cost_config(),
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
        cost_config=zero_cost_config(),
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
            "a_open": [100.0, 100.0, 100.0, 100.0, 100.0],
            "h_open": [50.0, 50.0, 50.0, 50.0, 50.0],
            "a_adv": [1_000_000.0] * 5,
            "h_adv": [1_000_000.0] * 5,
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
        cost_config=zero_cost_config(),
    )

    assert result.summary.trade_count == 1
    assert result.trades.iloc[0]["exit_reason"] == "cointegration_breakdown"
    assert result.equity_curve.loc[index[3], "position"] == "flat"


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
    zero_costs = zero_cost_config()

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
    zero_costs = zero_cost_config()

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
        cost_config=zero_cost_config(),
    )

    assert result.summary.trade_count > 0
    assert "signal_score" in result.equity_curve.columns


def test_next_open_execution_uses_lagged_signal_and_current_open_prices() -> None:
    index = pd.date_range("2024-01-01", periods=4, freq="B")
    signal_frame = pd.DataFrame(
        {
            "600036": [100.0, 101.0, 102.0, 103.0],
            "03968": [50.0, 49.0, 48.5, 48.0],
            "a_open": [100.0, 120.0, 121.0, 122.0],
            "h_open": [50.0, 40.0, 39.5, 39.0],
            "a_adv": [1_000_000.0] * 4,
            "h_adv": [1_000_000.0] * 4,
            "intercept": [0.1] * 4,
            "hedge_ratio": [1.0] * 4,
            "spread": [0.0, 0.0, 0.0, 0.0],
            "zscore": [1.3, 0.1, 0.0, 0.0],
            "cheap_leg": ["h", "flat", "flat", "flat"],
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
            stop_z=2.0,
            z_window=20,
            z_min_periods=20,
            max_holding_days=20,
            initial_capital=100_000.0,
            execution_mode="long_cheaper_leg_only",
            execution_timing="next_open",
        ),
        cost_config=zero_cost_config(),
    )

    assert result.summary.trade_count == 1
    assert result.trades.iloc[0]["entry_date"] == index[1]


def test_ecm_gate_blocks_entries_and_forces_exit() -> None:
    index = pd.date_range("2024-01-01", periods=5, freq="B")
    signal_frame = pd.DataFrame(
        {
            "600036": [100.0, 100.0, 100.0, 100.0, 100.0],
            "03968": [50.0, 50.0, 50.0, 50.0, 50.0],
            "a_open": [100.0] * 5,
            "h_open": [50.0] * 5,
            "a_adv": [1_000_000.0] * 5,
            "h_adv": [1_000_000.0] * 5,
            "intercept": [0.1] * 5,
            "hedge_ratio": [1.0] * 5,
            "spread": [0.12, 0.14, 0.13, 0.12, 0.01],
            "zscore": [1.2, 1.4, 1.1, 1.3, 0.1],
            "cheap_leg": ["h", "h", "h", "h", "flat"],
            "ecm_speed": [-0.2, -0.2, 0.1, 0.1, -0.1],
            "ecm_p_value": [0.01, 0.01, 0.4, 0.4, 0.01],
            "ecm_gate_pass": pd.Series([True, True, False, False, True], index=index, dtype="boolean"),
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
            initial_capital=100_000.0,
            execution_mode="paired",
            ecm_gate_mode="significant_negative",
        ),
        cost_config=zero_cost_config(),
    )

    assert result.summary.trade_count == 1
    assert result.trades.iloc[0]["exit_reason"] == "ecm_breakdown"


def test_adv_cap_reduces_position_size() -> None:
    signal_frame, hedge_ratio = make_ah_signal_frame()
    low_adv_frame = signal_frame.copy()
    low_adv_frame["h_adv"] = 1_500.0

    result = backtest_relative_value_strategy(
        signal_frame=low_adv_frame,
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
            initial_capital=100_000.0,
            execution_mode="long_cheaper_leg_only",
            max_adv_fraction=0.1,
            h_lot_size=100,
        ),
        cost_config=zero_cost_config(),
    )

    assert result.summary.trade_count > 0
    assert result.trades.iloc[0]["h_shares"] <= 100


def test_borrow_costs_reduce_paired_pnl() -> None:
    signal_frame, hedge_ratio = make_ah_signal_frame()
    strategy_config = StrategyConfig(
        entry_z_candidates=(0.8,),
        exit_z=0.25,
        stop_z=2.5,
        z_window=20,
        z_min_periods=20,
        max_holding_days=20,
        initial_capital=100_000.0,
        execution_mode="paired",
    )
    no_borrow = zero_cost_config()
    with_borrow = CostConfig(
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
        a_short_borrow_apr_bps=500.0,
        h_short_borrow_apr_bps=500.0,
        a_long_financing_apr_bps=0.0,
        h_long_financing_apr_bps=0.0,
    )

    baseline = backtest_relative_value_strategy(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=hedge_ratio,
        strategy_config=strategy_config,
        cost_config=no_borrow,
    )
    charged = backtest_relative_value_strategy(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=hedge_ratio,
        strategy_config=strategy_config,
        cost_config=with_borrow,
    )

    assert charged.summary.borrow_costs > 0.0
    assert charged.summary.final_capital < baseline.summary.final_capital


def test_mean_reversion_gate_blocks_entries_and_forces_exit() -> None:
    index = pd.date_range("2024-01-01", periods=5, freq="B")
    signal_frame = pd.DataFrame(
        {
            "600036": [100.0, 100.0, 100.0, 100.0, 100.0],
            "03968": [50.0, 50.0, 50.0, 50.0, 50.0],
            "a_open": [100.0] * 5,
            "h_open": [50.0] * 5,
            "a_adv": [1_000_000.0] * 5,
            "h_adv": [1_000_000.0] * 5,
            "intercept": [0.1] * 5,
            "hedge_ratio": [1.0] * 5,
            "spread": [0.12, 0.14, 0.13, 0.12, 0.01],
            "zscore": [1.2, 1.4, 1.1, 1.3, 0.1],
            "cheap_leg": ["h", "h", "h", "h", "flat"],
            "mean_reversion_gate_pass": pd.Series([True, True, False, False, True], index=index, dtype="boolean"),
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
            max_holding_days=20,
            initial_capital=100_000.0,
            execution_mode="paired",
            mean_reversion_gate_mode="both",
        ),
        cost_config=zero_cost_config(),
    )

    assert result.summary.trade_count == 1
    assert result.trades.iloc[0]["exit_reason"] == "mean_reversion_breakdown"
    assert result.equity_curve.loc[index[3], "position"] == "flat"


def test_mean_reversion_gate_requires_column_when_enabled() -> None:
    signal_frame, hedge_ratio = make_ah_signal_frame()
    try:
        backtest_relative_value_strategy(
            signal_frame=signal_frame,
            a_symbol="600036",
            h_symbol="03968",
            hedge_ratio=hedge_ratio,
            strategy_config=StrategyConfig(
                entry_z_candidates=(0.9,),
                exit_z=0.25,
                stop_z=2.5,
                max_holding_days=20,
                initial_capital=100_000.0,
                execution_mode="paired",
                mean_reversion_gate_mode="half_life_range",
            ),
            cost_config=zero_cost_config(),
        )
        raise AssertionError("expected a ValueError for a missing mean-reversion gate column")
    except ValueError as exc:
        assert "mean_reversion_gate_pass" in str(exc)


def test_vol_target_sizing_reduces_volatility() -> None:
    signal_frame, hedge_ratio = make_ah_signal_frame()
    common = dict(
        entry_z_candidates=(0.9,),
        exit_z=0.25,
        stop_z=2.5,
        z_window=20,
        z_min_periods=20,
        max_holding_days=20,
        position_size_fraction=1.0,
        initial_capital=100_000.0,
        execution_mode="long_cheaper_leg_only",
        vol_window=20,
        max_leverage=1.0,
    )
    fixed_result = backtest_relative_value_strategy(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=hedge_ratio,
        strategy_config=StrategyConfig(**common, position_sizing_mode="fixed"),
        cost_config=zero_cost_config(),
    )
    targeted_result = backtest_relative_value_strategy(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=hedge_ratio,
        strategy_config=StrategyConfig(**common, position_sizing_mode="vol_target", target_vol=0.05),
        cost_config=zero_cost_config(),
    )
    assert targeted_result.summary.trade_count > 0
    assert targeted_result.summary.annual_volatility < fixed_result.summary.annual_volatility


def test_drawdown_breaker_suspends_after_losses() -> None:
    index = pd.date_range("2024-01-01", periods=20, freq="B")
    signal_frame = pd.DataFrame(
        {
            "600036": [100.0] * 20,
            "03968": [50.0] * 20,
            "a_open": [100.0] * 20,
            "h_open": [50.0] * 20,
            "a_adv": [1_000_000.0] * 20,
            "h_adv": [1_000_000.0] * 20,
            "intercept": [0.1] * 20,
            "hedge_ratio": [1.0] * 20,
            "spread": [0.0] * 20,
            "zscore": [1.0] * 20,
            "cheap_leg": ["h"] * 20,
        },
        index=index,
    )
    strategy_config = StrategyConfig(
        entry_z_candidates=(0.5,),
        exit_z=0.0,
        stop_z=5.0,
        max_holding_days=100,
        position_size_fraction=1.0,
        initial_capital=100_000.0,
        execution_mode="long_cheaper_leg_only",
        max_drawdown=0.0,
        portfolio_max_drawdown=0.0,
    )
    result = backtest_relative_value_strategy(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        hedge_ratio=1.0,
        strategy_config=strategy_config,
        cost_config=zero_cost_config(),
    )
    assert result.summary.trade_count <= 1
