"""Tests for the weight-based spread-arbitrage backtest engine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ah_pairs_trading.config import CostConfig, DataConfig, PipelineConfig, StrategyConfig
from ah_pairs_trading.data import (
    build_ah_price_frame,
    load_ah_pair_data,
    prepare_log_price_frame,
    prepare_model_price_frame,
    prepare_signal_frame,
    simulate_pair_prices,
)
from ah_pairs_trading.ou import rolling_ou_mle_params
from ah_pairs_trading.weight_strategy import (
    backtest_spread_arbitrage_strategy,
    grid_search_entry_z_weight,
    make_dynamic_weight_signals,
    run_weight_benchmarks,
    vol_target_position_sizing,
)


def _weight_signal_frame(length: int = 320) -> pd.DataFrame:
    a_frame, h_frame = simulate_pair_prices("2018-01-01", "2019-06-01", seed=3, beta=1.1)
    fx = pd.DataFrame({"fx_rate": 0.92}, index=a_frame.index)
    aligned = build_ah_price_frame(a_frame, h_frame, fx, "2018-01-01", "2019-06-01")
    model_prices = prepare_model_price_frame(aligned, "600036", "03968")
    log_prices = prepare_log_price_frame(model_prices[["600036", "03968"]])
    ou_params = rolling_ou_mle_params(log_prices, "600036", "03968", window=80)
    return prepare_signal_frame(
        model_prices,
        a_symbol="600036",
        h_symbol="03968",
        intercept=0.0,
        hedge_ratio=1.1,
        z_window=40,
        min_periods=20,
        ou_params=ou_params,
        mean_reversion_gate_mode="both",
        half_life_min_days=1,
        half_life_max_days=200,
        lb_p_value_min=0.001,
    )


def _weight_strategy_config() -> StrategyConfig:
    return StrategyConfig(
        entry_z_candidates=(1.0,),
        exit_z=0.5,
        stop_z=3.0,
        max_holding_days=60,
        position_size_fraction=1.0,
        initial_capital=100_000.0,
        execution_mode="paired",
        min_weight=0.1,
        half_life_min_days=1,
        half_life_max_days=200,
        lb_p_value_min=0.001,
        target_vol=0.10,
        vol_window=40,
        max_leverage=2.0,
        max_drawdown=0.5,
        portfolio_max_drawdown=0.9,
    )


def test_weight_engine_produces_equity_and_trades() -> None:
    signal_frame = _weight_signal_frame()
    result = backtest_spread_arbitrage_strategy(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        strategy_config=_weight_strategy_config(),
        cost_config=CostConfig(),
    )

    assert not result.equity_curve.empty
    assert {"capital", "returns", "spread", "zscore", "position", "h", "gross_exposure", "drawdown"} <= set(
        result.equity_curve.columns
    )
    assert result.summary.trade_count > 0
    assert result.summary.annual_volatility > 0.0
    assert set(result.trades["direction"].unique()) <= {"short_a_long_h", "long_a_short_h"}


def test_weight_engine_requires_ou_diagnostics() -> None:
    signal_frame = _weight_signal_frame().drop(columns=["ou_k", "ou_L", "ou_a"])
    try:
        backtest_spread_arbitrage_strategy(
            signal_frame=signal_frame,
            a_symbol="600036",
            h_symbol="03968",
            strategy_config=_weight_strategy_config(),
            cost_config=CostConfig(),
        )
        raise AssertionError("expected a ValueError for missing OU diagnostics")
    except ValueError as exc:
        assert "OU diagnostics" in str(exc)


def test_make_dynamic_weight_signals_tilts_away_from_expensive_leg() -> None:
    signal_frame = _weight_signal_frame()
    signals = make_dynamic_weight_signals(
        signal_frame,
        entry_z=1.0,
        exit_z=0.5,
        stop_z=3.0,
        hl_min=1.0,
        hl_max=200.0,
        lb_p_min=0.001,
        min_weight=0.1,
    )
    positive_z = signals.loc[signals["z"] > 1.5]
    if not positive_z.empty:
        assert (positive_z["weight_a_target"] < 0.5).all()


def test_vol_target_position_sizing_caps_leverage() -> None:
    signal_frame = _weight_signal_frame()
    price_frame = signal_frame[["600036", "03968"]].copy()
    price_frame["ret_a"] = price_frame["600036"].pct_change().fillna(0.0)
    price_frame["ret_h"] = price_frame["03968"].pct_change().fillna(0.0)
    signals = make_dynamic_weight_signals(
        signal_frame,
        entry_z=1.0,
        exit_z=0.5,
        stop_z=3.0,
        hl_min=1.0,
        hl_max=200.0,
        lb_p_min=0.001,
        min_weight=0.1,
    )
    pos = vol_target_position_sizing(
        price_frame,
        signals,
        vol_window=40,
        vol_min_periods=20,
        target_vol=0.10,
        max_leverage=1.5,
    )
    assert pos["h"].max() <= 1.5 + 1e-12
    assert pos["h"].min() >= 0.0


def test_grid_search_entry_z_weight_picks_candidate() -> None:
    signal_frame = _weight_signal_frame()
    config = _weight_strategy_config()
    best_entry_z, grid = grid_search_entry_z_weight(
        signal_frame=signal_frame,
        a_symbol="600036",
        h_symbol="03968",
        strategy_config=config,
        cost_config=CostConfig(),
    )
    assert best_entry_z in config.entry_z_candidates
    assert "sharpe_ratio" in grid.columns


def test_weight_benchmarks_run_for_all_reference_curves() -> None:
    signal_frame = _weight_signal_frame()
    benchmarks = run_weight_benchmarks(
        signal_frame, "600036", "03968", _weight_strategy_config(), CostConfig()
    )
    assert set(benchmarks) == {"bm_hold_5050", "bm_mix_5050", "bm_mix_5050_risk", "bm_hold_a", "bm_hold_h"}
    for frame in benchmarks.values():
        assert "nav" in frame.columns


def test_simulated_pipeline_end_to_end() -> None:
    pipeline_config = PipelineConfig(
        a_symbol="600036",
        h_symbol="03968",
        start_date="2018-01-01",
        end_date="2019-06-01",
        train_end_date="2019-01-01",
        require_significant_cointegration=False,
        data=DataConfig(data_provider="simulated", constant_fx_rate=0.92),
        strategy=_weight_strategy_config(),
        cache_dir=None,
    )
    from ah_pairs_trading.pipeline import run_ah_relative_value_pipeline

    result = run_ah_relative_value_pipeline(pipeline_config)
    assert not result.test_backtest.equity_curve.empty
