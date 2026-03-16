"""Command-line interface for the A/H relative-value workflow."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import CostConfig, DataConfig, PipelineConfig, RollingConfig, StrategyConfig


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""

    parser = argparse.ArgumentParser(description="Run an A/H relative-value analysis pipeline.")
    parser.add_argument("--a-symbol", default="600036", help="A-share symbol used as the mainland leg.")
    parser.add_argument("--h-symbol", default="03968", help="H-share symbol used as the Hong Kong leg.")
    parser.add_argument("--benchmark", default=None, help="Optional benchmark symbol used for rolling beta.")
    parser.add_argument(
        "--benchmark-market",
        default="a",
        choices=("a", "h"),
        help="Market of the optional benchmark symbol.",
    )
    parser.add_argument(
        "--benchmark-mode",
        default="auto",
        choices=("auto", "external", "internal", "off"),
        help=(
            "How the reporting benchmark is resolved. "
            "`auto` prefers an explicit external benchmark and otherwise uses an internal A/H basket "
            "for `long_cheaper_leg_only`."
        ),
    )
    parser.add_argument(
        "--internal-benchmark-weighting",
        default="hedge_ratio",
        choices=("hedge_ratio", "equal_weight"),
        help="Weighting used when the internal passive A/H basket benchmark is enabled.",
    )
    parser.add_argument("--start-date", default="2018-01-01", help="Analysis start date in YYYY-MM-DD format.")
    parser.add_argument("--end-date", default="2024-12-31", help="Analysis end date in YYYY-MM-DD format.")
    parser.add_argument(
        "--train-end-date",
        default="2022-12-31",
        help="The last date included in the training sample.",
    )
    parser.add_argument(
        "--a-csv",
        type=Path,
        default=None,
        help="Optional user-supplied local CSV for the A-share history. When set, this exact file is used.",
    )
    parser.add_argument(
        "--h-csv",
        type=Path,
        default=None,
        help="Optional user-supplied local CSV for the H-share history. When set, this exact file is used.",
    )
    parser.add_argument(
        "--fx-csv",
        type=Path,
        default=None,
        help="Optional user-supplied local CSV for the HKD/CNY history. When set, this exact file is used.",
    )
    parser.add_argument(
        "--benchmark-csv",
        type=Path,
        default=None,
        help="Optional user-supplied local CSV for the benchmark history. When set, this exact file is used.",
    )
    parser.add_argument(
        "--constant-fx-rate",
        type=float,
        default=None,
        help="Fallback static HKD/CNY rate used when no FX history is provided.",
    )
    parser.add_argument("--share-ratio", type=float, default=1.0, help="Share-conversion ratio between A and H.")
    parser.add_argument(
        "--execution-mode",
        default="long_cheaper_leg_only",
        choices=("long_cheaper_leg_only", "paired"),
        help="Execution mode for the strategy.",
    )
    parser.add_argument("--z-window", type=int, default=120, help="Rolling window used to standardize the spread.")
    parser.add_argument(
        "--z-min-periods",
        type=int,
        default=60,
        help="Minimum observations required before a rolling z-score is considered valid.",
    )
    parser.add_argument(
        "--z-grid",
        default="1.5,2.0,2.5",
        help="Comma-separated entry z-score candidates used in the training grid search.",
    )
    parser.add_argument("--exit-z", type=float, default=0.5, help="Exit z-score threshold.")
    parser.add_argument("--stop-z", type=float, default=3.0, help="Stop-loss z-score threshold.")
    parser.add_argument(
        "--max-holding-days",
        type=int,
        default=15,
        help="Maximum holding period per trade in trading days.",
    )
    parser.add_argument(
        "--position-size-fraction",
        type=float,
        default=0.95,
        help="Fraction of current capital allocated to a new position.",
    )
    parser.add_argument("--a-lot-size", type=int, default=100, help="Lot size used for the A-share leg.")
    parser.add_argument("--h-lot-size", type=int, default=100, help="Lot size used for the H-share leg.")
    parser.add_argument("--initial-capital", type=float, default=100000.0, help="Initial capital for backtests.")
    parser.add_argument(
        "--objective",
        default="sharpe_ratio",
        choices=(
            "sharpe_ratio",
            "annual_return",
            "final_capital",
            "total_return",
            "win_rate",
        ),
        help="Metric used to select the best entry z-score on the training sample.",
    )
    parser.add_argument("--a-buy-cost-bps", type=float, default=2.0, help="A-share buy-side cost in bps.")
    parser.add_argument("--a-sell-cost-bps", type=float, default=2.0, help="A-share sell-side cost in bps.")
    parser.add_argument("--h-buy-cost-bps", type=float, default=8.0, help="H-share buy-side base cost in bps.")
    parser.add_argument("--h-sell-cost-bps", type=float, default=8.0, help="H-share sell-side base cost in bps.")
    parser.add_argument(
        "--h-stamp-duty-bps",
        type=float,
        default=10.0,
        help="Explicit H-share stamp duty assumption in bps.",
    )
    parser.add_argument(
        "--fx-conversion-bps",
        type=float,
        default=2.0,
        help="Implicit FX conversion cost applied to H-share trades in bps.",
    )
    parser.add_argument(
        "--allow-non-coint",
        action="store_true",
        help="Allow the pipeline to continue even if training-sample cointegration is not significant.",
    )
    parser.add_argument(
        "--same-issuer-check",
        default="strict",
        choices=("strict", "warn", "off"),
        help=(
            "Validate the A/H symbols against the built-in same-issuer registry. "
            "`strict` rejects known mismatches such as 601857/00883."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache/ah_pairs_trading"),
        help="Directory used for automatic data caching and optional stage checkpoints.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable all disk-backed caching for this run.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore existing cache entries and rebuild them from fresh data and calculations.",
    )
    parser.add_argument(
        "--resume-from-cache",
        action="store_true",
        help="Reuse cached pipeline stages so interrupted runs can continue from completed checkpoints without bypassing validation checks.",
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

    from .pipeline import build_pipeline_summary, render_pipeline_scorecard, run_ah_relative_value_pipeline

    config = PipelineConfig(
        a_symbol=args.a_symbol,
        h_symbol=args.h_symbol,
        benchmark_symbol=args.benchmark,
        benchmark_market=args.benchmark_market,
        benchmark_mode=args.benchmark_mode,
        internal_benchmark_weighting=args.internal_benchmark_weighting,
        start_date=args.start_date,
        end_date=args.end_date,
        train_end_date=args.train_end_date,
        require_significant_cointegration=not args.allow_non_coint,
        same_issuer_check=args.same_issuer_check,
        data=DataConfig(
            a_csv_path=args.a_csv,
            h_csv_path=args.h_csv,
            fx_csv_path=args.fx_csv,
            benchmark_csv_path=args.benchmark_csv,
            constant_fx_rate=args.constant_fx_rate,
            share_ratio=args.share_ratio,
        ),
        strategy=StrategyConfig(
            entry_z_candidates=z_grid,
            exit_z=args.exit_z,
            stop_z=args.stop_z,
            z_window=args.z_window,
            z_min_periods=args.z_min_periods,
            max_holding_days=args.max_holding_days,
            position_size_fraction=args.position_size_fraction,
            initial_capital=args.initial_capital,
            objective=args.objective,
            execution_mode=args.execution_mode,
            a_lot_size=args.a_lot_size,
            h_lot_size=args.h_lot_size,
        ),
        costs=CostConfig(
            a_buy_cost_bps=args.a_buy_cost_bps,
            a_sell_cost_bps=args.a_sell_cost_bps,
            h_buy_cost_bps=args.h_buy_cost_bps,
            h_sell_cost_bps=args.h_sell_cost_bps,
            h_stamp_duty_bps=args.h_stamp_duty_bps,
            fx_conversion_bps=args.fx_conversion_bps,
        ),
        rolling=RollingConfig(),
        cache_dir=None if args.no_cache else args.cache_dir,
        refresh_cache=args.refresh_cache,
        resume_from_cache=args.resume_from_cache,
        output_dir=args.output_dir,
    )

    result = run_ah_relative_value_pipeline(config)
    print(render_pipeline_scorecard(build_pipeline_summary(config, result)))
    return 0
