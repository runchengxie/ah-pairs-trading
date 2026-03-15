"""Trading strategy logic for A/H relative-value backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .config import CostConfig, StrategyConfig
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
    win_rate: float
    avg_holding_days: float
    total_costs: float

    def as_dict(self) -> dict[str, float | int]:
        """Return a JSON-serializable summary."""

        return {
            "final_capital": self.final_capital,
            "total_return": self.total_return,
            "annual_return": self.annual_return,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "trade_count": self.trade_count,
            "win_rate": self.win_rate,
            "avg_holding_days": self.avg_holding_days,
            "total_costs": self.total_costs,
        }


@dataclass(slots=True)
class BacktestResult:
    """Detailed data produced by a backtest run."""

    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    summary: BacktestSummary


@dataclass(slots=True)
class PositionState:
    """The currently open A/H relative-value position."""

    entry_date: pd.Timestamp
    entry_row_number: int
    direction: str
    entry_zscore: float
    a_side: int
    a_shares: int
    a_entry_price: float
    h_side: int
    h_shares: int
    h_entry_price: float
    entry_cost: float


def _round_lot_shares(notional: float, price: float, lot_size: int) -> int:
    if price <= 0:
        return 0
    raw_shares = int(notional // price)
    if lot_size > 1:
        raw_shares = raw_shares // lot_size * lot_size
    return max(raw_shares, 0)


def _transaction_cost(notional: float, market: str, side: int, costs: CostConfig) -> float:
    if notional <= 0:
        return 0.0

    if market == "a":
        rate_bps = costs.a_buy_cost_bps if side > 0 else costs.a_sell_cost_bps
    else:
        base_rate_bps = costs.h_buy_cost_bps if side > 0 else costs.h_sell_cost_bps
        rate_bps = base_rate_bps + costs.h_stamp_duty_bps + costs.fx_conversion_bps
    return float(notional * rate_bps / 10_000.0)


def _mark_to_market(position: PositionState, a_price: float, h_price: float) -> float:
    a_pnl = position.a_side * position.a_shares * (a_price - position.a_entry_price)
    h_pnl = position.h_side * position.h_shares * (h_price - position.h_entry_price)
    return float(a_pnl + h_pnl)


def _gross_exposure(position: PositionState, a_price: float, h_price: float) -> float:
    return float(abs(position.a_shares * a_price) + abs(position.h_shares * h_price))


def _build_position(
    *,
    date: pd.Timestamp,
    row_number: int,
    zscore: float,
    a_price: float,
    h_price: float,
    capital: float,
    hedge_ratio: float,
    strategy_config: StrategyConfig,
    cost_config: CostConfig,
) -> PositionState | None:
    gross_budget = capital * strategy_config.position_size_fraction
    if gross_budget <= 0:
        return None

    if strategy_config.execution_mode == "long_cheaper_leg_only":
        if zscore > 0:
            h_notional = gross_budget
            h_shares = _round_lot_shares(h_notional, h_price, strategy_config.h_lot_size)
            if h_shares <= 0:
                return None
            entry_cost = _transaction_cost(h_shares * h_price, "h", 1, cost_config)
            return PositionState(
                entry_date=date,
                entry_row_number=row_number,
                direction="long_h_only",
                entry_zscore=float(zscore),
                a_side=0,
                a_shares=0,
                a_entry_price=a_price,
                h_side=1,
                h_shares=h_shares,
                h_entry_price=h_price,
                entry_cost=entry_cost,
            )

        a_notional = gross_budget
        a_shares = _round_lot_shares(a_notional, a_price, strategy_config.a_lot_size)
        if a_shares <= 0:
            return None
        entry_cost = _transaction_cost(a_shares * a_price, "a", 1, cost_config)
        return PositionState(
            entry_date=date,
            entry_row_number=row_number,
            direction="long_a_only",
            entry_zscore=float(zscore),
            a_side=1,
            a_shares=a_shares,
            a_entry_price=a_price,
            h_side=0,
            h_shares=0,
            h_entry_price=h_price,
            entry_cost=entry_cost,
        )

    h_weight = max(abs(float(hedge_ratio)), 1e-8)
    unit_budget = gross_budget / (1.0 + h_weight)
    a_notional = unit_budget
    h_notional = unit_budget * h_weight

    a_shares = _round_lot_shares(a_notional, a_price, strategy_config.a_lot_size)
    h_shares = _round_lot_shares(h_notional, h_price, strategy_config.h_lot_size)
    if a_shares <= 0 or h_shares <= 0:
        return None

    if zscore > 0:
        a_side = -1
        h_side = 1
        direction = "short_a_long_h"
    else:
        a_side = 1
        h_side = -1
        direction = "long_a_short_h"

    entry_cost = _transaction_cost(a_shares * a_price, "a", a_side, cost_config) + _transaction_cost(
        h_shares * h_price,
        "h",
        h_side,
        cost_config,
    )
    return PositionState(
        entry_date=date,
        entry_row_number=row_number,
        direction=direction,
        entry_zscore=float(zscore),
        a_side=a_side,
        a_shares=a_shares,
        a_entry_price=a_price,
        h_side=h_side,
        h_shares=h_shares,
        h_entry_price=h_price,
        entry_cost=entry_cost,
    )


def _close_trade_record(
    position: PositionState,
    exit_date: pd.Timestamp,
    exit_zscore: float,
    gross_pnl: float,
    exit_cost: float,
    holding_days: int,
    exit_reason: str,
) -> dict[str, Any]:
    total_cost = position.entry_cost + exit_cost
    return {
        "entry_date": position.entry_date,
        "exit_date": exit_date,
        "direction": position.direction,
        "a_side": position.a_side,
        "a_shares": position.a_shares,
        "h_side": position.h_side,
        "h_shares": position.h_shares,
        "entry_zscore": position.entry_zscore,
        "exit_zscore": exit_zscore,
        "gross_pnl": gross_pnl,
        "entry_cost": position.entry_cost,
        "exit_cost": exit_cost,
        "net_pnl": gross_pnl - total_cost,
        "holding_days": holding_days,
        "exit_reason": exit_reason,
    }


def backtest_relative_value_strategy(
    signal_frame: pd.DataFrame,
    a_symbol: str,
    h_symbol: str,
    hedge_ratio: float,
    strategy_config: StrategyConfig,
    cost_config: CostConfig,
    close_on_end: bool = True,
) -> BacktestResult:
    """Backtest an A/H relative-value strategy on a prepared signal frame."""

    if signal_frame.empty:
        raise ValueError("The signal frame is empty.")
    if not strategy_config.entry_z_candidates:
        raise ValueError("At least one entry z-score candidate is required.")
    if strategy_config.stop_z <= strategy_config.exit_z:
        raise ValueError("The stop-loss z-score must be larger than the exit z-score.")

    open_position: PositionState | None = None
    realized_pnl = 0.0
    total_costs = 0.0
    equity_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    last_row_index = len(signal_frame) - 1

    for row_number, (date, row) in enumerate(signal_frame.iterrows()):
        a_price = float(row[a_symbol])
        h_price = float(row[h_symbol])
        zscore = float(row["zscore"]) if pd.notna(row["zscore"]) else np.nan
        spread = float(row["spread"]) if pd.notna(row["spread"]) else np.nan
        cheap_leg = row.get("cheap_leg", "flat")
        unrealized_pnl = 0.0
        closed_this_bar = False

        if open_position is not None:
            unrealized_pnl = _mark_to_market(open_position, a_price, h_price)
            holding_days = row_number - open_position.entry_row_number
            exit_reason: str | None = None
            if pd.notna(zscore) and abs(zscore) <= strategy_config.exit_z:
                exit_reason = "mean_reversion"
            elif pd.notna(zscore) and abs(zscore) >= strategy_config.stop_z:
                exit_reason = "stop_loss"
            elif holding_days >= strategy_config.max_holding_days:
                exit_reason = "max_holding_period"
            elif close_on_end and row_number == last_row_index:
                exit_reason = "end_of_sample"

            if exit_reason is not None:
                exit_cost = _transaction_cost(
                    open_position.a_shares * a_price,
                    "a",
                    -open_position.a_side,
                    cost_config,
                ) + _transaction_cost(
                    open_position.h_shares * h_price,
                    "h",
                    -open_position.h_side,
                    cost_config,
                )
                realized_pnl += unrealized_pnl - exit_cost
                total_costs += exit_cost
                trade_rows.append(
                    _close_trade_record(
                        position=open_position,
                        exit_date=date,
                        exit_zscore=float(zscore) if pd.notna(zscore) else np.nan,
                        gross_pnl=float(unrealized_pnl),
                        exit_cost=float(exit_cost),
                        holding_days=holding_days,
                        exit_reason=exit_reason,
                    )
                )
                open_position = None
                unrealized_pnl = 0.0
                closed_this_bar = True

        available_capital = strategy_config.initial_capital + realized_pnl
        if (
            open_position is None
            and not closed_this_bar
            and row_number != last_row_index
            and pd.notna(zscore)
            and abs(zscore) >= min(strategy_config.entry_z_candidates)
        ):
            candidate = _build_position(
                date=date,
                row_number=row_number,
                zscore=float(zscore),
                a_price=a_price,
                h_price=h_price,
                capital=available_capital,
                hedge_ratio=hedge_ratio,
                strategy_config=strategy_config,
                cost_config=cost_config,
            )
            if candidate is not None:
                open_position = candidate
                realized_pnl -= candidate.entry_cost
                total_costs += candidate.entry_cost
                available_capital -= candidate.entry_cost

        capital = strategy_config.initial_capital + realized_pnl + unrealized_pnl
        equity_rows.append(
            {
                "date": date,
                "capital": capital,
                "spread": spread,
                "zscore": zscore,
                "cheap_leg": cheap_leg,
                "position": "flat" if open_position is None else open_position.direction,
                "gross_exposure": 0.0 if open_position is None else _gross_exposure(open_position, a_price, h_price),
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
            "a_side",
            "a_shares",
            "h_side",
            "h_shares",
            "entry_zscore",
            "exit_zscore",
            "gross_pnl",
            "entry_cost",
            "exit_cost",
            "net_pnl",
            "holding_days",
            "exit_reason",
        ],
    )

    final_capital = float(equity_curve["capital"].iloc[-1])
    total_return = final_capital / strategy_config.initial_capital - 1.0
    annual_return = 0.0
    if len(equity_curve) > 0:
        annual_return = float((1.0 + total_return) ** (252 / len(equity_curve)) - 1.0)

    returns_std = float(equity_curve["returns"].std())
    sharpe_ratio = 0.0
    if returns_std > 0:
        sharpe_ratio = float(np.sqrt(252) * equity_curve["returns"].mean() / returns_std)

    win_rate = 0.0
    avg_holding_days = 0.0
    if not trade_frame.empty:
        win_rate = float((trade_frame["net_pnl"] > 0).mean())
        avg_holding_days = float(trade_frame["holding_days"].mean())

    summary = BacktestSummary(
        final_capital=final_capital,
        total_return=float(total_return),
        annual_return=annual_return,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=compute_max_drawdown(equity_curve["capital"]),
        trade_count=int(len(trade_frame)),
        win_rate=win_rate,
        avg_holding_days=avg_holding_days,
        total_costs=float(total_costs),
    )
    return BacktestResult(equity_curve=equity_curve, trades=trade_frame, summary=summary)


def grid_search_entry_z(
    signal_frame: pd.DataFrame,
    a_symbol: str,
    h_symbol: str,
    hedge_ratio: float,
    strategy_config: StrategyConfig,
    cost_config: CostConfig,
) -> tuple[float, pd.DataFrame]:
    """Run the strategy over several entry thresholds and pick the best one."""

    rows: list[dict[str, float | int]] = []
    for entry_z in strategy_config.entry_z_candidates:
        candidate_config = StrategyConfig(
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
            a_lot_size=strategy_config.a_lot_size,
            h_lot_size=strategy_config.h_lot_size,
        )
        result = backtest_relative_value_strategy(
            signal_frame=signal_frame,
            a_symbol=a_symbol,
            h_symbol=h_symbol,
            hedge_ratio=hedge_ratio,
            strategy_config=candidate_config,
            cost_config=cost_config,
        )
        row = result.summary.as_dict()
        row["entry_z"] = entry_z
        rows.append(row)

    if not rows:
        raise ValueError("At least one entry z-score candidate is required.")

    grid = pd.DataFrame(rows).set_index("entry_z").sort_index()
    if strategy_config.objective not in grid.columns:
        raise ValueError(f"Objective '{strategy_config.objective}' is not available in the grid-search output.")

    best_entry_z = float(grid[strategy_config.objective].idxmax())
    return best_entry_z, grid
