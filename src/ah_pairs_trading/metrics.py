"""Performance metric helpers used by the backtests."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm

TRADING_DAYS_PER_YEAR = 252


@dataclass(slots=True, frozen=True)
class DrawdownStats:
    """Summary statistics derived from an equity drawdown curve."""

    max_drawdown: float
    max_drawdown_duration: int
    recovery_days: int | None

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "max_drawdown": self.max_drawdown,
            "max_drawdown_duration": self.max_drawdown_duration,
            "recovery_days": self.recovery_days,
        }


@dataclass(slots=True, frozen=True)
class BenchmarkSummary:
    """Relative-performance metrics against an optional benchmark."""

    benchmark_label: str
    observations: int
    benchmark_total_return: float | None
    benchmark_annual_return: float | None
    excess_total_return: float | None
    relative_return: float | None
    tracking_error: float | None
    information_ratio: float | None
    beta: float | None
    alpha: float | None
    rolling_beta_mean: float | None
    rolling_beta_median: float | None

    def as_dict(self) -> dict[str, float | int | str | None]:
        return {
            "benchmark_label": self.benchmark_label,
            "observations": self.observations,
            "benchmark_total_return": self.benchmark_total_return,
            "benchmark_annual_return": self.benchmark_annual_return,
            "excess_total_return": self.excess_total_return,
            "relative_return": self.relative_return,
            "tracking_error": self.tracking_error,
            "information_ratio": self.information_ratio,
            "beta": self.beta,
            "alpha": self.alpha,
            "rolling_beta_mean": self.rolling_beta_mean,
            "rolling_beta_median": self.rolling_beta_median,
        }


def annualize_total_return(
    total_return: float,
    observations: int,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Convert a cumulative return into an annualized return."""

    if observations <= 0 or 1.0 + total_return <= 0:
        return 0.0
    return float((1.0 + total_return) ** (periods_per_year / observations) - 1.0)


