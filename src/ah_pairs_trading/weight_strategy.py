"""Weight-based spread-arbitrage backtest engine.

Ported from ``wu-pairs-spread-arbitrage`` (archived) and adapted to the A/H
relative-value workflow.  This engine mirrors the original design:

- Continuous z-score tilt weights between the two legs with entry/exit/stop
  thresholds and a mean-reversion quality filter (half-life range and/or
  Ljung-Box p-value on the OU innovations).
- T+1 execution of target weights.
- Volatility-targeted position sizing (risk budget) with a leverage cap.
- A turnover-based cost model plus single-pair and portfolio drawdown circuit
  breakers.
- Buy-and-hold, constant-mix, and volatility-targeted constant-mix benchmarks.

The engine consumes the rolling OU diagnostics produced by :mod:`.ou`, so the
signal frame must carry the ``ou_*`` columns.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from .config import CostConfig, StrategyConfig
from .metrics import TRADING_DAYS_PER_YEAR
from .strategy import BacktestResult, build_backtest_summary

_REQUIRED_OU_COLUMNS = ("ou_spread", "ou_L", "ou_a", "ou_k", "ou_half_life_days", "ou_lb_pvalue")

_POSITION_LABELS = {
    1: "short_a_long_h",
    -1: "long_a_short_h",
    0: "flat",
}


def _price_returns(price_frame: pd.DataFrame, a_symbol: str, h_symbol: str) -> pd.DataFrame:
    result = price_frame[[a_symbol, h_symbol]].copy()
    result["ret_a"] = result[a_symbol].pct_change().fillna(0.0)
    result["ret_h"] = result[h_symbol].pct_change().fillna(0.0)
    return result


def make_dynamic_weight_signals(
    signal_frame: pd.DataFrame,
    *,
    entry_z: float,
    exit_z: float,
    stop_z: float,
    hl_min: float,
    hl_max: float,
    lb_p_min: float,
    min_weight: float,
) -> pd.DataFrame:
    """Generate continuous tilt weights from the OU diagnostics.

    The dependent leg (``a_symbol``) is cheap when the OU z-score is negative,
    so a more negative z-score increases its target weight.  Rows outside the
    half-life range, failing the Ljung-Box filter, or inside the exit band
    return to the neutral 50/50 split; rows beyond the stop threshold are also
    neutralized.
    """

    spread = signal_frame["ou_spread"].to_numpy(dtype=float)
    k = signal_frame["ou_k"].to_numpy(dtype=float)
    L = signal_frame["ou_L"].to_numpy(dtype=float)
    a = signal_frame["ou_a"].to_numpy(dtype=float)
    half_life = signal_frame["ou_half_life_days"].to_numpy(dtype=float)
    lb_pvalue = signal_frame["ou_lb_pvalue"].to_numpy(dtype=float)

    stationary_std = a / np.sqrt(2 * k)
    z = (spread - L) / stationary_std

    tradeable = (half_life >= hl_min) & (half_life <= hl_max) & (lb_pvalue >= lb_p_min)

    min_weight = float(np.clip(min_weight, 0.0, 0.5))
    max_tilt = 0.5 - min_weight
    denom = max(float(entry_z), 1e-6)
    scaled = np.clip(z / denom, -1.0, 1.0)
    weight_a_target = 0.5 - max_tilt * scaled
    weight_h_target = 1.0 - weight_a_target

    neutral = (~tradeable) | (~np.isfinite(z)) | (np.abs(z) < exit_z) | (np.abs(z) > stop_z)
    weight_a_target = np.where(neutral, 0.5, weight_a_target)
    weight_h_target = np.where(neutral, 0.5, weight_h_target)
    signal = np.where(weight_a_target < 0.5, 1, np.where(weight_a_target > 0.5, -1, 0))

    return pd.DataFrame(
        {
            "z": z,
            "tradeable": tradeable.astype(int),
            "signal": signal,
            "weight_a_target": weight_a_target,
            "weight_h_target": weight_h_target,
        },
        index=signal_frame.index,
    )


def vol_target_position_sizing(
    price_returns: pd.DataFrame,
    signal_frame: pd.DataFrame,
    *,
    vol_window: int,
    vol_min_periods: int | None,
    target_vol: float,
    max_leverage: float,
) -> pd.DataFrame:
    """Compute executed weights and the leverage multiplier.

    Target weights are executed on the next bar (T+1).  The leverage ``h`` is
    the volatility target divided by the trailing annualized volatility of the
    un-levered 50/50 portfolio, capped at ``max_leverage``.
    """

    out = pd.DataFrame(index=signal_frame.index)
    target_a = signal_frame["weight_a_target"].to_numpy(dtype=float)
    target_h = signal_frame["weight_h_target"].to_numpy(dtype=float)
    n = len(signal_frame)

    weight_a = np.zeros(n)
    weight_h = np.zeros(n)
    weight_a[0] = 0.5
    weight_h[0] = 0.5
    weight_a[1:] = target_a[:-1]
    weight_h[1:] = target_h[:-1]

    port_ret_unlev = weight_a * price_returns["ret_a"].to_numpy(dtype=float) + weight_h * price_returns["ret_h"].to_numpy(dtype=float)
    window = max(int(vol_window), 1)
    minimum_periods = vol_min_periods or window
    vol = (
        pd.Series(port_ret_unlev).rolling(window, min_periods=minimum_periods).std().to_numpy() * np.sqrt(TRADING_DAYS_PER_YEAR)
    )

    h = np.where((vol > 1e-12) & np.isfinite(vol), target_vol / vol, 0.0)
    h = np.clip(h, 0.0, max_leverage)

    out["port_vol_ann"] = vol
    out["port_ret_unlev"] = port_ret_unlev
    out["h"] = h
    out["weight_a_exec"] = weight_a
    out["weight_h_exec"] = weight_h
    out["gross_exposure"] = np.abs(h * weight_a) + np.abs(h * weight_h)
    return out


def run_dynamic_weight_backtest(
    price_returns: pd.DataFrame,
    signal_frame: pd.DataFrame,
    pos_frame: pd.DataFrame,
    *,
    cost_config: CostConfig,
    risk_free_rate: float,
    budget_fraction: float,
    max_dd: float,
    suspend_days: int,
    port_max_dd: float,
) -> pd.DataFrame:
    """Run the weight-based backtest with costs and circuit breakers.

    Mirrors the original rolling-weight backtest: daily turnover-based cost,
    a single-pair drawdown breaker that suspends trading for ``suspend_days``,
    and a portfolio-level breaker that flattens permanently.
    """

    n = len(price_returns)
    out = pd.DataFrame(index=signal_frame.index)
    dt = 1.0 / TRADING_DAYS_PER_YEAR

    ret_a = price_returns["ret_a"].to_numpy(dtype=float)
    ret_h = price_returns["ret_h"].to_numpy(dtype=float)
    weight_a = pos_frame["weight_a_exec"].to_numpy(dtype=float).copy()
    weight_h = pos_frame["weight_h_exec"].to_numpy(dtype=float).copy()
    h = pos_frame["h"].to_numpy(dtype=float).copy()

    base_cost = float(cost_config.base_cost_bps) / 10_000.0
    impact_cost = float(cost_config.impact_cost_bps) / 10_000.0

    pos_a = budget_fraction * h * weight_a
    pos_h = budget_fraction * h * weight_h

    turnover = np.zeros(n)
    cost = np.zeros(n)
    ret = np.zeros(n)
    nav = np.ones(n)
    port_dd = np.zeros(n)

    peak = 1.0
    suspend_left = 0
    port_cut = False

    for t in range(1, n):
        if port_cut:
            weight_a[t] = 0.5
            weight_h[t] = 0.5
            h[t] = min(h[t], 1.0)
            pos_a[t] = budget_fraction * h[t] * weight_a[t]
            pos_h[t] = budget_fraction * h[t] * weight_h[t]
        elif suspend_left > 0:
            weight_a[t] = 0.5
            weight_h[t] = 0.5
            h[t] = min(h[t], 1.0)
            pos_a[t] = budget_fraction * h[t] * weight_a[t]
            pos_h[t] = budget_fraction * h[t] * weight_h[t]
            suspend_left -= 1

        turnover[t] = abs(pos_a[t] - pos_a[t - 1]) + abs(pos_h[t] - pos_h[t - 1])
        gross = abs(pos_a[t]) + abs(pos_h[t])
        cost[t] = base_cost * turnover[t] + impact_cost * turnover[t] * gross

        ret[t] = pos_a[t] * ret_a[t] + pos_h[t] * ret_h[t] + risk_free_rate * dt - cost[t]
        nav[t] = nav[t - 1] * (1.0 + ret[t])

        peak = max(peak, nav[t])
        port_dd[t] = nav[t] / peak - 1.0

        if port_dd[t] <= -abs(max_dd) and suspend_left == 0:
            suspend_left = max(int(suspend_days), 0)
        if port_dd[t] <= -abs(port_max_dd):
            port_cut = True

    out["turnover"] = turnover
    out["cost"] = cost
    out["pos_a"] = pos_a
    out["pos_h"] = pos_h
    out["h"] = h
    out["gross_exposure"] = np.abs(pos_a) + np.abs(pos_h)
    out["ret"] = ret
    out["nav"] = nav
    out["drawdown"] = port_dd
    return out


def _compute_drawdown(nav: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(nav)
    return np.where(peak > 0, nav / peak - 1.0, 0.0)


def run_buy_hold_benchmark(
    price_returns: pd.DataFrame,
    *,
    weight_a: float,
    weight_h: float,
    risk_free_rate: float,
) -> pd.DataFrame:
    """Buy-and-hold benchmark: weights drift with prices after the first bar."""

    total = float(weight_a) + float(weight_h)
    if total <= 0:
        raise ValueError("Benchmark weights must sum to a positive value.")
    weight_a = weight_a / total
    weight_h = weight_h / total

    ret_a = price_returns["ret_a"].to_numpy(dtype=float)
    ret_h = price_returns["ret_h"].to_numpy(dtype=float)
    n = len(price_returns)
    dt = 1.0 / TRADING_DAYS_PER_YEAR

    ret = np.zeros(n)
    nav = np.ones(n)
    wa = np.zeros(n)
    wh = np.zeros(n)
    wa[0] = weight_a
    wh[0] = weight_h

    for t in range(1, n):
        ret[t] = wa[t - 1] * ret_a[t] + wh[t - 1] * ret_h[t] + risk_free_rate * dt
        nav[t] = nav[t - 1] * (1.0 + ret[t])
        denom = 1.0 + wa[t - 1] * ret_a[t] + wh[t - 1] * ret_h[t]
        if denom > 1e-12:
            wa[t] = wa[t - 1] * (1.0 + ret_a[t]) / denom
            wh[t] = wh[t - 1] * (1.0 + ret_h[t]) / denom
        else:
            wa[t] = wa[t - 1]
            wh[t] = wh[t - 1]

    return pd.DataFrame(
        {
            "ret": ret,
            "nav": nav,
            "drawdown": _compute_drawdown(nav),
            "weight_a": wa,
            "weight_h": wh,
        },
        index=price_returns.index,
    )


def run_constant_mix_benchmark(
    price_returns: pd.DataFrame,
    *,
    weight_a: float,
    weight_h: float,
    cost_config: CostConfig,
    risk_free_rate: float,
    vol_window: int | None = None,
    target_vol: float | None = None,
    max_leverage: float = 1.0,
) -> pd.DataFrame:
    """Constant-mix benchmark with optional volatility targeting."""

    total = float(weight_a) + float(weight_h)
    if total <= 0:
        raise ValueError("Benchmark weights must sum to a positive value.")
    weight_a = weight_a / total
    weight_h = weight_h / total

    ret_a = price_returns["ret_a"].to_numpy(dtype=float)
    ret_h = price_returns["ret_h"].to_numpy(dtype=float)
    n = len(price_returns)
    dt = 1.0 / TRADING_DAYS_PER_YEAR
    base_cost = float(cost_config.base_cost_bps) / 10_000.0
    impact_cost = float(cost_config.impact_cost_bps) / 10_000.0

    port_ret_unlev = weight_a * ret_a + weight_h * ret_h
    if target_vol is None:
        vol = np.full(n, np.nan)
        h = np.ones(n)
    else:
        if not vol_window or vol_window <= 0:
            raise ValueError("vol_window must be positive when target_vol is set")
        vol = pd.Series(port_ret_unlev).rolling(vol_window).std().to_numpy() * np.sqrt(TRADING_DAYS_PER_YEAR)
        h = np.where((vol > 1e-12) & np.isfinite(vol), target_vol / vol, 0.0)
        h = np.clip(h, 0.0, max_leverage)

    ret = np.zeros(n)
    nav = np.ones(n)
    turnover = np.zeros(n)
    cost = np.zeros(n)
    pos_a = np.zeros(n)
    pos_h = np.zeros(n)
    pos_a[0] = h[0] * weight_a
    pos_h[0] = h[0] * weight_h

    for t in range(1, n):
        denom = 1.0 + weight_a * ret_a[t - 1] + weight_h * ret_h[t - 1]
        if denom > 1e-12:
            wa_drift = weight_a * (1.0 + ret_a[t - 1]) / denom
            wh_drift = weight_h * (1.0 + ret_h[t - 1]) / denom
        else:
            wa_drift = weight_a
            wh_drift = weight_h

        pos_a_drift = h[t - 1] * wa_drift
        pos_h_drift = h[t - 1] * wh_drift
        pos_a[t] = h[t] * weight_a
        pos_h[t] = h[t] * weight_h

        turnover[t] = abs(pos_a[t] - pos_a_drift) + abs(pos_h[t] - pos_h_drift)
        gross = abs(pos_a[t]) + abs(pos_h[t])
        cost[t] = base_cost * turnover[t] + impact_cost * turnover[t] * gross

        ret[t] = pos_a[t] * ret_a[t] + pos_h[t] * ret_h[t] + risk_free_rate * dt - cost[t]
        nav[t] = nav[t - 1] * (1.0 + ret[t])

    return pd.DataFrame(
        {
            "ret": ret,
            "nav": nav,
            "drawdown": _compute_drawdown(nav),
            "turnover": turnover,
            "cost": cost,
            "pos_a": pos_a,
            "pos_h": pos_h,
            "h": h,
            "gross_exposure": np.abs(pos_a) + np.abs(pos_h),
            "port_vol_ann": vol,
            "port_ret_unlev": port_ret_unlev,
        },
        index=price_returns.index,
    )


def _trades_from_position_runs(equity_curve: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    """Derive discrete trade records from contiguous position runs."""

    trade_rows: list[dict[str, Any]] = []
    position = equity_curve["position"]
    zscore = equity_curve["zscore"]
    capital = equity_curve["capital"]

    run_start_index: int | None = None
    run_position: str | None = None

    def close_run(exit_index: int, exit_reason: str) -> None:
        nonlocal run_start_index, run_position
        if run_start_index is None or run_position is None:
            return
        entry_index = run_start_index
        entry_date = equity_curve.index[entry_index]
        exit_date = equity_curve.index[exit_index]
        entry_signal = float(zscore.iloc[entry_index]) if pd.notna(zscore.iloc[entry_index]) else np.nan
        exit_signal = float(zscore.iloc[exit_index]) if pd.notna(zscore.iloc[exit_index]) else np.nan
        gross_pnl = float(capital.iloc[exit_index] - capital.iloc[entry_index])
        trade_rows.append(
            {
                "entry_date": entry_date,
                "exit_date": exit_date,
                "direction": run_position,
                "entry_zscore": entry_signal,
                "exit_zscore": exit_signal,
                "entry_signal_value": entry_signal,
                "exit_signal_value": exit_signal,
                "gross_pnl": gross_pnl,
                "net_pnl": gross_pnl,
                "entry_cost": 0.0,
                "exit_cost": 0.0,
                "borrow_cost": 0.0,
                "financing_cost": 0.0,
                "transaction_costs": 0.0,
                "slippage_costs": 0.0,
                "holding_days": int(exit_index - entry_index),
                "exit_reason": exit_reason,
            }
        )
        run_start_index = None
        run_position = None

    last_index = len(equity_curve) - 1
    for i, pos in enumerate(position):
        if pos != "flat":
            if run_position is None:
                run_start_index = i
                run_position = str(pos)
            elif pos != run_position:
                close_run(i - 1, "direction_change")
                run_start_index = i
                run_position = str(pos)
        else:
            if run_position is not None:
                close_run(i - 1, "direction_change")
        if i == last_index and run_position is not None:
            close_run(i, "end_of_sample")

    return pd.DataFrame(
        trade_rows,
        columns=[
            "entry_date",
            "exit_date",
            "direction",
            "entry_zscore",
            "exit_zscore",
            "entry_signal_value",
            "exit_signal_value",
            "gross_pnl",
            "net_pnl",
            "entry_cost",
            "exit_cost",
            "borrow_cost",
            "financing_cost",
            "transaction_costs",
            "slippage_costs",
            "holding_days",
            "exit_reason",
        ],
    )


def backtest_spread_arbitrage_strategy(
    signal_frame: pd.DataFrame,
    a_symbol: str,
    h_symbol: str,
    strategy_config: StrategyConfig,
    cost_config: CostConfig,
    close_on_end: bool = True,
    hedge_ratio: float | None = None,
) -> BacktestResult:
    """Run the weight-based spread-arbitrage backtest on a prepared signal frame.

    ``hedge_ratio`` is accepted for API compatibility with the trade engine but
    ignored here, since the rolling OU estimator supplies its own beta.
    """

    if signal_frame.empty:
        raise ValueError("The signal frame is empty.")
    if not strategy_config.entry_z_candidates:
        raise ValueError("At least one entry z-score candidate is required.")

    missing = sorted(set(_REQUIRED_OU_COLUMNS) - set(signal_frame.columns))
    if missing:
        raise ValueError(
            f"The signal frame is missing OU diagnostics required by the weight engine: {missing}. "
            "Run the pipeline with the rolling OU estimator enabled."
        )
    for column in (a_symbol, h_symbol):
        if column not in signal_frame.columns:
            raise ValueError(f"The signal frame is missing the price column '{column}'.")

    price_returns = _price_returns(signal_frame, a_symbol, h_symbol)

    signals = make_dynamic_weight_signals(
        signal_frame,
        entry_z=float(strategy_config.entry_z_candidates[0]),
        exit_z=strategy_config.exit_z,
        stop_z=strategy_config.stop_z,
        hl_min=strategy_config.half_life_min_days,
        hl_max=strategy_config.half_life_max_days,
        lb_p_min=strategy_config.lb_p_value_min,
        min_weight=strategy_config.min_weight,
    )

    pos_frame = vol_target_position_sizing(
        price_returns,
        signals,
        vol_window=strategy_config.vol_window,
        vol_min_periods=strategy_config.vol_min_periods,
        target_vol=strategy_config.target_vol,
        max_leverage=strategy_config.max_leverage,
    )

    backtest = run_dynamic_weight_backtest(
        price_returns,
        signals,
        pos_frame,
        cost_config=cost_config,
        risk_free_rate=strategy_config.risk_free_rate,
        budget_fraction=strategy_config.position_size_fraction,
        max_dd=strategy_config.max_drawdown,
        suspend_days=strategy_config.suspend_days,
        port_max_dd=strategy_config.portfolio_max_drawdown,
    )

    position_labels = [_POSITION_LABELS.get(int(signal), "flat") for signal in signals["signal"]]
    equity_curve = pd.DataFrame(
        {
            "capital": strategy_config.initial_capital * backtest["nav"],
            "returns": backtest["ret"],
            "spread": signal_frame["ou_spread"],
            "zscore": signals["z"],
            "position": position_labels,
            "weight_a": pos_frame["weight_a_exec"],
            "weight_h": pos_frame["weight_h_exec"],
            "h": pos_frame["h"],
            "gross_exposure": backtest["gross_exposure"],
            "turnover": backtest["turnover"],
            "cost": backtest["cost"],
            "drawdown": backtest["drawdown"],
        },
        index=signal_frame.index,
    )

    trade_frame = _trades_from_position_runs(equity_curve, strategy_config.initial_capital)
    summary = build_backtest_summary(
        equity_curve,
        trade_frame,
        strategy_config.initial_capital,
        total_costs=float(backtest["cost"].sum()),
    )
    return BacktestResult(equity_curve=equity_curve, trades=trade_frame, summary=summary)


def run_weight_benchmarks(
    signal_frame: pd.DataFrame,
    a_symbol: str,
    h_symbol: str,
    strategy_config: StrategyConfig,
    cost_config: CostConfig,
) -> dict[str, pd.DataFrame]:
    """Run the buy-and-hold and constant-mix benchmarks used for comparison."""

    price_returns = _price_returns(signal_frame, a_symbol, h_symbol)
    risk_free_rate = strategy_config.risk_free_rate
    result: dict[str, pd.DataFrame] = {}

    bm_basic_cols = ["ret", "nav", "drawdown"]
    result["bm_hold_5050"] = run_buy_hold_benchmark(
        price_returns,
        weight_a=0.5,
        weight_h=0.5,
        risk_free_rate=risk_free_rate,
    )[bm_basic_cols]
    result["bm_mix_5050"] = run_constant_mix_benchmark(
        price_returns,
        weight_a=0.5,
        weight_h=0.5,
        cost_config=cost_config,
        risk_free_rate=risk_free_rate,
    )
    result["bm_mix_5050_risk"] = run_constant_mix_benchmark(
        price_returns,
        weight_a=0.5,
        weight_h=0.5,
        cost_config=cost_config,
        risk_free_rate=risk_free_rate,
        vol_window=strategy_config.vol_window,
        target_vol=strategy_config.target_vol,
        max_leverage=strategy_config.max_leverage,
    )
    result["bm_hold_a"] = run_buy_hold_benchmark(
        price_returns,
        weight_a=1.0,
        weight_h=0.0,
        risk_free_rate=risk_free_rate,
    )[bm_basic_cols]
    result["bm_hold_h"] = run_buy_hold_benchmark(
        price_returns,
        weight_a=0.0,
        weight_h=1.0,
        risk_free_rate=risk_free_rate,
    )[bm_basic_cols]
    return result


def grid_search_entry_z_weight(
    signal_frame: pd.DataFrame,
    a_symbol: str,
    h_symbol: str,
    strategy_config: StrategyConfig,
    cost_config: CostConfig,
    hedge_ratio: float | None = None,
) -> tuple[float, pd.DataFrame]:
    """Run the weight engine over several entry thresholds and pick the best.

    ``hedge_ratio`` is accepted for API compatibility with the trade engine but
    ignored here.
    """

    rows: list[dict[str, float | int]] = []
    for entry_z in strategy_config.entry_z_candidates:
        candidate_config = replace(strategy_config, entry_z_candidates=(entry_z,))
        result = backtest_spread_arbitrage_strategy(
            signal_frame=signal_frame,
            a_symbol=a_symbol,
            h_symbol=h_symbol,
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
