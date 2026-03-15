"""End-to-end orchestration for the pairs trading package."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

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
from .config import PipelineConfig
from .data import (
    load_benchmark_returns,
    load_pair_price_frame,
    prepare_log_price_frame,
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
from .strategy import BacktestResult, grid_search_entry_z, backtest_fixed_beta


@dataclass(slots=True)
class PipelineResult:
    """The full set of outputs produced by the refactored workflow."""

    prices: pd.DataFrame
    log_prices: pd.DataFrame
    train_prices: pd.DataFrame
    test_prices: pd.DataFrame
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
        "roots": [
            {"real": float(root.real), "imag": float(root.imag)}
            for root in result.roots
        ],
        "inverse_root_magnitudes": result.inverse_root_magnitudes.tolist(),
    }


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

    _save_dataframe(result.prices, output_dir / "prices.csv")
    _save_dataframe(result.log_prices, output_dir / "log_prices.csv")
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
    }
    (output_dir / "summary.json").write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")

    _save_text(result.full_sample_cointegration.regression_summary_text, output_dir / "full_sample_ols_summary.txt")
    _save_text(result.training_cointegration.regression_summary_text, output_dir / "train_ols_summary.txt")
    _save_text(result.ecm.summary_text, output_dir / "ecm_summary.txt")
    _save_text(result.var_diagnostics.selection_summary_text, output_dir / "var_selection_summary.txt")
    _save_text(result.var_diagnostics.model_summary_text, output_dir / "var_model_summary.txt")

    plot_log_prices(
        result.log_prices,
        dependent_symbol=config.dependent_symbol,
        independent_symbol=config.independent_symbol,
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
    if not result.benchmark_comparison.empty:
        plot_cumulative_returns(
            result.benchmark_comparison,
            benchmark_label=config.benchmark_symbol,
            output_path=output_dir / "test_vs_benchmark.png",
        )
        plot_excess_returns(result.benchmark_comparison, output_path=output_dir / "test_excess_returns.png")
    if not result.rolling_beta.empty:
        plot_rolling_beta(
            result.rolling_beta,
            benchmark_label=config.benchmark_symbol,
            output_path=output_dir / "test_rolling_beta.png",
        )


def run_pairs_trading_pipeline(config: PipelineConfig) -> PipelineResult:
    """Execute the full data, analysis, and backtest pipeline."""

    prices = load_pair_price_frame(
        dependent_symbol=config.dependent_symbol,
        independent_symbol=config.independent_symbol,
        start_date=config.start_date,
        end_date=config.end_date,
        adjust=config.adjust,
    )
    log_prices = prepare_log_price_frame(prices)
    train_prices, test_prices = split_train_test(prices, config.train_end_date)
    train_log_prices = prepare_log_price_frame(train_prices)

    full_sample_cointegration = run_cointegration_analysis(
        log_prices,
        dependent_symbol=config.dependent_symbol,
        independent_symbol=config.independent_symbol,
        alpha=config.alpha,
    )
    training_cointegration = run_cointegration_analysis(
        train_log_prices,
        dependent_symbol=config.dependent_symbol,
        independent_symbol=config.independent_symbol,
        alpha=config.alpha,
    )

    ecm = fit_error_correction_model(log_prices, full_sample_cointegration)
    mean_reversion = estimate_mean_reversion(full_sample_cointegration.residuals)
    segment_analysis = run_segment_analysis(
        log_prices,
        dependent_symbol=config.dependent_symbol,
        independent_symbol=config.independent_symbol,
        segments=config.segments,
        alpha=config.alpha,
    )
    rolling_cointegration = run_rolling_cointegration(
        log_prices,
        dependent_symbol=config.dependent_symbol,
        independent_symbol=config.independent_symbol,
        window_size=config.rolling.cointegration_window,
        step_size=config.rolling.cointegration_step,
        alpha=config.alpha,
    )
    manual_ols = matrix_ols(log_prices[config.dependent_symbol], log_prices[config.independent_symbol])
    var_diagnostics = fit_var_diagnostics(
        log_prices,
        symbols=(config.dependent_symbol, config.independent_symbol),
        max_lags=config.rolling.var_max_lags,
    )

    best_entry_z, train_grid_search = grid_search_entry_z(
        price_frame=train_prices,
        dependent_symbol=config.dependent_symbol,
        independent_symbol=config.independent_symbol,
        intercept=training_cointegration.intercept,
        hedge_ratio=training_cointegration.hedge_ratio,
        residual_mean=training_cointegration.residual_mean,
        residual_std=training_cointegration.residual_std,
        entry_z_candidates=config.backtest.entry_z_candidates,
        exit_z=config.backtest.exit_z,
        initial_capital=config.backtest.initial_capital,
        objective=config.backtest.objective,
    )

    train_backtest = backtest_fixed_beta(
        price_frame=train_prices,
        dependent_symbol=config.dependent_symbol,
        independent_symbol=config.independent_symbol,
        intercept=training_cointegration.intercept,
        hedge_ratio=training_cointegration.hedge_ratio,
        residual_mean=training_cointegration.residual_mean,
        residual_std=training_cointegration.residual_std,
        entry_z=best_entry_z,
        exit_z=config.backtest.exit_z,
        initial_capital=config.backtest.initial_capital,
    )
    test_backtest = backtest_fixed_beta(
        price_frame=test_prices,
        dependent_symbol=config.dependent_symbol,
        independent_symbol=config.independent_symbol,
        intercept=training_cointegration.intercept,
        hedge_ratio=training_cointegration.hedge_ratio,
        residual_mean=training_cointegration.residual_mean,
        residual_std=training_cointegration.residual_std,
        entry_z=best_entry_z,
        exit_z=config.backtest.exit_z,
        initial_capital=config.backtest.initial_capital,
    )

    benchmark_returns = load_benchmark_returns(
        benchmark_symbol=config.benchmark_symbol,
        start_date=config.start_date,
        end_date=config.end_date,
        adjust=config.adjust,
    )
    benchmark_comparison = prepare_comparison_frame(
        strategy_returns=test_backtest.equity_curve["returns"],
        benchmark_returns=benchmark_returns,
        benchmark_label=config.benchmark_symbol,
    )
    test_rolling_sharpe = rolling_sharpe(
        test_backtest.equity_curve["returns"],
        window=config.rolling.sharpe_window,
    )
    test_rolling_beta = rolling_beta(
        benchmark_comparison,
        window=config.rolling.beta_window,
    )

    result = PipelineResult(
        prices=prices,
        log_prices=log_prices,
        train_prices=train_prices,
        test_prices=test_prices,
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