def compute_annualized_volatility(
    returns: pd.Series,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Compute annualized volatility from daily returns."""

    clean_returns = returns.dropna()
    if clean_returns.empty:
        return 0.0
    return float(clean_returns.std() * np.sqrt(periods_per_year))


def compute_sharpe_ratio(
    returns: pd.Series,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Compute an annualized Sharpe ratio."""

    clean_returns = returns.dropna()
    if clean_returns.empty:
        return 0.0
    returns_std = float(clean_returns.std())
    if returns_std <= 0:
        return 0.0
    return float(np.sqrt(periods_per_year) * clean_returns.mean() / returns_std)


def compute_sortino_ratio(
    returns: pd.Series,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """Compute an annualized Sortino ratio."""

    clean_returns = returns.dropna()
    if clean_returns.empty:
        return None
    downside_returns = clean_returns[clean_returns < 0]
    downside_std = float(downside_returns.std()) if not downside_returns.empty else 0.0
    if downside_std <= 0:
        return None
    return float(np.sqrt(periods_per_year) * clean_returns.mean() / downside_std)


def compute_max_drawdown(capital_series: pd.Series) -> float:
    """Return the worst drawdown observed in a capital series."""

    running_max = capital_series.cummax()
    drawdown = capital_series / running_max - 1.0
    return float(drawdown.min())


def add_drawdown_columns(equity_curve: pd.DataFrame) -> pd.DataFrame:
    """Attach running maximum and drawdown columns to an equity curve."""

    result = equity_curve.copy()
    result["running_max"] = result["capital"].cummax()
    result["drawdown"] = result["capital"] / result["running_max"] - 1.0
    return result


def compute_drawdown_stats(capital_series: pd.Series) -> DrawdownStats:
    """Return drawdown depth plus duration and recovery information."""

    if capital_series.empty:
        return DrawdownStats(max_drawdown=0.0, max_drawdown_duration=0, recovery_days=None)

    drawdown_frame = add_drawdown_columns(pd.DataFrame({"capital": capital_series}))
    drawdown = drawdown_frame["drawdown"]
    underwater = drawdown < 0

    duration = 0
    max_duration = 0
    for is_underwater in underwater:
        if is_underwater:
            duration += 1
            max_duration = max(max_duration, duration)
        else:
            duration = 0

    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0
    if max_drawdown == 0.0:
        return DrawdownStats(max_drawdown=max_drawdown, max_drawdown_duration=max_duration, recovery_days=0)

    trough_position = int(drawdown.to_numpy().argmin())
    peak_level = float(drawdown_frame["running_max"].iloc[trough_position])
    recovery_days: int | None = None
    for row_number in range(trough_position + 1, len(drawdown_frame)):
        if float(drawdown_frame["capital"].iloc[row_number]) >= peak_level:
            recovery_days = row_number - trough_position
            break

    return DrawdownStats(
        max_drawdown=max_drawdown,
        max_drawdown_duration=max_duration,
        recovery_days=recovery_days,
    )


def rolling_sharpe(returns: pd.Series, window: int = 60) -> pd.Series:
    """Compute a rolling annualized Sharpe ratio from daily returns."""

    rolling_mean = returns.rolling(window).mean()
    rolling_std = returns.rolling(window).std()
    sharpe = np.sqrt(TRADING_DAYS_PER_YEAR) * rolling_mean / rolling_std
    return sharpe.replace([np.inf, -np.inf], np.nan).rename("rolling_sharpe")


def prepare_comparison_frame(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    benchmark_label: str = "benchmark",
) -> pd.DataFrame:
    """Align strategy and benchmark returns into one reporting frame."""

    comparison = pd.DataFrame(
        {
            "strategy_ret": strategy_returns,
            "benchmark_ret": benchmark_returns,
        }
    ).dropna()

    comparison["cum_strategy"] = (1.0 + comparison["strategy_ret"]).cumprod()
    comparison["cum_benchmark"] = (1.0 + comparison["benchmark_ret"]).cumprod()
    comparison["excess_ret"] = comparison["strategy_ret"] - comparison["benchmark_ret"]
    comparison["cum_excess_ret"] = (1.0 + comparison["excess_ret"]).cumprod()
    comparison.attrs["benchmark_label"] = benchmark_label
    return comparison


def rolling_beta(comparison: pd.DataFrame, window: int = 60) -> pd.Series:
    """Estimate a rolling beta of strategy returns against benchmark returns."""

    if window >= len(comparison):
        return pd.Series(dtype=float, name="rolling_beta")

    betas: list[float] = []
    index_values: list[pd.Timestamp] = []
    for row_index in range(window, len(comparison) + 1):
        sample = comparison.iloc[row_index - window : row_index]
        regression_x = sm.add_constant(sample["benchmark_ret"])
        regression = sm.OLS(sample["strategy_ret"], regression_x).fit()
        betas.append(float(regression.params["benchmark_ret"]))
        index_values.append(sample.index[-1])

    return pd.Series(betas, index=index_values, name="rolling_beta")


def compute_monthly_win_rate(returns: pd.Series) -> float | None:
    """Measure the fraction of months with positive compounded returns."""

    clean_returns = returns.dropna()
    if clean_returns.empty:
        return None

    monthly_returns = clean_returns.resample("ME").apply(lambda values: (1.0 + values).prod() - 1.0).dropna()
    if monthly_returns.empty:
        return None
    return float((monthly_returns > 0).mean())


def compute_max_consecutive_losses(trade_frame: pd.DataFrame) -> int:
    """Return the longest losing streak observed in sequential trades."""

    if trade_frame.empty or "net_pnl" not in trade_frame.columns:
        return 0

    consecutive_losses = 0
    max_losses = 0
    for is_loss in (trade_frame["net_pnl"] <= 0).tolist():
        if is_loss:
            consecutive_losses += 1
            max_losses = max(max_losses, consecutive_losses)
        else:
            consecutive_losses = 0
    return max_losses


def summarize_benchmark(
    comparison: pd.DataFrame,
    rolling_beta_series: pd.Series | None = None,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> BenchmarkSummary:
    """Summarize relative performance versus a benchmark return stream."""

    benchmark_label = str(comparison.attrs.get("benchmark_label", "benchmark"))
    clean_comparison = comparison.dropna().copy()
    if clean_comparison.empty:
        return BenchmarkSummary(
            benchmark_label=benchmark_label,
            observations=0,
            benchmark_total_return=None,
            benchmark_annual_return=None,
            excess_total_return=None,
            relative_return=None,
            tracking_error=None,
            information_ratio=None,
            beta=None,
            alpha=None,
            rolling_beta_mean=None,
            rolling_beta_median=None,
        )

    strategy_total_return = float(clean_comparison["cum_strategy"].iloc[-1] - 1.0)
    benchmark_total_return = float(clean_comparison["cum_benchmark"].iloc[-1] - 1.0)
    benchmark_annual_return = annualize_total_return(benchmark_total_return, len(clean_comparison), periods_per_year)
    excess_returns = clean_comparison["strategy_ret"] - clean_comparison["benchmark_ret"]
    tracking_error = compute_annualized_volatility(excess_returns, periods_per_year=periods_per_year)
    information_ratio = None
    excess_std = float(excess_returns.std()) if not excess_returns.empty else 0.0
    if excess_std > 0:
        information_ratio = float(np.sqrt(periods_per_year) * excess_returns.mean() / excess_std)

    beta: float | None = None
    alpha: float | None = None
    if clean_comparison["benchmark_ret"].std() > 0:
        regression_x = sm.add_constant(clean_comparison["benchmark_ret"])
        regression = sm.OLS(clean_comparison["strategy_ret"], regression_x).fit()
        beta = float(regression.params["benchmark_ret"])
        alpha = float(regression.params["const"] * periods_per_year)

    rolling_beta_mean: float | None = None
    rolling_beta_median: float | None = None
    if rolling_beta_series is not None:
        clean_beta = rolling_beta_series.dropna()
        if not clean_beta.empty:
            rolling_beta_mean = float(clean_beta.mean())
            rolling_beta_median = float(clean_beta.median())

    relative_return = None
    benchmark_curve_end = float(clean_comparison["cum_benchmark"].iloc[-1])
    if benchmark_curve_end > 0:
        relative_return = float(clean_comparison["cum_strategy"].iloc[-1] / benchmark_curve_end - 1.0)

    return BenchmarkSummary(
        benchmark_label=benchmark_label,
        observations=int(len(clean_comparison)),
        benchmark_total_return=benchmark_total_return,
        benchmark_annual_return=benchmark_annual_return,
        excess_total_return=float(strategy_total_return - benchmark_total_return),
        relative_return=relative_return,
        tracking_error=tracking_error,
        information_ratio=information_ratio,
        beta=beta,
        alpha=alpha,
        rolling_beta_mean=rolling_beta_mean,
        rolling_beta_median=rolling_beta_median,
    )
