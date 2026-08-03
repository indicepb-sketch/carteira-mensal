from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
INPUT_T25 = EXCEL_DIR / "shadow_teste25_relaxamento_qualificado.xlsx"
INPUT_EXPOST = EXCEL_DIR / "shadow_regime_16_risk_on_off.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste28a_auditoria_risco_resultado.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_teste28a_auditoria_risco_resultado.log"


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def realized_bucket(ret: float) -> str:
    if pd.isna(ret):
        return "indefinido"
    if ret >= 0:
        return "alta"
    if ret <= -0.03:
        return "queda_forte"
    return "queda_leve"


def predicted_bucket(row: pd.Series) -> str:
    mes = str(row.get("mes", ""))
    if mes == "2026-06":
        return "jun_oportunidade"
    ibov = row.get("retorno_ibov_periodo", np.nan)
    return realized_bucket(float(ibov)) if pd.notna(ibov) else "indefinido"


def aggregate(group: pd.DataFrame) -> pd.Series:
    return pd.Series(
        {
            "n_linhas": int(len(group)),
            "n_meses": int(group["mes"].nunique()) if "mes" in group.columns else np.nan,
            "n_tickers": int(group["ticker"].nunique()) if "ticker" in group.columns else np.nan,
            "peso_medio": float(group["peso_recomendado"].mean()),
            "peso_total_linhas": float(group["peso_recomendado"].sum()),
            "retorno_medio_simples": float(group["retorno_realizado_periodo"].mean()),
            "alpha_medio_simples": float(group["alpha_ativo"].mean()),
            "taxa_bateu_ibov": float(group["bateu_ibov"].mean()),
            "retorno_ponderado_linhas": float((group["peso_recomendado"] * group["retorno_realizado_periodo"]).sum()),
            "alpha_ponderado_linhas": float((group["peso_recomendado"] * group["alpha_ativo"]).sum()),
            "beta_medio": float(pd.to_numeric(group["beta"], errors="coerce").mean()),
            "correlacao_media": float(pd.to_numeric(group["correlacao_ibov"], errors="coerce").mean()),
            "retorno_medio_historico_medio": float(pd.to_numeric(group["retorno_medio_original_audit"], errors="coerce").mean()),
            "desvio_padrao_medio": float(pd.to_numeric(group["desvio_padrao"], errors="coerce").mean()),
            "cv_medio": float(pd.to_numeric(group["cv"], errors="coerce").mean()),
            "nota_media": float(pd.to_numeric(group["nota_final"], errors="coerce").mean()),
            "forca_media": float(pd.to_numeric(group["forca_relativa_score"], errors="coerce").mean()),
        }
    )


def aggregate_by(df: pd.DataFrame, column: str, label: str) -> pd.DataFrame:
    out = df.groupby(column, observed=True, dropna=False).apply(aggregate).reset_index()
    out.insert(0, "analise", label)
    return out.rename(columns={column: "grupo"})


def aggregate_by_two(df: pd.DataFrame, col1: str, col2: str, label: str) -> pd.DataFrame:
    out = df.groupby([col1, col2], observed=True, dropna=False).apply(aggregate).reset_index()
    out.insert(0, "analise", label)
    return out.rename(columns={col1: "grupo_1", col2: "grupo_2"})


def qcut_label(series: pd.Series, q: int, prefix: str) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    try:
        buckets = pd.qcut(values, q=q, duplicates="drop")
    except ValueError:
        return pd.Series("dados_insuficientes", index=series.index)
    return buckets.astype(str).where(values.notna(), "dados_insuficientes")


