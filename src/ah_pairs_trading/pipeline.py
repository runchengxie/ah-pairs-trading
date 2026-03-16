"""End-to-end orchestration for the A/H relative-value package."""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
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
from .metrics import prepare_comparison_frame, rolling_beta, rolling_sharpe, summarize_benchmark
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
from .pairs import validate_same_issuer_pair
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


_PIPELINE_CACHE_VERSION = 2


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


def _rolling_series_summary(series: pd.Series) -> dict[str, float | int | None]:
    clean_series = series.dropna()
    if clean_series.empty:
        return {
            "observations": 0,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
        }
    return {
        "observations": int(len(clean_series)),
        "mean": float(clean_series.mean()),
        "median": float(clean_series.median()),
        "min": float(clean_series.min()),
        "max": float(clean_series.max()),
    }


def _validate_pair_configuration(config: PipelineConfig) -> dict[str, str | None]:
    validation = validate_same_issuer_pair(config.a_symbol, config.h_symbol)
    if validation.status == "mismatch":
        if config.same_issuer_check == "strict":
            raise ValueError(validation.message)
        if config.same_issuer_check == "warn":
            warnings.warn(validation.message, stacklevel=2)
    return validation.as_dict()


def _internal_benchmark_weights(weighting: str, hedge_ratio: float) -> tuple[float, float]:
    if weighting == "equal_weight":
        return 0.5, 0.5

    normalized_hedge_ratio = abs(float(hedge_ratio))
    if not math.isfinite(normalized_hedge_ratio) or normalized_hedge_ratio <= 0:
        return 0.5, 0.5
    a_weight = 1.0 / (1.0 + normalized_hedge_ratio)
    h_weight = normalized_hedge_ratio / (1.0 + normalized_hedge_ratio)
    return a_weight, h_weight


def _build_internal_benchmark_returns(
    prices: pd.DataFrame,
    *,
    a_symbol: str,
    h_symbol: str,
    hedge_ratio: float,
    weighting: str,
) -> pd.Series:
    benchmark_prices = prices[[a_symbol, h_symbol]].dropna()
    if benchmark_prices.empty:
        return pd.Series(dtype=float, name="benchmark_ret")

    a_weight, h_weight = _internal_benchmark_weights(weighting, hedge_ratio)
    normalized_a = benchmark_prices[a_symbol] / benchmark_prices[a_symbol].iloc[0]
    normalized_h = benchmark_prices[h_symbol] / benchmark_prices[h_symbol].iloc[0]
    basket_level = a_weight * normalized_a + h_weight * normalized_h
    basket_returns = basket_level.astype(float).pct_change().rename("benchmark_ret").dropna()
    basket_returns.attrs["benchmark_label"] = f"Internal A/H Basket ({weighting})"
    basket_returns.attrs["benchmark_source"] = "internal"
    basket_returns.attrs["benchmark_weighting"] = weighting
    basket_returns.attrs["benchmark_weights"] = {
        "a_weight": a_weight,
        "h_weight": h_weight,
    }
    return basket_returns


def _resolve_benchmark_returns(
    config: PipelineConfig,
    loaded_benchmark_returns: pd.Series,
    *,
    prices: pd.DataFrame,
    training_hedge_ratio: float,
) -> pd.Series:
    has_external_benchmark = not loaded_benchmark_returns.empty

    if config.benchmark_mode == "off":
        return pd.Series(dtype=float, name="benchmark_ret")
    if config.benchmark_mode == "external":
        if has_external_benchmark:
            return loaded_benchmark_returns
        raise ValueError(
            "`benchmark_mode='external'` requires `--benchmark`, `--benchmark-csv`, or a supplied benchmark frame."
        )
    if config.benchmark_mode == "internal":
        return _build_internal_benchmark_returns(
            prices,
            a_symbol=config.a_symbol,
            h_symbol=config.h_symbol,
            hedge_ratio=training_hedge_ratio,
            weighting=config.internal_benchmark_weighting,
        )
    if has_external_benchmark:
        return loaded_benchmark_returns
    if config.strategy.execution_mode == "long_cheaper_leg_only":
        return _build_internal_benchmark_returns(
            prices,
            a_symbol=config.a_symbol,
            h_symbol=config.h_symbol,
            hedge_ratio=training_hedge_ratio,
            weighting=config.internal_benchmark_weighting,
        )
    return pd.Series(dtype=float, name="benchmark_ret")


