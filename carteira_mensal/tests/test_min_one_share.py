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
from production_registry import load_active  # noqa: E402


def frame(rows):
    return pd.DataFrame(rows, columns=["ticker", "peso_recomendado", "preco_entrada_fechamento_mes_anterior"])


def test_position_that_cannot_buy_one_share_is_removed_and_weight_redistributed():
    portfolio = frame([("AAA3.SA", 0.60, 10.0), ("BBB3.SA", 0.395, 50.0), ("CCC3.SA", 0.005, 20.47)])
    kept, removed = ft.drop_unbuyable_positions(portfolio, exposure=0.30, capital=10_000.0)
    assert [r["ticker"] for r in removed] == ["CCC3.SA"]
    assert removed[0]["valor_alvo"] == pytest.approx(0.005 * 0.30 * 10_000.0) and removed[0]["valor_alvo"] < 20.47
    assert kept["ticker"].tolist() == ["AAA3.SA", "BBB3.SA"]
    assert kept["peso_recomendado"].sum() == pytest.approx(1.0)                       # soma do modelo preservada
    assert kept["peso_recomendado"].iloc[0] / kept["peso_recomendado"].iloc[1] == pytest.approx(0.60 / 0.395)


def test_rule_is_exactly_one_share_not_a_percentage_floor():
    # 0,8% de R$ 10 mil = R$ 80: compra 1 acao de R$ 5 (permanece) mas nao uma de R$ 90 (sai)
    portfolio = frame([("AAA3.SA", 0.992, 100.0), ("BARATA3.SA", 0.004, 5.0), ("CARA3.SA", 0.004, 90.0)])
    kept, removed = ft.drop_unbuyable_positions(portfolio, exposure=1.0, capital=10_000.0)
    assert set(kept["ticker"]) == {"AAA3.SA", "BARATA3.SA"}
    assert [r["ticker"] for r in removed] == ["CARA3.SA"]


def test_nothing_changes_when_every_position_is_buyable_or_price_is_unknown():
    portfolio = frame([("AAA3.SA", 0.5, 10.0), ("BBB3.SA", 0.5, np.nan)])
    kept, removed = ft.drop_unbuyable_positions(portfolio, exposure=1.0, capital=10_000.0)
    assert removed == [] and kept["peso_recomendado"].tolist() == [0.5, 0.5]


def test_rule_applies_from_the_v3_effective_month_only():
    assert ft.min_one_share_capital("2026-10") is None
    assert ft.min_one_share_capital("2026-11") == 10_000.0
    assert ft.min_one_share_capital("2027-01") == 10_000.0


def test_official_november_portfolio_only_has_buyable_positions_and_no_flry3():
    path, manifest = load_active(ROOT)
    assert manifest["month"] == "2026-11" and manifest["version"] == "forward-13b-v3"
    carteira = pd.read_excel(path, sheet_name="Carteira Aplicada")
    stocks = carteira[carteira["ticker"] != "CDI"]
    assert "FLRY3.SA" not in set(stocks["ticker"])
    shares = np.floor(stocks["peso_recomendado"] * 10_000.0 / stocks["preco_entrada_fechamento_mes_anterior"])
    assert (shares >= 1).all()
    assert carteira["peso_recomendado"].sum() == pytest.approx(1.0)
    summary = dict(zip(*pd.read_excel(path, sheet_name="Resumo Forward").iloc[:, :2].T.values))
    assert "FLRY3.SA" in str(summary["posicoes_removidas_por_inviabilidade"])
    assert pd.read_excel(path, sheet_name="Validacao")["ok"].all()
