"""Tests for the rolling OU MLE estimator ported from wu-pairs-spread-arbitrage."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ah_pairs_trading.config import DataConfig, PipelineConfig
from ah_pairs_trading.data import (
    build_ah_price_frame,
    prepare_log_price_frame,
    prepare_model_price_frame,
    simulate_pair_prices,
)
from ah_pairs_trading.ou import (
    mean_reversion_gate_pass_series,
    mle_ou,
    rolling_ou_mle_params,
)


def _make_ou_series(n_days: int = 800, k: float = 5.0, L: float = 0.0, a: float = 0.3, seed: int = 1) -> np.ndarray:
    dt = 1.0 / 252.0
    rng = np.random.default_rng(seed)
    phi = np.exp(-k * dt)
    sdS = a * np.sqrt((1 - np.exp(-2 * k * dt)) / (2 * k))
    series = np.empty(n_days)
    series[0] = 0.0
    for t in range(1, n_days):
        series[t] = L + (series[t - 1] - L) * phi + sdS * rng.standard_normal()
    return series


def _simulated_log_prices() -> tuple[pd.DataFrame, pd.DataFrame]:
    a_frame, h_frame = simulate_pair_prices("2018-01-01", "2020-12-31", seed=1, beta=1.1)
    fx = pd.DataFrame({"fx_rate": 0.92}, index=a_frame.index)
    aligned = build_ah_price_frame(a_frame, h_frame, fx, "2018-01-01", "2020-12-31")
    model_prices = prepare_model_price_frame(aligned, "600036", "03968")
    return prepare_log_price_frame(model_prices[["600036", "03968"]]), model_prices


def test_mle_ou_recovers_parameters() -> None:
    series = _make_ou_series(k=5.0, L=0.0, a=0.3)
    k_hat, L_hat, a_hat, _ = mle_ou(series)
    assert abs(k_hat - 5.0) / 5.0 < 0.3
    assert abs(a_hat - 0.3) / 0.3 < 0.3
    assert abs(L_hat) < 0.15


def test_johansen_beta_recovers_simulated_beta() -> None:
    from ah_pairs_trading.ou import estimate_johansen_beta

    log_prices, _ = _simulated_log_prices()
    beta = estimate_johansen_beta(log_prices["600036"].to_numpy(), log_prices["03968"].to_numpy())
    assert abs(beta - 1.1) < 0.2


def test_rolling_ou_mle_params_produces_expected_columns() -> None:
    log_prices, _ = _simulated_log_prices()
    params = rolling_ou_mle_params(log_prices, "600036", "03968", window=100)
    expected = {
        "ou_beta",
        "ou_mu_a",
        "ou_sigma_a",
        "ou_k",
        "ou_L",
        "ou_a",
        "ou_rho",
        "ou_half_life_days",
        "ou_nll",
        "ou_lb_pvalue",
        "ou_spread",
    }
    assert set(params.columns) == expected
    assert params.index.equals(log_prices.index)
    assert params["ou_half_life_days"].dropna().shape[0] >= 100


def test_rolling_ou_params_match_simulated_regime() -> None:
    log_prices, _ = _simulated_log_prices()
    params = rolling_ou_mle_params(log_prices, "600036", "03968", window=100)
    clean = params.dropna()
    assert abs(clean["ou_beta"].mean() - 1.1) < 0.4
    assert clean["ou_half_life_days"].mean() > 0.0


def test_mean_reversion_gate_pass_series_modes() -> None:
    index = pd.date_range("2024-01-01", periods=4, freq="B")
    frame = pd.DataFrame(
        {
            "ou_half_life_days": [3.0, 10.0, 120.0, np.nan],
            "ou_lb_pvalue": [0.9, 0.01, 0.5, np.nan],
            "ou_k": [1.0, 1.0, 1.0, 1.0],
        },
        index=index,
    )

    off = mean_reversion_gate_pass_series(frame, mode="off", half_life_min_days=5, half_life_max_days=90, lb_p_value_min=0.05)
    assert (off == True).all()

    hl = mean_reversion_gate_pass_series(
        frame, mode="half_life_range", half_life_min_days=5, half_life_max_days=90, lb_p_value_min=0.05
    )
    assert list(hl) == [False, True, False, False]

    lb = mean_reversion_gate_pass_series(frame, mode="lb_filter", half_life_min_days=5, half_life_max_days=90, lb_p_value_min=0.05)
    assert list(lb) == [True, False, True, False]

    both = mean_reversion_gate_pass_series(frame, mode="both", half_life_min_days=5, half_life_max_days=90, lb_p_value_min=0.05)
    assert list(both) == [False, False, False, False]


def test_simulated_provider_needs_no_network() -> None:
    config = PipelineConfig(
        a_symbol="600036",
        h_symbol="03968",
        start_date="2018-01-01",
        end_date="2020-12-31",
        data=DataConfig(data_provider="simulated", constant_fx_rate=0.92),
    )
    from ah_pairs_trading.data import load_ah_pair_data

    loaded = load_ah_pair_data(config)
    assert not loaded.model_prices.empty
    assert {"600036", "03968"} <= set(loaded.model_prices.columns)
    assert loaded.model_prices.index.is_monotonic_increasing
