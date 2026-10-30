from argparse import Namespace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app import streamlit_app_user as app
from scripts import forward_partial
from portfolio_execution import executable_portfolio, practical_partial_return


def test_data_cutoff_does_not_borrow_file_modification_time(tmp_path):
    path = tmp_path / "partial.xlsx"
    path.touch()
    assert app.format_updated_at("2026-10-28", path) == "28/10/2026"


def test_dashboard_and_generator_share_execution_and_reject_missing_cdi():
    portfolio = pd.DataFrame([
        {"ticker": "AAA3.SA", "nota_final": 3, "peso_recomendado": .5,
         "preco_entrada_fechamento_mes_anterior": 100.0},
        {"ticker": "BBB3.SA", "nota_final": 2, "peso_recomendado": .3,
         "preco_entrada_fechamento_mes_anterior": 100.0},
        {"ticker": "CCC3.SA", "nota_final": 1, "peso_recomendado": .2,
         "preco_entrada_fechamento_mes_anterior": 100.0},
    ])
    assets = pd.DataFrame([
        {"ticker": "AAA3.SA", "retorno_periodo": .10},
        {"ticker": "BBB3.SA", "retorno_periodo": -.05},
        {"ticker": "CCC3.SA", "retorno_periodo": -.20},
    ])
    generator, removed, allocation = executable_portfolio(portfolio, 10000, .01, True, 2)
    previous_limit = app.PLATFORM_MAX_STOCKS
    try:
        app.PLATFORM_MAX_STOCKS = 2
        dashboard, _, _ = app.executable_portfolio(portfolio, 10000, .01, True)
    finally:
        app.PLATFORM_MAX_STOCKS = previous_limit
    pd.testing.assert_frame_equal(generator, dashboard)
    assert removed["ticker"].tolist() == ["CCC3.SA"]
    assert allocation["cdi_value"] == pytest.approx(2100.0)
    result, _ = practical_partial_return(generator, assets, .01)
    assert result == pytest.approx(.0376)
    missing, _ = practical_partial_return(generator, assets, np.nan)
    assert np.isnan(missing)


def test_dashboard_blocks_mismatch_with_frozen_official_return():
    executable = pd.DataFrame([
        {"ticker": "AAA3.SA", "peso_executavel": .9},
        {"ticker": "CDI", "peso_executavel": .1},
    ])
    assets = pd.DataFrame([{"ticker": "AAA3.SA", "retorno_periodo": .02}])
    summary = {"calculo_oficial": "executavel_top15_capital_10000",
               "retorno_cdi_liquido_periodo": .01,
               "retorno_modelo_executavel": .03}
    with pytest.raises(ValueError, match="diverge do arquivo oficial"):
        app.checked_partial_return(executable, summary, assets, 10000, .01, True)
    result, _ = app.checked_partial_return(executable, summary, assets, 5000, .01, True)
    assert result == pytest.approx(.019)


def test_dashboard_selects_only_official_partial_for_active_version(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "EXCEL_DIR", tmp_path)

    def write_partial(stamp, **overrides):
        values = {
            "calculo_oficial": "executavel_top15_capital_10000",
            "arquivo_forward_usado": "carteira_forward_2026_10.xlsx",
            "versao_modelo": "v1.0.0",
            "data_corte_comum": "2026-10-28",
            "data_avaliacao_parcial": "2026-10-28",
            "status": "parcial_mes_em_andamento",
            "retorno_modelo_executavel": -.1036,
        }
        values.update(overrides)
        path = tmp_path / f"parcial_carteira_forward_2026_10_v{stamp}.xlsx"
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame([{"metrica": key, "valor": value} for key, value in values.items()]).to_excel(
                writer, sheet_name="Resumo Parcial", index=False)
        return path

    valid = write_partial("20261030_090000")
    write_partial("20261030_090001", versao_modelo="other")
    write_partial("20261030_090002", calculo_oficial="bruto_recomendacoes")
    write_partial("20261030_090003", data_avaliacao_parcial="2026-10-29")
    assert app.latest_official_partial("2026-10", "carteira_forward_2026_10.xlsx", "v1.0.0") == valid


