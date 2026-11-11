"""Aba Historico: perfis lado a lado, um unico grafico de evolucao e a tabela de rentabilidade com alternador de perfil."""
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from app import streamlit_app_user as app

PAGE = Path(__file__).resolve().parents[1] / "app" / "streamlit_app_user.py"


def history(perfil):
    token = app._PROFILE.set(perfil)
    try:
        return app.monthly(app.files())
    finally:
        app._PROFILE.reset(token)


def test_evolution_chart_has_both_profiles_ibov_and_cdi_in_the_same_figure():
    dfs = {"moderado": history("moderado"), "arrojado": history("arrojado")}
    fig = app.cumulative_profiles(dfs)
    names = [trace.name for trace in fig.data]
    assert names == ["Moderado", "Arrojado", "IBOV", "CDI liquido"]
    final = {trace.name: float(trace.y[-1]) for trace in fig.data}
    assert final["Arrojado"] > final["Moderado"] > final["IBOV"]                    # ordem observada no historico refeito
    assert all(len(trace.x) == len(dfs["moderado"]) for trace in fig.data)


def test_history_totals_match_the_profile_histories():
    for perfil in ("moderado", "arrojado"):
        totals = app.history_totals(history(perfil))
        assert totals["modelo"] == pytest.approx(float((1 + history(perfil)["retorno_modelo"]).prod() - 1))
        assert 0.4 < totals["acerto"] < 0.7 and totals["ganho"] == pytest.approx(totals["modelo"] - totals["ibov"])


def test_history_tab_shows_both_profiles_side_by_side_and_a_profile_switch_for_the_table():
    view = AppTest.from_file(str(PAGE)).run(timeout=120)
    assert not view.exception
    headings = [m.value for m in view.markdown]
    assert headings.count("#### Moderado") >= 2 and headings.count("#### Arrojado") >= 2        # resultado acumulado e taxa media (e a parcial do mes anterior, se aberta): um bloco por perfil
    switch = [r for r in view.radio if r.key == "perfil_tabela_historica"][0]
    assert list(switch.options) == ["Moderado", "Arrojado"] and switch.value == "Moderado"
    moderate_table = [m.value for m in view.markdown if m.value.startswith('<div class="hist-card">')][0]
    view = [r for r in view.radio if r.key == "perfil_tabela_historica"][0].set_value("Arrojado").run(timeout=120)
    assert not view.exception
    arrojado_table = [m.value for m in view.markdown if m.value.startswith('<div class="hist-card">')][0]
    assert arrojado_table != moderate_table                                                        # o alternador troca a tabela de perfil


def test_risk_stats_computes_drawdown_extreme_months_volatility_and_sharpe():
    import numpy as np
    df = pd.DataFrame({"mes": ["2024-01", "2024-02", "2024-03", "2024-04"],
                       "retorno_modelo": [0.10, -0.20, 0.05, 0.10],
                       "retorno_expost_ibov": [0.02, -0.05, 0.01, 0.03],
                       "retorno_cdi_liquido_periodo": [0.01, 0.01, 0.01, 0.01]})
    k = app.risk_stats(df)["modelo"]
    assert round(k["dd"], 4) == -0.2 and k["dd_mes"] == "2024-02"          # pico 1,10 -> vale 0,88
    assert (k["worst"], k["worst_mes"], k["best"], k["best_mes"]) == (-0.20, "2024-02", 0.10, "2024-01")
    sd = float(np.std(df["retorno_modelo"], ddof=1))
    assert abs(k["vol"] - sd * np.sqrt(12)) < 1e-12
    assert abs(k["sharpe"] - (df["retorno_modelo"] - 0.01).mean() / sd * np.sqrt(12)) < 1e-12