def make_monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for mes, g in df.groupby("mes", sort=True):
        ibov = float(g["retorno_ibov_periodo"].dropna().iloc[0]) if g["retorno_ibov_periodo"].notna().any() else np.nan
        rows.append(
            {
                "mes": mes,
                "regime_real": realized_bucket(ibov),
                "retorno_carteira_100_acoes": float((g["peso_recomendado"] * g["retorno_realizado_periodo"]).sum()),
                "retorno_ibov": ibov,
                "alpha_carteira_100_acoes": float((g["peso_recomendado"] * g["alpha_ativo"]).sum()),
                "n_ativos": int(g["ticker"].nunique()),
                "beta_ponderado": weighted_avg(g["beta"], g["peso_recomendado"]),
                "correlacao_ponderada": weighted_avg(g["correlacao_ibov"], g["peso_recomendado"]),
                "retorno_medio_hist_ponderado": weighted_avg(g["retorno_medio_original_audit"], g["peso_recomendado"]),
                "desvio_padrao_ponderado": weighted_avg(g["desvio_padrao"], g["peso_recomendado"]),
                "cv_ponderado": weighted_avg(g["cv"], g["peso_recomendado"]),
                "peso_beta_alto": float(g.loc[g["beta_bucket"].eq(">1.2"), "peso_recomendado"].sum()),
                "alpha_beta_alto": float((g.loc[g["beta_bucket"].eq(">1.2"), "peso_recomendado"] * g.loc[g["beta_bucket"].eq(">1.2"), "alpha_ativo"]).sum()),
                "peso_ret_medio_negativo": float(g.loc[g["retorno_medio_sinal"].eq("retorno_medio_negativo"), "peso_recomendado"].sum()),
                "alpha_ret_medio_negativo": float((g.loc[g["retorno_medio_sinal"].eq("retorno_medio_negativo"), "peso_recomendado"] * g.loc[g["retorno_medio_sinal"].eq("retorno_medio_negativo"), "alpha_ativo"]).sum()),
                "peso_cv_alto": float(g.loc[g["cv_rank"].astype(str).str.contains("alto", na=False), "peso_recomendado"].sum()),
            }
        )
    return pd.DataFrame(rows)


