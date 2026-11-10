from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import streamlit_app_user as app  # noqa: E402

HISTORY = ROOT / "output" / "excel" / "historico_v2_top15_capital.xlsx"
pytestmark = pytest.mark.skipif(not HISTORY.exists(), reason="historico v2 nao gerado")


def monthly() -> pd.DataFrame:
    frame = pd.read_excel(HISTORY, sheet_name="Mes a Mes")
    frame["mes"] = frame["mes"].astype(str).str[:7]
    return frame


def frame_oct() -> float:
    return float(monthly().set_index("mes").loc["2026-10", "retorno"])


def test_history_is_labelled_as_v4_retroactive_backtest():
    notes = dict(zip(*pd.read_excel(HISTORY, sheet_name="Metodologia").iloc[:, :2].T.values))
    assert "forward-13b-v7" in str(notes["versao_metodologia"])
    assert "BACKTEST" in str(notes["aplicacao_retroativa"]) and "minima variancia" in str(notes["regra_pesos"])
    assert (ROOT / "output" / "excel" / "historico_v3_referencia_top15_capital.xlsx").exists()


def test_history_covers_every_month_from_2020_to_october_2026():
    frame = monthly()
    expected = list(pd.period_range("2020-01", "2026-10", freq="M").astype(str))
    assert frame["mes"].tolist() == expected
    assert set(frame["cenario"]) == {"TOP15"} and set(frame["capital"]) == {10000.0}


def test_each_month_is_a_valid_executable_portfolio():
    frame = monthly()
    assert (frame["soma_pesos"] - 1).abs().max() < 1e-8
    assert frame["qtd_acoes"].max() <= app.PLATFORM_MAX_STOCKS
    assert frame["regime_previsto"].isin(["alta", "oportunidade", "queda_leve", "queda_forte"]).all()
    # exposicao coerente com o motor: queda_forte ~30% em acoes; alta/queda_leve bem investidas (o Top 15 e o minimo de 1%
    # mandam parte do peso do modelo para o CDI, como no plano executavel da plataforma)
    strong = frame[frame["regime_previsto"] == "queda_forte"]["peso_acoes"]
    assert strong.max() <= 0.35
    assert frame[frame["regime_previsto"].isin(["alta", "queda_leve"])]["peso_acoes"].min() > 0.60


def test_returns_follow_the_portfolio_rows():
    carteiras = pd.read_excel(HISTORY, sheet_name="Carteiras")
    carteiras["mes"] = carteiras["mes"].astype(str).str[:7]
    by_month = carteiras.groupby("mes")["contribuicao"].sum()
    frame = monthly().set_index("mes")
    assert (by_month - frame["retorno"]).abs().max() < 1e-9


def test_october_history_is_the_v4_backtest_while_the_issued_v2_partial_keeps_its_own_result():
    """O historico e a v4 aplicada retroativamente (backtest): outubro difere da carteira v2 que foi de fato emitida e fechou -11,43%."""
    frame = monthly().set_index("mes")
    partial = sorted((ROOT / "output" / "excel").glob("parcial_carteira_forward_2026_10_v*.xlsx"))[-1]
    summary = dict(zip(*pd.read_excel(partial, sheet_name="Resumo Parcial").iloc[:, :2].T.values))
    assert "fechamento" in str(summary["status"])
    assert float(summary["retorno_modelo_executavel"]) == pytest.approx(-0.1143, abs=3e-4)       # resultado da carteira v2 emitida
    assert frame.loc["2026-10", "retorno"] != pytest.approx(float(summary["retorno_modelo_executavel"]), abs=2e-4)
    assert frame.loc["2026-10", "retorno_ibov"] == pytest.approx(float(summary["retorno_ibov_parcial"]), abs=1e-6)


def test_platform_reads_the_v2_history_and_keeps_its_structure():
    files = app.files()
    assert files.operational is not None and files.operational.name == "historico_v2_top15_capital.xlsx"
    history = app.monthly(files)
    months = history["mes"].astype(str).str[:7].tolist()
    assert months[0] == "2020-01" and "2026-10" in months and months == sorted(months)
    assert {"retorno_modelo", "retorno_expost_ibov"}.issubset(history.columns)
    row = history[history["mes"].astype(str).str[:7] == "2026-10"].iloc[0]
    assert row["retorno_modelo"] == pytest.approx(frame_oct(), abs=1e-9)


def test_one_share_rule_is_applied_to_every_historical_month():
    carteiras = pd.read_excel(HISTORY, sheet_name="Carteiras")
    stocks = carteiras[carteiras["tipo_linha"] == "acao"]
    assert (stocks["quantidade"] >= 1).all()
    assert (stocks["valor_executado"] >= stocks["preco_entrada"] - 1e-9).all()          # cada posicao paga ao menos 1 acao
    audit = pd.read_excel(HISTORY, sheet_name="Auditoria")
    assert "removidas_por_inviabilidade_1_acao" in audit.columns
    removed = audit["removidas_por_inviabilidade_1_acao"].fillna("").astype(str)
    assert (removed != "").sum() >= 1                                                   # com os pesos da v4 as posicoes sao maiores: a regra atua em poucos meses
    assert "EMBJ3" in removed[audit["mes"].astype(str) == "2024-10"].iloc[0]           # exemplo conferido (v7, preco nominal)
    assert "HAPV3 (R$ 43.10 < R$ 62.14)" in removed[audit["mes"].astype(str) == "2020-10"].iloc[0]   # preco da epoca: o ajustado por grupamento mostrava R$ 192
