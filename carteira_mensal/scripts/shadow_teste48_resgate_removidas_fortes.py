from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_FILE = EXCEL_DIR / "shadow_teste46_carteira_executavel.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste48_resgate_removidas_fortes.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste48_resgate_removidas_fortes.log"
CAPITAL_BASE = 10_000.0
TOP_N = 15

SCENARIOS = [
    "ATUAL_T46",
    "TOP15_OPERACIONAL",
    "RESGATE_NOTA_FORTE",
    "RESGATE_CONVICCAO_PESO",
    "RESGATE_SCORE_HIBRIDO",
]


def pct(value: Any) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


def compound(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return np.nan
    return float((1 + values).prod() - 1)


def max_drawdown(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return np.nan
    curve = (1 + values).cumprod()
    peak = curve.cummax()
    return float((curve / peak - 1).min())


def minmax(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() == 0:
        return pd.Series(0.0, index=series.index)
    values = values.fillna(values.median())
    lo = float(values.min())
    hi = float(values.max())
    if abs(hi - lo) < 1e-12:
        return pd.Series(0.5, index=series.index)
    return (values - lo) / (hi - lo)


def rank_stocks(stocks: pd.DataFrame) -> pd.DataFrame:
    out = stocks.copy()
    for col in ["nota_final", "peso_executavel_total", "contribuicao_executavel", "beta", "cv", "retorno_periodo"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["_rank_nota"] = out.get("nota_final", pd.Series(index=out.index, dtype=float)).fillna(-999)
    out["_rank_peso"] = out.get("peso_executavel_total", pd.Series(index=out.index, dtype=float)).fillna(0)
    out["_rank_contrib"] = out.get("contribuicao_executavel", pd.Series(index=out.index, dtype=float)).fillna(-999)
    out = out.sort_values(["_rank_nota", "_rank_peso", "_rank_contrib", "ticker"], ascending=[False, False, False, True]).reset_index(drop=True)
    out["rank_top15"] = np.arange(1, len(out) + 1)
    out["score_resgate"] = 0.65 * minmax(out["nota_final"]) + 0.35 * minmax(out["peso_executavel_total"])
    return out


def select_tickers(ranked: pd.DataFrame, scenario: str) -> tuple[list[str], pd.DataFrame]:
    if scenario == "ATUAL_T46":
        chosen = ranked.copy()
        chosen["motivo_resgate"] = "carteira_original"
        chosen["foi_resgatada"] = False
        return chosen["ticker"].astype(str).tolist(), chosen

    chosen = ranked.head(TOP_N).copy()
    chosen["motivo_resgate"] = "top15_original"
    chosen["foi_resgatada"] = False
    removed = ranked.iloc[TOP_N:].copy()
    if scenario == "TOP15_OPERACIONAL" or removed.empty:
        return chosen["ticker"].astype(str).tolist(), chosen

    if scenario == "RESGATE_NOTA_FORTE":
        candidates = removed[(removed["nota_final"] >= 55) & (removed["peso_executavel_total"] >= 0.015)].copy()
        candidates["motivo_resgate"] = "nota>=55_e_peso>=1.5pct"
        order_cols = ["nota_final", "peso_executavel_total", "score_resgate"]
    elif scenario == "RESGATE_CONVICCAO_PESO":
        candidates = removed[((removed["peso_executavel_total"] >= 0.04) & (removed["nota_final"] >= 35) & (removed["rank_top15"] <= 18)) | ((removed["nota_final"] >= 60) & (removed["rank_top15"] <= 19))].copy()
        candidates["motivo_resgate"] = "peso>=4pct_com_nota>=35_rank<=18_ou_nota>=60"
        order_cols = ["peso_executavel_total", "nota_final", "score_resgate"]
    elif scenario == "RESGATE_SCORE_HIBRIDO":
        weakest_score = float(chosen["score_resgate"].min()) if not chosen.empty else 0.0
        candidates = removed[(removed["score_resgate"] >= weakest_score + 0.02) & (removed["rank_top15"] <= 18)].copy()
        candidates["motivo_resgate"] = "score_hibrido_superior_ao_mais_fraco"
        order_cols = ["score_resgate", "nota_final", "peso_executavel_total"]
    else:
        candidates = removed.iloc[0:0].copy()
        order_cols = ["score_resgate"]

    if candidates.empty:
        return chosen["ticker"].astype(str).tolist(), chosen

    candidates = candidates.sort_values(order_cols, ascending=[False] * len(order_cols))
    chosen = chosen.copy()
    for _, cand in candidates.iterrows():
        if cand["ticker"] in set(chosen["ticker"].astype(str)):
            continue
        # Substitui a posicao de menor conviccao operacional, desde que o candidato tenha score maior
        weakest_idx = chosen.sort_values(["score_resgate", "nota_final", "peso_executavel_total"], ascending=[True, True, True]).index[0]
        weakest = chosen.loc[weakest_idx]
        if float(cand["score_resgate"]) <= float(weakest["score_resgate"]):
            continue
        cand_row = cand.copy()
        cand_row["foi_resgatada"] = True
        chosen = chosen.drop(index=weakest_idx)
        chosen = pd.concat([chosen, pd.DataFrame([cand_row])], ignore_index=True)
    chosen = chosen.sort_values(["score_resgate", "nota_final", "peso_executavel_total"], ascending=[False, False, False]).reset_index(drop=True)
    return chosen["ticker"].astype(str).tolist(), chosen


def build_portfolio(month: str, rows: pd.DataFrame, perf_row: pd.Series, scenario: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    stocks = rows[rows["ticker"].astype(str).str.upper().ne("CDI")].copy()
    cdi = rows[rows["ticker"].astype(str).str.upper().eq("CDI")].copy()
    ranked = rank_stocks(stocks)
    selected_tickers, selected_audit = select_tickers(ranked, scenario)
    selected = ranked[ranked["ticker"].astype(str).isin(selected_tickers)].copy()
    selected = selected.merge(selected_audit[["ticker", "motivo_resgate", "foi_resgatada"]], on="ticker", how="left", suffixes=("", "_audit"))
    selected["motivo_resgate"] = selected["motivo_resgate"].fillna("selecionada")
    selected["foi_resgatada"] = selected["foi_resgatada"].fillna(False).astype(bool)

    original_stock_weight = float(pd.to_numeric(stocks["peso_executavel_total"], errors="coerce").sum())
    atual_return = float(pd.to_numeric(perf_row.get("retorno_executavel"), errors="coerce"))
    ibov = float(pd.to_numeric(perf_row.get("retorno_ibov"), errors="coerce"))
    cdi_return = float(pd.to_numeric(cdi["retorno_periodo"], errors="coerce").dropna().iloc[0]) if not cdi.empty else 0.0

    if scenario == "ATUAL_T46":
        selected = ranked.copy()
        selected["cenario"] = scenario
        selected["motivo_resgate"] = "carteira_original"
        selected["foi_resgatada"] = False
        selected["quantidade_cenario"] = pd.to_numeric(selected.get("quantidade"), errors="coerce")
        selected["valor_executado_cenario"] = pd.to_numeric(selected.get("valor_executado"), errors="coerce")
        selected["peso_final"] = pd.to_numeric(selected.get("peso_executavel_total"), errors="coerce")
        selected["contribuicao_final"] = pd.to_numeric(selected.get("contribuicao_executavel"), errors="coerce")
        cdi_value_original = float(pd.to_numeric(cdi.get("valor_executado", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not cdi.empty else 0.0
        cdi_row = pd.DataFrame([{
            "mes": month,
            "cenario": scenario,
            "ticker": "CDI",
            "nome": "Reserva/CDI liquido",
            "setor": "Protecao",
            "tipo_alocacao": "cdi_residual",
            "rank_top15": np.nan,
            "motivo_resgate": "cdi_original",
            "foi_resgatada": False,
            "score_resgate": np.nan,
            "preco_entrada": np.nan,
            "quantidade_cenario": np.nan,
            "valor_executado_cenario": cdi_value_original,
            "peso_final": float(pd.to_numeric(cdi.get("peso_executavel_total", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not cdi.empty else 0.0,
            "retorno_periodo": cdi_return,
            "contribuicao_final": float(pd.to_numeric(cdi.get("contribuicao_executavel", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not cdi.empty else 0.0,
            "nota_final": np.nan,
            "beta": np.nan,
            "cv": np.nan,
        }])
        cols = ["mes", "cenario", "ticker", "nome", "setor", "tipo_alocacao", "rank_top15", "motivo_resgate", "foi_resgatada", "score_resgate", "preco_entrada", "quantidade_cenario", "valor_executado_cenario", "peso_final", "retorno_periodo", "contribuicao_final", "nota_final", "beta", "cv"]
        for col in cols:
            if col not in selected.columns:
                selected[col] = np.nan
        portfolio = pd.concat([selected[cols], cdi_row[cols]], ignore_index=True)
        retorno = atual_return
        meta = {
            "mes": month,
            "cenario": scenario,
            "qtd_acoes": int(len(selected)),
            "qtd_resgatadas": 0,
            "tickers_resgatados": "",
            "qtd_removidas": 0,
            "qtd_top15_original_removidas_por_troca": 0,
            "peso_acoes": original_stock_weight,
            "peso_cdi": float(portfolio.loc[portfolio["ticker"].astype(str).str.upper().eq("CDI"), "peso_final"].sum()),
            "retorno": retorno,
            "retorno_ibov": ibov,
            "alfa_vs_ibov": retorno - ibov,
            "bateu_ibov": retorno > ibov,
            "delta_retorno_vs_atual": 0.0,
        }
        return portfolio, pd.DataFrame(), meta

    weight_base = pd.to_numeric(selected.get("peso_executavel_total"), errors="coerce").fillna(0.0)
    if selected.empty or weight_base.sum() <= 0 or original_stock_weight <= 0:
        selected = selected.iloc[0:0].copy()
        stock_value = 0.0
    else:
        selected["peso_modelo_cenario"] = weight_base / weight_base.sum() * original_stock_weight
        selected["valor_alvo_cenario"] = selected["peso_modelo_cenario"] * CAPITAL_BASE
        selected["preco_entrada"] = pd.to_numeric(selected["preco_entrada"], errors="coerce")
        selected["quantidade_cenario"] = np.floor(selected["valor_alvo_cenario"] / selected["preco_entrada"]).replace([np.inf, -np.inf], np.nan).fillna(0).clip(lower=0).astype(int)
        selected["valor_executado_cenario"] = selected["quantidade_cenario"] * selected["preco_entrada"]
        selected = selected[selected["quantidade_cenario"] > 0].copy()
        stock_value = float(selected["valor_executado_cenario"].sum())

    if not selected.empty:
        selected["cenario"] = scenario
        selected["peso_final"] = selected["valor_executado_cenario"] / CAPITAL_BASE
        selected["retorno_periodo"] = pd.to_numeric(selected["retorno_periodo"], errors="coerce")
        selected["contribuicao_final"] = selected["peso_final"] * selected["retorno_periodo"]

    cdi_value = max(CAPITAL_BASE - stock_value, 0.0)
    cdi_row = pd.DataFrame([{
        "mes": month,
        "cenario": scenario,
        "ticker": "CDI",
        "nome": "Reserva/CDI liquido",
        "setor": "Protecao",
        "tipo_alocacao": "cdi_residual",
        "rank_top15": np.nan,
        "motivo_resgate": "cdi_residual",
        "foi_resgatada": False,
        "score_resgate": np.nan,
        "preco_entrada": np.nan,
        "quantidade_cenario": np.nan,
        "valor_executado_cenario": cdi_value,
        "peso_final": cdi_value / CAPITAL_BASE,
        "retorno_periodo": cdi_return,
        "contribuicao_final": (cdi_value / CAPITAL_BASE) * cdi_return,
        "nota_final": np.nan,
        "beta": np.nan,
        "cv": np.nan,
    }])
    cols = ["mes", "cenario", "ticker", "nome", "setor", "tipo_alocacao", "rank_top15", "motivo_resgate", "foi_resgatada", "score_resgate", "preco_entrada", "quantidade_cenario", "valor_executado_cenario", "peso_final", "retorno_periodo", "contribuicao_final", "nota_final", "beta", "cv"]
    for col in cols:
        if col not in selected.columns:
            selected[col] = np.nan
    portfolio = pd.concat([selected[cols], cdi_row[cols]], ignore_index=True)
    retorno = float(pd.to_numeric(portfolio["contribuicao_final"], errors="coerce").fillna(0).sum())

    original_tickers = set(ranked["ticker"].astype(str).head(TOP_N).tolist()) if scenario != "ATUAL_T46" else set(ranked["ticker"].astype(str).tolist())
    final_tickers = set(selected["ticker"].astype(str).tolist())
    removed = ranked[~ranked["ticker"].astype(str).isin(final_tickers)].copy()
    removed["mes"] = month
    removed["cenario"] = scenario
    removed["foi_removida_final"] = True
    removed["era_top15_original"] = removed["ticker"].astype(str).isin(original_tickers)
    removed["retorno_relativo_vs_ibov"] = pd.to_numeric(removed["retorno_periodo"], errors="coerce") - ibov
    removed["bateu_ibov_ativo"] = pd.to_numeric(removed["retorno_periodo"], errors="coerce") > ibov

    rescued = selected[selected["foi_resgatada"].fillna(False).astype(bool)].copy()
    meta = {
        "mes": month,
        "cenario": scenario,
        "qtd_acoes": int(portfolio[portfolio["ticker"].astype(str).str.upper().ne("CDI")].shape[0]),
        "qtd_resgatadas": int(len(rescued)),
        "tickers_resgatados": ", ".join(rescued["ticker"].astype(str).tolist()) if not rescued.empty else "",
        "qtd_removidas": int(len(removed)) if scenario != "ATUAL_T46" else 0,
        "qtd_top15_original_removidas_por_troca": int(removed["era_top15_original"].sum()) if scenario not in {"ATUAL_T46", "TOP15_OPERACIONAL"} and not removed.empty else 0,
        "peso_acoes": float(portfolio.loc[portfolio["ticker"].astype(str).str.upper().ne("CDI"), "peso_final"].sum()),
        "peso_cdi": float(portfolio.loc[portfolio["ticker"].astype(str).str.upper().eq("CDI"), "peso_final"].sum()),
        "retorno": retorno,
        "retorno_ibov": ibov,
        "alfa_vs_ibov": retorno - ibov,
        "bateu_ibov": retorno > ibov,
        "delta_retorno_vs_atual": retorno - atual_return,
    }
    return portfolio, removed, meta


def summarize(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario, data in monthly.groupby("cenario"):
        data = data.sort_values("mes")
        model = compound(data["retorno"])
        ibov = compound(data["retorno_ibov"])
        rows.append({
            "cenario": scenario,
            "meses": len(data),
            "retorno_modelo": model,
            "retorno_ibov": ibov,
            "alfa_vs_ibov": model - ibov,
            "taxa_acerto": pd.to_numeric(data["bateu_ibov"], errors="coerce").mean(),
            "drawdown": max_drawdown(data["retorno"]),
            "qtd_acoes_media": pd.to_numeric(data["qtd_acoes"], errors="coerce").mean(),
            "qtd_acoes_min": pd.to_numeric(data["qtd_acoes"], errors="coerce").min(),
            "qtd_acoes_max": pd.to_numeric(data["qtd_acoes"], errors="coerce").max(),
            "qtd_resgatadas_total": pd.to_numeric(data["qtd_resgatadas"], errors="coerce").sum(),
            "meses_com_resgate": int((pd.to_numeric(data["qtd_resgatadas"], errors="coerce") > 0).sum()),
            "delta_soma_mensal_vs_atual": pd.to_numeric(data["delta_retorno_vs_atual"], errors="coerce").sum(),
        })
    return pd.DataFrame(rows).sort_values("cenario")


def summarize_year(monthly: pd.DataFrame) -> pd.DataFrame:
    df = monthly.copy()
    df["ano"] = df["mes"].astype(str).str[:4]
    rows = []
    for (scenario, year), data in df.groupby(["cenario", "ano"]):
        model = compound(data["retorno"])
        ibov = compound(data["retorno_ibov"])
        rows.append({
            "cenario": scenario,
            "ano": year,
            "meses": len(data),
            "retorno_modelo": model,
            "retorno_ibov": ibov,
            "alfa_vs_ibov": model - ibov,
            "taxa_acerto": pd.to_numeric(data["bateu_ibov"], errors="coerce").mean(),
            "qtd_acoes_media": pd.to_numeric(data["qtd_acoes"], errors="coerce").mean(),
            "qtd_resgatadas_total": pd.to_numeric(data["qtd_resgatadas"], errors="coerce").sum(),
            "delta_soma_mensal_vs_atual": pd.to_numeric(data["delta_retorno_vs_atual"], errors="coerce").sum(),
        })
    return pd.DataFrame(rows).sort_values(["ano", "cenario"])


def main() -> None:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(INPUT_FILE)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    perf = pd.read_excel(INPUT_FILE, sheet_name="Mes a Mes")
    portfolios = pd.read_excel(INPUT_FILE, sheet_name="Carteiras Executaveis")
    perf["mes"] = perf["mes"].astype(str).str[:7]
    portfolios["mes"] = portfolios["mes"].astype(str).str[:7]

    all_ports = []
    all_removed = []
    monthly_rows = []
    for _, perf_row in perf.sort_values("mes").iterrows():
        month = str(perf_row["mes"])
        rows = portfolios[portfolios["mes"].eq(month)].copy()
        for scenario in SCENARIOS:
            port, removed, meta = build_portfolio(month, rows, perf_row, scenario)
            all_ports.append(port)
            if not removed.empty and scenario != "ATUAL_T46":
                all_removed.append(removed)
            monthly_rows.append(meta)

    portfolio_df = pd.concat(all_ports, ignore_index=True)
    removed_df = pd.concat(all_removed, ignore_index=True) if all_removed else pd.DataFrame()
    monthly = pd.DataFrame(monthly_rows)
    summary = summarize(monthly)
    yearly = summarize_year(monthly)

    rescued = portfolio_df[portfolio_df["foi_resgatada"].fillna(False).astype(bool)].copy()
    if not rescued.empty:
        rescued_summary = rescued.groupby(["cenario", "ticker"], as_index=False).agg(
            vezes_resgatada=("mes", "count"),
            peso_medio=("peso_final", "mean"),
            retorno_medio=("retorno_periodo", "mean"),
            contribuicao_total=("contribuicao_final", "sum"),
        ).sort_values(["cenario", "vezes_resgatada", "contribuicao_total"], ascending=[True, False, False])
    else:
        rescued_summary = pd.DataFrame()

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo", index=False)
        yearly.to_excel(writer, sheet_name="Resumo Ano", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        portfolio_df.to_excel(writer, sheet_name="Carteiras", index=False)
        rescued.to_excel(writer, sheet_name="Resgatadas", index=False)
        rescued_summary.to_excel(writer, sheet_name="Resumo Resgatadas", index=False)
        removed_df.to_excel(writer, sheet_name="Removidas Finais", index=False)

    lines = ["Teste 48 - Top 15 com Regra de Resgate de Removidas Fortes", f"Entrada: {INPUT_FILE.name}", ""]
    for _, row in summary.sort_values("retorno_modelo", ascending=False).iterrows():
        lines.append(
            f"{row['cenario']}: retorno={pct(row['retorno_modelo'])}; IBOV={pct(row['retorno_ibov'])}; "
            f"alfa={pct(row['alfa_vs_ibov'])}; acerto={pct(row['taxa_acerto'])}; drawdown={pct(row['drawdown'])}; "
            f"qtd_media={row['qtd_acoes_media']:.1f}; resgates={int(row['qtd_resgatadas_total'])}; meses_resgate={int(row['meses_com_resgate'])}"
        )
    lines.append("")
    if not rescued_summary.empty:
        lines.append("Principais resgatadas: " + "; ".join(f"{r['cenario']} {r['ticker']} ({int(r['vezes_resgatada'])}x)" for _, r in rescued_summary.head(12).iterrows()))
    lines.extend([f"Arquivo gerado: {OUTPUT_FILE}", f"Log gerado: {LOG_FILE}"])
    LOG_FILE.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

