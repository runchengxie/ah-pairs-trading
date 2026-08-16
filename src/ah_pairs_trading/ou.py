"""Rolling MLE estimation for mean-reverting spreads.

Ported from ``wu-pairs-spread-arbitrage`` (archived) and adapted to the A/H
relative-value workflow.  The estimation pipeline mirrors the original
"daily rolling MLE" design:

1.  Johansen estimates the cointegration beta each window.
2.  ``S = ln(dependent) - beta * ln(independent)`` follows an OU process.
3.  OU and GBM parameters are fitted by exact discrete conditional MLE.
4.  The correlation rho between the standardized innovations is estimated.
5.  A Ljung-Box test is applied to the standardized OU innovations.

The resulting per-day parameter frame feeds both the weight-based backtest
engine and the mean-reversion quality gate used by the trade-based engine.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.vector_ar.vecm import coint_johansen

from .metrics import TRADING_DAYS_PER_YEAR

OU_COLUMNS = (
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
)

_INVALID_OU_PARAMETERS = 1e50


def ou_nll(params: tuple[float, float, float], S: np.ndarray, dt: float) -> float:
    """Negative log-likelihood of an OU process under exact discretization.

    The OU dynamics are ``dS = k(L - S)dt + a dW`` with exact transition

    ``S_{t+1} | S_t ~ N(L + (S_t - L)e^{-k dt},  a^2(1 - e^{-2k dt}) / (2k))``.
    """

    k, L, a = params
    if k <= 0 or a <= 0:
        return _INVALID_OU_PARAMETERS
    phi = np.exp(-k * dt)
    var = (a * a) * (1 - np.exp(-2 * k * dt)) / (2 * k)
    if var <= 0 or not np.isfinite(var):
        return _INVALID_OU_PARAMETERS
    S0, S1 = S[:-1], S[1:]
    mean = L + (S0 - L) * phi
    return float(0.5 * np.sum(np.log(2 * np.pi * var) + (S1 - mean) ** 2 / var))


def mle_ou(S: np.ndarray, dt: float = 1.0 / TRADING_DAYS_PER_YEAR) -> tuple[float, float, float, float]:
    """Fit the OU parameters ``(k, L, a)`` by numeric maximum likelihood.

    Initial values use the AR(1) approximation.  Returns ``(k, L, a, nll)``.
    """

    S0, S1 = S[:-1], S[1:]
    if len(S0) < 2:
        return float("nan"), float("nan"), float("nan"), float("nan")

    X = np.column_stack([np.ones_like(S0), S0])
    alpha, phi = np.linalg.lstsq(X, S1, rcond=None)[0]
    phi = float(np.clip(phi, 1e-8, 1 - 1e-8))
    k0 = -np.log(phi) / dt
    L0 = float(alpha / (1 - phi))
    eps = S1 - (alpha + phi * S0)
    sigma_eps = float(np.sqrt(np.mean(eps**2)))
    a0 = sigma_eps * np.sqrt(2 * k0 / (1 - np.exp(-2 * k0 * dt)))

    x0 = np.array([k0, L0, a0], dtype=float)
    bounds = [(1e-8, None), (None, None), (1e-8, None)]
    res = minimize(lambda params: ou_nll(params, S, dt), x0=x0, method="L-BFGS-B", bounds=bounds)

    k, L, a = res.x
    return float(k), float(L), float(a), float(res.fun)


def mle_gbm_from_lnA(lnA: np.ndarray, dt: float = 1.0 / TRADING_DAYS_PER_YEAR) -> tuple[float, float]:
    """Fit GBM parameters from a log-price series.

    ``d lnA = (mu - 1/2 sigma^2)dt + sigma dZ`` gives
    ``sigma = sqrt(Var(d lnA)/dt)`` and ``mu = Mean(d lnA)/dt + 1/2 sigma^2``.
    Returns ``(mu, sigma)``.
    """

    dlnA = np.diff(lnA)
    if len(dlnA) < 2:
        return float("nan"), float("nan")
    m = np.mean(dlnA)
    v = np.mean((dlnA - m) ** 2)
    sigma = np.sqrt(max(v / dt, 1e-18))
    mu = m / dt + 0.5 * sigma**2
    return float(mu), float(sigma)


def estimate_johansen_beta(
    ln_dependent: np.ndarray,
    ln_independent: np.ndarray,
    det_order: int = 0,
    k_ar_diff: int = 1,
) -> float:
    """Estimate the cointegration beta via Johansen.

    Returns ``beta`` such that ``S = ln(dependent) - beta * ln(independent)``,
    matching the A/H convention used elsewhere in the pipeline.
    """

    data = np.column_stack([ln_dependent, ln_independent])
    try:
        result = coint_johansen(data, det_order, k_ar_diff)
    except Exception:
        return float("nan")

    vec = result.evec[:, 0]
    if not np.all(np.isfinite(vec)) or abs(vec[0]) < 1e-12:
        return float("nan")
    if vec[0] < 0:
        vec = -vec
    beta = -vec[1] / vec[0]
    return float(beta)


def estimate_rho_from_innovations(
    ln_dependent: np.ndarray,
    S: np.ndarray,
    dt: float,
    mu_a: float,
    sigma_a: float,
    k: float,
    L: float,
    a: float,
) -> float:
    """Estimate the correlation between the GBM and OU standardized innovations."""

    if not all(np.isfinite(value) for value in (mu_a, sigma_a, k, L, a)) or sigma_a <= 0 or a <= 0:
        return float("nan")

    dlnA = np.diff(ln_dependent)
    drift_lnA = (mu_a - 0.5 * sigma_a**2) * dt
    z1 = (dlnA - drift_lnA) / (sigma_a * np.sqrt(dt))

    phi = np.exp(-k * dt)
    sdS = a * np.sqrt((1 - np.exp(-2 * k * dt)) / (2 * k))
    S0, S1 = S[:-1], S[1:]
    meanS = L + (S0 - L) * phi
    z2 = (S1 - meanS) / sdS

    rho = float(np.corrcoef(z1, z2)[0, 1])
    return float(np.clip(rho, -0.999, 0.999))


def ou_ljung_box_pvalue(
    S: np.ndarray,
    k: float,
    L: float,
    a: float,
    dt: float = 1.0 / TRADING_DAYS_PER_YEAR,
    lags: int = 10,
) -> float:
    """Ljung-Box p-value of the standardized OU innovations.

    A low p-value indicates leftover autocorrelation, which usually means the
    OU model does not fully capture the mean-reversion dynamics.
    """

    if not np.isfinite(k) or not np.isfinite(a) or k <= 0 or a <= 0 or len(S) <= lags + 1:
        return float("nan")

    phi = np.exp(-k * dt)
    sdS = a * np.sqrt((1 - np.exp(-2 * k * dt)) / (2 * k))
    S0, S1 = S[:-1], S[1:]
    meanS = L + (S0 - L) * phi
    innovations = (S1 - meanS) / sdS

    try:
        lb = acorr_ljungbox(innovations, lags=[lags], return_df=True)
        return float(lb["lb_pvalue"].iloc[0])
    except Exception:
        return float("nan")


def rolling_ou_mle_params(
    log_prices: pd.DataFrame,
    dependent_symbol: str,
    independent_symbol: str,
    window: int,
    dt: float = 1.0 / TRADING_DAYS_PER_YEAR,
    det_order: int = 0,
    k_ar_diff: int = 1,
    lags: int = 10,
) -> pd.DataFrame:
    """Estimate rolling MLE parameters over trailing windows of log prices.

    For every ``t >= window - 1`` the most recent ``window`` observations are
    used to estimate the Johansen beta, the OU parameters of the spread, the
    GBM parameters of the dependent leg, and the innovation correlation.  The
    output is a frame with the same index as ``log_prices``; leading rows are
    ``NaN`` and then forward-filled so downstream code can use the most recent
    successful estimate.
    """

    if window < 3:
        raise ValueError("The rolling OU window must be at least 3 observations.")

    ln_dep = log_prices[dependent_symbol].to_numpy(dtype=float)
    ln_ind = log_prices[independent_symbol].to_numpy(dtype=float)
    n = len(log_prices)

    out = pd.DataFrame(np.nan, index=log_prices.index, columns=list(OU_COLUMNS))

    for t in range(window - 1, n):
        dep_win = ln_dep[t - window + 1 : t + 1]
        ind_win = ln_ind[t - window + 1 : t + 1]

        beta = estimate_johansen_beta(dep_win, ind_win, det_order=det_order, k_ar_diff=k_ar_diff)
        if not np.isfinite(beta):
            continue

        S_win = dep_win - beta * ind_win
        mu_a, sigma_a = mle_gbm_from_lnA(dep_win, dt)
        k, L, a, nll = mle_ou(S_win, dt)
        if not np.isfinite(k) or not np.isfinite(a):
            continue

        rho = estimate_rho_from_innovations(dep_win, S_win, dt, mu_a, sigma_a, k, L, a)
        half_life_days = (np.log(2) / k) / dt
        lb_pvalue = ou_ljung_box_pvalue(S_win, k, L, a, dt, lags)

        out.iloc[t] = [
            beta,
            mu_a,
            sigma_a,
            k,
            L,
            a,
            rho,
            half_life_days,
            nll,
            lb_pvalue,
            float(S_win[-1]),
        ]

    return out.ffill()


def add_ou_diagnostics(signal_frame: pd.DataFrame, ou_params: pd.DataFrame | None) -> pd.DataFrame:
    """Join a rolling OU parameter frame into a signal frame.

    Also attaches the two Ito drift decomposition columns derived from the OU
    and GBM estimates, mirroring the original spreadsheet-style output.
    """

    result = signal_frame.copy()
    if ou_params is None or ou_params.empty:
        return result

    aligned = ou_params.reindex(result.index).ffill()
    for column in OU_COLUMNS:
        if column not in result.columns:
            result[column] = aligned[column].astype(float)

    drift_spread = result["ou_k"] * (result["ou_L"] - result["ou_spread"])
    diff_var = (result["ou_beta"] * result["ou_sigma_a"]) ** 2 + result["ou_a"] ** 2 + 2 * result["ou_beta"] * result["ou_sigma_a"] * result["ou_a"] * result["ou_rho"]
    drift_over_price = result["ou_beta"] * (result["ou_mu_a"] - 0.5 * result["ou_sigma_a"] ** 2) + result["ou_k"] * (result["ou_L"] - result["ou_spread"]) + 0.5 * diff_var
    result["ou_drift_spread"] = drift_spread.astype(float)
    result["ou_drift_over_price"] = drift_over_price.astype(float)
    return result


def mean_reversion_gate_pass_series(
    signal_frame: pd.DataFrame,
    *,
    mode: str,
    half_life_min_days: float,
    half_life_max_days: float,
    lb_p_value_min: float,
) -> pd.Series:
    """Compute the mean-reversion quality gate boolean series.

    ``mode`` is one of ``off``, ``half_life_range``, ``lb_filter``, or ``both``.
    The gate requires the OU half-life and/or the Ljung-Box p-value to stay
    inside the configured bounds.  Rows without OU estimates fail closed.
    """

    index = signal_frame.index
    if mode == "off" or "ou_k" not in signal_frame.columns:
        return pd.Series(True, index=index, dtype="boolean")

    half_life = signal_frame["ou_half_life_days"]
    lb_pvalue = signal_frame["ou_lb_pvalue"]

    half_life_pass = (half_life >= half_life_min_days) & (half_life <= half_life_max_days)
    lb_pass = lb_pvalue >= lb_p_value_min

    if mode == "half_life_range":
        gate = half_life_pass
    elif mode == "lb_filter":
        gate = lb_pass
    else:  # "both"
        gate = half_life_pass & lb_pass

    missing = half_life.isna() | lb_pvalue.isna()
    gate = gate & ~missing
    return gate.fillna(False).astype("boolean")


def ou_params_to_dict(ou_params: pd.DataFrame) -> dict[str, Any]:
    """Summarize a rolling OU parameter frame for run diagnostics."""

    clean = ou_params.dropna()
    if clean.empty:
        return {
            "observations": 0,
            "mean_half_life_days": None,
            "median_half_life_days": None,
            "mean_k": None,
            "mean_lb_pvalue": None,
            "last_beta": None,
            "last_k": None,
            "last_half_life_days": None,
            "last_lb_pvalue": None,
        }
    last = clean.iloc[-1]
    return {
        "observations": int(len(clean)),
        "mean_half_life_days": float(clean["ou_half_life_days"].mean()),
        "median_half_life_days": float(clean["ou_half_life_days"].median()),
        "mean_k": float(clean["ou_k"].mean()),
        "mean_lb_pvalue": float(clean["ou_lb_pvalue"].mean()),
        "last_beta": float(last["ou_beta"]),
        "last_k": float(last["ou_k"]),
        "last_half_life_days": float(last["ou_half_life_days"]),
        "last_lb_pvalue": float(last["ou_lb_pvalue"]),
    }
