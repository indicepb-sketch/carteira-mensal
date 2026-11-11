"""CXSE3 (Caixa Seguridade) e acao: o nome com 'caixa' nao pode ser confundido com reserva/CDI."""
import pandas as pd

from portfolio_execution import executable_portfolio, is_cdi


def test_only_real_cash_lines_are_treated_as_cdi():
    assert not is_cdi(pd.Series({"ticker": "CXSE3.SA", "nome": "CAIXA SEGURI", "setor": "Previdencia e Seguros"}))
    assert is_cdi(pd.Series({"ticker": "CDI", "nome": "CDI / Tesouro Selic liquido", "setor": "Renda fixa"}))
    assert is_cdi(pd.Series({"ticker": "X", "nome": "Reserva/CDI liquido", "setor": "Protecao"}))


def test_caixa_seguridade_stays_in_the_executable_plan():
    frame = pd.DataFrame([
        {"ticker": "CXSE3.SA", "nome": "CAIXA SEGURI", "setor": "Previdencia e Seguros", "peso_recomendado": 0.5, "preco_entrada_fechamento_mes_anterior": 20.0, "nota_final": 80},
        {"ticker": "WEGE3.SA", "nome": "WEG", "setor": "Maquinas", "peso_recomendado": 0.5, "preco_entrada_fechamento_mes_anterior": 50.0, "nota_final": 80},
    ])
    plan, removed, totals = executable_portfolio(frame, 10000.0, 0.01, True)
    assert set(plan["ticker"]) == {"CXSE3.SA", "WEGE3.SA", "CDI"} and totals["stock_value"] > 9990.0
