"""End-to-end orchestration for the A/H relative-value package."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .cache import StageCache, frame_digest
from .analysis import (
    CointegrationResult,
    ECMResult,
    MatrixOLSResult,
    MeanReversionResult,
    VARDiagnostics,
    estimate_mean_reversion,
    fit_error_correction_model,
    fit_var_diagnostics,
    matrix_ols,
    run_cointegration_analysis,
    run_rolling_cointegration,
    run_segment_analysis,
)
from .config import PipelineConfig, StrategyConfig
from .data import (
    load_ah_pair_data,
    prepare_log_price_frame,
    prepare_signal_frame,
    split_train_test,
)
from .metrics import prepare_comparison_frame, rolling_beta, rolling_sharpe
from .plotting import (
    plot_cumulative_returns,
    plot_equity_curve,
    plot_excess_returns,
    plot_log_prices,
    plot_rolling_beta,
    plot_rolling_cointegration,
    plot_rolling_sharpe,
    plot_z_search,
)
from .strategy import BacktestResult, backtest_relative_value_strategy, grid_search_entry_z


@dataclass(slots=True)
class PipelineResult:
    """The full set of outputs produced by the A/H workflow."""

    aligned_prices: pd.DataFrame
    prices: pd.DataFrame
    log_prices: pd.DataFrame
    signal_frame: pd.DataFrame
    train_prices: pd.DataFrame
    test_prices: pd.DataFrame
    train_signal_frame: pd.DataFrame
    test_signal_frame: pd.DataFrame
    full_sample_cointegration: CointegrationResult
    training_cointegration: CointegrationResult
    ecm: ECMResult
    mean_reversion: MeanReversionResult
    segment_analysis: pd.DataFrame
    rolling_cointegration: pd.DataFrame
    matrix_ols: MatrixOLSResult
    var_diagnostics: VARDiagnostics
    train_grid_search: pd.DataFrame
    best_entry_z: float
    train_backtest: BacktestResult
    test_backtest: BacktestResult
    benchmark_comparison: pd.DataFrame
    rolling_sharpe: pd.Series
    rolling_beta: pd.Series


_PIPELINE_CACHE_VERSION = 1


def _cointegration_to_dict(result: CointegrationResult) -> dict[str, Any]:
    return {
        "dependent_symbol": result.dependent_symbol,
        "independent_symbol": result.independent_symbol,
        "alpha": result.alpha,
        "score": result.score,
        "p_value": result.p_value,
        "critical_values": list(result.critical_values),
        "significant": result.significant,
        "intercept": result.intercept,
        "hedge_ratio": result.hedge_ratio,
        "residual_mean": result.residual_mean,
        "residual_std": result.residual_std,
    }


def _mean_reversion_to_dict(result: MeanReversionResult) -> dict[str, Any]:
    return {
        "ar_coefficient": result.ar_coefficient,
        "theta": result.theta,
        "half_life": result.half_life,
    }


def _matrix_ols_to_dict(result: MatrixOLSResult) -> dict[str, Any]:
    return {
        "intercept": result.intercept,
        "hedge_ratio": result.hedge_ratio,
    }


def _var_diagnostics_to_dict(result: VARDiagnostics) -> dict[str, Any]:
    return {
        "lag_order": result.lag_order,
        "selected_orders": result.selected_orders,
        "is_stable": result.is_stable,
        "roots": [{"real": float(root.real), "imag": float(root.imag)} for root in result.roots],
        "inverse_root_magnitudes": result.inverse_root_magnitudes.tolist(),
    }


def _strategy_with_entry_z(strategy_config: StrategyConfig, entry_z: float) -> StrategyConfig:
    return StrategyConfig(
        entry_z_candidates=(entry_z,),
        exit_z=strategy_config.exit_z,
        stop_z=strategy_config.stop_z,
        z_window=strategy_config.z_window,
        z_min_periods=strategy_config.z_min_periods,
        max_holding_days=strategy_config.max_holding_days,
        position_size_fraction=strategy_config.position_size_fraction,
        initial_capital=strategy_config.initial_capital,
        objective=strategy_config.objective,
        execution_mode=strategy_config.execution_mode,
        a_lot_size=strategy_config.a_lot_size,
        h_lot_size=strategy_config.h_lot_size,
    )


def _save_dataframe(frame: pd.DataFrame | pd.Series, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(frame, pd.Series):
        frame.to_frame().to_csv(output_path)
    else:
        frame.to_csv(output_path)


def _save_text(text: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")


def _save_outputs(config: PipelineConfig, result: PipelineResult) -> None:
    if config.output_dir is None:
        return

    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    _save_dataframe(result.aligned_prices, output_dir / "aligned_prices.csv")
    _save_dataframe(result.prices, output_dir / "model_prices.csv")
    _save_dataframe(result.log_prices, output_dir / "log_prices.csv")
    _save_dataframe(result.signal_frame, output_dir / "signal_frame.csv")
    _save_dataframe(result.segment_analysis, output_dir / "segment_analysis.csv")
    _save_dataframe(result.rolling_cointegration, output_dir / "rolling_cointegration.csv")
    _save_dataframe(result.train_grid_search, output_dir / "train_grid_search.csv")
    _save_dataframe(result.train_backtest.equity_curve, output_dir / "train_equity_curve.csv")
    _save_dataframe(result.train_backtest.trades, output_dir / "train_trades.csv")
    _save_dataframe(result.test_backtest.equity_curve, output_dir / "test_equity_curve.csv")
    _save_dataframe(result.test_backtest.trades, output_dir / "test_trades.csv")
    _save_dataframe(result.benchmark_comparison, output_dir / "test_benchmark_comparison.csv")
    _save_dataframe(result.rolling_sharpe, output_dir / "test_rolling_sharpe.csv")
    _save_dataframe(result.rolling_beta, output_dir / "test_rolling_beta.csv")

    summary_payload = {
        "full_sample_cointegration": _cointegration_to_dict(result.full_sample_cointegration),
        "training_cointegration": _cointegration_to_dict(result.training_cointegration),
        "ecm": {
            "error_correction_speed": result.ecm.error_correction_speed,
            "error_correction_p_value": result.ecm.error_correction_p_value,
            "coefficients": {key: float(value) for key, value in result.ecm.coefficients.items()},
        },
        "mean_reversion": _mean_reversion_to_dict(result.mean_reversion),
        "matrix_ols": _matrix_ols_to_dict(result.matrix_ols),
        "var_diagnostics": _var_diagnostics_to_dict(result.var_diagnostics),
        "best_entry_z": result.best_entry_z,
        "train_backtest": result.train_backtest.summary.as_dict(),
        "test_backtest": result.test_backtest.summary.as_dict(),
        "execution_mode": config.strategy.execution_mode,
        "share_ratio": config.data.share_ratio,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")

    _save_text(result.full_sample_cointegration.regression_summary_text, output_dir / "full_sample_ols_summary.txt")
    _save_text(result.training_cointegration.regression_summary_text, output_dir / "train_ols_summary.txt")
    _save_text(result.ecm.summary_text, output_dir / "ecm_summary.txt")
    _save_text(result.var_diagnostics.selection_summary_text, output_dir / "var_selection_summary.txt")
    _save_text(result.var_diagnostics.model_summary_text, output_dir / "var_model_summary.txt")

    plot_log_prices(
        result.log_prices,
        dependent_symbol=config.a_symbol,
        independent_symbol=config.h_symbol,
        output_path=output_dir / "log_prices.png",
    )
    if not result.rolling_cointegration.empty:
        plot_rolling_cointegration(result.rolling_cointegration, output_path=output_dir / "rolling_cointegration.png")
    plot_z_search(result.train_grid_search, output_path=output_dir / "z_grid_search.png")
    plot_equity_curve(result.train_backtest.equity_curve, "Train Equity Curve", output_path=output_dir / "train_equity_curve.png")
    plot_equity_curve(result.test_backtest.equity_curve, "Test Equity Curve", output_path=output_dir / "test_equity_curve.png")
    plot_rolling_sharpe(
        result.rolling_sharpe,
        "Test Rolling Sharpe Ratio",
        output_path=output_dir / "test_rolling_sharpe.png",
    )
    if not result.benchmark_comparison.empty and config.benchmark_symbol is not None:
        plot_cumulative_returns(
            result.benchmark_comparison,
            benchmark_label=config.benchmark_symbol,
            output_path=output_dir / "test_vs_benchmark.png",
        )
        plot_excess_returns(result.benchmark_comparison, output_path=output_dir / "test_excess_returns.png")
    if not result.rolling_beta.empty and config.benchmark_symbol is not None:
        plot_rolling_beta(
            result.rolling_beta,
            benchmark_label=config.benchmark_symbol,
            output_path=output_dir / "test_rolling_beta.png",
        )


def run_ah_relative_value_pipeline(
    config: PipelineConfig,
    *,
    a_frame: pd.DataFrame | None = None,
    h_frame: pd.DataFrame | None = None,
    fx_frame: pd.DataFrame | None = None,
    benchmark_frame: pd.DataFrame | None = None,
) -> PipelineResult:
    """Execute the full data, analysis, and A/H backtest workflow."""

    stage_cache = StageCache(
        config.cache_dir if config.resume_from_cache else None,
        refresh=config.refresh_cache,
    )

    loaded = load_ah_pair_data(
        config,
        a_frame=a_frame,
        h_frame=h_frame,
        fx_frame=fx_frame,
        benchmark_frame=benchmark_frame,
    )
    prices = loaded.model_prices
    prices_digest = frame_digest(prices)
    log_prices = stage_cache.load_or_compute(
        "log_prices",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "prices_digest": prices_digest,
        },
        lambda: prepare_log_price_frame(prices),
    )
    log_prices_digest = frame_digest(log_prices)
    train_prices, test_prices = split_train_test(prices, config.train_end_date)
    train_prices_digest = frame_digest(train_prices)
    test_prices_digest = frame_digest(test_prices)
    train_log_prices = stage_cache.load_or_compute(
        "train_log_prices",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "train_prices_digest": train_prices_digest,
        },
        lambda: prepare_log_price_frame(train_prices),
    )
    train_log_prices_digest = frame_digest(train_log_prices)

    full_sample_cointegration = stage_cache.load_or_compute(
        "full_sample_cointegration",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "log_prices_digest": log_prices_digest,
            "dependent_symbol": config.a_symbol,
            "independent_symbol": config.h_symbol,
            "alpha": config.alpha,
        },
        lambda: run_cointegration_analysis(
            log_prices,
            dependent_symbol=config.a_symbol,
            independent_symbol=config.h_symbol,
            alpha=config.alpha,
        ),
    )
    training_cointegration = stage_cache.load_or_compute(
        "training_cointegration",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "train_log_prices_digest": train_log_prices_digest,
            "dependent_symbol": config.a_symbol,
            "independent_symbol": config.h_symbol,
            "alpha": config.alpha,
        },
        lambda: run_cointegration_analysis(
            train_log_prices,
            dependent_symbol=config.a_symbol,
            independent_symbol=config.h_symbol,
            alpha=config.alpha,
        ),
    )
    if config.require_significant_cointegration and not training_cointegration.significant:
        raise ValueError("Training-sample cointegration is not significant; aborting by configuration.")

    ecm = stage_cache.load_or_compute(
        "ecm",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "log_prices_digest": log_prices_digest,
            "cointegration_residuals_digest": frame_digest(full_sample_cointegration.residuals),
            "dependent_symbol": full_sample_cointegration.dependent_symbol,
            "independent_symbol": full_sample_cointegration.independent_symbol,
        },
        lambda: fit_error_correction_model(log_prices, full_sample_cointegration),
    )
    mean_reversion = stage_cache.load_or_compute(
        "mean_reversion",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "residuals_digest": frame_digest(full_sample_cointegration.residuals),
        },
        lambda: estimate_mean_reversion(full_sample_cointegration.residuals),
    )
    segment_analysis = stage_cache.load_or_compute(
        "segment_analysis",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "log_prices_digest": log_prices_digest,
            "dependent_symbol": config.a_symbol,
            "independent_symbol": config.h_symbol,
            "segments": config.segments,
            "alpha": config.alpha,
        },
        lambda: run_segment_analysis(
            log_prices,
            dependent_symbol=config.a_symbol,
            independent_symbol=config.h_symbol,
            segments=config.segments,
            alpha=config.alpha,
        ),
    )
    rolling_cointegration = stage_cache.load_or_compute(
        "rolling_cointegration",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "log_prices_digest": log_prices_digest,
            "dependent_symbol": config.a_symbol,
            "independent_symbol": config.h_symbol,
            "window_size": config.rolling.cointegration_window,
            "step_size": config.rolling.cointegration_step,
            "alpha": config.alpha,
        },
        lambda: run_rolling_cointegration(
            log_prices,
            dependent_symbol=config.a_symbol,
            independent_symbol=config.h_symbol,
            window_size=config.rolling.cointegration_window,
            step_size=config.rolling.cointegration_step,
            alpha=config.alpha,
        ),
    )
    manual_ols = stage_cache.load_or_compute(
        "matrix_ols",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "dependent_digest": frame_digest(log_prices[config.a_symbol]),
            "independent_digest": frame_digest(log_prices[config.h_symbol]),
        },
        lambda: matrix_ols(log_prices[config.a_symbol], log_prices[config.h_symbol]),
    )
    var_diagnostics = stage_cache.load_or_compute(
        "var_diagnostics",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "log_prices_digest": log_prices_digest,
            "symbols": (config.a_symbol, config.h_symbol),
            "max_lags": config.rolling.var_max_lags,
        },
        lambda: fit_var_diagnostics(
            log_prices,
            symbols=(config.a_symbol, config.h_symbol),
            max_lags=config.rolling.var_max_lags,
        ),
    )

    signal_frame = stage_cache.load_or_compute(
        "signal_frame",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "prices_digest": prices_digest,
            "a_symbol": config.a_symbol,
            "h_symbol": config.h_symbol,
            "intercept": training_cointegration.intercept,
            "hedge_ratio": training_cointegration.hedge_ratio,
            "z_window": config.strategy.z_window,
            "z_min_periods": config.strategy.z_min_periods,
        },
        lambda: prepare_signal_frame(
            prices,
            a_symbol=config.a_symbol,
            h_symbol=config.h_symbol,
            intercept=training_cointegration.intercept,
            hedge_ratio=training_cointegration.hedge_ratio,
            z_window=config.strategy.z_window,
            min_periods=config.strategy.z_min_periods,
        ),
    )
    train_signal_frame, test_signal_frame = split_train_test(signal_frame, config.train_end_date)
    train_signal_frame_digest = frame_digest(train_signal_frame)
    test_signal_frame_digest = frame_digest(test_signal_frame)

    best_entry_z, train_grid_search = stage_cache.load_or_compute(
        "train_grid_search",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "signal_frame_digest": train_signal_frame_digest,
            "a_symbol": config.a_symbol,
            "h_symbol": config.h_symbol,
            "hedge_ratio": training_cointegration.hedge_ratio,
            "strategy_config": config.strategy,
            "cost_config": config.costs,
        },
        lambda: grid_search_entry_z(
            signal_frame=train_signal_frame,
            a_symbol=config.a_symbol,
            h_symbol=config.h_symbol,
            hedge_ratio=training_cointegration.hedge_ratio,
            strategy_config=config.strategy,
            cost_config=config.costs,
        ),
    )
    selected_strategy = _strategy_with_entry_z(config.strategy, best_entry_z)

    train_backtest = stage_cache.load_or_compute(
        "train_backtest",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "signal_frame_digest": train_signal_frame_digest,
            "a_symbol": config.a_symbol,
            "h_symbol": config.h_symbol,
            "hedge_ratio": training_cointegration.hedge_ratio,
            "strategy_config": selected_strategy,
            "cost_config": config.costs,
        },
        lambda: backtest_relative_value_strategy(
            signal_frame=train_signal_frame,
            a_symbol=config.a_symbol,
            h_symbol=config.h_symbol,
            hedge_ratio=training_cointegration.hedge_ratio,
            strategy_config=selected_strategy,
            cost_config=config.costs,
        ),
    )
    test_backtest = stage_cache.load_or_compute(
        "test_backtest",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "signal_frame_digest": test_signal_frame_digest,
            "a_symbol": config.a_symbol,
            "h_symbol": config.h_symbol,
            "hedge_ratio": training_cointegration.hedge_ratio,
            "strategy_config": selected_strategy,
            "cost_config": config.costs,
        },
        lambda: backtest_relative_value_strategy(
            signal_frame=test_signal_frame,
            a_symbol=config.a_symbol,
            h_symbol=config.h_symbol,
            hedge_ratio=training_cointegration.hedge_ratio,
            strategy_config=selected_strategy,
            cost_config=config.costs,
        ),
    )

    benchmark_comparison = pd.DataFrame()
    test_rolling_beta = pd.Series(dtype=float, name="rolling_beta")
    if not loaded.benchmark_returns.empty:
        benchmark_comparison = stage_cache.load_or_compute(
            "benchmark_comparison",
            {
                "version": _PIPELINE_CACHE_VERSION,
                "strategy_returns_digest": frame_digest(test_backtest.equity_curve["returns"]),
                "benchmark_returns_digest": frame_digest(loaded.benchmark_returns),
                "benchmark_label": config.benchmark_symbol or "benchmark",
            },
            lambda: prepare_comparison_frame(
                strategy_returns=test_backtest.equity_curve["returns"],
                benchmark_returns=loaded.benchmark_returns,
                benchmark_label=config.benchmark_symbol or "benchmark",
            ),
        )
        test_rolling_beta = stage_cache.load_or_compute(
            "rolling_beta",
            {
                "version": _PIPELINE_CACHE_VERSION,
                "benchmark_comparison_digest": frame_digest(benchmark_comparison),
                "window": config.rolling.beta_window,
            },
            lambda: rolling_beta(
                benchmark_comparison,
                window=config.rolling.beta_window,
            ),
        )

    test_rolling_sharpe = stage_cache.load_or_compute(
        "rolling_sharpe",
        {
            "version": _PIPELINE_CACHE_VERSION,
            "returns_digest": frame_digest(test_backtest.equity_curve["returns"]),
            "window": config.rolling.sharpe_window,
        },
        lambda: rolling_sharpe(
            test_backtest.equity_curve["returns"],
            window=config.rolling.sharpe_window,
        ),
    )

    result = PipelineResult(
        aligned_prices=loaded.aligned_prices,
        prices=prices,
        log_prices=log_prices,
        signal_frame=signal_frame,
        train_prices=train_prices,
        test_prices=test_prices,
        train_signal_frame=train_signal_frame,
        test_signal_frame=test_signal_frame,
        full_sample_cointegration=full_sample_cointegration,
        training_cointegration=training_cointegration,
        ecm=ecm,
        mean_reversion=mean_reversion,
        segment_analysis=segment_analysis,
        rolling_cointegration=rolling_cointegration,
        matrix_ols=manual_ols,
        var_diagnostics=var_diagnostics,
        train_grid_search=train_grid_search,
        best_entry_z=best_entry_z,
        train_backtest=train_backtest,
        test_backtest=test_backtest,
        benchmark_comparison=benchmark_comparison,
        rolling_sharpe=test_rolling_sharpe,
        rolling_beta=test_rolling_beta,
    )
    _save_outputs(config, result)
    return result


run_pairs_trading_pipeline = run_ah_relative_value_pipeline
