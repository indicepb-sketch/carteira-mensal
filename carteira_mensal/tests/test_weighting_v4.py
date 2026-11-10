from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import forward_test as ft  # noqa: E402
from weighting import ledoit_wolf_covariance, min_variance_for_tickers, min_variance_weights  # noqa: E402


def synthetic_prices(seed=1, days=200, vols=(0.005, 0.01, 0.02, 0.03)):
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2026-01-02", periods=days)
    market = rng.normal(0, 0.008, days)
    data = {f"T{i}.SA": 10 * np.exp(np.cumsum(0.5 * market + rng.normal(0, v, days))) for i, v in enumerate(vols)}
    return pd.DataFrame(data, index=index)


def test_min_variance_weights_sum_to_one_respect_cap_and_favor_low_volatility():
    prices = synthetic_prices()
    returns = np.log(prices).diff().dropna().to_numpy()
    weights, shrink = min_variance_weights(returns, cap=0.6)
    assert weights.sum() == pytest.approx(1.0)
    assert (weights >= 0).all() and weights.max() <= 0.6 + 1e-9
    assert weights[0] == weights.max()                      # o papel menos volatil recebe o maior peso
    assert 0.0 <= shrink <= 1.0


def test_ledoit_wolf_is_positive_definite_and_shrinks_toward_identity():
    returns = np.log(synthetic_prices(days=60, vols=(0.01,) * 8)).diff().dropna().to_numpy()
    cov, shrink = ledoit_wolf_covariance(returns)
    assert np.linalg.eigvalsh(cov).min() > 0
    assert 0.0 < shrink <= 1.0


def test_weights_use_only_prices_up_to_the_cutoff():
    prices = synthetic_prices()
    cutoff = prices.index[150]
    base, _ = min_variance_for_tickers(list(prices.columns), prices, cutoff)
    altered = prices.copy()
    altered.loc[altered.index > cutoff] *= 7.0                # mudar o futuro nao pode mexer nos pesos
    again, _ = min_variance_for_tickers(list(prices.columns), altered, cutoff)
    assert base.to_numpy() == pytest.approx(again.to_numpy())


def test_weighting_policy_is_v4_min_variance_from_effective_month_only():
    assert ft.weighting_policy("2026-10") is None             # outubro nasceu na v2: pesos do otimizador
    for month in ("2026-11", "2027-01"):
        cfg = ft.weighting_policy(month)
        assert cfg["method"] == "min_variance_lw" and cfg["window"] == 120 and cfg["cap"] == 0.25 and cfg["version"] == "forward-13b-v7"
    assert ft.weighting_policy("2024-05", force=True)["method"] == "min_variance_lw"      # historico refeito na metodologia vigente


def test_apply_weighting_preserves_total_drops_tiny_positions_and_keeps_audit_column():
    prices = synthetic_prices(vols=(0.004, 0.006, 0.02, 0.03))
    cutoff = prices.index[-1]
    portfolio = pd.DataFrame({"ticker": list(prices.columns), "peso_recomendado": [0.4, 0.3, 0.2, 0.1]})
    out, info = ft.apply_weighting(portfolio, "2026-11", cutoff, prices=prices)
    assert info["ativa"] and info["metodo"] == "min_variance_lw"
    assert out["peso_recomendado"].sum() == pytest.approx(1.0)
    assert (out["peso_recomendado"] >= 0.01 - 1e-9).all()
    assert "peso_otimizador_original" in out.columns
    assert out["ticker"].is_unique


def test_apply_weighting_is_a_noop_before_the_effective_month():
    prices = synthetic_prices()
    portfolio = pd.DataFrame({"ticker": list(prices.columns), "peso_recomendado": [0.25] * 4})
    out, info = ft.apply_weighting(portfolio, "2026-10", prices.index[-1], prices=prices)
    assert info == {"ativa": False} and out["peso_recomendado"].tolist() == [0.25] * 4
