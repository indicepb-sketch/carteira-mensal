from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_16 = ROOT / "output" / "excel" / "shadow_regime_16_risk_on_off.xlsx"
OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_teste23_melhorar_alta.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_teste23_melhorar_alta.log"

SCENARIO = "risk_on_off_mm50"
TOP_N = 15


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def classify_real_regime(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    ibov = pd.to_numeric(out["retorno_expost_ibov"], errors="coerce")
    out["tipo_regime_expost"] = np.select(
        [
            out["mes"].astype(str).eq("2026-06"),
            ibov >= 0,
            ibov <= -0.03,
        ],
        ["jun_oportunidade", "alta", "queda_forte"],
        default="queda_leve",
    )
    return out


def selected_weight_column(frame: pd.DataFrame) -> pd.Series:
    for col in ["peso_recomendado", "peso_final"]:
        if col in frame.columns:
            vals = pd.to_numeric(frame[col], errors="coerce").fillna(0.0)
            if vals.abs().sum() > 0:
                return vals
    return pd.Series(0.0, index=frame.index)


def status_bucket(row: pd.Series) -> str:
    status = str(row.get("status_na_selecao", "")).lower()
    decision = str(row.get("decisao_preliminar_ajustada", "")).lower()
    motivo = str(row.get("motivo_bloqueio_ou_status", "")).lower()
    motivo_ot = str(row.get("motivo_bloqueio_otimizacao", "")).lower()
    selected = float(row.get("peso_modelo_100pct", 0.0) or 0.0) > 1e-9
    if selected:
        return "selecionada"
    if "watchlist" in decision or "watchlist" in motivo or "watchlist" in motivo_ot:
        return "watchlist"
    if "bloque" in status or "bloque" in motivo or "bloque" in motivo_ot:
        return "bloqueada"
    if "descartar" in decision or "descartar" in motivo:
        return "descartada"
    if "aprovada" in status or "candidata" in decision:
        return "aprovada_nao_selecionada"
    if "fora" in status:
        return "fora_do_funil"
    return "nao_classificada"


def primary_loss_reason(row: pd.Series) -> str:
    if float(row.get("peso_modelo_100pct", 0.0) or 0.0) > 1e-9:
        return "entrou_na_carteira"
    text = " | ".join(
        str(row.get(col, ""))
        for col in [
            "decisao_preliminar_ajustada",
            "status_na_selecao",
            "motivo_bloqueio_ou_status",
            "motivo_bloqueio_otimizacao",
            "tipo_timing",
            "alertas_nao_bloqueantes",
            "penalizacoes_otimizacao",
        ]
    ).lower()
    checks = [
        ("watchlist_timing", ["watchlist", "timing"]),
        ("sobrecompra_esticada", ["sobrecompra", "esticad"]),
        ("tendencia_tecnica", ["tendencia", "tecnica"]),
        ("retorno_medio_negativo", ["retorno_medio_negativo"]),
        ("fundamentalista", ["fundament"]),
        ("risco_beta_cv", ["beta", "cv", "risco"]),
        ("setor_bloco", ["setor", "bloco"]),
        ("fora_funil", ["fora_do_funil"]),
    ]
    for label, needles in checks:
        if any(needle in text for needle in needles):
            return label
    return "sem_motivo_claro"


def main() -> None:
    logs: list[str] = []

    def log(message: str) -> None:
        print(message, flush=True)
        logs.append(message)

    if not INPUT_16.exists():
        raise FileNotFoundError(INPUT_16)

    monthly_all = pd.read_excel(INPUT_16, sheet_name="mes_a_mes")
    monthly = monthly_all[monthly_all["cenario"].eq(SCENARIO)].copy()
    monthly = classify_real_regime(monthly)
    high_months = monthly[monthly["tipo_regime_expost"].eq("alta")].copy()
    high_month_ids = high_months["mes"].astype(str).tolist()

    expost = pd.read_excel(INPUT_16, sheet_name="expost_universo")
    expost["mes"] = expost["mes"].astype(str)
    expost = expost[expost["mes"].isin(high_month_ids)].copy()
    expost["retorno_realizado_periodo"] = pd.to_numeric(expost["retorno_realizado_periodo"], errors="coerce")
    expost["retorno_ibov_periodo"] = pd.to_numeric(expost["retorno_ibov_periodo"], errors="coerce")

    cart_cols = [
        "cenario",
        "mes",
        "ticker",
        "peso_final",
        "peso_recomendado",
        "decisao_preliminar_ajustada",
        "status_para_risco",
        "bloqueado_otimizacao",
        "motivo_bloqueio_otimizacao",
        "nota_final",
        "forca_relativa_score",
        "retorno_acumulado_1m",
        "retorno_acumulado_4m",
        "rsi",
        "bollinger_status",
        "tipo_timing",
        "tendencia_mensal",
        "beta",
        "alertas_nao_bloqueantes",
        "penalizacoes_otimizacao",
        "shadow_tamanho_livre_aprovada",
    ]
    cart = pd.read_excel(INPUT_16, sheet_name="carteiras", usecols=lambda c: c in cart_cols)
    cart = cart[cart["cenario"].eq(SCENARIO)].copy()
    cart["mes"] = cart["mes"].astype(str)
    cart["peso_modelo_100pct"] = selected_weight_column(cart)
    cart = cart.drop(columns=[c for c in ["peso_final", "peso_recomendado"] if c in cart.columns], errors="ignore")

    merged = expost.merge(
        cart.drop(columns=["cenario"], errors="ignore"),
        on=["mes", "ticker"],
        how="left",
        suffixes=("", "_cart"),
    )
    merged["peso_modelo_100pct"] = pd.to_numeric(merged.get("peso_modelo_100pct", 0.0), errors="coerce").fillna(0.0)
    merged["selecionada_modelo"] = merged["peso_modelo_100pct"] > 1e-9
    merged["status_bucket_teste23"] = merged.apply(status_bucket, axis=1)
    merged["motivo_perda_lider"] = merged.apply(primary_loss_reason, axis=1)
    merged["alfa_ativo_vs_ibov"] = merged["retorno_realizado_periodo"] - merged["retorno_ibov_periodo"]

    top_rows: list[pd.DataFrame] = []
    month_summary_rows: list[dict[str, Any]] = []
    for mes, group in merged.groupby("mes", sort=True):
        group = group.dropna(subset=["retorno_realizado_periodo"]).copy()
        top = group.sort_values("retorno_realizado_periodo", ascending=False).head(TOP_N).copy()
        top["rank_retorno_mes"] = range(1, len(top) + 1)
        top_rows.append(top)

        mrow = high_months[high_months["mes"].astype(str).eq(mes)].iloc[0]
        selected = group[group["selecionada_modelo"]].copy()
        top10 = top.head(10)
        top15 = top
        month_summary_rows.append(
            {
                "mes": mes,
                "bucket_regime_previsto": mrow.get("bucket_regime"),
                "retorno_modelo_100pct": mrow.get("retorno_expost_sombra_100pct"),
                "retorno_ibov": mrow.get("retorno_expost_ibov"),
                "alfa_modelo_100pct": mrow.get("retorno_expost_sombra_100pct") - mrow.get("retorno_expost_ibov"),
                "bateu_ibov": (mrow.get("retorno_expost_sombra_100pct") - mrow.get("retorno_expost_ibov")) > 0,
                "retorno_medio_top10_universo": top10["retorno_realizado_periodo"].mean(),
                "retorno_medio_top15_universo": top15["retorno_realizado_periodo"].mean(),
                "retorno_medio_selecionadas": selected["retorno_realizado_periodo"].mean(),
                "n_selecionadas": int(selected["ticker"].nunique()),
                "n_top10_na_carteira": int(top10["selecionada_modelo"].sum()),
                "n_top15_na_carteira": int(top15["selecionada_modelo"].sum()),
                "peso_top10_na_carteira": float(top10["peso_modelo_100pct"].sum()),
                "peso_top15_na_carteira": float(top15["peso_modelo_100pct"].sum()),
                "n_top10_watchlist_ou_bloqueada": int(top10["status_bucket_teste23"].isin(["watchlist", "bloqueada", "descartada"]).sum()),
                "n_top15_watchlist_ou_bloqueada": int(top15["status_bucket_teste23"].isin(["watchlist", "bloqueada", "descartada"]).sum()),
            }
        )

    top_winners = pd.concat(top_rows, ignore_index=True) if top_rows else pd.DataFrame()
    month_summary = pd.DataFrame(month_summary_rows)

    reason_summary = (
        top_winners.groupby(["motivo_perda_lider"], dropna=False)
        .agg(
            ocorrencias=("ticker", "count"),
            retorno_medio_real=("retorno_realizado_periodo", "mean"),
            alfa_medio_vs_ibov=("alfa_ativo_vs_ibov", "mean"),
            selecionadas=("selecionada_modelo", "sum"),
            peso_medio=("peso_modelo_100pct", "mean"),
        )
        .reset_index()
        .sort_values(["ocorrencias", "alfa_medio_vs_ibov"], ascending=[False, False])
    )

    status_summary = (
        top_winners.groupby(["status_bucket_teste23"], dropna=False)
        .agg(
            ocorrencias=("ticker", "count"),
            retorno_medio_real=("retorno_realizado_periodo", "mean"),
            alfa_medio_vs_ibov=("alfa_ativo_vs_ibov", "mean"),
            peso_medio=("peso_modelo_100pct", "mean"),
        )
        .reset_index()
        .sort_values(["ocorrencias", "alfa_medio_vs_ibov"], ascending=[False, False])
    )

    selected_vs_not = (
        merged.groupby(["selecionada_modelo"], dropna=False)
        .agg(
            ativos_mes=("ticker", "count"),
            retorno_medio=("retorno_realizado_periodo", "mean"),
            alfa_medio_vs_ibov=("alfa_ativo_vs_ibov", "mean"),
            nota_media=("nota_final", "mean"),
            forca_media=("forca_relativa_score", "mean"),
            rsi_medio=("rsi", "mean"),
            beta_medio=("beta", "mean"),
            retorno_1m_formacao_medio=("retorno_acumulado_1m", "mean"),
            retorno_4m_formacao_medio=("retorno_acumulado_4m", "mean"),
        )
        .reset_index()
    )

    top_winners = top_winners[
        [
            "mes",
            "rank_retorno_mes",
            "ticker",
            "nome",
            "setor",
            "retorno_realizado_periodo",
            "retorno_ibov_periodo",
            "alfa_ativo_vs_ibov",
            "selecionada_modelo",
            "peso_modelo_100pct",
            "status_bucket_teste23",
            "motivo_perda_lider",
            "status_na_selecao",
            "motivo_bloqueio_ou_status",
            "decisao_preliminar_ajustada",
            "status_para_risco",
            "bloqueado_otimizacao",
            "motivo_bloqueio_otimizacao",
            "nota_final",
            "forca_relativa_score",
            "retorno_acumulado_1m",
            "retorno_acumulado_4m",
            "rsi",
            "bollinger_status",
            "tipo_timing",
            "tendencia_mensal",
            "beta",
        ]
    ]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        month_summary.to_excel(writer, sheet_name="resumo_meses_alta", index=False)
        top_winners.to_excel(writer, sheet_name="top15_vencedoras_alta", index=False)
        reason_summary.to_excel(writer, sheet_name="causas_perda_lideres", index=False)
        status_summary.to_excel(writer, sheet_name="status_top15", index=False)
        selected_vs_not.to_excel(writer, sheet_name="selecionadas_vs_universo", index=False)
        merged.to_excel(writer, sheet_name="base_alta_completa", index=False)

    n_high = int(month_summary["mes"].nunique())
    beats = int(month_summary["bateu_ibov"].sum())
    avg_top15_selected = float(month_summary["n_top15_na_carteira"].mean())
    avg_weight_top15 = float(month_summary["peso_top15_na_carteira"].mean())
    selected_mean = float(selected_vs_not.loc[selected_vs_not["selecionada_modelo"].eq(True), "retorno_medio"].iloc[0])
    universe_mean = float(merged["retorno_realizado_periodo"].mean())

    log("Teste 23 - Melhorar Selecao nos Meses de Alta")
    log(f"Cenario auditado: {SCENARIO}")
    log(f"Meses de alta real avaliados: {n_high}")
    log(f"Carteira 100% do modelo bateu IBOV em alta: {beats}/{n_high} ({beats / n_high:.1%})")
    log(f"Media de top15 vencedoras capturadas por mes: {avg_top15_selected:.2f} de 15")
    log(f"Peso medio nas top15 vencedoras: {avg_weight_top15:.2%}")
    log(f"Retorno medio das selecionadas: {pct(selected_mean)}")
    log(f"Retorno medio do universo nos meses de alta: {pct(universe_mean)}")
    log("Principais motivos de perda das top15:")
    for _, row in reason_summary.head(8).iterrows():
        log(
            f"  {row['motivo_perda_lider']}: ocorrencias={int(row['ocorrencias'])}; "
            f"ret_medio={pct(row['retorno_medio_real'])}; alfa_medio={pct(row['alfa_medio_vs_ibov'])}; "
            f"selecionadas={int(row['selecionadas'])}"
        )
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