def weighted_avg(values: pd.Series, weights: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce")
    w = pd.to_numeric(weights, errors="coerce")
    mask = vals.notna() & w.notna()
    if not mask.any() or float(w[mask].sum()) == 0:
        return np.nan
    return float(np.average(vals[mask], weights=w[mask]))


def label_terciles(series: pd.Series, low_label: str, mid_label: str, high_label: str) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    q1 = values.quantile(1 / 3)
    q2 = values.quantile(2 / 3)
    out = pd.Series("dados_insuficientes", index=series.index, dtype=object)
    out.loc[values.notna() & (values <= q1)] = low_label
    out.loc[values.notna() & (values > q1) & (values <= q2)] = mid_label
    out.loc[values.notna() & (values > q2)] = high_label
    return out


def top_bottom(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    cols = [
        "mes",
        "ticker",
        "setor",
        "peso_recomendado",
        "retorno_realizado_periodo",
        "retorno_ibov_periodo",
        "alpha_ativo",
        "contrib_alpha",
        "beta",
        "correlacao_ibov",
        "retorno_medio",
        "desvio_padrao",
        "cv",
        "beta_bucket",
        "correlacao_bucket",
        "retorno_medio_sinal",
        "desvio_rank",
        "cv_rank",
        "nota_final",
        "forca_relativa_score",
        "tipo_timing",
        "teste25_status_relaxamento",
    ]
    use = [c for c in cols if c in df.columns]
    return df.sort_values("contrib_alpha", ascending=False)[use].head(30), df.sort_values("contrib_alpha", ascending=True)[use].head(30)


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    if not INPUT_T25.exists():
        raise FileNotFoundError(INPUT_T25)
    if not INPUT_EXPOST.exists():
        raise FileNotFoundError(INPUT_EXPOST)

    port = pd.read_excel(INPUT_T25, sheet_name="carteiras")
    port = port[port["cenario_teste25"].astype(str).eq("relaxa_qualificado_cap7_5")].copy()
    expost = pd.read_excel(INPUT_EXPOST, sheet_name="expost_universo")
    expost = expost[["mes", "ticker", "retorno_realizado_periodo", "retorno_ibov_periodo"]].copy()
    df = port.merge(expost, on=["mes", "ticker"], how="left")
    df["alpha_ativo"] = df["retorno_realizado_periodo"] - df["retorno_ibov_periodo"]
    df["contrib_alpha"] = df["peso_recomendado"] * df["alpha_ativo"]
    df["contrib_retorno"] = df["peso_recomendado"] * df["retorno_realizado_periodo"]
    df["bateu_ibov"] = df["alpha_ativo"] > 0
    df["regime_real"] = df.apply(predicted_bucket, axis=1)
    df["retorno_medio_original_audit"] = pd.to_numeric(df.get("retorno_medio_original_shadow", df.get("retorno_medio", np.nan)), errors="coerce")

    df["beta_bucket"] = pd.cut(
        pd.to_numeric(df["beta"], errors="coerce"),
        bins=[-999, 0.6, 0.9, 1.2, 999],
        labels=["<=0.6", "0.6-0.9", "0.9-1.2", ">1.2"],
    )
    df["correlacao_bucket"] = pd.cut(
        pd.to_numeric(df["correlacao_ibov"], errors="coerce"),
        bins=[-999, 0.2, 0.5, 0.75, 999],
        labels=["<=0.2", "0.2-0.5", "0.5-0.75", ">0.75"],
    )
    df["retorno_medio_sinal"] = np.where(
        pd.to_numeric(df["retorno_medio_original_audit"], errors="coerce") < 0,
        "retorno_medio_negativo",
        "retorno_medio_positivo",
    )
    df.loc[pd.to_numeric(df["retorno_medio_original_audit"], errors="coerce").isna(), "retorno_medio_sinal"] = "dados_insuficientes"
    df["retorno_medio_rank"] = label_terciles(df["retorno_medio_original_audit"], "ret_medio_baixo", "ret_medio_medio", "ret_medio_alto")
    df["desvio_rank"] = label_terciles(df["desvio_padrao"], "desvio_baixo", "desvio_medio", "desvio_alto")
    df["cv_rank"] = label_terciles(df["cv"], "cv_baixo", "cv_medio", "cv_alto")
    df["beta_corr_combo"] = df["beta_bucket"].astype(str) + " | corr " + df["correlacao_bucket"].astype(str)
    df["risco_combo"] = df["retorno_medio_sinal"].astype(str) + " | " + df["desvio_rank"].astype(str) + " | " + df["cv_rank"].astype(str)

    grouped = pd.concat(
        [
            aggregate_by(df, "beta_bucket", "beta"),
            aggregate_by(df, "correlacao_bucket", "correlacao_ibov"),
            aggregate_by(df, "retorno_medio_sinal", "retorno_medio_sinal"),
            aggregate_by(df, "retorno_medio_rank", "retorno_medio_tercil"),
            aggregate_by(df, "desvio_rank", "desvio_padrao_tercil"),
            aggregate_by(df, "cv_rank", "cv_tercil"),
            aggregate_by(df, "beta_corr_combo", "beta_correlacao_combo"),
            aggregate_by(df, "risco_combo", "retorno_desvio_cv_combo"),
        ],
        ignore_index=True,
        sort=False,
    )
    by_regime = pd.concat(
        [
            aggregate_by_two(df, "regime_real", "beta_bucket", "regime_x_beta"),
            aggregate_by_two(df, "regime_real", "correlacao_bucket", "regime_x_correlacao"),
            aggregate_by_two(df, "regime_real", "retorno_medio_sinal", "regime_x_retorno_medio"),
            aggregate_by_two(df, "regime_real", "desvio_rank", "regime_x_desvio"),
            aggregate_by_two(df, "regime_real", "cv_rank", "regime_x_cv"),
            aggregate_by_two(df, "regime_real", "beta_corr_combo", "regime_x_beta_corr"),
        ],
        ignore_index=True,
        sort=False,
    )
    monthly = make_monthly_summary(df)
    best, worst = top_bottom(df)
    corr_cols = [
        "peso_recomendado",
        "beta",
        "correlacao_ibov",
        "retorno_medio",
        "desvio_padrao",
        "cv",
        "nota_final",
        "forca_relativa_score",
        "alpha_ativo",
        "retorno_realizado_periodo",
        "contrib_alpha",
    ]
    correlations = df[[c for c in corr_cols if c in df.columns]].corr(numeric_only=True).reset_index().rename(columns={"index": "variavel"})
    summary = pd.DataFrame(
        [
            {"metrica": "retorno_carteira_100_acoes_composto", "valor": compound(monthly["retorno_carteira_100_acoes"])},
            {"metrica": "retorno_ibov_composto", "valor": compound(monthly["retorno_ibov"])},
            {"metrica": "alfa_100_acoes_composto", "valor": compound(monthly["retorno_carteira_100_acoes"]) - compound(monthly["retorno_ibov"])},
            {"metrica": "taxa_acerto_100_acoes", "valor": float((monthly["alpha_carteira_100_acoes"] > 0).mean())},
            {"metrica": "corr_beta_alpha_ativo", "valor": float(df[["beta", "alpha_ativo"]].corr(numeric_only=True).iloc[0, 1])},
            {"metrica": "corr_correlacao_alpha_ativo", "valor": float(df[["correlacao_ibov", "alpha_ativo"]].corr(numeric_only=True).iloc[0, 1])},
            {"metrica": "corr_retorno_medio_original_alpha_ativo", "valor": float(df[["retorno_medio_original_audit", "alpha_ativo"]].corr(numeric_only=True).iloc[0, 1])},
            {"metrica": "corr_desvio_alpha_ativo", "valor": float(df[["desvio_padrao", "alpha_ativo"]].corr(numeric_only=True).iloc[0, 1])},
            {"metrica": "corr_cv_alpha_ativo", "valor": float(df[["cv", "alpha_ativo"]].corr(numeric_only=True).iloc[0, 1])},
        ]
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        grouped.to_excel(writer, sheet_name="analises_risco", index=False)
        by_regime.to_excel(writer, sheet_name="risco_por_regime", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes", index=False)
        correlations.to_excel(writer, sheet_name="correlacoes", index=False)
        best.to_excel(writer, sheet_name="top_contribuidores", index=False)
        worst.to_excel(writer, sheet_name="piores_contribuidores", index=False)
        df.to_excel(writer, sheet_name="base_ativos_carteira", index=False)

    log("Teste 28A - Auditoria Beta, Correlacao, Retorno Medio, Desvio Padrao e CV")
    log(f"Periodo: {monthly['mes'].min()} a {monthly['mes'].max()} | linhas ativo-mes: {len(df)}")
    log(f"Carteira 100% acoes T25 cap 7,5%: retorno={pct(summary.loc[summary['metrica'].eq('retorno_carteira_100_acoes_composto'), 'valor'].iloc[0])}; alfa={pct(summary.loc[summary['metrica'].eq('alfa_100_acoes_composto'), 'valor'].iloc[0])}; acerto={pct(summary.loc[summary['metrica'].eq('taxa_acerto_100_acoes'), 'valor'].iloc[0])}")
    for metric in [
        "corr_beta_alpha_ativo",
        "corr_correlacao_alpha_ativo",
        "corr_retorno_medio_original_alpha_ativo",
        "corr_desvio_alpha_ativo",
        "corr_cv_alpha_ativo",
    ]:
        log(f"{metric}: {summary.loc[summary['metrica'].eq(metric), 'valor'].iloc[0]:.3f}")
    for analysis in ["beta", "correlacao_ibov", "retorno_medio_sinal", "desvio_padrao_tercil", "cv_tercil"]:
        log(f"\n{analysis}:")
        sample = grouped[grouped["analise"].eq(analysis)][["grupo", "n_linhas", "alpha_medio_simples", "taxa_bateu_ibov", "alpha_ponderado_linhas"]]
        for _, row in sample.iterrows():
            log(f"  {row['grupo']}: n={int(row['n_linhas'])}; alpha medio={pct(row['alpha_medio_simples'])}; taxa={pct(row['taxa_bateu_ibov'])}; contrib={pct(row['alpha_ponderado_linhas'])}")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()

