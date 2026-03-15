"""Configuration objects for the pairs trading pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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
class BacktestConfig:
    """Parameters for the entry threshold search and backtest."""

    entry_z_candidates: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0)
    exit_z: float = 0.2
    initial_capital: float = 100_000.0
    objective: str = "sharpe_ratio"


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
    """Top-level configuration for the entire workflow."""

    dependent_symbol: str = "KO"
    independent_symbol: str = "PEP"
    benchmark_symbol: str = "SPY"
    start_date: str = "2014-01-01"
    end_date: str = "2024-12-31"
    train_end_date: str = "2020-12-31"
    adjust: str = "qfq"
    alpha: float = 0.05
    segments: tuple[SegmentWindow, ...] = field(
        default_factory=lambda: (
            SegmentWindow("2019-01-01", "2020-12-31", "2019_2020"),
            SegmentWindow("2021-01-01", "2022-12-31", "2021_2022"),
            SegmentWindow("2023-01-01", "2023-12-31", "2023"),
        )
    )
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    rolling: RollingConfig = field(default_factory=RollingConfig)
    output_dir: Path | None = None
