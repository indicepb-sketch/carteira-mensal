from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import shadow_regime_16_risk_on_off as r16


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_EXPOST = EXCEL_DIR / "shadow_regime_16_risk_on_off.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste29_vencedoras_barradas_funil.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste29_vencedoras_barradas_funil.log"

KEY_COLS = [
    "ticker",
    "nome",
    "setor",
    "subsetor",
    "decisao_preliminar_ajustada",
    "motivo_decisao_preliminar",
    "status_para_risco",
    "motivo_status_para_risco",
    "categoria_elegibilidade",
    "tipo_timing",
    "sinal_timing",
    "tendencia_mensal",
    "contexto_estrutural",
    "leitura_forca_relativa_mensal",
    "classificacao_forca_relativa",
    "qualidade_fundamentalista",
    "risco_fundamentalista_mensal",
    "fundamento_bloqueante",
    "motivo_fundamento_bloqueante",
    "watchlist_qualificada",
    "motivo_watchlist_qualificada",
    "bloqueado_otimizacao",
    "motivo_bloqueio_otimizacao",
    "tipo_bloqueio_otimizacao",
    "liberado_para_otimizacao",
    "alertas_nao_bloqueantes",
    "penalizacoes_otimizacao",
    "nota_final",
    "score_prioridade_otimizacao",
    "retorno_acumulado_1m",
    "retorno_acumulado_4m",
    "retorno_YTD",
    "retorno_ytd_ret",
    "forca_relativa_score",
    "rsi",
    "bollinger_status",
    "distancia_banda_superior_pct",
    "distancia_banda_inferior_pct",
    "beta",
    "correlacao_ibov",
    "retorno_medio",
    "desvio_padrao",
    "cv",
    "peso_final",
    "peso_recomendado",
]


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "sim", "yes", "verdadeiro"}


def safe_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def workbook_path(mes: str) -> Path:
    return r16.workbook_path(mes)


def read_preliminar(mes: str) -> pd.DataFrame:
    path = workbook_path(mes)
    frame = pd.read_excel(path, sheet_name="Analise Preliminar")
    if "ticker" not in frame.columns:
        raise ValueError(f"Aba Analise Preliminar sem coluna ticker: {path}")
    frame["ticker"] = frame["ticker"].astype(str).str.strip()
    cols = [c for c in KEY_COLS if c in frame.columns]
    out = frame[cols].copy()
    out.insert(0, "arquivo_origem", path.name)
    out.insert(0, "mes", mes)
    return out


def primary_decision(row: pd.Series) -> str:
    decision = safe_text(row.get("decisao_preliminar_ajustada")).lower()
    legacy = safe_text(row.get("status_na_selecao")).lower()
    status_risk = safe_text(row.get("status_para_risco")).lower()
    if decision:
        return decision
    if legacy:
        return legacy
    if status_risk:
        return status_risk
    return "sem_classificacao"


def barrier_category(row: pd.Series) -> str:
    selected = to_bool(row.get("selecionada_carteira"))
    if selected:
        return "selecionada"
    decision = primary_decision(row)
    status_risk = safe_text(row.get("status_para_risco")).lower()
    blocked_opt = to_bool(row.get("bloqueado_otimizacao"))
    if decision == "candidata_para_risco":
        return "candidata_limpa_nao_selecionada"
    if decision == "candidata_com_restricao":
        return "candidata_com_restricao_nao_selecionada"
    if "watchlist" in decision or to_bool(row.get("watchlist_qualificada")):
        return "watchlist_qualificada"
    if "fundamental" in decision or to_bool(row.get("fundamento_bloqueante")):
        return "descartar_fundamentalista"
    if "dados" in decision:
        return "descartar_dados_insuficientes"
    if "descartar" in decision or "tecnico" in decision:
        return "descartar_tecnico"
    if "bloqueada" in status_risk or blocked_opt:
        return "bloqueada_para_otimizacao"
    return "fora_do_funil_ou_nao_classificada"


