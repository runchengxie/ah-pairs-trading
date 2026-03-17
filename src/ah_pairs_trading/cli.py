"""Command-line interface for the A/H relative-value workflow."""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
import sys
import tomllib

from .config import (
    DEFAULT_CACHE_DIR,
    DEFAULT_RUNS_DIR,
    CostConfig,
    DataConfig,
    PipelineConfig,
    RollingConfig,
    StrategyConfig,
)


def _config_pre_parser() -> argparse.ArgumentParser:
    """Parse `--config` early so TOML presets can seed the main argv."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path, default=None)
    return parser


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""

    parser = argparse.ArgumentParser(description="Run an A/H relative-value analysis pipeline.")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional TOML preset. Explicit CLI flags override keys loaded from this file.",
    )
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
    parser.add_argument(
        "--entry-signal-mode",
        default="zscore",
        choices=("zscore", "ret_spread_ema", "ret_spread_sma"),
        help="Primary signal used for entry threshold search and trade management.",
    )
    parser.add_argument(
        "--hedge-ratio-mode",
        default="training",
        choices=("training", "rolling"),
        help="Use the training-sample hedge ratio or time-varying rolling estimates for spread construction and paired sizing.",
    )
    parser.add_argument("--z-window", type=int, default=120, help="Rolling window used to standardize the spread.")
    parser.add_argument(
        "--z-min-periods",
        type=int,
        default=60,
        help="Minimum observations required before a rolling z-score is considered valid.",
    )
    parser.add_argument(
        "--return-filter-mode",
        default="off",
        choices=("off", "ema", "sma"),
        help="Optional moving-average confirmation filter applied on the return spread.",
    )
    parser.add_argument(
        "--cointegration-gate-mode",
        default="off",
        choices=("off", "significant"),
        help="Optional rolling cointegration gate. `significant` only allows trades when the latest rolling p-value stays below alpha.",
    )
    parser.add_argument(
        "--return-filter-window",
        type=int,
        default=10,
        help="Window used to build the return-spread EMA/SMA signals.",
    )
    parser.add_argument(
        "--return-filter-min-periods",
        type=int,
        default=None,
        help="Minimum observations required before the return-spread filter is considered valid.",
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
        default=DEFAULT_CACHE_DIR,
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
        help="Rebuild market-data master histories from fresh downloads and ignore existing stage-cache entries for this run.",
    )
    parser.add_argument(
        "--resume-from-cache",
        action="store_true",
        help="Reuse cached pipeline stages only; the market-data cache still uses its default automatic incremental symbol-history behavior.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"Optional directory used to save CSV, JSON, text, and PNG artifacts. Recommended under `{DEFAULT_RUNS_DIR}/<run-name>`.",
    )
    return parser


def _load_config_payload(path: Path) -> dict[str, object]:
    """Load a flat TOML config file for CLI-style overrides."""

    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"Config file `{path}` does not exist.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Config file `{path}` is not valid TOML: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Config file `{path}` must contain a top-level TOML table.")
    return payload


def _serialize_config_scalar(value: object) -> str:
    """Convert supported TOML scalar values into CLI token strings."""

    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _resolve_config_path_value(config_path: Path, value: object) -> Path:
    """Resolve TOML path-like values relative to the config file location."""

    raw_path = Path(str(value))
    if raw_path.is_absolute():
        return raw_path
    return (config_path.parent / raw_path).resolve()


def _config_tokens_from_payload(parser: argparse.ArgumentParser, path: Path) -> list[str]:
    """Convert flat TOML entries into CLI tokens so argparse validates them."""

    payload = _load_config_payload(path)
    actions_by_dest = {
        action.dest: action
        for action in parser._actions
        if action.option_strings and action.dest not in {"help", "config"}
    }

    tokens: list[str] = []
    unknown_keys: list[str] = []

    for key, value in payload.items():
        action = actions_by_dest.get(key)
        if action is None:
            unknown_keys.append(key)
            continue
        if isinstance(value, dict):
            raise ValueError(
                f"Config file `{path}` uses a nested table for `{key}`. "
                "Use flat top-level keys that match CLI flag names."
            )

        option = action.option_strings[0]
        is_store_true = (
            action.nargs == 0
            and getattr(action, "const", None) is True
            and bool(getattr(action, "default", False)) is False
        )
        if is_store_true:
            if not isinstance(value, bool):
                raise ValueError(f"Config key `{key}` in `{path}` must be a boolean.")
            if value:
                tokens.append(option)
            continue

        if key == "z_grid":
            if isinstance(value, (list, tuple)):
                tokens.extend((option, ",".join(_serialize_config_scalar(item) for item in value)))
                continue
            tokens.extend((option, _serialize_config_scalar(value)))
            continue
        if isinstance(value, (list, tuple)):
            raise ValueError(
                f"Config key `{key}` in `{path}` must be a scalar value. "
                "Only `z_grid` currently accepts an array."
            )
        if action.type is Path:
            tokens.extend((option, str(_resolve_config_path_value(path, value))))
            continue

        tokens.extend((option, _serialize_config_scalar(value)))

    if unknown_keys:
        supported = ", ".join(sorted(actions_by_dest))
        raise ValueError(
            f"Config file `{path}` contains unsupported keys: {', '.join(sorted(unknown_keys))}. "
            f"Supported keys match CLI destinations: {supported}."
        )
    return tokens


def _merge_config_argv(parser: argparse.ArgumentParser, argv: list[str]) -> list[str]:
    """Merge built-in defaults, TOML overrides, and explicit CLI flags."""

    config_path = _config_pre_parser().parse_known_args(argv)[0].config
    if config_path is None:
        return argv
    return _config_tokens_from_payload(parser, config_path) + argv


def _parse_z_grid(raw_value: str) -> tuple[float, ...]:
    """Parse the CLI/config z-grid into a validated numeric tuple."""

    grid = tuple(float(value.strip()) for value in raw_value.split(",") if value.strip())
    if not grid:
        raise ValueError("`--z-grid` must contain at least one numeric threshold.")
    return grid


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the pipeline, and print a concise summary."""

    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(_merge_config_argv(parser, raw_argv))
    z_grid = _parse_z_grid(args.z_grid)

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
            entry_signal_mode=args.entry_signal_mode,
            hedge_ratio_mode=args.hedge_ratio_mode,
            return_filter_mode=args.return_filter_mode,
            cointegration_gate_mode=args.cointegration_gate_mode,
            return_filter_window=args.return_filter_window,
            return_filter_min_periods=args.return_filter_min_periods,
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
