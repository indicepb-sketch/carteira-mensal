from pathlib import Path

import pandas as pd
import pytest

from app import streamlit_app_user as app


def test_open_month_history_uses_the_same_executable_portfolio(monkeypatch):
    forward = Path("carteira_forward_2026_10.xlsx")
    partial = Path("parcial_carteira_forward_2026_10.xlsx")
    operational = Path("historico.xlsx")
    files = app.AppFiles(forward, partial, operational, "forward-13b-v1")
    portfolio = pd.DataFrame([
        {"ticker": "AAA3.SA", "nome": "A", "setor": "A", "nota_final": 3, "peso_recomendado": 0.5, "preco_entrada_fechamento_mes_anterior": 1300},
        {"ticker": "BBB3.SA", "nome": "B", "setor": "B", "nota_final": 2, "peso_recomendado": 0.3, "preco_entrada_fechamento_mes_anterior": 500},
        {"ticker": "CCC3.SA", "nome": "C", "setor": "C", "nota_final": 1, "peso_recomendado": 0.2, "preco_entrada_fechamento_mes_anterior": 100},
    ])
    partial_fields = {"mes": "2026-10", "status": "parcial_mes_em_andamento", "retorno_carteira_parcial_aplicada": 0.03,
                      "retorno_ibov_parcial": 0.005, "retorno_cdi_liquido_periodo": 0.001}
    assets = pd.DataFrame([
        {"ticker": "AAA3.SA", "retorno_periodo": 0.02},
        {"ticker": "BBB3.SA", "retorno_periodo": 0.01},
        {"ticker": "CCC3.SA", "retorno_periodo": 0.10},
    ])
    frames = {
        (str(forward), "Resumo Forward"): pd.DataFrame([{"campo": "mes_forward", "valor": "2026-10"}]),
        (str(forward), "Carteira Aplicada"): portfolio,
        (str(partial), "Resumo Parcial"): pd.DataFrame(partial_fields.items(), columns=["metrica", "valor"]),
        (str(partial), "Ativos"): assets,
        (str(operational), "Mes a Mes"): pd.DataFrame([{
            "mes": "2026-09", "cenario": "TOP15", "capital": 10000,
            "retorno_modelo": 0.02, "retorno_expost_ibov": 0.01,
            "retorno_cdi_liquido_periodo": 0.005,
        }]),
    }
    monkeypatch.setattr(app, "sheet", lambda path, name: frames.get((path, name), pd.DataFrame()))
    monkeypatch.setattr(app, "finalized_partial_forward", lambda ps, mes: forward)
    monkeypatch.setattr(app, "finalized_partial_files", lambda: [])
    monkeypatch.setattr(app, "calendar_cdi_monthly", lambda: pd.DataFrame())
    monkeypatch.setattr(app, "calendar_ibov_monthly", lambda: pd.DataFrame())
    monkeypatch.setattr(app, "PLATFORM_MAX_STOCKS", 2)

    for capital in (10000, 5000):
        executable, _, _ = app.executable_portfolio(portfolio, capital, 0.01, True)
        expected, _ = app.practical_partial_return(executable, partial_fields, assets)
        history = app.monthly(files, capital, 0.01, True)
        current = history.loc[history["mes"].eq("2026-10")].iloc[0]
        assert current["retorno_modelo"] == pytest.approx(expected)
        assert current["retorno_modelo"] != partial_fields["retorno_carteira_parcial_aplicada"]
        assert current["peso_acoes_executavel"] == pytest.approx(
            executable.loc[~executable.apply(app.is_cdi, axis=1), "peso_executavel"].sum()
        )
        assert history["mes"].tolist() == ["2026-09", "2026-10"]

    frames[(str(partial), "Ativos")] = assets.drop(columns=["retorno_periodo"])
    assert app.monthly(files)["mes"].tolist() == ["2026-09"]
    executable, _, _ = app.executable_portfolio(portfolio, 10000, 0.01, True)
    assert pd.isna(app.practical_partial_return(executable, partial_fields, frames[(str(partial), "Ativos")])[0])


def test_current_closed_month_is_not_added_twice(monkeypatch):
    forward = Path("carteira_forward_2026_10.xlsx")
    partial = Path("parcial_carteira_forward_2026_10.xlsx")
    files = app.AppFiles(forward, partial, Path("historico.xlsx"), "forward-13b-v1")
    monkeypatch.setattr(app, "sheet", lambda path, name: pd.DataFrame([{"mes": "2026-09", "retorno_modelo": 0.02}]) if name == "Mes a Mes" else pd.DataFrame())
    monkeypatch.setattr(app, "calendar_cdi_monthly", lambda: pd.DataFrame())
    monkeypatch.setattr(app, "calendar_ibov_monthly", lambda: pd.DataFrame())
    monkeypatch.setattr(app, "partial", lambda f: ({"mes": "2026-10"}, pd.DataFrame()))
    row = pd.DataFrame([{"mes": "2026-10", "retorno_modelo": 0.01}])
    monkeypatch.setattr(app, "all_finalized_partial_month_rows", lambda f: row)
    monkeypatch.setattr(app, "finalized_partial_month_row", lambda *args, **kwargs: row)

    assert app.monthly(files)["mes"].tolist() == ["2026-09", "2026-10"]
