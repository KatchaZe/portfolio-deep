"""
Risk math for the Allocation (Risk Desk) and Correlation tabs. Pure functions over
plain numbers, lists and dicts: no I/O, no network, and no numpy, which the app does
not ship. Portfolios are a handful of names, so plain loops are fast enough.

    returns       sample stats over daily returns, and date alignment
    covariance    realized / proxy / hybrid / crisis covariance matrices
    portfolio     weights, vol, diversification, risk contribution, score, sizing
    stress        scenario and historical stress, VaR/CVaR, suitability
    correlation   the Correlation Monitor and the downside-risk lens

Conventions: w_i = market_value_i / Σ market_value (sums to 1); sigma = annualized
stdev of daily returns; sigma_p = sqrt(w' Sigma w); RC_i = w_i*MCR_i with Σ RC_i =
sigma_p; signed %RC = RC_i / sigma_p (negative for a diversifier). Epistemic tags
([FACT]/[CALC]/[JUDG-PROXY]/[JUDG-SCENARIO]) are attached by the caller.
"""
from .returns import (TRADING_DAYS, align_on_dates, align_pair, align_returns,
                      annualized_vol, daily_returns, pair_corr, portfolio_returns,
                      portfolio_returns_dated)
from .covariance import (CRISIS_EQUITY_CORR, CRISIS_MIN_DAYS, CRISIS_MIN_DRAWDOWN, PROXY_VOL,
                         corr_from_cov, cov_matrix, crisis_cov, crisis_cov_measured,
                         crisis_days, crisis_pair_corr, crisis_window, hybrid_cov, proxy_cov,
                         shrink_to_psd)
from .portfolio import (add_blocker, beta_risk_contribution, capital_weights, concentration,
                        diversification_ratio, diversification_score, effective_bets,
                        gap_coverage, group_exposure, portfolio_beta, portfolio_vol,
                        position_sizing, rebalance, risk_contributions)
from .stress import (HISTORICAL, SCENARIOS, effective_duration, historical_var,
                     rate_stress, reverse_stress, stress_test, suitability, var_cvar)
from .correlation import (downside_beta, downside_corr, pair_corr_dated, rolling_corr,
                          semideviation, sector_corr, sortino, top_pairs)
