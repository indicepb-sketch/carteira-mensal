"""Historico mensal da carteira refeito pelo motor de producao (metodologia forward-13b-v6: fundamentos da CVM).

Gera `output/excel/historico_v2_top15_capital.xlsx` no mesmo formato do T49 (cenario TOP15, R$ 10 mil),
para 2020-01..2026-10, de modo que o historico exibido na plataforma e nos relatorios seja produzido pelo
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
import openpyxl
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
import perfil as profiles  # noqa: E402

EXCEL_DIR = ROOT / "output" / "excel"
CACHE_DIR = ROOT / "data" / "history_cache"
ADJ_CACHE = CACHE_DIR / "precos_ajustados.csv"
OUT = EXCEL_DIR / "historico_v2_top15_capital.xlsx"
IBOV_CSV = ROOT / "data" / "processed" / "ibov_mensal_oficial.csv"
CDI_CSV = ROOT / "data" / "processed" / "cdi_mensal_ipeadata.csv"
FIRST, LAST = "2020-01", "2026-10"
IBOV_DAILY_CSV = CACHE_DIR / "ibov_diario.csv"
CAPITAL, MIN_WEIGHT, MAX_STOCKS, CDI_IR = 10_000.0, 0.01, 15, 0.225
VERSION = "forward-13b-v7 (nota v2 + regra de 1 acao + pesos de minima variancia + regime de defesa v5 + fundamentos da CVM + funil de 35 candidatas; perfis Moderado e Arrojado; aplicada retroativamente a todo o historico)"


def funnel_workbook(source: Path, month: str, funnel: int) -> Path:
    """Base mensal com o funil de `funnel` candidatas (maiores retornos no ano; desempate pela nota final).

    As bases historicas guardam as 25 candidatas do coletor. A aba 'Analise Preliminar' tem todas as acoes com todas as colunas, na mesma ordem do funil do coletor
    (conferido: as 25 primeiras reproduzem a aba 'Candidatas Risco' nos 82 meses), entao as `funnel` primeiras sao exatamente as que o coletor produziria com esse tamanho.
    O arquivo derivado (..._c35.xlsx) nao e versionado.
    """
    target = source.with_name(source.stem + f"_c{funnel}.xlsx")
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    prelim = pd.read_excel(source, sheet_name="Analise Preliminar")
    ordered = prelim.assign(_k=pd.to_numeric(prelim["retorno_ytd"], errors="coerce")).sort_values(["_k", "nota_final"], ascending=[False, False]).head(funnel)
    book = openpyxl.load_workbook(source)
    for sheet in ("Candidatas Risco", "Otimizacao"):
        ws = book[sheet]
        header = [c.value for c in ws[1]]
        ws.delete_rows(2, ws.max_row)
        for _, row in ordered.iterrows():
            values = []
            for name in header:
                value = row.get(name)
                if isinstance(value, np.generic):
                    value = value.item()
                values.append(None if (value is None or (not isinstance(value, (list, dict)) and pd.isna(value))) else (value.to_pydatetime() if isinstance(value, pd.Timestamp) else value))
            ws.append(values)
    book.save(target)
    return target


def month_workbook(month: str) -> Path:
    year, mon = month.split("-")
    cvm_ttm = EXCEL_DIR / f"carteira_historica_{year}_{mon}_corrigida2.xlsx"        # v6: fundamentos da CVM em 12 meses moveis, base do controlador (sem olhar o futuro)
    if cvm_ttm.exists():
        funnel = ft.candidate_funnel_size(month, force=True)
        return funnel_workbook(cvm_ttm, month, funnel) if funnel and funnel != 25 else cvm_ttm
    corrected = EXCEL_DIR / f"carteira_historica_{year}_{mon}_corrigida.xlsx"      # corte no ultimo pregao do mes anterior; fundamentos da base anterior
    if corrected.exists():
        return corrected
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


def benchmark_2021(month: str) -> tuple[float, float, str]:
    """IBOV (do IBOV diario) e CDI liquido (serie IPEA, outubro interpolado) para os meses anteriores a 2022."""
    close = pd.read_csv(IBOV_DAILY_CSV, index_col=0, parse_dates=True)["close"]
    monthly = close.groupby(close.index.to_period("M")).last()
    monthly.index = monthly.index.astype(str)
    previous = str(pd.Period(month, "M") - 1)
    cdi = pd.read_csv(CDI_CSV).set_index("mes")["cdi_bruto_mensal"].astype(float)
    cdi = cdi.reindex(pd.period_range(cdi.index.min(), cdi.index.max(), freq="M").astype(str)).interpolate()
    return float(monthly[month] / monthly[previous] - 1), float(cdi[month]) * (1 - CDI_IR), "IBOV diario + serie IPEA (2021)"


def benchmark_returns(month: str) -> tuple[float, float, str]:
    if month < "2022-01":
        return benchmark_2021(month)
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
    wanted = "todos"
    if "--perfil" in sys.argv:
        wanted = sys.argv[sys.argv.index("--perfil") + 1].lower()
    available = profiles.profiles_for_month(LAST, ROOT, force=True)
    for perfil in (available if wanted == "todos" else [wanted]):
        if perfil not in available:
            raise SystemExit(f"Perfil {perfil!r} inexistente na metodologia vigente (disponiveis: {available}).")
        ft.set_profile(perfil)
        try:
            build_history(perfil)
        finally:
            ft.set_profile("moderado")


def build_history(perfil: str) -> None:
    out = OUT if perfil == profiles.DEFAULT_PROFILE else profiles.history_file(perfil, ROOT)
    out.parent.mkdir(parents=True, exist_ok=True)
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
        "nota", "fundamentos", "precos_e_retornos", "cdi_e_ibov", "bases_mensais", "ordem_de_grandeza", "regra_1_acao", "regra_pesos", "aplicacao_retroativa", "perfil", "funil_de_entrada"],
        "valor": [VERSION, f"{datetime.now():%Y-%m-%d %H:%M}", f"{FIRST} a {LAST}", "Top 15, minimo de 1%, quantidades inteiras, R$ 10 mil, saldo no CDI",
                  "13B conservador do motor forward (amplitude de tendencia; demais entradas so existem a partir de 2026)",
                  "5 componentes (tendencia, timing, fundamentos, setor, risco); colunas ausentes completadas da aba Analise Preliminar da mesma base",
                  "balancos da CVM (DFP/ITR), 12 meses moveis, base do controlador, ROE sobre PL medio e ROIC = NOPAT/(passivo oneroso + PL); so o que ja tinha sido divulgado na data (sem olhar o futuro). Em jan-mai/2020 a quantidade de acoes usa o primeiro arquivo da CVM com esse dado (so o P/L desses meses olha um pouco adiante)",
                  "yfinance ajustado: entrada no fechamento da data de selecao, saida no ultimo pregao do mes",
                  "CDI liquido de IR 22,5% (IPEA ate jul/2026; depois parcial fechada) e IBOV oficial (CSV; depois parcial fechada)",
                  "Bases mensais de 2020-2026 geradas com corte no ultimo pregao do mes anterior (corrigidas); campos de amplitude ausentes reconstruidos da aba Analise Preliminar para que as regras do regime valham igualmente em todos os anos",
                  "Substitui o historico anterior (T49 ate jun/2026 + parciais): ver docs/metodologia_forward_13b_v2.md",
                  "Cada posicao precisa comportar pelo menos 1 acao no capital de referencia (R$ 10 mil); as que nao comportam saem da carteira e o peso e redistribuido proporcionalmente (aba Auditoria)",
                  "pesos de minima variancia sobre as acoes escolhidas e regime de defesa da v5, com as mesmas regras do classificador em todos os anos (campos ausentes de 2022-2025 reconstruidos da aba Analise Preliminar)",
                  "ATENCAO: a v5 foi escolhida depois de testes de sombra sobre estes mesmos meses; o historico e um BACKTEST retroativo, nao desempenho operado. Referencias anteriores: historico_v3_referencia_top15_capital.xlsx e historico_v4_referencia_top15_capital.xlsx", f"{profiles.display_name(perfil)} ({perfil})", "25 candidatas ate a v6; 35 candidatas (maiores retornos no ano) na v7"]})
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo Capital", index=False)
        by_year.to_excel(writer, sheet_name="Resumo Ano", index=False)
        by_regime.to_excel(writer, sheet_name="Resumo Regime", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)
        portfolios.to_excel(writer, sheet_name="Carteiras", index=False)
        pd.DataFrame(audits).to_excel(writer, sheet_name="Auditoria", index=False)
        notes.to_excel(writer, sheet_name="Metodologia", index=False)
    comp = lambda s: float((1 + s).prod() - 1)  # noqa: E731
    print(f"\nArquivo: {out}\nPerfil: {perfil}\nMeses: {len(monthly)} | modelo {comp(monthly['retorno']):+.1%} | IBOV {comp(monthly['retorno_ibov']):+.1%}")


if __name__ == "__main__":
    main()
