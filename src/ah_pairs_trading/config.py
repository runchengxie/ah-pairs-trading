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
ReturnFilterMode = Literal["off", "ema", "sma"]
EntrySignalMode = Literal["zscore", "ret_spread_ema", "ret_spread_sma"]
HedgeRatioMode = Literal["training", "rolling"]
CointegrationGateMode = Literal["off", "significant"]
ExecutionTiming = Literal["close", "next_open"]
ECMGateMode = Literal["off", "significant_negative"]
HalfLifeAnchorMode = Literal["off", "training"]
DataProvider = Literal["akshare", "tushare", "simulated"]
BacktestEngine = Literal["trade", "weight"]
MeanReversionGateMode = Literal["off", "half_life_range", "lb_filter", "both"]
PositionSizingMode = Literal["fixed", "vol_target"]

DEFAULT_CACHE_DIR = Path("artifacts/cache/ah_pairs_trading")
DEFAULT_RUNS_DIR = Path("artifacts/runs")


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

    data_provider: DataProvider = "akshare"
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
    tushare_token: str | None = None
    tushare_ts_code_a: str | None = None
    tushare_ts_code_h: str | None = None
    tushare_adjust: str = "qfq"
    simulation_seed: int = 7
    simulation_n_days: int = 1500
    simulation_regime_shift_day: int | None = 900
    simulation_mu_h: float = 0.08
    simulation_sigma_h: float = 0.22
    simulation_k: float = 20.0
    simulation_l: float = 0.0
    simulation_a: float = 0.05
    simulation_beta: float = 1.1
    simulation_rho: float = -0.25


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
    execution_timing: ExecutionTiming = "next_open"
    entry_signal_mode: EntrySignalMode = "zscore"
    hedge_ratio_mode: HedgeRatioMode = "training"
    return_filter_mode: ReturnFilterMode = "off"
    cointegration_gate_mode: CointegrationGateMode = "off"
    ecm_gate_mode: ECMGateMode = "off"
    half_life_anchor_mode: HalfLifeAnchorMode = "off"
    half_life_z_window_multiplier: float | None = None
    half_life_max_holding_multiplier: float | None = None
    return_filter_window: int = 10
    return_filter_min_periods: int | None = None
    adv_window: int = 20
    adv_min_periods: int | None = None
    max_adv_fraction: float | None = 0.05
    a_lot_size: int = 100
    h_lot_size: int = 100
    backtest_engine: BacktestEngine = "trade"
    mean_reversion_gate_mode: MeanReversionGateMode = "off"
    ou_window: int = 252
    ou_det_order: int = 0
    ou_k_ar_diff: int = 1
    ou_lags: int = 10
    half_life_min_days: float = 5.0
    half_life_max_days: float = 90.0
    lb_p_value_min: float = 0.05
    min_weight: float = 0.10
    position_sizing_mode: PositionSizingMode = "fixed"
    target_vol: float = 0.10
    vol_window: int = 60
    vol_min_periods: int | None = None
    max_leverage: float = 2.0
    max_drawdown: float = 0.10
    suspend_days: int = 20
    portfolio_max_drawdown: float = 0.18
    risk_free_rate: float = 0.0


@dataclass(slots=True, frozen=True)
class CostConfig:
    """Explicit transaction-cost assumptions for each leg."""

    a_buy_cost_bps: float = 2.0
    a_sell_cost_bps: float = 2.0
    h_buy_cost_bps: float = 8.0
    h_sell_cost_bps: float = 8.0
    h_stamp_duty_bps: float = 10.0
    fx_conversion_bps: float = 2.0
    a_slippage_bps: float = 3.0
    h_slippage_bps: float = 6.0
    a_impact_bps_per_100pct_adv: float = 15.0
    h_impact_bps_per_100pct_adv: float = 25.0
    a_short_borrow_apr_bps: float = 250.0
    h_short_borrow_apr_bps: float = 150.0
    a_long_financing_apr_bps: float = 0.0
    h_long_financing_apr_bps: float = 0.0
    base_cost_bps: float = 3.0
    impact_cost_bps: float = 2.0


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
    cache_dir: Path | None = DEFAULT_CACHE_DIR
    refresh_cache: bool = False
    resume_from_cache: bool = False
    output_dir: Path | None = None
