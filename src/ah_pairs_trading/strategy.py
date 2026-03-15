"""Trading strategy logic for the fixed-hedge-ratio backtest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .metrics import compute_max_drawdown


@dataclass(slots=True)
class BacktestSummary:
    """Aggregated performance metrics for a backtest."""

    final_capital: float
    total_return: float
    annual_return: float
    sharpe_ratio: float
    max_drawdown: float
    trade_count: int

    def as_dict(self) -> dict[str, float | int]:
        """Return a JSON-serializable summary."""

        return {
            "final_capital": self.final_capital,
            "total_return": self.total_return,
            "annual_return": self.annual_return,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "trade_count": self.trade_count,
        }


@dataclass(slots=True)
class BacktestResult:
    """Detailed data produced by a backtest run."""

    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    summary: BacktestSummary


@dataclass(slots=True)
class PositionState:
    """The currently open spread position, if any."""

    entry_date: pd.Timestamp
    direction: str
    dependent_shares: float
    independent_shares: float
    dependent_entry_price: float
    independent_entry_price: float
    entry_zscore: float


def calculate_spread(
    dependent_price: float,
    independent_price: float,
    intercept: float,
    hedge_ratio: float,
) -> float:
    """Compute the log-price spread implied by the cointegration model."""

    return float(np.log(dependent_price) - intercept - hedge_ratio * np.log(independent_price))


def _mark_to_market(state: PositionState, dependent_price: float, independent_price: float) -> float:
    """Calculate the current mark-to-market value of the open position."""

    dependent_pnl = state.dependent_shares * (dependent_price - state.dependent_entry_price)
    independent_pnl = state.independent_shares * (independent_price - state.independent_entry_price)
    return float(dependent_pnl + independent_pnl)


def _close_trade_record(
    state: PositionState,
    exit_date: pd.Timestamp,
    exit_zscore: float,
    pnl: float,
) -> dict[str, Any]:
    """Build a trade record for the completed position."""

    return {
        "entry_date": state.entry_date,
        "exit_date": exit_date,
        "direction": state.direction,
        "dependent_shares": state.dependent_shares,
        "independent_shares": state.independent_shares,
        "entry_zscore": state.entry_zscore,
        "exit_zscore": exit_zscore,
        "pnl": pnl,
    }


def backtest_fixed_beta(
    price_frame: pd.DataFrame,
    dependent_symbol: str,
    independent_symbol: str,
    intercept: float,
    hedge_ratio: float,
    residual_mean: float,
    residual_std: float,
    entry_z: float,
    exit_z: float = 0.2,
    initial_capital: float = 100_000.0,
    close_on_end: bool = True,
) -> BacktestResult:
    """Backtest a fixed hedge-ratio pairs strategy on an aligned price frame."""

    if residual_std <= 0:
        raise ValueError("Residual standard deviation must be positive for z-score based trading.")

    open_position: PositionState | None = None
    realized_pnl = 0.0
    equity_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    last_row_index = len(price_frame) - 1
    independent_units = max(abs(float(hedge_ratio)), 1e-8)

    for row_number, (date, row) in enumerate(price_frame.iterrows()):
        dependent_price = float(row[dependent_symbol])
        independent_price = float(row[independent_symbol])
        spread = calculate_spread(
            dependent_price=dependent_price,
            independent_price=independent_price,
            intercept=intercept,
            hedge_ratio=hedge_ratio,
        )
        zscore = (spread - residual_mean) / residual_std
        unrealized_pnl = 0.0

        if open_position is None:
            if zscore > entry_z:
                open_position = PositionState(
                    entry_date=date,
                    direction="short_spread",
                    dependent_shares=-1.0,
                    independent_shares=independent_units,
                    dependent_entry_price=dependent_price,
                    independent_entry_price=independent_price,
                    entry_zscore=float(zscore),
                )
            elif zscore < -entry_z:
                open_position = PositionState(
                    entry_date=date,
                    direction="long_spread",
                    dependent_shares=1.0,
                    independent_shares=-independent_units,
                    dependent_entry_price=dependent_price,
                    independent_entry_price=independent_price,
                    entry_zscore=float(zscore),
                )

        if open_position is not None:
            unrealized_pnl = _mark_to_market(
                open_position,
                dependent_price=dependent_price,
                independent_price=independent_price,
            )
            should_close = abs(zscore) < exit_z
            if close_on_end and row_number == last_row_index:
                should_close = True

            if should_close:
                realized_pnl += unrealized_pnl
                trade_rows.append(
                    _close_trade_record(
                        state=open_position,
                        exit_date=date,
                        exit_zscore=float(zscore),
                        pnl=float(unrealized_pnl),
                    )
                )
                open_position = None
                unrealized_pnl = 0.0

        capital = initial_capital + realized_pnl + unrealized_pnl
        equity_rows.append(
            {
                "date": date,
                "capital": capital,
                "spread": spread,
                "zscore": zscore,
                "position": 0 if open_position is None else (1 if open_position.direction == "long_spread" else -1),
            }
        )

    equity_curve = pd.DataFrame(equity_rows).set_index("date")
    equity_curve["returns"] = equity_curve["capital"].pct_change().fillna(0.0)

    trade_frame = pd.DataFrame(
        trade_rows,
        columns=[
            "entry_date",
            "exit_date",
            "direction",
            "dependent_shares",
            "independent_shares",
            "entry_zscore",
            "exit_zscore",
            "pnl",
        ],
    )
    final_capital = float(equity_curve["capital"].iloc[-1])
    total_return = final_capital / initial_capital - 1.0
    annual_return = 0.0
    if len(equity_curve) > 0:
        annual_return = float((1.0 + total_return) ** (252 / len(equity_curve)) - 1.0)

    returns_std = float(equity_curve["returns"].std())
    sharpe_ratio = 0.0
    if returns_std > 0:
        sharpe_ratio = float(np.sqrt(252) * equity_curve["returns"].mean() / returns_std)

    summary = BacktestSummary(
        final_capital=final_capital,
        total_return=float(total_return),
        annual_return=annual_return,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=compute_max_drawdown(equity_curve["capital"]),
        trade_count=int(len(trade_frame)),
    )

    return BacktestResult(equity_curve=equity_curve, trades=trade_frame, summary=summary)


def grid_search_entry_z(
    price_frame: pd.DataFrame,
    dependent_symbol: str,
    independent_symbol: str,
    intercept: float,
    hedge_ratio: float,
    residual_mean: float,
    residual_std: float,
    entry_z_candidates: tuple[float, ...],
    exit_z: float = 0.2,
    initial_capital: float = 100_000.0,
    objective: str = "sharpe_ratio",
) -> tuple[float, pd.DataFrame]:
    """Run the backtest over several entry thresholds and pick the best one."""

    rows: list[dict[str, float | int]] = []
    for entry_z in entry_z_candidates:
        result = backtest_fixed_beta(
            price_frame=price_frame,
            dependent_symbol=dependent_symbol,
            independent_symbol=independent_symbol,
            intercept=intercept,
            hedge_ratio=hedge_ratio,
            residual_mean=residual_mean,
            residual_std=residual_std,
            entry_z=entry_z,
            exit_z=exit_z,
            initial_capital=initial_capital,
        )
        row = result.summary.as_dict()
        row["entry_z"] = entry_z
        rows.append(row)

    if not rows:
        raise ValueError("At least one entry z-score candidate is required.")

    grid = pd.DataFrame(rows).set_index("entry_z").sort_index()
    if objective not in grid.columns:
        raise ValueError(f"Objective '{objective}' is not available in the grid-search output.")

    best_entry_z = float(grid[objective].idxmax())
    return best_entry_z, grid
