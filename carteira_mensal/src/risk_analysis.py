from __future__ import annotations

import numpy as np
import pandas as pd


def log_returns(prices: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    return np.log(prices / prices.shift(1)).dropna(how="all")


def population_std(returns: pd.Series) -> float:
    return returns.dropna().std(ddof=0)


def coefficient_of_variation(mean_return: float, std: float) -> float:
    if pd.isna(mean_return) or mean_return <= 0:
        return np.nan
    return std / mean_return


def beta(asset_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    data = pd.concat([asset_returns, benchmark_returns], axis=1).dropna()
    if len(data) < 2:
        return np.nan
    cov = np.cov(data.iloc[:, 0], data.iloc[:, 1], ddof=0)[0, 1]
    var = np.var(data.iloc[:, 1], ddof=0)
    return np.nan if var == 0 else cov / var


def risk_metrics(asset_returns: pd.DataFrame, ibov_returns: pd.Series, settings: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows = []
    corr = asset_returns.corr()
    cov = asset_returns.cov(ddof=0)
    for ticker in asset_returns.columns:
        series = asset_returns[ticker].dropna()
        mean = series.mean()
        std = population_std(series)
        cv = coefficient_of_variation(mean, std)
        b = beta(asset_returns[ticker], ibov_returns)
        corr_ibov = pd.concat([asset_returns[ticker], ibov_returns], axis=1).dropna().corr().iloc[0, 1]
        others = [col for col in asset_returns.columns if col != ticker]
        mean_corr_others = corr.loc[ticker, others].mean() if others else np.nan
        rows.append(
            {
                "ticker": ticker,
                "retorno_medio": mean,
                "desvio_padrao": std,
                "cv": cv,
                "beta": b,
                "correlacao_ibov": corr_ibov,
                "correlacao_media_ativos": mean_corr_others,
            }
        )
    return pd.DataFrame(rows), corr, cov


def annualize_return(daily_return: float, trading_days: int = 252) -> float:
    return (1 + daily_return) ** trading_days - 1


def annualize_risk(daily_std: float, trading_days: int = 252) -> float:
    return daily_std * np.sqrt(trading_days)


def portfolio_return(weights: np.ndarray, mean_returns: np.ndarray) -> float:
    return float(np.dot(weights, mean_returns))


def portfolio_risk(weights: np.ndarray, covariance: np.ndarray) -> float:
    return float(np.sqrt(weights @ covariance @ weights.T))


def portfolio_beta(weights: np.ndarray, betas: np.ndarray) -> float:
    return float(np.dot(weights, betas))


def sharpe_ratio(port_return: float, port_risk: float, risk_free: float) -> float:
    if port_risk == 0 or pd.isna(port_risk):
        return np.nan
    return (port_return - risk_free) / port_risk