def test_official_partial_uses_common_cutoff_and_executable_return(monkeypatch, tmp_path):
    portfolio = pd.DataFrame([
        {"ticker": "AAA3.SA", "nota_final": 2, "peso_recomendado": .6,
         "preco_entrada_fechamento_mes_anterior": 120.0},
        {"ticker": "BBB3.SA", "nota_final": 1, "peso_recomendado": .4,
         "preco_entrada_fechamento_mes_anterior": 120.0},
    ])
    dates = pd.to_datetime(["2026-09-30", "2026-10-02", "2026-10-05"])
    prices = pd.DataFrame({"AAA3.SA": [100.0, 110.0, 115.0],
                           "BBB3.SA": [100.0, 90.0, 95.0],
                           "^BVSP": [100000.0, 99000.0, 98000.0]}, index=dates)
    cdi = pd.DataFrame({"data": pd.to_datetime(["2026-10-01", "2026-10-02"]),
                        "cdi_diario_pct": [.05, .05],
                        "cdi_diario_decimal": [.0005, .0005]})
    monkeypatch.setattr(forward_partial, "parse_args", lambda: Namespace(
        mes="2026-10", arquivo=None, data_avaliacao="2026-10-05",
        allow_network=True, cdi_auto=True))
    monkeypatch.setattr(forward_partial, "load_active", lambda root: (
        Path("carteira_forward_2026_10.xlsx"), {"month": "2026-10", "version": "test"}))
    monkeypatch.setattr(forward_partial, "load_methodology", lambda version, root: {
        "execution": {"scenario": "TOP15", "unallocated_to": "CDI", "capital_reference": 10000,
                      "min_weight_default": .01, "fractional_default": True, "max_stocks": 15}})
    monkeypatch.setattr(forward_partial, "load_portfolio", lambda path: portfolio)
    monkeypatch.setattr(forward_partial, "read_fields", lambda path, sheet: {
        "data_limite_dados_selecao": "2026-09-30"})
    monkeypatch.setattr(forward_partial, "download_prices", lambda tickers, start, end: prices)
    monkeypatch.setattr(forward_partial, "fetch_cdi_gross_return", lambda start, end: (
        .00100025, cdi, "bcb_sgs_12"))
    monkeypatch.setattr(forward_partial, "EXCEL_DIR", tmp_path)
    monkeypatch.setattr(forward_partial, "LOG_DIR", tmp_path)

    forward_partial.main()
    output = next(tmp_path.glob("parcial_carteira_forward_2026_10_v*.xlsx"))
    summary_frame = pd.read_excel(output, sheet_name="Resumo Parcial")
    summary = dict(zip(summary_frame["metrica"], summary_frame["valor"]))
    assert summary["data_solicitada"] == "2026-10-05"
    assert summary["data_corte_comum"] == summary["data_avaliacao_parcial"] == "2026-10-02"
    assert summary["calculo_oficial"] == "executavel_top15_capital_10000"
    assets = pd.read_excel(output, sheet_name="Ativos")
    assert set(assets["data_avaliacao"]) == {"2026-10-02"}
    executable = pd.read_excel(output, sheet_name="Carteira Executavel")
    assert summary["retorno_modelo_executavel"] == pytest.approx(executable["contribuicao_pratica"].sum())
    assert summary["retorno_carteira_parcial_aplicada"] == summary["retorno_modelo_executavel"]
    assert summary["retorno_bruto_recomendacoes_diagnostico"] != summary["retorno_modelo_executavel"]

    broken = prices.drop(columns=["BBB3.SA"])
    monkeypatch.setattr(forward_partial, "download_prices", lambda tickers, start, end: broken)
    with pytest.raises(RuntimeError, match="BBB3.SA"):
        forward_partial.main()
    assert len(list(tmp_path.glob("parcial_carteira_forward_2026_10_v*.xlsx"))) == 1
