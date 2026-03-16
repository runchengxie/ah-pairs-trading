"""Configuration objects for the A/H relative-value workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


ExecutionMode = Literal["long_cheaper_leg_only", "paired"]
BenchmarkMarket = Literal["a", "h"]
BenchmarkMode = Literal["auto", "external", "internal", "off"]
InternalBenchmarkWeighting = Literal["hedge_ratio", "equal_weight"]
SameIssuerCheck = Literal["strict", "warn", "off"]


@dataclass(slots=True, frozen=True)
class SegmentWindow:
    """A fixed date window used for segmented analysis."""

    start: str
    end: str
    label: str | None = None

    def resolved_label(self) -> str:
        """Return a stable label for reporting."""

        return self.label or f"{self.start}_to_{self.end}"


@dataclass(slots=True, frozen=True)
class DataConfig:
    """Inputs that determine how A/H and FX histories are loaded."""

    data_provider: str = "akshare"
    a_adjust: str = "qfq"
    h_adjust: str = "qfq"
    fx_symbol: str = "HKD/CNY"
    share_ratio: float = 1.0
    calendar_mode: Literal["intersection"] = "intersection"
    a_csv_path: Path | None = None
    h_csv_path: Path | None = None
    fx_csv_path: Path | None = None
    benchmark_csv_path: Path | None = None
    constant_fx_rate: float | None = None


@dataclass(slots=True, frozen=True)
class StrategyConfig:
    """Signal-generation and execution parameters."""

    entry_z_candidates: tuple[float, ...] = (1.5, 2.0, 2.5)
    exit_z: float = 0.5
    stop_z: float = 3.0
    z_window: int = 120
    z_min_periods: int = 60
    max_holding_days: int = 15
    position_size_fraction: float = 0.95
    initial_capital: float = 100_000.0
    objective: str = "sharpe_ratio"
    execution_mode: ExecutionMode = "long_cheaper_leg_only"
    a_lot_size: int = 100
    h_lot_size: int = 100


@dataclass(slots=True, frozen=True)
class CostConfig:
    """Explicit transaction-cost assumptions for each leg."""

    a_buy_cost_bps: float = 2.0
    a_sell_cost_bps: float = 2.0
    h_buy_cost_bps: float = 8.0
    h_sell_cost_bps: float = 8.0
    h_stamp_duty_bps: float = 10.0
    fx_conversion_bps: float = 2.0


@dataclass(slots=True, frozen=True)
class RollingConfig:
    """Parameters for rolling diagnostics."""

    cointegration_window: int = 180
    cointegration_step: int = 5
    sharpe_window: int = 60
    beta_window: int = 60
    var_max_lags: int = 5


@dataclass(slots=True, frozen=True)
class PipelineConfig:
    """Top-level configuration for the entire A/H workflow."""

    a_symbol: str = "600036"
    h_symbol: str = "03968"
    benchmark_symbol: str | None = None
    benchmark_market: BenchmarkMarket = "a"
    benchmark_mode: BenchmarkMode = "auto"
    internal_benchmark_weighting: InternalBenchmarkWeighting = "hedge_ratio"
    start_date: str = "2018-01-01"
    end_date: str = "2024-12-31"
    train_end_date: str = "2022-12-31"
    alpha: float = 0.05
    require_significant_cointegration: bool = True
    same_issuer_check: SameIssuerCheck = "strict"
    segments: tuple[SegmentWindow, ...] = field(
        default_factory=lambda: (
            SegmentWindow("2019-01-01", "2020-12-31", "2019_2020"),
            SegmentWindow("2021-01-01", "2022-12-31", "2021_2022"),
            SegmentWindow("2023-01-01", "2023-12-31", "2023"),
        )
    )
    data: DataConfig = field(default_factory=DataConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    rolling: RollingConfig = field(default_factory=RollingConfig)
    cache_dir: Path | None = Path(".cache/ah_pairs_trading")
    refresh_cache: bool = False
    resume_from_cache: bool = False
    output_dir: Path | None = None
