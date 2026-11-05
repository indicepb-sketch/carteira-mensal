from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import shadow_teste56_exposicao_continua_confirmacao_ibov as t56  # noqa: E402


def synthetic_ibov(seed: int = 3) -> pd.Series:
    index = pd.bdate_range("2020-06-01", "2026-10-05")
    rng = np.random.default_rng(seed)
    return pd.Series(100_000 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, len(index)))), index=index)


def test_features_do_ibov_nao_veem_o_futuro():
    prices = synthetic_ibov()
    month = "2026-05"
    cutoff = t56.selection_cutoff(month)
    assert cutoff == pd.Timestamp("2026-04-30")
    normal = t56.ibov_features(prices, month)
    poisoned = prices.copy()
    poisoned[poisoned.index > cutoff] = 1.0  # futuro corrompido
    assert t56.ibov_features(poisoned, month) == normal
    truncated = prices[prices.index <= cutoff]
    assert t56.ibov_features(truncated, month) == normal


def frame_sintetico() -> pd.DataFrame:
    return pd.DataFrame({
        "mes": ["2024-01", "2024-02", "2024-03"],
        "regime": ["alta", "queda_forte", "queda_leve"],
        "w0": [0.97, 0.25, 0.96],
        "r_acoes": [0.05, 0.02, -0.01],
        "r_cdi": [0.008, 0.008, 0.008],
        "pct_tendencia_favoravel": [0.7, 0.2, 0.3],
        "ibov_alta_estrita": [True, False, True],
        "ibov_alta_leve": [True, True, True],
        "regime_anterior": [None, "alta", "queda_forte"],
    })


def test_baseline_reproduz_o_retorno_original():
    frame = frame_sintetico()
    sim = t56.simulate(frame, t56.h_base)
    expected = frame["w0"] * frame["r_acoes"] + (1 - frame["w0"]) * frame["r_cdi"]
    assert sim["retorno"].to_numpy() == pytest.approx(expected.to_numpy())
    assert not sim["mudou"].any()


def test_exposicao_continua_respeita_limites():
    assert t56.linear_exposure(0.0, 0.2, 0.6) == pytest.approx(0.30)
    assert t56.linear_exposure(0.9, 0.2, 0.6) == pytest.approx(1.0)
    assert t56.linear_exposure(0.4, 0.2, 0.6) == pytest.approx(0.65)
    assert t56.linear_exposure(float("nan"), 0.2, 0.6) == 1.0


def test_confirmacao_so_eleva_exposicao_defensiva():
    frame = frame_sintetico()
    sim = t56.simulate(frame, t56.h_confirma("ibov_alta_leve"))
    # marco (queda_leve efetiva 100%) e janeiro (alta) nao mudam; fevereiro sobe de 30% para 60%
    assert sim["mudou"].tolist() == [False, True, False]
    assert sim.loc[1, "peso_acoes"] == pytest.approx(0.25 * 0.60 / 0.30)


def test_decisao_nao_depende_de_retornos_do_mes():
    frame = frame_sintetico()
    altered = frame.copy()
    altered["r_acoes"] = [9.0, -9.0, 9.0]  # retornos do mes mudam, decisao nao pode mudar
    for name, (_, decide) in t56.HYPOTHESES.items():
        a = t56.simulate(frame, decide)["exposicao_nova"].tolist()
        b = t56.simulate(altered, decide)["exposicao_nova"].tolist()
        assert a == b, name


def test_arquivos_de_producao_sao_monitorados():
    names = {p.name for p in t56.PROTECTED}
    assert {"active.json", "2026-11.json", "carteira_forward_2026_11.xlsx"} <= names
    assert t56.LAST_MONTH < "2026-11"