def _benchmark_source(result: PipelineResult) -> str | None:
    if result.benchmark_comparison.empty:
        return None
    return str(result.benchmark_comparison.attrs.get("benchmark_source", "benchmark"))


def _benchmark_label(config: PipelineConfig, result: PipelineResult) -> str | None:
    if not result.benchmark_comparison.empty:
        return str(result.benchmark_comparison.attrs.get("benchmark_label", "benchmark"))
    if config.benchmark_symbol is not None:
        return config.benchmark_symbol
    if config.data.benchmark_csv_path is not None:
        return Path(config.data.benchmark_csv_path).stem
    return None


def build_pipeline_summary(config: PipelineConfig, result: PipelineResult) -> dict[str, Any]:
    """Build a structured run summary for terminal display and saved artifacts."""

    pair_validation = validate_same_issuer_pair(config.a_symbol, config.h_symbol).as_dict()
    benchmark_metrics: dict[str, Any] | None = None
    if not result.benchmark_comparison.empty:
        benchmark_metrics = summarize_benchmark(
            result.benchmark_comparison,
            rolling_beta_series=result.rolling_beta,
        ).as_dict()

    return {
        "run_meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "start_date": config.start_date,
            "end_date": config.end_date,
            "train_end_date": config.train_end_date,
            "require_significant_cointegration": config.require_significant_cointegration,
            "output_dir": str(config.output_dir) if config.output_dir is not None else None,
        },
        "instrument_meta": {
            "a_symbol": config.a_symbol,
            "h_symbol": config.h_symbol,
            "share_ratio": config.data.share_ratio,
            "pair_validation": pair_validation,
            "benchmark_symbol": config.benchmark_symbol,
            "benchmark_market": config.benchmark_market if _benchmark_label(config, result) is not None else None,
            "benchmark_label": _benchmark_label(config, result),
            "benchmark_source": _benchmark_source(result),
        },
        "strategy_params": {
            "entry_z_candidates": list(config.strategy.entry_z_candidates),
            "best_entry_z": result.best_entry_z,
            "exit_z": config.strategy.exit_z,
            "stop_z": config.strategy.stop_z,
            "z_window": config.strategy.z_window,
            "z_min_periods": config.strategy.z_min_periods,
            "entry_signal_mode": config.strategy.entry_signal_mode,
            "return_filter_mode": config.strategy.return_filter_mode,
            "return_filter_window": config.strategy.return_filter_window,
            "return_filter_min_periods": config.strategy.return_filter_min_periods,
            "max_holding_days": config.strategy.max_holding_days,
            "position_size_fraction": config.strategy.position_size_fraction,
            "initial_capital": config.strategy.initial_capital,
            "objective": config.strategy.objective,
            "execution_mode": config.strategy.execution_mode,
            "a_lot_size": config.strategy.a_lot_size,
            "h_lot_size": config.strategy.h_lot_size,
            "benchmark_mode": config.benchmark_mode,
            "internal_benchmark_weighting": config.internal_benchmark_weighting,
            "same_issuer_check": config.same_issuer_check,
        },
        "cost_assumptions": {
            "a_buy_cost_bps": config.costs.a_buy_cost_bps,
            "a_sell_cost_bps": config.costs.a_sell_cost_bps,
            "h_buy_cost_bps": config.costs.h_buy_cost_bps,
            "h_sell_cost_bps": config.costs.h_sell_cost_bps,
            "h_stamp_duty_bps": config.costs.h_stamp_duty_bps,
            "fx_conversion_bps": config.costs.fx_conversion_bps,
        },
        "train_metrics": result.train_backtest.summary.as_dict(),
        "test_metrics": result.test_backtest.summary.as_dict(),
        "benchmark_metrics": benchmark_metrics,
        "rolling_metrics": {
            "sharpe_window": config.rolling.sharpe_window,
            "beta_window": config.rolling.beta_window,
            "test_rolling_sharpe": _rolling_series_summary(result.rolling_sharpe),
            "test_rolling_beta": _rolling_series_summary(result.rolling_beta),
        },
        "diagnostics": {
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
        },
    }


