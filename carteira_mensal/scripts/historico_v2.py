"""Historico mensal da carteira refeito pelo motor de producao (metodologia forward-13b-v2).

Gera `output/excel/historico_v2_top15_capital.xlsx` no mesmo formato do T49 (cenario TOP15, R$ 10 mil),
para 2022-01..2026-10, de modo que o historico exibido na plataforma e nos relatorios seja produzido pelo
MESMO motor que forma as carteiras oficiais (nota com 5 componentes, regime 13B conservador, Sinal A / V3,
Top 15, minimo de 1%, quantidades inteiras).

Como cada mes e refeito
- Carteira: `forward_test.run_forward_portfolio(..., force_completion=True)` sobre a base de formacao do mes
  (dados ate o ultimo pregao do mes anterior; sem informacao posterior).
- Execucao: `portfolio_execution.executable_portfolio` (a mesma da plataforma), preco de entrada = fechamento
  ajustado na data de selecao; retorno = mesma serie ajustada (fechamento do ultimo pregao do mes).
- CDI liquido (IR 22,5%) e IBOV oficiais, como na plataforma.
Limitacoes registradas na aba 'Metodologia'. Uso: python scripts/historico_v2.py [--offline]
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import forward_test as ft  # noqa: E402
import shadow_backtest_2025 as bt25  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
import shadow_teste49_top15_regime_capital as t49  # noqa: E402
from portfolio_execution import executable_portfolio, is_cdi  # noqa: E402
from utils import load_settings  # noqa: E402

EXCEL_DIR = ROOT / "output" / "excel"
CACHE_DIR = ROOT / "data" / "history_cache"
ADJ_CACHE = CACHE_DIR / "precos_ajustados.csv"
OUT = EXCEL_DIR / "historico_v2_top15_capital.xlsx"
IBOV_CSV = ROOT / "data" / "processed" / "ibov_mensal_oficial.csv"
CDI_CSV = ROOT / "data" / "processed" / "cdi_mensal_ipeadata.csv"
FIRST, LAST = "2022-01", "2026-10"
CAPITAL, MIN_WEIGHT, MAX_STOCKS, CDI_IR = 10_000.0, 0.01, 15, 0.225
VERSION = "forward-13b-v5 (nota v2 + regra de 1 acao + pesos de minima variancia + regime de defesa v5; aplicada retroativamente a todo o historico)"


def month_workbook(month: str) -> Path:
    year, mon = month.split("-")
    historic = EXCEL_DIR / f"carteira_historica_{year}_{mon}.xlsx"
    if historic.exists():
        return historic
    found = sorted(EXCEL_DIR.glob(f"carteira_recomendada_{year}_{mon}_v*.xlsx"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise FileNotFoundError(f"Base de formacao de {month} ausente")
    return found[-1]


def selection_cutoff(month: str) -> pd.Timestamp:
    return pd.Timestamp(f"{month}-01") - pd.Timedelta(days=1)


def month_end(month: str) -> pd.Timestamp:
    return pd.Timestamp(f"{month}-01") + pd.offsets.MonthEnd(0)


def load_prices(tickers: list[str], allow_network: bool) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    wide = pd.read_csv(ADJ_CACHE, index_col=0, parse_dates=True) if ADJ_CACHE.exists() else pd.DataFrame()
    missing = [t for t in tickers if t not in wide.columns]
    stale = (not wide.empty) and wide.index.max() < pd.Timestamp("2026-10-28")
    if (missing or stale or wide.empty) and allow_network:
        import yfinance as yf

        raw = yf.download(tickers, start="2021-06-01", end="2026-11-03", auto_adjust=False, progress=False, group_by="column", threads=True)
        adj = raw["Adj Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
        adj.index = pd.to_datetime(adj.index).tz_localize(None)
        wide = adj.astype(float).sort_index()
        wide.to_csv(ADJ_CACHE)
    if wide.empty:
        raise RuntimeError("Sem precos ajustados (cache vazio e rede desativada)")
    return wide


def last_price(series: pd.Series, date: pd.Timestamp) -> float:
    window = series[series.index <= date].dropna()
    return float(window.iloc[-1]) if len(window) else np.nan


def official_closed_partial(month: str) -> dict[str, Any]:
    """Valores oficiais de IBOV e CDI liquido de uma parcial fechada do mes (usado quando o CSV mensal nao cobre o mes)."""
    best = None
    for path in sorted(EXCEL_DIR.glob(f"parcial_carteira_forward_{month.replace('-', '_')}_v*.xlsx")):
        frame = pd.read_excel(path, sheet_name="Resumo Parcial")
        values = dict(zip(frame["metrica"].astype(str), frame["valor"]))
        if "fechamento" in str(values.get("status", "")):
            best = values
    return best or {}


def t49_cdi_fallback(month: str) -> float:
    """CDI liquido do mes gravado no T49 (a serie mensal do IPEA nao tem os meses de outubro); mesma reserva que a plataforma usa."""
    frame = pd.read_excel(EXCEL_DIR / "shadow_teste49_top15_regime_capital.xlsx", sheet_name="Carteiras")
    frame["mes"] = frame["mes"].astype(str).str[:7]
    rows = frame[(frame["mes"] == month) & (frame["cenario"] == "TOP15") & (frame["capital"] == CAPITAL) & (frame["ticker"].astype(str).str.upper() == "CDI")]
    return float(rows["retorno_periodo"].iloc[0]) if len(rows) else np.nan


def benchmark_returns(month: str) -> tuple[float, float, str]:
    ibov = pd.read_csv(IBOV_CSV)
    ibov["mes"] = ibov["mes"].astype(str).str[:7]
    cdi = pd.read_csv(CDI_CSV)
    cdi["mes"] = cdi["mes"].astype(str).str[:7]
    official = official_closed_partial(month)
    row_i, row_c = ibov[ibov["mes"] == month], cdi[cdi["mes"] == month]
    ibov_ret = float(row_i["retorno_ibov_oficial"].iloc[0]) if len(row_i) else float(official.get("retorno_ibov_parcial", np.nan))
    if len(row_c):
        return ibov_ret, float(row_c["cdi_bruto_mensal"].iloc[0]) * (1 - CDI_IR), "csv"
    cdi_value = float(official.get("retorno_cdi_liquido_periodo", np.nan))
    if np.isnan(cdi_value):
        return ibov_ret, t49_cdi_fallback(month), "T49 (CDI)"
    return ibov_ret, cdi_value, "parcial fechada"


def form_month(month: str, settings: dict, prices: pd.DataFrame) -> dict[str, Any]:
    workbook = month_workbook(month)
    result = ft.run_forward_portfolio(month, workbook, settings, force_completion=True)
    portfolio = result["portfolio"].copy()
    exposure, _ = ft.exposure_for_metrics(result.get("metrics", {}), month, force=True)
    portfolio["ticker"] = portfolio["ticker"].astype(str).str.upper()
    cutoff, end = selection_cutoff(month), month_end(month)
    portfolio["preco_entrada_fechamento_mes_anterior"] = portfolio["ticker"].map(lambda t: last_price(prices[t], cutoff) if t in prices.columns else np.nan)
    # regra de viabilidade (v3), aplicada a TODO o historico: posicao que nao comporta 1 acao no capital de referencia nao entra na
    # carteira e o peso e redistribuido proporcionalmente (mesma funcao usada na formacao oficial)
    # v4: pesos de minima variancia (Ledoit-Wolf) sobre as acoes escolhidas, com precos ate a data de selecao (sem informacao posterior)
    portfolio, _weighting = ft.apply_weighting(portfolio, month, cutoff, prices=prices, force=True)
    portfolio, removed = ft.drop_unbuyable_positions(portfolio, exposure, CAPITAL)
    portfolio["peso_recomendado"] = pd.to_numeric(portfolio["peso_recomendado"], errors="coerce").fillna(0.0) * exposure
    portfolio["preco_fim"] = portfolio["ticker"].map(lambda t: last_price(prices[t], end) if t in prices.columns else np.nan)
    portfolio["retorno_periodo"] = portfolio["preco_fim"] / portfolio["preco_entrada_fechamento_mes_anterior"] - 1
    return {"portfolio": portfolio, "bucket": str(result.get("metrics", {}).get("forward_13b_bucket", "")), "exposure": float(exposure),
            "workbook": workbook.name, "removed_unbuyable": removed}


def build_rows(month: str, formed: dict[str, Any], ibov: float, cdi: float) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    portfolio = formed["portfolio"]
    usable = portfolio[portfolio["preco_entrada_fechamento_mes_anterior"].gt(0) & portfolio["retorno_periodo"].notna()].copy()
    dropped = int(len(portfolio) - len(usable))
    executable, removed, totals = executable_portfolio(usable, CAPITAL, MIN_WEIGHT, True, MAX_STOCKS)
    stocks = executable[~executable.apply(is_cdi, axis=1)].copy()
    stocks["mes"], stocks["cenario"], stocks["capital"], stocks["tipo_linha"] = month, "TOP15", CAPITAL, "acao"
    stocks["rank_operacional"] = np.arange(1, len(stocks) + 1)
    stocks["preco_entrada"] = stocks["preco_referencia"]
    stocks["valor_executado"] = stocks["valor_estimado"]
    stocks["peso_final"] = stocks["peso_executavel"]
    stocks["contribuicao"] = stocks["peso_final"] * stocks["retorno_periodo"]
    cdi_value = float(totals["cdi_value"])
    cdi_row = {"mes": month, "cenario": "TOP15", "capital": CAPITAL, "ticker": "CDI", "nome": "Reserva/CDI liquido", "setor": "Protecao", "tipo_linha": "cdi",
               "rank_operacional": np.nan, "preco_entrada": np.nan, "quantidade": np.nan, "valor_alvo": cdi_value, "valor_executado": cdi_value,
               "peso_final": cdi_value / CAPITAL, "retorno_periodo": cdi, "contribuicao": cdi_value / CAPITAL * cdi, "nota_final": np.nan, "beta": np.nan, "cv": np.nan}
    cols = ["mes", "cenario", "capital", "ticker", "nome", "setor", "tipo_linha", "rank_operacional", "preco_entrada", "quantidade", "valor_alvo",
            "valor_executado", "peso_final", "retorno_periodo", "contribuicao", "nota_final", "beta", "cv"]
    for col in cols:
        if col not in stocks.columns:
            stocks[col] = np.nan
    table = pd.concat([stocks[cols], pd.DataFrame([cdi_row])[cols]], ignore_index=True)
    ret = float(table["contribuicao"].fillna(0).sum())
    stock_rows = table[table["tipo_linha"] == "acao"]
    label = "queda_forte" if ibov <= -0.03 else ("queda_leve" if ibov < 0 else "alta")
    meta = {"mes": month, "cenario": "TOP15", "capital": CAPITAL, "tipo_regime_expost": label, "regime_previsto": formed["bucket"],
            "qtd_acoes": int(len(stock_rows)), "valor_acoes": float(stock_rows["valor_executado"].sum()), "valor_cdi": cdi_value,
            "peso_acoes": float(stock_rows["peso_final"].sum()), "peso_cdi": cdi_value / CAPITAL, "retorno": ret, "retorno_ibov": ibov,
            "alfa_vs_ibov": ret - ibov, "bateu_ibov": ret > ibov,
            "max_acoes_setor": int(stock_rows.groupby("setor")["ticker"].count().max()) if len(stock_rows) else 0,
            "max_peso_setor": float(stock_rows.groupby("setor")["peso_final"].sum().max()) if len(stock_rows) else 0.0,
            "soma_pesos": float(table["peso_final"].sum()), "ano": month[:4], "capital_label": t49.money(CAPITAL)}
    audit = {"mes": month, "base_formacao": formed["workbook"], "regime": formed["bucket"], "exposicao": formed["exposure"],
             "acoes_modelo": int(len(portfolio)), "acoes_sem_preco": dropped, "removidas_top15_ou_minimo": int(len(removed)),
             "removidas_por_inviabilidade_1_acao": "; ".join(f"{r['ticker'].replace('.SA', '')} (R$ {r['valor_alvo']:.2f} < R$ {r['preco']:.2f})"
                                                              for r in formed.get("removed_unbuyable", [])) or ""}
    return table, meta, audit


def main() -> None:
    allow_network = "--offline" not in sys.argv
    months = list(pd.period_range(FIRST, LAST, freq="M").astype(str))
    settings = load_settings()
    universe = set()
    for month in months:
        book = pd.read_excel(month_workbook(month), sheet_name="Analise Preliminar", usecols=["ticker"])
        universe |= set(book["ticker"].astype(str).str.upper())
    prices = load_prices(sorted(universe), allow_network)

    original_loader = bt25.patch_sector_enrichment(bt25.load_sector_map())   # setor das bases historicas (como nos backtests)
    tables, metas, audits = [], [], []
    try:
        for month in months:
            ibov, cdi, source = benchmark_returns(month)
            if np.isnan(ibov) or np.isnan(cdi):
                raise RuntimeError(f"IBOV/CDI indisponivel para {month}")
            formed = form_month(month, settings, prices)
            table, meta, audit = build_rows(month, formed, ibov, cdi)
            audit["fonte_cdi_ibov"] = source
            tables.append(table)
            metas.append(meta)
            audits.append(audit)
            print(f"{month}: {formed['bucket']:<12} exp={formed['exposure']:.0%} acoes={meta['qtd_acoes']:>2} ret={meta['retorno']:+.2%} ibov={ibov:+.2%}", flush=True)
    finally:
        sh.load_candidate_input = original_loader

    monthly = pd.DataFrame(metas)
    portfolios = pd.concat(tables, ignore_index=True)
    summary = t49.summarize(monthly.groupby(["cenario", "capital"]), ["cenario", "capital"])
    by_year = t49.summarize(monthly.groupby(["cenario", "capital", "ano"]), ["cenario", "capital", "ano"])
    by_regime = t49.summarize(monthly.groupby(["cenario", "capital", "tipo_regime_expost"]), ["cenario", "capital", "tipo_regime_expost"])
    validation = monthly.copy()
    validation["pesos_ok"] = (pd.to_numeric(validation["soma_pesos"], errors="coerce") - 1.0).abs() <= 1e-8
    validation["qtd_ok_top15"] = validation["qtd_acoes"] <= MAX_STOCKS
    notes = pd.DataFrame({"item": [
        "versao_metodologia", "gerado_em", "periodo", "execucao", "regime",
        "nota", "fundamentos_jan_jun_2026", "precos_e_retornos", "cdi_e_ibov", "limitacao_2022_2025", "ordem_de_grandeza", "regra_1_acao", "regra_pesos", "aplicacao_retroativa"],
        "valor": [VERSION, f"{datetime.now():%Y-%m-%d %H:%M}", f"{FIRST} a {LAST}", "Top 15, minimo de 1%, quantidades inteiras, R$ 10 mil, saldo no CDI",
                  "13B conservador do motor forward (amplitude de tendencia; demais entradas so existem a partir de 2026)",
                  "5 componentes (tendencia, timing, fundamentos, setor, risco); colunas ausentes completadas da aba Analise Preliminar da mesma base",
                  "fundamentos do Fundamentus coletados depois da formacao (look-ahead leve); validos a partir de jul/2026",
                  "yfinance ajustado: entrada no fechamento da data de selecao, saida no ultimo pregao do mes",
                  "CDI liquido de IR 22,5% (IPEA ate jul/2026; depois parcial fechada) e IBOV oficial (CSV; depois parcial fechada)",
                  "Em 2022-2025 o regime usa so a amplitude de tendencia, pois as bases historicas nao guardam as demais entradas do 13B",
                  "Substitui o historico anterior (T49 ate jun/2026 + parciais): ver docs/metodologia_forward_13b_v2.md",
                  "Cada posicao precisa comportar pelo menos 1 acao no capital de referencia (R$ 10 mil); as que nao comportam saem da carteira e o peso e redistribuido proporcionalmente (aba Auditoria)",
                  "pesos de minima variancia sobre as acoes escolhidas e regime de defesa da v5, com as mesmas regras do classificador em todos os anos (campos ausentes de 2022-2025 reconstruidos da aba Analise Preliminar)",
                  "ATENCAO: a v5 foi escolhida depois de testes de sombra sobre estes mesmos meses; o historico e um BACKTEST retroativo, nao desempenho operado. Referencias anteriores: historico_v3_referencia_top15_capital.xlsx e historico_v4_referencia_top15_capital.xlsx"]})
    with pd.ExcelWriter(OUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo Capital", index=False)
        by_year.to_excel(writer, sheet_name="Resumo Ano", index=False)
        by_regime.to_excel(writer, sheet_name="Resumo Regime", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)
        portfolios.to_excel(writer, sheet_name="Carteiras", index=False)
        pd.DataFrame(audits).to_excel(writer, sheet_name="Auditoria", index=False)
        notes.to_excel(writer, sheet_name="Metodologia", index=False)
    comp = lambda s: float((1 + s).prod() - 1)  # noqa: E731
    print(f"\nArquivo: {OUT}\nMeses: {len(monthly)} | modelo {comp(monthly['retorno']):+.1%} | IBOV {comp(monthly['retorno_ibov']):+.1%}")


if __name__ == "__main__":
    main()
