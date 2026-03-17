"""Trading strategy logic for A/H relative-value backtests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pandas as pd

from .config import CostConfig, StrategyConfig
from .metrics import (
    TRADING_DAYS_PER_YEAR,
    annualize_total_return,
    compute_annualized_volatility,
    compute_drawdown_stats,
    compute_max_consecutive_losses,
    compute_monthly_win_rate,
    compute_sharpe_ratio,
    compute_sortino_ratio,
)


@dataclass(slots=True)
class BacktestSummary:
    """Aggregated performance metrics for a backtest."""

    final_capital: float
    total_return: float
    annual_return: float
    annual_volatility: float
    sharpe_ratio: float
    sortino_ratio: float | None
    max_drawdown: float
    calmar_ratio: float | None
    max_drawdown_duration: int
    recovery_days: int | None
    trade_count: int
    win_rate: float
    payoff_ratio: float | None
    profit_factor: float | None
    avg_trade_pnl: float | None
    avg_holding_days: float
    avg_win_pnl: float | None
    avg_loss_pnl: float | None
    gross_pnl: float
    net_pnl: float
    total_costs: float
    transaction_costs: float
    slippage_costs: float
    borrow_costs: float
    financing_costs: float
    cost_to_gross_pnl: float | None
    time_in_market: float
    max_consecutive_losses: int
    monthly_win_rate: float | None

    def as_dict(self) -> dict[str, float | int | None]:
        """Return a JSON-serializable summary."""

        return {
            "final_capital": self.final_capital,
            "total_return": self.total_return,
            "annual_return": self.annual_return,
            "annual_volatility": self.annual_volatility,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "max_drawdown": self.max_drawdown,
            "calmar_ratio": self.calmar_ratio,
            "max_drawdown_duration": self.max_drawdown_duration,
            "recovery_days": self.recovery_days,
            "trade_count": self.trade_count,
            "win_rate": self.win_rate,
            "payoff_ratio": self.payoff_ratio,
            "profit_factor": self.profit_factor,
            "avg_trade_pnl": self.avg_trade_pnl,
            "avg_holding_days": self.avg_holding_days,
            "avg_win_pnl": self.avg_win_pnl,
            "avg_loss_pnl": self.avg_loss_pnl,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "total_costs": self.total_costs,
            "transaction_costs": self.transaction_costs,
            "slippage_costs": self.slippage_costs,
            "borrow_costs": self.borrow_costs,
            "financing_costs": self.financing_costs,
            "cost_to_gross_pnl": self.cost_to_gross_pnl,
            "time_in_market": self.time_in_market,
            "max_consecutive_losses": self.max_consecutive_losses,
            "monthly_win_rate": self.monthly_win_rate,
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
    entry_transaction_cost: float
    entry_slippage_cost: float
    entry_cost: float
    accrued_borrow_cost: float = 0.0
    accrued_financing_cost: float = 0.0


@dataclass(slots=True)
class ExecutionCostBreakdown:
    """Explicit execution costs applied when opening or closing a trade."""

    transaction_cost: float
    slippage_cost: float

    @property
    def total(self) -> float:
        return float(self.transaction_cost + self.slippage_cost)


@dataclass(slots=True)
class CarryCostBreakdown:
    """Daily carry costs accrued while a position is open."""

    borrow_cost: float
    financing_cost: float

    @property
    def total(self) -> float:
        return float(self.borrow_cost + self.financing_cost)


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


def _slippage_cost(notional: float, market: str, shares: int, adv: float | None, costs: CostConfig) -> float:
    if notional <= 0:
        return 0.0

    if market == "a":
        base_bps = costs.a_slippage_bps
        impact_bps = costs.a_impact_bps_per_100pct_adv
    else:
        base_bps = costs.h_slippage_bps
        impact_bps = costs.h_impact_bps_per_100pct_adv

    participation = 0.0
    if adv is not None and pd.notna(adv) and float(adv) > 0 and shares > 0:
        participation = max(float(shares) / float(adv), 0.0)
    return float(notional * (base_bps + impact_bps * participation) / 10_000.0)


def _execution_cost(
    *,
    notional: float,
    market: str,
    side: int,
    shares: int,
    adv: float | None,
    costs: CostConfig,
) -> ExecutionCostBreakdown:
    return ExecutionCostBreakdown(
        transaction_cost=_transaction_cost(notional, market, side, costs),
        slippage_cost=_slippage_cost(notional, market, shares, adv, costs),
    )


def _cap_shares_by_adv(shares: int, adv: float | None, max_adv_fraction: float | None, lot_size: int) -> int:
    if shares <= 0 or max_adv_fraction is None or max_adv_fraction <= 0:
        return max(shares, 0)
    if adv is None or pd.isna(adv) or float(adv) <= 0:
        return max(shares, 0)

    capped_shares = min(shares, int(float(adv) * max_adv_fraction))
    if lot_size > 1:
        capped_shares = capped_shares // lot_size * lot_size
    return max(capped_shares, 0)


def _daily_carry_cost(position: PositionState, a_price: float, h_price: float, costs: CostConfig) -> CarryCostBreakdown:
    a_short_notional = abs(position.a_shares * a_price) if position.a_side < 0 else 0.0
    h_short_notional = abs(position.h_shares * h_price) if position.h_side < 0 else 0.0
    a_long_notional = position.a_shares * a_price if position.a_side > 0 else 0.0
    h_long_notional = position.h_shares * h_price if position.h_side > 0 else 0.0
    borrow_cost = (
        a_short_notional * costs.a_short_borrow_apr_bps + h_short_notional * costs.h_short_borrow_apr_bps
    ) / (10_000.0 * TRADING_DAYS_PER_YEAR)
    financing_cost = (
        a_long_notional * costs.a_long_financing_apr_bps + h_long_notional * costs.h_long_financing_apr_bps
    ) / (10_000.0 * TRADING_DAYS_PER_YEAR)
    return CarryCostBreakdown(
        borrow_cost=float(borrow_cost),
        financing_cost=float(financing_cost),
    )


def _mark_to_market(position: PositionState, a_price: float, h_price: float) -> float:
    a_pnl = position.a_side * position.a_shares * (a_price - position.a_entry_price)
    h_pnl = position.h_side * position.h_shares * (h_price - position.h_entry_price)
    return float(a_pnl + h_pnl)


def _gross_exposure(position: PositionState, a_price: float, h_price: float) -> float:
    return float(abs(position.a_shares * a_price) + abs(position.h_shares * h_price))


def _entry_signal_columns(strategy_config: StrategyConfig) -> tuple[str, str]:
    if strategy_config.entry_signal_mode == "zscore":
        return "zscore", "cheap_leg"
    if strategy_config.entry_signal_mode == "ret_spread_ema":
        return "ret_spread_ema_zscore", "ret_spread_ema_cheap_leg"
    return "ret_spread_sma_zscore", "ret_spread_sma_cheap_leg"


def _build_position(
    *,
    date: pd.Timestamp,
    row_number: int,
    signal_value: float,
    a_price: float,
    h_price: float,
    a_adv: float | None,
    h_adv: float | None,
    capital: float,
    hedge_ratio: float,
    strategy_config: StrategyConfig,
    cost_config: CostConfig,
) -> PositionState | None:
    gross_budget = capital * strategy_config.position_size_fraction
    if gross_budget <= 0:
        return None

    if strategy_config.execution_mode == "long_cheaper_leg_only":
        if signal_value > 0:
            h_notional = gross_budget
            h_shares = _round_lot_shares(h_notional, h_price, strategy_config.h_lot_size)
            h_shares = _cap_shares_by_adv(h_shares, h_adv, strategy_config.max_adv_fraction, strategy_config.h_lot_size)
            if h_shares <= 0:
                return None
            execution_cost = _execution_cost(
                notional=h_shares * h_price,
                market="h",
                side=1,
                shares=h_shares,
                adv=h_adv,
                costs=cost_config,
            )
            return PositionState(
                entry_date=date,
                entry_row_number=row_number,
                direction="long_h_only",
                entry_zscore=float(signal_value),
                a_side=0,
                a_shares=0,
                a_entry_price=a_price,
                h_side=1,
                h_shares=h_shares,
                h_entry_price=h_price,
                entry_transaction_cost=execution_cost.transaction_cost,
                entry_slippage_cost=execution_cost.slippage_cost,
                entry_cost=execution_cost.total,
            )

        a_notional = gross_budget
        a_shares = _round_lot_shares(a_notional, a_price, strategy_config.a_lot_size)
        a_shares = _cap_shares_by_adv(a_shares, a_adv, strategy_config.max_adv_fraction, strategy_config.a_lot_size)
        if a_shares <= 0:
            return None
        execution_cost = _execution_cost(
            notional=a_shares * a_price,
            market="a",
            side=1,
            shares=a_shares,
            adv=a_adv,
            costs=cost_config,
        )
        return PositionState(
            entry_date=date,
            entry_row_number=row_number,
            direction="long_a_only",
            entry_zscore=float(signal_value),
            a_side=1,
            a_shares=a_shares,
            a_entry_price=a_price,
            h_side=0,
            h_shares=0,
            h_entry_price=h_price,
            entry_transaction_cost=execution_cost.transaction_cost,
            entry_slippage_cost=execution_cost.slippage_cost,
            entry_cost=execution_cost.total,
        )

    h_weight = max(abs(float(hedge_ratio)), 1e-8)
    unit_budget = gross_budget / (1.0 + h_weight)
    a_notional = unit_budget
    h_notional = unit_budget * h_weight

    a_shares = _round_lot_shares(a_notional, a_price, strategy_config.a_lot_size)
    h_shares = _round_lot_shares(h_notional, h_price, strategy_config.h_lot_size)
    a_shares = _cap_shares_by_adv(a_shares, a_adv, strategy_config.max_adv_fraction, strategy_config.a_lot_size)
    h_shares = _cap_shares_by_adv(h_shares, h_adv, strategy_config.max_adv_fraction, strategy_config.h_lot_size)
    if a_shares <= 0 or h_shares <= 0:
        return None

    if signal_value > 0:
        a_side = -1
        h_side = 1
        direction = "short_a_long_h"
    else:
        a_side = 1
        h_side = -1
        direction = "long_a_short_h"

    a_execution_cost = _execution_cost(
        notional=a_shares * a_price,
        market="a",
        side=a_side,
        shares=a_shares,
        adv=a_adv,
        costs=cost_config,
    )
    h_execution_cost = _execution_cost(
        notional=h_shares * h_price,
        market="h",
        side=h_side,
        shares=h_shares,
        adv=h_adv,
        costs=cost_config,
    )
    return PositionState(
        entry_date=date,
        entry_row_number=row_number,
        direction=direction,
        entry_zscore=float(signal_value),
        a_side=a_side,
        a_shares=a_shares,
        a_entry_price=a_price,
        h_side=h_side,
        h_shares=h_shares,
        h_entry_price=h_price,
        entry_transaction_cost=a_execution_cost.transaction_cost + h_execution_cost.transaction_cost,
        entry_slippage_cost=a_execution_cost.slippage_cost + h_execution_cost.slippage_cost,
        entry_cost=a_execution_cost.total + h_execution_cost.total,
    )


def _close_trade_record(
    position: PositionState,
    exit_date: pd.Timestamp,
    exit_signal_value: float,
    gross_pnl: float,
    exit_transaction_cost: float,
    exit_slippage_cost: float,
    holding_days: int,
    exit_reason: str,
    entry_signal_mode: str,
) -> dict[str, Any]:
    borrow_cost = position.accrued_borrow_cost
    financing_cost = position.accrued_financing_cost
    total_cost = (
        position.entry_transaction_cost
        + position.entry_slippage_cost
        + exit_transaction_cost
        + exit_slippage_cost
        + borrow_cost
        + financing_cost
    )
    return {
        "entry_date": position.entry_date,
        "exit_date": exit_date,
        "direction": position.direction,
        "a_side": position.a_side,
        "a_shares": position.a_shares,
        "h_side": position.h_side,
        "h_shares": position.h_shares,
        "entry_signal_mode": entry_signal_mode,
        "entry_signal_value": position.entry_zscore,
        "exit_signal_value": exit_signal_value,
        "entry_zscore": position.entry_zscore,
        "exit_zscore": exit_signal_value,
        "gross_pnl": gross_pnl,
        "entry_transaction_cost": position.entry_transaction_cost,
        "entry_slippage_cost": position.entry_slippage_cost,
        "entry_cost": position.entry_cost,
        "exit_transaction_cost": exit_transaction_cost,
        "exit_slippage_cost": exit_slippage_cost,
        "exit_cost": exit_transaction_cost + exit_slippage_cost,
        "borrow_cost": borrow_cost,
        "financing_cost": financing_cost,
        "net_pnl": gross_pnl - total_cost,
        "holding_days": holding_days,
        "exit_reason": exit_reason,
    }


def _return_filter_pass_column(strategy_config: StrategyConfig) -> str | None:
    if strategy_config.return_filter_mode == "off":
        return None
    if strategy_config.return_filter_mode == "ema":
        return "ret_spread_ema_filter_pass"
    return "ret_spread_sma_filter_pass"


def _return_signal_column(strategy_config: StrategyConfig) -> str | None:
    if strategy_config.return_filter_mode == "off":
        return None
    if strategy_config.return_filter_mode == "ema":
        return "ret_spread_ema"
    return "ret_spread_sma"


def _cointegration_gate_column(strategy_config: StrategyConfig) -> str | None:
    if strategy_config.cointegration_gate_mode == "off":
        return None
    return "cointegration_gate_pass"


def _ecm_gate_column(strategy_config: StrategyConfig) -> str | None:
    if strategy_config.ecm_gate_mode == "off":
        return None
    return "ecm_gate_pass"


def _passes_return_filter(row: pd.Series, strategy_config: StrategyConfig) -> bool:
    filter_column = _return_filter_pass_column(strategy_config)
    if filter_column is None:
        return True

    filter_value = row.get(filter_column, pd.NA)
    if pd.isna(filter_value):
        return False
    return bool(filter_value)


def _passes_cointegration_gate(row: pd.Series, strategy_config: StrategyConfig) -> bool:
    gate_column = _cointegration_gate_column(strategy_config)
    if gate_column is None:
        return True

    gate_value = row.get(gate_column, pd.NA)
    if pd.isna(gate_value):
        return False
    return bool(gate_value)


def _passes_ecm_gate(row: pd.Series, strategy_config: StrategyConfig) -> bool:
    gate_column = _ecm_gate_column(strategy_config)
    if gate_column is None:
        return True

    gate_value = row.get(gate_column, pd.NA)
    if pd.isna(gate_value):
        return False
    return bool(gate_value)


def _effective_hedge_ratio(row: pd.Series, fallback_hedge_ratio: float) -> float:
    row_hedge_ratio = row.get("hedge_ratio", np.nan)
    if pd.notna(row_hedge_ratio):
        return float(row_hedge_ratio)
    return float(fallback_hedge_ratio)


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
    signal_column, cheap_leg_column = _entry_signal_columns(strategy_config)
    if signal_column not in signal_frame.columns:
        raise ValueError(
            f"The signal frame is missing '{signal_column}' required by entry_signal_mode="
            f"'{strategy_config.entry_signal_mode}'."
        )
    if strategy_config.entry_signal_mode != "zscore" and strategy_config.return_filter_mode != "off":
        raise ValueError("The return filter is only supported when `entry_signal_mode='zscore'`.")
    return_filter_column = _return_filter_pass_column(strategy_config)
    return_signal_column = _return_signal_column(strategy_config)
    if return_filter_column is not None and return_filter_column not in signal_frame.columns:
        raise ValueError(
            f"The signal frame is missing '{return_filter_column}' required by return_filter_mode="
            f"'{strategy_config.return_filter_mode}'."
        )
    cointegration_gate_column = _cointegration_gate_column(strategy_config)
    if cointegration_gate_column is not None and cointegration_gate_column not in signal_frame.columns:
        raise ValueError(
            f"The signal frame is missing '{cointegration_gate_column}' required by cointegration_gate_mode="
            f"'{strategy_config.cointegration_gate_mode}'."
        )
    ecm_gate_column = _ecm_gate_column(strategy_config)
    if ecm_gate_column is not None and ecm_gate_column not in signal_frame.columns:
        raise ValueError(
            f"The signal frame is missing '{ecm_gate_column}' required by ecm_gate_mode="
            f"'{strategy_config.ecm_gate_mode}'."
        )

    open_position: PositionState | None = None
    realized_pnl = 0.0
    total_costs = 0.0
    transaction_costs = 0.0
    slippage_costs = 0.0
    borrow_costs = 0.0
    financing_costs = 0.0
    equity_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    last_row_index = len(signal_frame) - 1

    for row_number, (date, row) in enumerate(signal_frame.iterrows()):
        a_close = float(row[a_symbol])
        h_close = float(row[h_symbol])
        a_open = float(row["a_open"]) if pd.notna(row.get("a_open")) else a_close
        h_open = float(row["h_open"]) if pd.notna(row.get("h_open")) else h_close
        a_adv = float(row["a_adv"]) if pd.notna(row.get("a_adv")) else None
        h_adv = float(row["h_adv"]) if pd.notna(row.get("h_adv")) else None
        signal_value = float(row[signal_column]) if pd.notna(row.get(signal_column)) else np.nan
        raw_zscore = float(row["zscore"]) if pd.notna(row.get("zscore")) else np.nan
        spread = float(row["spread"]) if pd.notna(row["spread"]) else np.nan
        cheap_leg = row.get(cheap_leg_column, "flat")
        row_hedge_ratio = _effective_hedge_ratio(row, hedge_ratio)
        row_intercept = float(row["intercept"]) if pd.notna(row.get("intercept", np.nan)) else np.nan
        return_signal = (
            float(row[return_signal_column])
            if return_signal_column is not None and pd.notna(row.get(return_signal_column))
            else np.nan
        )
        return_filter_pass = (
            bool(row[return_filter_column])
            if return_filter_column is not None and pd.notna(row.get(return_filter_column))
            else pd.NA
        )
        cointegration_p_value = (
            float(row["cointegration_p_value"])
            if "cointegration_p_value" in signal_frame.columns and pd.notna(row.get("cointegration_p_value"))
            else np.nan
        )
        cointegration_significant = (
            bool(row["cointegration_significant"])
            if "cointegration_significant" in signal_frame.columns and pd.notna(row.get("cointegration_significant"))
            else pd.NA
        )
        cointegration_gate_pass = (
            bool(row[cointegration_gate_column])
            if cointegration_gate_column is not None and pd.notna(row.get(cointegration_gate_column))
            else pd.NA
        )
        ecm_speed = float(row["ecm_speed"]) if "ecm_speed" in signal_frame.columns and pd.notna(row.get("ecm_speed")) else np.nan
        ecm_p_value = (
            float(row["ecm_p_value"]) if "ecm_p_value" in signal_frame.columns and pd.notna(row.get("ecm_p_value")) else np.nan
        )
        ecm_gate_pass = (
            bool(row[ecm_gate_column])
            if ecm_gate_column is not None and pd.notna(row.get(ecm_gate_column))
            else pd.NA
        )
        decision_row: pd.Series | None
        execution_a_price: float
        execution_h_price: float
        if strategy_config.execution_timing == "next_open":
            decision_row = signal_frame.iloc[row_number - 1] if row_number > 0 else None
            execution_a_price = a_open
            execution_h_price = h_open
        else:
            decision_row = row
            execution_a_price = a_close
            execution_h_price = h_close

        decision_signal_value = np.nan
        decision_hedge_ratio = hedge_ratio
        decision_gate_allows_trading = False
        if decision_row is not None:
            decision_signal_value = (
                float(decision_row[signal_column]) if pd.notna(decision_row.get(signal_column)) else np.nan
            )
            decision_hedge_ratio = _effective_hedge_ratio(decision_row, hedge_ratio)
            decision_gate_allows_trading = _passes_cointegration_gate(decision_row, strategy_config) and _passes_ecm_gate(
                decision_row, strategy_config
            )

        unrealized_pnl = 0.0
        closed_this_bar = False
        daily_transaction_cost = 0.0
        daily_slippage_cost = 0.0
        daily_borrow_cost = 0.0
        daily_financing_cost = 0.0

        if open_position is not None:
            holding_days = row_number - open_position.entry_row_number
            exit_reason: str | None = None
            if decision_row is not None:
                if not _passes_cointegration_gate(decision_row, strategy_config):
                    exit_reason = "cointegration_breakdown"
                elif not _passes_ecm_gate(decision_row, strategy_config):
                    exit_reason = "ecm_breakdown"
                elif pd.notna(decision_signal_value) and abs(decision_signal_value) <= strategy_config.exit_z:
                    exit_reason = "mean_reversion"
                elif pd.notna(decision_signal_value) and abs(decision_signal_value) >= strategy_config.stop_z:
                    exit_reason = "stop_loss"
                elif holding_days >= strategy_config.max_holding_days:
                    exit_reason = "max_holding_period"
            if close_on_end and strategy_config.execution_timing == "close" and row_number == last_row_index:
                exit_reason = "end_of_sample"

            if exit_reason is not None:
                if strategy_config.execution_timing == "close":
                    carry_cost = _daily_carry_cost(open_position, a_close, h_close, cost_config)
                    open_position.accrued_borrow_cost += carry_cost.borrow_cost
                    open_position.accrued_financing_cost += carry_cost.financing_cost
                    realized_pnl -= carry_cost.total
                    total_costs += carry_cost.total
                    borrow_costs += carry_cost.borrow_cost
                    financing_costs += carry_cost.financing_cost
                    daily_borrow_cost += carry_cost.borrow_cost
                    daily_financing_cost += carry_cost.financing_cost
                gross_pnl = _mark_to_market(open_position, execution_a_price, execution_h_price)
                a_exit_cost = _execution_cost(
                    notional=open_position.a_shares * execution_a_price,
                    market="a",
                    side=-open_position.a_side,
                    shares=open_position.a_shares,
                    adv=a_adv,
                    costs=cost_config,
                )
                h_exit_cost = _execution_cost(
                    notional=open_position.h_shares * execution_h_price,
                    market="h",
                    side=-open_position.h_side,
                    shares=open_position.h_shares,
                    adv=h_adv,
                    costs=cost_config,
                )
                exit_transaction_cost = a_exit_cost.transaction_cost + h_exit_cost.transaction_cost
                exit_slippage_cost = a_exit_cost.slippage_cost + h_exit_cost.slippage_cost
                exit_cost = a_exit_cost.total + h_exit_cost.total
                realized_pnl += gross_pnl - exit_cost
                total_costs += exit_cost
                transaction_costs += exit_transaction_cost
                slippage_costs += exit_slippage_cost
                daily_transaction_cost += exit_transaction_cost
                daily_slippage_cost += exit_slippage_cost
                trade_rows.append(
                    _close_trade_record(
                        position=open_position,
                        exit_date=date,
                        exit_signal_value=float(decision_signal_value) if pd.notna(decision_signal_value) else np.nan,
                        gross_pnl=float(gross_pnl),
                        exit_transaction_cost=float(exit_transaction_cost),
                        exit_slippage_cost=float(exit_slippage_cost),
                        holding_days=holding_days,
                        exit_reason=exit_reason,
                        entry_signal_mode=strategy_config.entry_signal_mode,
                    )
                )
                open_position = None
                closed_this_bar = True

        available_capital = strategy_config.initial_capital + realized_pnl
        if (
            open_position is None
            and not closed_this_bar
            and (not close_on_end or row_number != last_row_index)
            and decision_row is not None
            and pd.notna(decision_signal_value)
            and abs(decision_signal_value) >= min(strategy_config.entry_z_candidates)
            and decision_gate_allows_trading
            and _passes_return_filter(decision_row, strategy_config)
        ):
            candidate = _build_position(
                date=date,
                row_number=row_number,
                signal_value=float(decision_signal_value),
                a_price=execution_a_price,
                h_price=execution_h_price,
                a_adv=a_adv,
                h_adv=h_adv,
                capital=available_capital,
                hedge_ratio=decision_hedge_ratio,
                strategy_config=strategy_config,
                cost_config=cost_config,
            )
            if candidate is not None:
                open_position = candidate
                realized_pnl -= candidate.entry_cost
                total_costs += candidate.entry_cost
                transaction_costs += candidate.entry_transaction_cost
                slippage_costs += candidate.entry_slippage_cost
                daily_transaction_cost += candidate.entry_transaction_cost
                daily_slippage_cost += candidate.entry_slippage_cost
                available_capital -= candidate.entry_cost

        if open_position is not None:
            carry_cost = _daily_carry_cost(open_position, a_close, h_close, cost_config)
            open_position.accrued_borrow_cost += carry_cost.borrow_cost
            open_position.accrued_financing_cost += carry_cost.financing_cost
            realized_pnl -= carry_cost.total
            total_costs += carry_cost.total
            borrow_costs += carry_cost.borrow_cost
            financing_costs += carry_cost.financing_cost
            daily_borrow_cost += carry_cost.borrow_cost
            daily_financing_cost += carry_cost.financing_cost
            unrealized_pnl = _mark_to_market(open_position, a_close, h_close)
        else:
            unrealized_pnl = 0.0

        if open_position is not None and close_on_end and row_number == last_row_index:
            gross_pnl = unrealized_pnl
            a_exit_cost = _execution_cost(
                notional=open_position.a_shares * a_close,
                market="a",
                side=-open_position.a_side,
                shares=open_position.a_shares,
                adv=a_adv,
                costs=cost_config,
            )
            h_exit_cost = _execution_cost(
                notional=open_position.h_shares * h_close,
                market="h",
                side=-open_position.h_side,
                shares=open_position.h_shares,
                adv=h_adv,
                costs=cost_config,
            )
            exit_transaction_cost = a_exit_cost.transaction_cost + h_exit_cost.transaction_cost
            exit_slippage_cost = a_exit_cost.slippage_cost + h_exit_cost.slippage_cost
            exit_cost = a_exit_cost.total + h_exit_cost.total
            realized_pnl += gross_pnl - exit_cost
            total_costs += exit_cost
            transaction_costs += exit_transaction_cost
            slippage_costs += exit_slippage_cost
            daily_transaction_cost += exit_transaction_cost
            daily_slippage_cost += exit_slippage_cost
            trade_rows.append(
                _close_trade_record(
                    position=open_position,
                    exit_date=date,
                    exit_signal_value=float(signal_value) if pd.notna(signal_value) else np.nan,
                    gross_pnl=float(gross_pnl),
                    exit_transaction_cost=float(exit_transaction_cost),
                    exit_slippage_cost=float(exit_slippage_cost),
                    holding_days=row_number - open_position.entry_row_number,
                    exit_reason="end_of_sample",
                    entry_signal_mode=strategy_config.entry_signal_mode,
                )
            )
            open_position = None
            unrealized_pnl = 0.0

        capital = strategy_config.initial_capital + realized_pnl + unrealized_pnl
        equity_rows.append(
            {
                "date": date,
                "capital": capital,
                "spread": spread,
                "zscore": raw_zscore,
                "signal_score": signal_value,
                "intercept": row_intercept,
                "hedge_ratio": row_hedge_ratio,
                "cheap_leg": cheap_leg,
                "return_signal": return_signal,
                "return_filter_pass": return_filter_pass,
                "cointegration_p_value": cointegration_p_value,
                "cointegration_significant": cointegration_significant,
                "cointegration_gate_pass": cointegration_gate_pass,
                "ecm_speed": ecm_speed,
                "ecm_p_value": ecm_p_value,
                "ecm_gate_pass": ecm_gate_pass,
                "a_adv": a_adv,
                "h_adv": h_adv,
                "daily_transaction_cost": daily_transaction_cost,
                "daily_slippage_cost": daily_slippage_cost,
                "daily_borrow_cost": daily_borrow_cost,
                "daily_financing_cost": daily_financing_cost,
                "position": "flat" if open_position is None else open_position.direction,
                "gross_exposure": 0.0 if open_position is None else _gross_exposure(open_position, a_close, h_close),
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
            "entry_signal_mode",
            "entry_signal_value",
            "exit_signal_value",
            "entry_zscore",
            "exit_zscore",
            "gross_pnl",
            "entry_transaction_cost",
            "entry_slippage_cost",
            "entry_cost",
            "exit_transaction_cost",
            "exit_slippage_cost",
            "exit_cost",
            "borrow_cost",
            "financing_cost",
            "net_pnl",
            "holding_days",
            "exit_reason",
        ],
    )

    final_capital = float(equity_curve["capital"].iloc[-1])
    total_return = final_capital / strategy_config.initial_capital - 1.0
    annual_return = annualize_total_return(total_return, len(equity_curve))
    annual_volatility = compute_annualized_volatility(equity_curve["returns"])
    sharpe_ratio = compute_sharpe_ratio(equity_curve["returns"])
    sortino_ratio = compute_sortino_ratio(equity_curve["returns"])
    drawdown_stats = compute_drawdown_stats(equity_curve["capital"])
    calmar_ratio = None
    if drawdown_stats.max_drawdown < 0:
        calmar_ratio = float(annual_return / abs(drawdown_stats.max_drawdown))

    win_rate = 0.0
    avg_holding_days = 0.0
    payoff_ratio: float | None = None
    profit_factor: float | None = None
    avg_trade_pnl: float | None = None
    avg_win_pnl: float | None = None
    avg_loss_pnl: float | None = None
    gross_pnl = 0.0
    cost_to_gross_pnl: float | None = None
    if not trade_frame.empty:
        win_rate = float((trade_frame["net_pnl"] > 0).mean())
        avg_holding_days = float(trade_frame["holding_days"].mean())
        avg_trade_pnl = float(trade_frame["net_pnl"].mean())
        gross_pnl = float(trade_frame["gross_pnl"].sum())

        winning_trades = trade_frame.loc[trade_frame["net_pnl"] > 0, "net_pnl"]
        losing_trades = trade_frame.loc[trade_frame["net_pnl"] < 0, "net_pnl"]
        if not winning_trades.empty:
            avg_win_pnl = float(winning_trades.mean())
        if not losing_trades.empty:
            avg_loss_pnl = float(losing_trades.mean())
        if avg_win_pnl is not None and avg_loss_pnl is not None and avg_loss_pnl != 0:
            payoff_ratio = float(avg_win_pnl / abs(avg_loss_pnl))
        if not winning_trades.empty and not losing_trades.empty:
            gross_wins = float(winning_trades.sum())
            gross_losses = float(losing_trades.sum())
            if gross_losses != 0:
                profit_factor = float(gross_wins / abs(gross_losses))
        if gross_pnl != 0:
            cost_to_gross_pnl = float(total_costs / abs(gross_pnl))

    time_in_market = float((equity_curve["position"] != "flat").mean())
    net_pnl = float(final_capital - strategy_config.initial_capital)

    summary = BacktestSummary(
        final_capital=final_capital,
        total_return=float(total_return),
        annual_return=annual_return,
        annual_volatility=annual_volatility,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        max_drawdown=drawdown_stats.max_drawdown,
        calmar_ratio=calmar_ratio,
        max_drawdown_duration=drawdown_stats.max_drawdown_duration,
        recovery_days=drawdown_stats.recovery_days,
        trade_count=int(len(trade_frame)),
        win_rate=win_rate,
        payoff_ratio=payoff_ratio,
        profit_factor=profit_factor,
        avg_trade_pnl=avg_trade_pnl,
        avg_holding_days=avg_holding_days,
        avg_win_pnl=avg_win_pnl,
        avg_loss_pnl=avg_loss_pnl,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        total_costs=float(total_costs),
        transaction_costs=float(transaction_costs),
        slippage_costs=float(slippage_costs),
        borrow_costs=float(borrow_costs),
        financing_costs=float(financing_costs),
        cost_to_gross_pnl=cost_to_gross_pnl,
        time_in_market=time_in_market,
        max_consecutive_losses=compute_max_consecutive_losses(trade_frame),
        monthly_win_rate=compute_monthly_win_rate(equity_curve["returns"]),
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
        candidate_config = replace(strategy_config, entry_z_candidates=(entry_z,))
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
