"""Plot generation helpers used when an output directory is requested."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def _prepare_output_path(output_path: str | Path | None) -> Path | None:
    if output_path is None:
        return None
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def plot_log_prices(
    log_prices: pd.DataFrame,
    dependent_symbol: str,
    independent_symbol: str,
    output_path: str | Path | None = None,
):
    """Plot the two log-price series."""

    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(10, 6))
    axis.plot(log_prices.index, log_prices[dependent_symbol], label=f"Log {dependent_symbol}")
    axis.plot(log_prices.index, log_prices[independent_symbol], label=f"Log {independent_symbol}")
    axis.set_title(f"{dependent_symbol} vs {independent_symbol} Log Price Comparison")
    axis.set_xlabel("Date")
    axis.set_ylabel("Log Price")
    axis.grid(True, linestyle="--", alpha=0.6)
    axis.legend()

    path = _prepare_output_path(output_path)
    if path is not None:
        figure.savefig(path, dpi=160, bbox_inches="tight")
    return figure


def plot_rolling_cointegration(rolling_frame: pd.DataFrame, output_path: str | Path | None = None):
    """Plot rolling cointegration p-values."""

    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(10, 6))
    axis.plot(rolling_frame.index, rolling_frame["p_value"], marker="o", linewidth=1.2)
    axis.axhline(0.05, color="red", linestyle="--", label="alpha=0.05")
    axis.set_title("Rolling Cointegration P-Values")
    axis.set_ylabel("P-Value")
    axis.grid(True, linestyle="--", alpha=0.5)
    axis.legend()

    path = _prepare_output_path(output_path)
    if path is not None:
        figure.savefig(path, dpi=160, bbox_inches="tight")
    return figure


def plot_z_search(train_grid: pd.DataFrame, output_path: str | Path | None = None):
    """Plot annual return and Sharpe ratio against entry z-scores."""

    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(train_grid.index, train_grid["annual_return"], marker="o")
    axes[0].set_ylabel("Annual Return")
    axes[0].grid(True, linestyle="--", alpha=0.6)
    axes[0].set_title("Grid Search by Entry Z-Score")

    axes[1].plot(train_grid.index, train_grid["sharpe_ratio"], marker="s", color="tab:red")
    axes[1].set_xlabel("Entry Z-Score")
    axes[1].set_ylabel("Sharpe Ratio")
    axes[1].grid(True, linestyle="--", alpha=0.6)

    path = _prepare_output_path(output_path)
    if path is not None:
        figure.savefig(path, dpi=160, bbox_inches="tight")
    return figure


def plot_equity_curve(
    equity_curve: pd.DataFrame,
    title: str,
    output_path: str | Path | None = None,
):
    """Plot the strategy equity curve and highlight the max drawdown point."""

    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(12, 6))
    axis.plot(equity_curve.index, equity_curve["capital"], label="Capital")
    drawdown_date = (equity_curve["capital"] / equity_curve["capital"].cummax() - 1.0).idxmin()
    axis.scatter(
        drawdown_date,
        equity_curve.loc[drawdown_date, "capital"],
        color="red",
        s=64,
        label="Max Drawdown",
    )
    axis.set_title(title)
    axis.set_xlabel("Date")
    axis.set_ylabel("Capital")
    axis.grid(True, linestyle="--", alpha=0.6)
    axis.legend()

    path = _prepare_output_path(output_path)
    if path is not None:
        figure.savefig(path, dpi=160, bbox_inches="tight")
    return figure


def plot_rolling_sharpe(
    rolling_sharpe_series: pd.Series,
    title: str,
    output_path: str | Path | None = None,
):
    """Plot a rolling Sharpe ratio series."""

    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(12, 5))
    axis.plot(rolling_sharpe_series.index, rolling_sharpe_series, label=rolling_sharpe_series.name or "Rolling Sharpe")
    axis.set_title(title)
    axis.set_xlabel("Date")
    axis.set_ylabel("Sharpe Ratio")
    axis.grid(True, linestyle="--", alpha=0.6)
    axis.legend()

    path = _prepare_output_path(output_path)
    if path is not None:
        figure.savefig(path, dpi=160, bbox_inches="tight")
    return figure


def plot_cumulative_returns(
    comparison: pd.DataFrame,
    benchmark_label: str,
    output_path: str | Path | None = None,
):
    """Plot cumulative strategy and benchmark returns."""

    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(10, 6))
    axis.plot(comparison.index, comparison["cum_strategy"], label="Strategy")
    axis.plot(comparison.index, comparison["cum_benchmark"], label=benchmark_label)
    axis.set_title("Cumulative Returns")
    axis.set_ylabel("Growth of 1")
    axis.grid(True, linestyle="--", alpha=0.6)
    axis.legend()

    path = _prepare_output_path(output_path)
    if path is not None:
        figure.savefig(path, dpi=160, bbox_inches="tight")
    return figure


def plot_rolling_beta(
    beta_series: pd.Series,
    benchmark_label: str,
    output_path: str | Path | None = None,
):
    """Plot rolling beta against the benchmark."""

    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(10, 5))
    axis.plot(beta_series.index, beta_series, label=f"Rolling Beta vs {benchmark_label}")
    axis.axhline(0.0, color="red", linestyle="--")
    axis.set_title("Rolling Beta")
    axis.set_xlabel("Date")
    axis.set_ylabel("Beta")
    axis.grid(True, linestyle="--", alpha=0.6)
    axis.legend()

    path = _prepare_output_path(output_path)
    if path is not None:
        figure.savefig(path, dpi=160, bbox_inches="tight")
    return figure


def plot_excess_returns(comparison: pd.DataFrame, output_path: str | Path | None = None):
    """Plot cumulative excess returns of the strategy over the benchmark."""

    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(10, 6))
    axis.plot(comparison.index, comparison["cum_excess_ret"], label="Cumulative Excess Return")
    axis.set_title("Cumulative Excess Returns")
    axis.set_ylabel("Growth of 1")
    axis.grid(True, linestyle="--", alpha=0.6)
    axis.legend()

    path = _prepare_output_path(output_path)
    if path is not None:
        figure.savefig(path, dpi=160, bbox_inches="tight")
    return figure