def _format_float(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):,.{digits}f}"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2%}"


def _format_days(value: int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value} days"


def _return_filter_label(strategy_params: dict[str, Any]) -> str:
    mode = strategy_params.get("return_filter_mode", "off")
    if mode == "off":
        return "off"
    window = strategy_params.get("return_filter_window")
    return f"{str(mode).upper()}({window})"


def _entry_signal_label(strategy_params: dict[str, Any]) -> str:
    mode = str(strategy_params.get("entry_signal_mode", "zscore"))
    if mode == "zscore":
        return "zscore"
    return f"{mode} (z-scored)"


def _render_backtest_section(title: str, metrics: dict[str, Any]) -> list[str]:
    return [
        title,
        f"- Final capital: {_format_float(metrics.get('final_capital'))}",
        f"- Total return: {_format_percent(metrics.get('total_return'))}",
        f"- Annual return: {_format_percent(metrics.get('annual_return'))}",
        f"- Annual volatility: {_format_percent(metrics.get('annual_volatility'))}",
        f"- Sharpe: {_format_float(metrics.get('sharpe_ratio'), digits=3)}",
        f"- Sortino: {_format_float(metrics.get('sortino_ratio'), digits=3)}",
        f"- Max drawdown: {_format_percent(metrics.get('max_drawdown'))}",
        f"- Calmar: {_format_float(metrics.get('calmar_ratio'), digits=3)}",
        f"- Max drawdown duration: {_format_days(metrics.get('max_drawdown_duration'))}",
        f"- Recovery days: {_format_days(metrics.get('recovery_days'))}",
        f"- Trades: {int(metrics.get('trade_count', 0))}",
        f"- Win rate: {_format_percent(metrics.get('win_rate'))}",
        f"- Payoff ratio: {_format_float(metrics.get('payoff_ratio'), digits=3)}",
        f"- Profit factor: {_format_float(metrics.get('profit_factor'), digits=3)}",
        f"- Avg trade PnL: {_format_float(metrics.get('avg_trade_pnl'))}",
        f"- Avg holding days: {_format_float(metrics.get('avg_holding_days'), digits=1)}",
        f"- Avg win PnL: {_format_float(metrics.get('avg_win_pnl'))}",
        f"- Avg loss PnL: {_format_float(metrics.get('avg_loss_pnl'))}",
        f"- Gross PnL: {_format_float(metrics.get('gross_pnl'))}",
        f"- Net PnL: {_format_float(metrics.get('net_pnl'))}",
        f"- Total costs: {_format_float(metrics.get('total_costs'))}",
        f"- Cost / |gross PnL|: {_format_percent(metrics.get('cost_to_gross_pnl'))}",
        f"- Time in market: {_format_percent(metrics.get('time_in_market'))}",
        f"- Max consecutive losses: {int(metrics.get('max_consecutive_losses', 0))}",
        f"- Monthly win rate: {_format_percent(metrics.get('monthly_win_rate'))}",
    ]


def _render_rolling_beta_line(summary: dict[str, Any]) -> str:
    rolling_metrics = summary["rolling_metrics"]
    benchmark_metrics = summary.get("benchmark_metrics")
    beta_window = rolling_metrics["beta_window"]
    beta_stats = rolling_metrics["test_rolling_beta"]
    if int(beta_stats.get("observations", 0)) > 0:
        return (
            f"- Rolling Beta ({beta_window}d): "
            f"mean={_format_float(beta_stats['mean'], digits=3)}, "
            f"median={_format_float(beta_stats['median'], digits=3)}, "
            f"min={_format_float(beta_stats['min'], digits=3)}, "
            f"max={_format_float(beta_stats['max'], digits=3)}"
        )
    if benchmark_metrics is None:
        return f"- Rolling Beta ({beta_window}d): unavailable because no benchmark was resolved"
    return f"- Rolling Beta ({beta_window}d): unavailable because there are fewer than {beta_window} aligned observations"


