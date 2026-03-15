"""Command-line interface for the refactored pairs trading project."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import BacktestConfig, PipelineConfig, RollingConfig


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""

    parser = argparse.ArgumentParser(description="Run a modular pairs trading analysis pipeline.")
    parser.add_argument("--dependent", default="KO", help="Dependent symbol in the cointegration regression.")
    parser.add_argument("--independent", default="PEP", help="Independent symbol in the cointegration regression.")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark symbol used for rolling beta.")
    parser.add_argument("--start-date", default="2014-01-01", help="Analysis start date in YYYY-MM-DD format.")
    parser.add_argument("--end-date", default="2024-12-31", help="Analysis end date in YYYY-MM-DD format.")
    parser.add_argument(
        "--train-end-date",
        default="2020-12-31",
        help="The last date included in the training sample.",
    )
    parser.add_argument(
        "--z-grid",
        default="0.5,1.0,1.5,2.0",
        help="Comma-separated entry z-score candidates used in the training grid search.",
    )
    parser.add_argument("--exit-z", type=float, default=0.2, help="Exit z-score threshold.")
    parser.add_argument("--initial-capital", type=float, default=100000.0, help="Initial capital for backtests.")
    parser.add_argument(
        "--objective",
        default="sharpe_ratio",
        choices=("sharpe_ratio", "annual_return", "final_capital", "total_return"),
        help="Metric used to select the best entry z-score on the training sample.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional directory used to save CSV, JSON, text, and PNG artifacts.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the pipeline, and print a concise summary."""

    parser = build_parser()
    args = parser.parse_args(argv)
    z_grid = tuple(float(value.strip()) for value in args.z_grid.split(",") if value.strip())

    from .pipeline import run_pairs_trading_pipeline

    config = PipelineConfig(
        dependent_symbol=args.dependent,
        independent_symbol=args.independent,
        benchmark_symbol=args.benchmark,
        start_date=args.start_date,
        end_date=args.end_date,
        train_end_date=args.train_end_date,
        backtest=BacktestConfig(
            entry_z_candidates=z_grid,
            exit_z=args.exit_z,
            initial_capital=args.initial_capital,
            objective=args.objective,
        ),
        rolling=RollingConfig(),
        output_dir=args.output_dir,
    )

    result = run_pairs_trading_pipeline(config)
    print(
        "\n".join(
            [
                f"Full-sample cointegration p-value: {result.full_sample_cointegration.p_value:.6f}",
                f"Training hedge ratio: {result.training_cointegration.hedge_ratio:.6f}",
                f"Residual half-life: {result.mean_reversion.half_life:.2f} days"
                if result.mean_reversion.half_life is not None
                else "Residual half-life: unavailable",
                f"Best entry z-score: {result.best_entry_z:.2f}",
                f"Train Sharpe ratio: {result.train_backtest.summary.sharpe_ratio:.4f}",
                f"Test Sharpe ratio: {result.test_backtest.summary.sharpe_ratio:.4f}",
                f"Test max drawdown: {result.test_backtest.summary.max_drawdown:.2%}",
                f"Artifacts saved to: {args.output_dir}" if args.output_dir else "Artifacts were not written to disk.",
            ]
        )
    )
    return 0
