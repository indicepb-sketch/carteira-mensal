"""Pesos de minima variancia com encolhimento de Ledoit-Wolf (metodologia forward-13b-v4).

Entre as acoes ja escolhidas pelo motor (selecao, regime e exposicao inalterados), os pesos passam a minimizar a variancia da carteira,
long-only, com teto por papel. A covariancia usa apenas os retornos logaritmicos diarios dos ultimos `window` pregoes ANTERIORES a data de
corte (sem informacao posterior). O retorno medio historico NAO entra no calculo (e a entrada mais ruidosa da otimizacao de Markowitz).

Encolhimento: Ledoit-Wolf (2004) em direcao a identidade escalada pelo traco medio da matriz amostral.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize

DEFAULT_WINDOW = 120
DEFAULT_CAP = 0.25


def ledoit_wolf_covariance(returns: np.ndarray) -> tuple[np.ndarray, float]:
    """Covariancia amostral encolhida para a identidade escalada. Retorna (matriz, intensidade do encolhimento)."""
    n, p = returns.shape
    x = returns - returns.mean(axis=0)
    sample = x.T @ x / n
    mu = float(np.trace(sample) / p)
    d2 = float(((sample - mu * np.eye(p)) ** 2).sum() / p)
    if d2 <= 0:
        return mu * np.eye(p), 1.0
    b2 = float(sum(((np.outer(row, row) - sample) ** 2).sum() for row in x) / (n * n * p))
    shrink = min(b2, d2) / d2
    return shrink * mu * np.eye(p) + (1.0 - shrink) * sample, shrink


def min_variance_weights(returns: np.ndarray, cap: float = DEFAULT_CAP) -> tuple[np.ndarray, float]:
    """Pesos long-only de minima variancia (soma 1, teto `cap`). Falha da otimizacao cai em pesos iguais."""
    n = returns.shape[1]
    if n < 2:
        return np.ones(n), 0.0
    cov, shrink = ledoit_wolf_covariance(returns)
    upper = max(cap, 1.0 / n + 1e-6)
    result = minimize(
        lambda w: float(w @ (cov * 1e4) @ w),     # escala 1e4: o SLSQP converge mal com variancias diarias (~1e-4)
        np.ones(n) / n,
        method="SLSQP",
        bounds=[(0.0, upper)] * n,
        constraints=[{"type": "eq", "fun": lambda w: float(w.sum() - 1.0)}],
    )
    weights = np.clip(result.x, 0.0, None) if result.success else np.ones(n) / n
    return weights / weights.sum(), shrink


def min_variance_for_tickers(tickers: list[str], prices: pd.DataFrame, cutoff: pd.Timestamp, window: int = DEFAULT_WINDOW, cap: float = DEFAULT_CAP) -> tuple[pd.Series, dict]:
    """Pesos por ticker usando os `window` pregoes ate `cutoff` (inclusive). `prices`: fechamentos ajustados, colunas = tickers."""
    log_returns = np.log(prices[prices.index <= cutoff]).diff().tail(window)
    sample = log_returns.reindex(columns=tickers)
    covered = int(sample.notna().all(axis=1).sum())
    weights, shrink = min_variance_weights(sample.fillna(0.0).to_numpy(float), cap)
    info = {"janela_pregoes": int(len(sample)), "pregoes_completos": covered, "encolhimento": float(shrink), "teto": cap,
            "tickers_sem_preco": [t for t in tickers if t not in prices.columns]}
    return pd.Series(weights, index=tickers), info
