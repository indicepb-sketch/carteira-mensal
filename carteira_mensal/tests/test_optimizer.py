import numpy as np
import pandas as pd

from optimizer import optimize_weights, validate_portfolio


def settings():
    return {
        "strategy": {"max_assets": 5, "min_assets": 3},
        "portfolio": {"min_weight": 0.05, "max_weight": 0.5, "max_sector_weight": 0.6},
        "risk_free_rate": {"annual_rate": 0.15},
        "risk": {"trading_days_year": 252},
    }


def candidates():
    return pd.DataFrame(
        {
            "ticker": ["A", "B", "C"],
            "setor": ["X", "X", "Y"],
            "retorno_medio": [0.01, 0.012, 0.009],
            "beta": [0.8, 1.1, 0.9],
            "nota_final": [90, 80, 70],
        }
    )


def test_optimize_weights_constraints():
    cov = pd.DataFrame(np.eye(3) * 0.0004, index=["A", "B", "C"], columns=["A", "B", "C"])
    portfolio, _ = optimize_weights(candidates(), cov, settings())
    assert np.isclose(portfolio["peso_recomendado"].sum(), 1)
    assert (portfolio["peso_recomendado"] >= 0.05 - 1e-6).all()
    assert (portfolio["peso_recomendado"] <= 0.5 + 1e-6).all()
    assert portfolio.groupby("setor")["peso_recomendado"].sum().max() <= 0.6 + 1e-6


def test_validate_portfolio():
    portfolio = candidates()
    portfolio["peso_recomendado"] = [0.4, 0.2, 0.4]
    alerts = validate_portfolio(portfolio, settings())
    assert alerts == []