def main_reason(row: pd.Series) -> str:
    for col in [
        "motivo_decisao_preliminar",
        "motivo_watchlist_qualificada",
        "motivo_status_para_risco",
        "motivo_bloqueio_otimizacao",
        "motivo_fundamento_bloqueante",
        "motivo_bloqueio_ou_status",
        "penalizacoes_otimizacao",
        "alertas_nao_bloqueantes",
    ]:
        text = safe_text(row.get(col))
        if text:
            return text[:500]
    return ""


def build_detail() -> pd.DataFrame:
    if not INPUT_EXPOST.exists():
        raise FileNotFoundError(INPUT_EXPOST)
    expost = pd.read_excel(INPUT_EXPOST, sheet_name="expost_universo")
    expost["mes"] = expost["mes"].astype(str)
    expost["ticker"] = expost["ticker"].astype(str).str.strip()
    prelim = pd.concat([read_preliminar(mes) for mes in r16.MONTHS], ignore_index=True, sort=False)
    detail = expost.merge(prelim, on=["mes", "ticker"], how="left", suffixes=("_expost", ""))
    detail["retorno_realizado_periodo"] = pd.to_numeric(detail["retorno_realizado_periodo"], errors="coerce")
    detail["retorno_ibov_periodo"] = pd.to_numeric(detail["retorno_ibov_periodo"], errors="coerce")
    detail["retorno_relativo_vs_ibov"] = detail["retorno_realizado_periodo"] - detail["retorno_ibov_periodo"]
    detail["bateu_ibov"] = detail["retorno_relativo_vs_ibov"] > 0
    detail["peso_final_num"] = pd.to_numeric(detail.get("peso_final_expost", detail.get("peso_final")), errors="coerce").fillna(0.0)
    if "peso_final" in detail.columns:
        detail["peso_final_preliminar_num"] = pd.to_numeric(detail["peso_final"], errors="coerce").fillna(0.0)
    else:
        detail["peso_final_preliminar_num"] = 0.0
    detail["selecionada_carteira"] = (detail["peso_final_num"] > 0) | (detail["peso_final_preliminar_num"] > 0)
    detail["saida_funil_inicial"] = detail.apply(primary_decision, axis=1)
    detail["categoria_barreira"] = detail.apply(barrier_category, axis=1)
    detail["motivo_principal_barreira"] = detail.apply(main_reason, axis=1)
    detail["vencedora_barrada"] = detail["bateu_ibov"] & ~detail["selecionada_carteira"]
    detail["vencedora_barrada_funil_inicial"] = detail["vencedora_barrada"] & ~detail["categoria_barreira"].isin(
        ["candidata_limpa_nao_selecionada", "candidata_com_restricao_nao_selecionada"]
    )
    detail["top15_mes"] = False
    for mes, idx in detail.groupby("mes")["retorno_realizado_periodo"].nlargest(15).index:
        detail.loc[idx, "top15_mes"] = True
    return detail