def render_pipeline_scorecard(summary: dict[str, Any]) -> str:
    """Render a human-readable scorecard from a structured pipeline summary."""

    run_meta = summary["run_meta"]
    instrument_meta = summary["instrument_meta"]
    strategy_params = summary["strategy_params"]
    diagnostics = summary["diagnostics"]
    benchmark_metrics = summary.get("benchmark_metrics")
    rolling_metrics = summary["rolling_metrics"]
    training_cointegration = diagnostics["training_cointegration"]
    mean_reversion = diagnostics["mean_reversion"]
    pair_validation = instrument_meta["pair_validation"]

    lines = [
        "Run Overview",
        f"- Pair: {instrument_meta['a_symbol']} / {instrument_meta['h_symbol']}",
        f"- Pair validation: {pair_validation['status']}",
        (
            f"- Registered issuer: {pair_validation['issuer_name']}"
            if pair_validation["issuer_name"] is not None
            else "- Registered issuer: unknown"
        ),
        f"- Window: {run_meta['start_date']} to {run_meta['end_date']}",
        f"- Train/Test split: train <= {run_meta['train_end_date']}, test > {run_meta['train_end_date']}",
        f"- Execution mode: {strategy_params['execution_mode']}",
        f"- Best entry z-score: {_format_float(strategy_params['best_entry_z'], digits=2)}",
        f"- Entry z candidates: {', '.join(str(value) for value in strategy_params['entry_z_candidates'])}",
        f"- Entry signal: {_entry_signal_label(strategy_params)}",
        f"- Return filter: {_return_filter_label(strategy_params)}",
        f"- Benchmark mode: {strategy_params['benchmark_mode']}",
        f"- Benchmark: {instrument_meta['benchmark_label'] or 'not provided'}",
        f"- Benchmark source: {instrument_meta['benchmark_source'] or 'not provided'}",
        f"- Training cointegration p-value: {_format_float(training_cointegration['p_value'], digits=6)}",
        f"- Training hedge ratio: {_format_float(training_cointegration['hedge_ratio'], digits=6)}",
        (
            f"- Residual half-life: {_format_float(mean_reversion['half_life'], digits=2)} days"
            if mean_reversion["half_life"] is not None
            else "- Residual half-life: unavailable"
        ),
        "",
    ]
    lines.extend(_render_backtest_section("Train Scorecard", summary["train_metrics"]))
    lines.append("")
    lines.extend(_render_backtest_section("Test Scorecard", summary["test_metrics"]))

    if benchmark_metrics is not None:
        lines.extend(
            [
                "",
                "Benchmark Comparison",
                f"- Benchmark label: {benchmark_metrics['benchmark_label']}",
                f"- Benchmark observations: {benchmark_metrics['observations']}",
                f"- Benchmark total return: {_format_percent(benchmark_metrics['benchmark_total_return'])}",
                f"- Benchmark annual return: {_format_percent(benchmark_metrics['benchmark_annual_return'])}",
                f"- Excess total return: {_format_percent(benchmark_metrics['excess_total_return'])}",
                f"- Relative return vs benchmark: {_format_percent(benchmark_metrics['relative_return'])}",
                f"- Tracking error: {_format_percent(benchmark_metrics['tracking_error'])}",
                f"- Information ratio: {_format_float(benchmark_metrics['information_ratio'], digits=3)}",
                f"- Beta: {_format_float(benchmark_metrics['beta'], digits=3)}",
                f"- Alpha: {_format_percent(benchmark_metrics['alpha'])}",
            ]
        )

    lines.extend(
        [
            "",
            "Rolling Diagnostics",
            (
                f"- Rolling Sharpe ({rolling_metrics['sharpe_window']}d): "
                f"mean={_format_float(rolling_metrics['test_rolling_sharpe']['mean'], digits=3)}, "
                f"median={_format_float(rolling_metrics['test_rolling_sharpe']['median'], digits=3)}, "
                f"min={_format_float(rolling_metrics['test_rolling_sharpe']['min'], digits=3)}, "
                f"max={_format_float(rolling_metrics['test_rolling_sharpe']['max'], digits=3)}"
            ),
            _render_rolling_beta_line(summary),
            (
                f"- Artifacts: {run_meta['output_dir']}"
                if run_meta["output_dir"] is not None
                else "- Artifacts: not written to disk"
            ),
        ]
    )
    return "\n".join(lines)


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
        entry_signal_mode=strategy_config.entry_signal_mode,
        return_filter_mode=strategy_config.return_filter_mode,
        return_filter_window=strategy_config.return_filter_window,
        return_filter_min_periods=strategy_config.return_filter_min_periods,
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

    summary_payload = build_pipeline_summary(config, result)
    (output_dir / "summary.json").write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    _save_text(render_pipeline_scorecard(summary_payload), output_dir / "summary.md")

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
    benchmark_label = _benchmark_label(config, result)
    if not result.benchmark_comparison.empty and benchmark_label is not None:
        plot_cumulative_returns(
            result.benchmark_comparison,
            benchmark_label=benchmark_label,
            output_path=output_dir / "test_vs_benchmark.png",
        )
        plot_excess_returns(result.benchmark_comparison, output_path=output_dir / "test_excess_returns.png")
    if not result.rolling_beta.empty and benchmark_label is not None:
        plot_rolling_beta(
            result.rolling_beta,
            benchmark_label=benchmark_label,
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
    _validate_pair_configuration(config)

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
        raise ValueError(
            "Training-sample cointegration is not significant "
            f"(p-value={training_cointegration.p_value:.6f}, alpha={config.alpha:.2f}) "
            f"for {config.a_symbol}/{config.h_symbol}. "
            "Aborting because `require_significant_cointegration` is enabled. "
            "Re-run with `--allow-non-coint` to continue. "
            "`--resume-from-cache` does not bypass this validation. "
            "Also verify the A/H symbols refer to the same issuer."
        )

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
            "return_filter_window": config.strategy.return_filter_window,
            "return_filter_min_periods": config.strategy.return_filter_min_periods,
        },
        lambda: prepare_signal_frame(
            prices,
            a_symbol=config.a_symbol,
            h_symbol=config.h_symbol,
            intercept=training_cointegration.intercept,
            hedge_ratio=training_cointegration.hedge_ratio,
            z_window=config.strategy.z_window,
            min_periods=config.strategy.z_min_periods,
            return_filter_window=config.strategy.return_filter_window,
            return_filter_min_periods=config.strategy.return_filter_min_periods,
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

    benchmark_returns = _resolve_benchmark_returns(
        config,
        loaded.benchmark_returns,
        prices=prices,
        training_hedge_ratio=training_cointegration.hedge_ratio,
    )

    benchmark_comparison = pd.DataFrame()
    test_rolling_beta = pd.Series(dtype=float, name="rolling_beta")
    if not benchmark_returns.empty:
        benchmark_label = str(benchmark_returns.attrs.get("benchmark_label", "benchmark"))
        benchmark_source = str(benchmark_returns.attrs.get("benchmark_source", "benchmark"))
        benchmark_comparison = stage_cache.load_or_compute(
            "benchmark_comparison",
            {
                "version": _PIPELINE_CACHE_VERSION,
                "strategy_returns_digest": frame_digest(test_backtest.equity_curve["returns"]),
                "benchmark_returns_digest": frame_digest(benchmark_returns),
                "benchmark_label": benchmark_label,
                "benchmark_source": benchmark_source,
            },
            lambda: prepare_comparison_frame(
                strategy_returns=test_backtest.equity_curve["returns"],
                benchmark_returns=benchmark_returns,
                benchmark_label=benchmark_label,
            ),
        )
        benchmark_comparison.attrs["benchmark_source"] = benchmark_source
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
