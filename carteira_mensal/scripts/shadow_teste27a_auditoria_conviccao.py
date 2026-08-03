from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
INPUT_T25 = EXCEL_DIR / "shadow_teste25_relaxamento_qualificado.xlsx"
INPUT_EXPOST = EXCEL_DIR / "shadow_regime_16_risk_on_off.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste27a_auditoria_conviccao.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_teste27a_auditoria_conviccao.log"


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def weighted_avg(values: pd.Series, weights: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce")
    w = pd.to_numeric(weights, errors="coerce")
    mask = vals.notna() & w.notna()
    if not mask.any() or float(w[mask].sum()) == 0:
        return np.nan
    return float(np.average(vals[mask], weights=w[mask]))


def realized_bucket(ret: float) -> str:
    if pd.isna(ret):
        return "indefinido"
    if ret >= 0:
        return "alta"
    if ret <= -0.03:
        return "queda_forte"
    return "queda_leve"


def classify_conviction(row: pd.Series) -> str:
    nota = pd.to_numeric(pd.Series([row.get("nota_final", np.nan)]), errors="coerce").iloc[0]
    forca = pd.to_numeric(pd.Series([row.get("forca_relativa_score", np.nan)]), errors="coerce").iloc[0]
    if pd.isna(nota) or pd.isna(forca):
        return "dados_insuficientes"
    if nota >= 80 and forca >= 4:
        return "conviccao_muito_alta"
    if nota >= 70 and forca >= 4:
        return "conviccao_alta"
    if nota >= 60 and forca >= 3:
        return "conviccao_media_alta"
    if nota >= 50 or forca >= 3:
        return "conviccao_media"
    return "conviccao_baixa"


def aggregate(group: pd.DataFrame) -> pd.Series:
    weighted_alpha = float((group["peso_recomendado"] * group["alpha_ativo"]).sum())
    weighted_return = float((group["peso_recomendado"] * group["retorno_realizado_periodo"]).sum())
    return pd.Series(
        {
            "n_linhas": int(len(group)),
            "n_meses": int(group["mes"].nunique()) if "mes" in group.columns else np.nan,
            "n_tickers": int(group["ticker"].nunique()),
            "peso_medio": float(group["peso_recomendado"].mean()),
            "peso_total_linhas": float(group["peso_recomendado"].sum()),
            "retorno_medio_simples": float(group["retorno_realizado_periodo"].mean()),
            "alpha_medio_simples": float(group["alpha_ativo"].mean()),
            "taxa_bateu_ibov": float(group["bateu_ibov"].mean()),
            "retorno_ponderado_linhas": weighted_return,
            "alpha_ponderado_linhas": weighted_alpha,
            "nota_media": float(pd.to_numeric(group["nota_final"], errors="coerce").mean()),
            "forca_media": float(pd.to_numeric(group["forca_relativa_score"], errors="coerce").mean()),
            "beta_medio": float(pd.to_numeric(group["beta"], errors="coerce").mean()),
            "cv_medio": float(pd.to_numeric(group["cv"], errors="coerce").mean()),
        }
    )


def aggregate_by(df: pd.DataFrame, column: str, label: str) -> pd.DataFrame:
    out = df.groupby(column, observed=True, dropna=False).apply(aggregate).reset_index()
    out.insert(0, "analise", label)
    return out.rename(columns={column: "grupo"})


def make_monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for mes, g in df.groupby("mes", sort=True):
        rows.append(
            {
                "mes": mes,
                "retorno_carteira": float((g["peso_recomendado"] * g["retorno_realizado_periodo"]).sum()),
                "retorno_ibov": float(g["retorno_ibov_periodo"].dropna().iloc[0]) if g["retorno_ibov_periodo"].notna().any() else np.nan,
                "alpha_carteira": float((g["peso_recomendado"] * g["alpha_ativo"]).sum()),
                "tipo_regime_expost": realized_bucket(float(g["retorno_ibov_periodo"].dropna().iloc[0])) if g["retorno_ibov_periodo"].notna().any() else "indefinido",
                "n_ativos": int(g["ticker"].nunique()),
                "nota_ponderada": weighted_avg(g["nota_final"], g["peso_recomendado"]),
                "forca_ponderada": weighted_avg(g["forca_relativa_score"], g["peso_recomendado"]),
                "beta_ponderado": weighted_avg(g["beta"], g["peso_recomendado"]),
                "cv_ponderado": weighted_avg(g["cv"], g["peso_recomendado"]),
                "peso_conviccao_alta_ou_mais": float(g.loc[g["conviccao_grupo"].isin(["conviccao_alta", "conviccao_muito_alta"]), "peso_recomendado"].sum()),
                "peso_conviccao_baixa": float(g.loc[g["conviccao_grupo"].eq("conviccao_baixa"), "peso_recomendado"].sum()),
                "alpha_conviccao_alta_ou_mais": float((g.loc[g["conviccao_grupo"].isin(["conviccao_alta", "conviccao_muito_alta"]), "peso_recomendado"] * g.loc[g["conviccao_grupo"].isin(["conviccao_alta", "conviccao_muito_alta"]), "alpha_ativo"]).sum()),
                "alpha_conviccao_baixa": float((g.loc[g["conviccao_grupo"].eq("conviccao_baixa"), "peso_recomendado"] * g.loc[g["conviccao_grupo"].eq("conviccao_baixa"), "alpha_ativo"]).sum()),
            }
        )
    return pd.DataFrame(rows)


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
        "nota_final",
        "forca_relativa_score",
        "conviccao_grupo",
        "beta",
        "cv",
        "tipo_timing",
        "qualidade_fundamentalista",
        "teste25_status_relaxamento",
    ]
    use_cols = [c for c in cols if c in df.columns]
    return (
        df.sort_values("contrib_alpha", ascending=False)[use_cols].head(30).copy(),
        df.sort_values("contrib_alpha", ascending=True)[use_cols].head(30).copy(),
    )


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

    df["peso_bucket"] = pd.cut(
        df["peso_recomendado"],
        bins=[-1, 0.025, 0.05, 0.075, 0.10, 0.15, 0.25, 1],
        labels=["<=2.5%", "2.5-5%", "5-7.5%", "7.5-10%", "10-15%", "15-25%", ">25%"],
    )
    df["nota_bucket"] = pd.cut(
        pd.to_numeric(df["nota_final"], errors="coerce"),
        bins=[-999, 20, 40, 60, 80, 999],
        labels=["<=20", "20-40", "40-60", "60-80", ">80"],
    )
    df["forca_bucket"] = pd.cut(
        pd.to_numeric(df["forca_relativa_score"], errors="coerce"),
        bins=[-1, 0, 2, 3, 5],
        labels=["0", "1-2", "3", "4-5"],
    )
    df["beta_bucket"] = pd.cut(
        pd.to_numeric(df["beta"], errors="coerce"),
        bins=[-999, 0.6, 0.9, 1.2, 999],
        labels=["<=0.6", "0.6-0.9", "0.9-1.2", ">1.2"],
    )
    df["cv_bucket"] = pd.qcut(pd.to_numeric(df["cv"], errors="coerce"), q=4, duplicates="drop")
    df["conviccao_grupo"] = df.apply(classify_conviction, axis=1)
    df["relaxamento_grupo"] = df.get("teste25_status_relaxamento", pd.Series("", index=df.index)).fillna("").replace("", "normal")

    grouped = pd.concat(
        [
            aggregate_by(df, "conviccao_grupo", "conviccao_nota_forca"),
            aggregate_by(df, "nota_bucket", "nota_final"),
            aggregate_by(df, "forca_bucket", "forca_relativa"),
            aggregate_by(df, "peso_bucket", "peso_atual"),
            aggregate_by(df, "beta_bucket", "beta"),
            aggregate_by(df, "cv_bucket", "cv_quartil"),
            aggregate_by(df, "tipo_timing", "timing"),
            aggregate_by(df, "qualidade_fundamentalista", "fundamentos"),
            aggregate_by(df, "setor", "setor"),
            aggregate_by(df, "relaxamento_grupo", "relaxamento_t25"),
        ],
        ignore_index=True,
        sort=False,
    )
    monthly = make_monthly_summary(df)
    monthly_by_conviction = (
        df.groupby(["mes", "conviccao_grupo"], observed=True)
        .apply(aggregate)
        .reset_index()
        .sort_values(["mes", "conviccao_grupo"])
    )
    correlation_cols = [
        "peso_recomendado",
        "nota_final",
        "forca_relativa_score",
        "beta",
        "cv",
        "retorno_medio_original_shadow",
        "retorno_medio",
        "alpha_ativo",
        "retorno_realizado_periodo",
        "contrib_alpha",
    ]
    correlations = df[[c for c in correlation_cols if c in df.columns]].corr(numeric_only=True).reset_index().rename(columns={"index": "variavel"})
    best, worst = top_bottom(df)

    summary_rows = [
        {
            "metrica": "retorno_carteira_composto",
            "valor": compound(monthly["retorno_carteira"]),
            "observacao": "Retorno composto da carteira Teste 25 cap 7,5%",
        },
        {
            "metrica": "retorno_ibov_composto",
            "valor": compound(monthly["retorno_ibov"]),
            "observacao": "Retorno composto do IBOV no mesmo periodo",
        },
        {
            "metrica": "alfa_composto",
            "valor": compound(monthly["retorno_carteira"]) - compound(monthly["retorno_ibov"]),
            "observacao": "Carteira menos IBOV",
        },
        {
            "metrica": "taxa_acerto_mensal",
            "valor": float((monthly["alpha_carteira"] > 0).mean()),
            "observacao": "Meses em que carteira bateu o IBOV",
        },
        {
            "metrica": "correlacao_nota_alpha_ativo",
            "valor": float(df[["nota_final", "alpha_ativo"]].corr(numeric_only=True).iloc[0, 1]),
            "observacao": "CorrelaÃ§Ã£o simples, nao prova causalidade",
        },
        {
            "metrica": "correlacao_forca_alpha_ativo",
            "valor": float(df[["forca_relativa_score", "alpha_ativo"]].corr(numeric_only=True).iloc[0, 1]),
            "observacao": "CorrelaÃ§Ã£o simples, nao prova causalidade",
        },
        {
            "metrica": "correlacao_peso_alpha_ativo",
            "valor": float(df[["peso_recomendado", "alpha_ativo"]].corr(numeric_only=True).iloc[0, 1]),
            "observacao": "Peso atual quase nao se correlacionou com alpha do ativo",
        },
    ]
    summary = pd.DataFrame(summary_rows)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        grouped.to_excel(writer, sheet_name="analises_por_grupo", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes", index=False)
        monthly_by_conviction.to_excel(writer, sheet_name="mes_por_conviccao", index=False)
        correlations.to_excel(writer, sheet_name="correlacoes", index=False)
        best.to_excel(writer, sheet_name="top_contribuidores", index=False)
        worst.to_excel(writer, sheet_name="piores_contribuidores", index=False)
        df.to_excel(writer, sheet_name="base_ativos_carteira", index=False)

    high_conv = grouped[(grouped["analise"].eq("conviccao_nota_forca")) & (grouped["grupo"].isin(["conviccao_alta", "conviccao_muito_alta"]))]
    low_conv = grouped[(grouped["analise"].eq("conviccao_nota_forca")) & (grouped["grupo"].eq("conviccao_baixa"))]
    log("Teste 27A - Auditoria Estatistica de Conviccao")
    log(f"Periodo: {monthly['mes'].min()} a {monthly['mes'].max()} | linhas ativo-mes: {len(df)}")
    log(f"Carteira T25 cap 7,5%: retorno={pct(summary.loc[summary['metrica'].eq('retorno_carteira_composto'), 'valor'].iloc[0])}; alfa={pct(summary.loc[summary['metrica'].eq('alfa_composto'), 'valor'].iloc[0])}; taxa acerto={pct(summary.loc[summary['metrica'].eq('taxa_acerto_mensal'), 'valor'].iloc[0])}")
    if not high_conv.empty:
        log("Conviccao alta/muito alta:")
        for _, row in high_conv.iterrows():
            log(f"  {row['grupo']}: n={int(row['n_linhas'])}; alpha medio={pct(row['alpha_medio_simples'])}; taxa bateu={pct(row['taxa_bateu_ibov'])}; contrib alpha={pct(row['alpha_ponderado_linhas'])}")
    if not low_conv.empty:
        row = low_conv.iloc[0]
        log(f"Conviccao baixa: n={int(row['n_linhas'])}; alpha medio={pct(row['alpha_medio_simples'])}; taxa bateu={pct(row['taxa_bateu_ibov'])}; contrib alpha={pct(row['alpha_ponderado_linhas'])}")
    log(f"Correlacao nota vs alpha ativo: {summary.loc[summary['metrica'].eq('correlacao_nota_alpha_ativo'), 'valor'].iloc[0]:.3f}")
    log(f"Correlacao forca vs alpha ativo: {summary.loc[summary['metrica'].eq('correlacao_forca_alpha_ativo'), 'valor'].iloc[0]:.3f}")
    log(f"Correlacao peso atual vs alpha ativo: {summary.loc[summary['metrica'].eq('correlacao_peso_alpha_ativo'), 'valor'].iloc[0]:.3f}")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()