def summarize_by_exit(detail: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (mes, cat), group in detail.groupby(["mes", "categoria_barreira"], dropna=False):
        ret = pd.to_numeric(group["retorno_realizado_periodo"], errors="coerce")
        rel = pd.to_numeric(group["retorno_relativo_vs_ibov"], errors="coerce")
        rows.append(
            {
                "mes": mes,
                "categoria_barreira": cat,
                "n_acoes": int(len(group)),
                "retorno_medio": float(ret.mean()) if ret.notna().any() else np.nan,
                "retorno_mediano": float(ret.median()) if ret.notna().any() else np.nan,
                "retorno_relativo_medio_vs_ibov": float(rel.mean()) if rel.notna().any() else np.nan,
                "n_bateu_ibov": int(group["bateu_ibov"].sum()),
                "pct_bateu_ibov": float(group["bateu_ibov"].mean()) if len(group) else np.nan,
                "n_vencedoras_barradas": int(group["vencedora_barrada"].sum()),
                "n_top15_mes": int(group["top15_mes"].sum()),
            }
        )
    return pd.DataFrame(rows)


def summarize_overall(detail: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for cat, group in detail.groupby("categoria_barreira", dropna=False):
        ret = pd.to_numeric(group["retorno_realizado_periodo"], errors="coerce")
        rel = pd.to_numeric(group["retorno_relativo_vs_ibov"], errors="coerce")
        rows.append(
            {
                "categoria_barreira": cat,
                "n_acoes_mes": int(len(group)),
                "retorno_composto_medio_por_acao_mes": float(ret.mean()) if ret.notna().any() else np.nan,
                "retorno_relativo_medio_vs_ibov": float(rel.mean()) if rel.notna().any() else np.nan,
                "n_bateu_ibov": int(group["bateu_ibov"].sum()),
                "pct_bateu_ibov": float(group["bateu_ibov"].mean()) if len(group) else np.nan,
                "n_vencedoras_barradas": int(group["vencedora_barrada"].sum()),
                "n_top15_mes": int(group["top15_mes"].sum()),
                "retorno_relativo_total_das_barradas": float(rel[group["vencedora_barrada"]].sum()) if group["vencedora_barrada"].any() else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values("retorno_relativo_total_das_barradas", ascending=False)


def top_winners_blocked(detail: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "mes",
        "ticker",
        "nome_expost",
        "setor_expost",
        "retorno_realizado_periodo",
        "retorno_ibov_periodo",
        "retorno_relativo_vs_ibov",
        "categoria_barreira",
        "saida_funil_inicial",
        "status_para_risco",
        "tipo_timing",
        "tendencia_mensal",
        "classificacao_forca_relativa",
        "nota_final_expost",
        "nota_final",
        "motivo_principal_barreira",
    ]
    winners = detail[detail["vencedora_barrada"]].copy()
    winners = winners.sort_values(["mes", "retorno_relativo_vs_ibov"], ascending=[True, False])
    top = winners.groupby("mes").head(15)
    return top[[c for c in cols if c in top.columns]]


def criterion_cost(detail: pd.DataFrame) -> pd.DataFrame:
    blocked = detail[detail["vencedora_barrada"]].copy()
    rows: list[dict[str, Any]] = []
    for cat, group in blocked.groupby("categoria_barreira", dropna=False):
        rows.append(
            {
                "categoria_barreira": cat,
                "n_vencedoras_barradas": int(len(group)),
                "retorno_relativo_medio_vs_ibov": float(group["retorno_relativo_vs_ibov"].mean()) if len(group) else np.nan,
                "retorno_relativo_total_vs_ibov": float(group["retorno_relativo_vs_ibov"].sum()) if len(group) else 0.0,
                "maior_vencedora_barrada": safe_text(group.sort_values("retorno_relativo_vs_ibov", ascending=False)["ticker"].iloc[0]) if len(group) else "",
                "maior_retorno_relativo": float(group["retorno_relativo_vs_ibov"].max()) if len(group) else np.nan,
                "meses_com_vencedora_barrada": int(group["mes"].nunique()),
            }
        )
    return pd.DataFrame(rows).sort_values("retorno_relativo_total_vs_ibov", ascending=False)


def monthly_winner_leakage(detail: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for mes, group in detail.groupby("mes"):
        winners = group[group["bateu_ibov"]]
        blocked = group[group["vencedora_barrada"]]
        blocked_initial = group[group["vencedora_barrada_funil_inicial"]]
        selected_winners = group[group["bateu_ibov"] & group["selecionada_carteira"]]
        rows.append(
            {
                "mes": mes,
                "n_universo": int(len(group)),
                "retorno_ibov_periodo": float(group["retorno_ibov_periodo"].dropna().iloc[0]) if group["retorno_ibov_periodo"].notna().any() else np.nan,
                "n_acoes_bateram_ibov": int(len(winners)),
                "pct_acoes_bateram_ibov": float(len(winners) / len(group)) if len(group) else np.nan,
                "n_vencedoras_selecionadas": int(len(selected_winners)),
                "n_vencedoras_barradas_total": int(len(blocked)),
                "n_vencedoras_barradas_funil_inicial": int(len(blocked_initial)),
                "pct_vencedoras_barradas": float(len(blocked) / len(winners)) if len(winners) else np.nan,
                "maior_vencedora_barrada": safe_text(blocked.sort_values("retorno_relativo_vs_ibov", ascending=False)["ticker"].iloc[0]) if len(blocked) else "",
                "maior_retorno_relativo_barrado": float(blocked["retorno_relativo_vs_ibov"].max()) if len(blocked) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def compact_detail(detail: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "mes",
        "ticker",
        "nome_expost",
        "setor_expost",
        "retorno_realizado_periodo",
        "retorno_ibov_periodo",
        "retorno_relativo_vs_ibov",
        "bateu_ibov",
        "top15_mes",
        "selecionada_carteira",
        "vencedora_barrada",
        "vencedora_barrada_funil_inicial",
        "categoria_barreira",
        "saida_funil_inicial",
        "motivo_principal_barreira",
        "status_na_selecao",
        "motivo_bloqueio_ou_status",
        "status_para_risco",
        "motivo_status_para_risco",
        "tipo_timing",
        "tendencia_mensal",
        "contexto_estrutural",
        "classificacao_forca_relativa",
        "qualidade_fundamentalista",
        "fundamento_bloqueante",
        "bloqueado_otimizacao",
        "liberado_para_otimizacao",
        "nota_final_expost",
        "nota_final",
        "forca_relativa_score",
        "rsi",
        "bollinger_status",
        "beta",
        "correlacao_ibov",
        "retorno_medio",
        "desvio_padrao",
        "cv",
    ]
    return detail[[c for c in cols if c in detail.columns]].copy()


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    log("Teste 29 - Auditoria de Vencedoras Barradas no Funil Inicial")
    log("Modo leitura/medicao: cruza Analise Preliminar das planilhas mensais com expost_universo.")
    detail = build_detail()
    by_exit = summarize_by_exit(detail)
    overall = summarize_overall(detail)
    winners = top_winners_blocked(detail)
    cost = criterion_cost(detail)
    monthly = monthly_winner_leakage(detail)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        monthly.to_excel(writer, sheet_name="resumo_mensal", index=False)
        overall.to_excel(writer, sheet_name="resumo_por_barreira", index=False)
        by_exit.to_excel(writer, sheet_name="mes_x_barreira", index=False)
        cost.to_excel(writer, sheet_name="ranking_custo_criterio", index=False)
        winners.to_excel(writer, sheet_name="top_vencedoras_barradas", index=False)
        compact_detail(detail).to_excel(writer, sheet_name="detalhe_universo", index=False)

    total = int(len(detail))
    winners_total = int(detail["bateu_ibov"].sum())
    blocked_total = int(detail["vencedora_barrada"].sum())
    initial_blocked = int(detail["vencedora_barrada_funil_inicial"].sum())
    selected_winners = int((detail["bateu_ibov"] & detail["selecionada_carteira"]).sum())
    log(f"Total acoes-mes analisadas: {total}")
    log(f"Acoes-mes que bateram o IBOV: {winners_total}")
    log(f"Vencedoras selecionadas: {selected_winners}")
    log(f"Vencedoras barradas/nao selecionadas: {blocked_total}")
    log(f"Vencedoras barradas no funil inicial: {initial_blocked}")
    log("Ranking por custo de vencedoras barradas:")
    for _, row in cost.head(8).iterrows():
        log(
            f"  {row['categoria_barreira']}: n={int(row['n_vencedoras_barradas'])}; "
            f"rel_total={pct(row['retorno_relativo_total_vs_ibov'])}; "
            f"rel_medio={pct(row['retorno_relativo_medio_vs_ibov'])}; "
            f"maior={row['maior_vencedora_barrada']} ({pct(row['maior_retorno_relativo'])})"
        )
    log("Resumo mensal de vazamento de vencedoras:")
    for _, row in monthly.iterrows():
        log(
            f"  {row['mes']}: bateram={int(row['n_acoes_bateram_ibov'])}; "
            f"barradas_total={int(row['n_vencedoras_barradas_total'])}; "
            f"barradas_funil={int(row['n_vencedoras_barradas_funil_inicial'])}; "
            f"maior={row['maior_vencedora_barrada']} ({pct(row['maior_retorno_relativo_barrado'])})"
        )
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
