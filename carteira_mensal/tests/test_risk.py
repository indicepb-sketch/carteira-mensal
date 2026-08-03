import numpy as np
import pandas as pd

from risk_analysis import (
    beta,
    coefficient_of_variation,
    log_returns,
    population_std,
    portfolio_beta,
    portfolio_return,
    portfolio_risk,
    sharpe_ratio,
)


def test_log_returns():
    prices = pd.Series([100, 110, 121], dtype=float)
    result = log_returns(prices)
    assert np.allclose(result.values, [np.log(1.1), np.log(1.1)])


def test_population_std():
    returns = pd.Series([0.01, 0.02, 0.03])
    assert np.isclose(population_std(returns), np.std(returns, ddof=0))


def test_cv_requires_positive_return():
    assert np.isnan(coefficient_of_variation(0, 0.1))
    assert coefficient_of_variation(0.02, 0.1) == 5


def test_beta():
    market = pd.Series([0.01, 0.02, -0.01, 0.03])
    asset = market * 1.5
    assert np.isclose(beta(asset, market), 1.5)


def test_correlation_and_covariance():
    frame = pd.DataFrame({"A": [0.01, 0.02, 0.03], "B": [0.02, 0.04, 0.06]})
    assert np.isclose(frame.corr().loc["A", "B"], 1.0)
    assert np.isclose(frame.cov(ddof=0).loc["A", "B"], np.cov(frame["A"], frame["B"], ddof=0)[0, 1])


def test_portfolio_metrics():
    weights = np.array([0.5, 0.5])
    mean_returns = np.array([0.01, 0.02])
    cov = np.array([[0.0004, 0.0], [0.0, 0.0009]])
    assert np.isclose(portfolio_return(weights, mean_returns), 0.015)
    assert np.isclose(portfolio_risk(weights, cov), np.sqrt(0.000325))
    assert np.isclose(portfolio_beta(weights, np.array([0.8, 1.2])), 1.0)
    assert sharpe_ratio(0.02, 0.01, 0.005) == 1.5
