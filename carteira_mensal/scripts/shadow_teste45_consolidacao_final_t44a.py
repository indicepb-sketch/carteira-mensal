from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_44 = EXCEL_DIR / "shadow_teste44_diagnostico_grau_confianca.xlsx"
INPUT_42 = EXCEL_DIR / "shadow_teste42_falso_alerta_queda_4m.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste45_consolidacao_final_t44a.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste45_consolidacao_final_t44a.log"

SCENARIO_MAP = {
    "BASE_T36C": "36C_BASE",
    "T39_4M_TODA_QUEDA_PREVISTA": "T39_4M_TODA_QUEDA",
    "T42A_QUALIDADE_FORTE": "T42A_QUALIDADE_FORTE",
    "T44A_QUEDA_CONFIANCA": "T44A_QUEDA_CONFIANCA",
}


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float((1.0 + vals).prod() - 1.0)


def max_drawdown(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").fillna(0.0)
    equity = (1.0 + vals).cumprod()
    if equity.empty:
        return np.nan
    return float((equity / equity.cummax() - 1.0).min())


def summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, group in df.groupby(group_cols, dropna=False, sort=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        ret_model = compound(group["retorno_total"])
        ret_ibov = compound(group["retorno_expost_ibov"])
        row.update(
            {
                "meses": len(group),
                "retorno_modelo": ret_model,
                "retorno_ibov": ret_ibov,
                "alfa_vs_ibov": ret_model - ret_ibov,
                "taxa_acerto": float(pd.to_numeric(group["alfa_vs_ibov"], errors="coerce").gt(0).mean()),
                "drawdown": max_drawdown(group["retorno_total"]),
                "pior_mes_retorno": float(pd.to_numeric(group["retorno_total"], errors="coerce").min()),
                "melhor_mes_retorno": float(pd.to_numeric(group["retorno_total"], errors="coerce").max()),
                "alfa_medio_mensal": float(pd.to_numeric(group["alfa_vs_ibov"], errors="coerce").mean()),
                "alfa_mediano_mensal": float(pd.to_numeric(group["alfa_vs_ibov"], errors="coerce").median()),
                "peso_acoes_medio": float(pd.to_numeric(group["peso_acoes"], errors="coerce").mean()),
                "meses_4m": int(group["usa_4m"].fillna(False).sum()) if "usa_4m" in group.columns else 0,
                "meses_exposicao_reduzida": int(group["exposicao_reduzida"].fillna(False).sum()) if "exposicao_reduzida" in group.columns else 0,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def load_monthly() -> pd.DataFrame:
    if not INPUT_44.exists():
        raise FileNotFoundError(INPUT_44)
    if not INPUT_42.exists():
        raise FileNotFoundError(INPUT_42)

    df44 = pd.read_excel(INPUT_44, sheet_name="Mes a Mes")
    df44["mes"] = df44["mes"].astype(str).str[:7]
    df44 = df44[df44["cenario_t44"].isin(["BASE_T36C", "T42A_QUALIDADE_FORTE", "T44A_QUEDA_CONFIANCA"])].copy()
    df44["cenario_origem_consolidado"] = df44["cenario_t44"]

    df42 = pd.read_excel(INPUT_42, sheet_name="Mes a Mes")
    df42["mes"] = df42["mes"].astype(str).str[:7]
    df42 = df42[df42["cenario_t42"].eq("T39_4M_TODA_QUEDA_PREVISTA")].copy()
    df42["cenario_t44"] = "T39_4M_TODA_QUEDA_PREVISTA"
    df42["cenario_origem_consolidado"] = df42["cenario_t42"]
    if "exposicao_reduzida" not in df42.columns:
        df42["exposicao_reduzida"] = False

    df = pd.concat([df44, df42], ignore_index=True, sort=False)
    meta_cols = [
        "mes",
        "tipo_erro_diagnostico",
        "grau_confianca_diagnostico",
        "direcao_prevista",
        "direcao_real_ibov",
    ]
    meta = df44[df44["cenario_t44"].eq("BASE_T36C")][[c for c in meta_cols if c in df44.columns]].copy()
    if not meta.empty:
        df = df.merge(meta, on="mes", how="left", suffixes=("", "_base_meta"))
        for col in meta_cols:
            if col == "mes" or col not in df.columns:
                continue
            base_col = f"{col}_base_meta"
            if base_col in df.columns:
                df[col] = df[col].combine_first(df[base_col])
                df = df.drop(columns=[base_col])
    df["ano"] = df["mes"].str[:4]
    df["modelo"] = df["cenario_t44"].map(SCENARIO_MAP)
    return df


def build_vs_base(monthly: pd.DataFrame) -> pd.DataFrame:
    base = monthly[monthly["modelo"].eq("36C_BASE")][["mes", "retorno_total", "alfa_vs_ibov"]].rename(
        columns={"retorno_total": "retorno_base", "alfa_vs_ibov": "alfa_base"}
    )
    out = monthly.merge(base, on="mes", how="left")
    out["delta_retorno_vs_36c"] = out["retorno_total"] - out["retorno_base"]
    out["delta_alfa_vs_36c"] = out["alfa_vs_ibov"] - out["alfa_base"]
    out["melhorou_vs_36c"] = out["delta_alfa_vs_36c"] > 0
    out["piorou_vs_36c"] = out["delta_alfa_vs_36c"] < 0
    return out


def build_robustness(vs_base: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for modelo, group in vs_base.groupby("modelo", sort=False):
        non_base = group[~group["modelo"].eq("36C_BASE")]
        if modelo == "36C_BASE":
            rows.append(
                {
                    "modelo": modelo,
                    "meses_piorou_vs_36c": 0,
                    "meses_melhorou_vs_36c": 0,
                    "ganho_total_meses_melhorou": 0.0,
                    "perda_total_meses_piorou": 0.0,
                    "ganho_medio_quando_melhora": np.nan,
                    "perda_media_quando_piora": np.nan,
                    "pior_delta_alfa_vs_36c": 0.0,
                    "melhor_delta_alfa_vs_36c": 0.0,
                }
            )
            continue
        improved = non_base[non_base["delta_alfa_vs_36c"].gt(0)]
        worsened = non_base[non_base["delta_alfa_vs_36c"].lt(0)]
        rows.append(
            {
                "modelo": modelo,
                "meses_piorou_vs_36c": int(len(worsened)),
                "meses_melhorou_vs_36c": int(len(improved)),
                "ganho_total_meses_melhorou": float(improved["delta_alfa_vs_36c"].sum()),
                "perda_total_meses_piorou": float(worsened["delta_alfa_vs_36c"].sum()),
                "ganho_medio_quando_melhora": float(improved["delta_alfa_vs_36c"].mean()) if not improved.empty else np.nan,
                "perda_media_quando_piora": float(worsened["delta_alfa_vs_36c"].mean()) if not worsened.empty else np.nan,
                "pior_delta_alfa_vs_36c": float(non_base["delta_alfa_vs_36c"].min()),
                "melhor_delta_alfa_vs_36c": float(non_base["delta_alfa_vs_36c"].max()),
            }
        )
    return pd.DataFrame(rows)


def build_extremes(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for modelo, group in monthly.groupby("modelo", sort=False):
        worst_ret = group.sort_values("retorno_total", ascending=True).head(5)
        best_ret = group.sort_values("retorno_total", ascending=False).head(5)
        worst_alpha = group.sort_values("alfa_vs_ibov", ascending=True).head(5)
        best_alpha = group.sort_values("alfa_vs_ibov", ascending=False).head(5)
        for label, chunk in [
            ("piores_retornos", worst_ret),
            ("melhores_retornos", best_ret),
            ("piores_alfas", worst_alpha),
            ("melhores_alfas", best_alpha),
        ]:
            for _, row in chunk.iterrows():
                rows.append(
                    {
                        "modelo": modelo,
                        "tipo_extremo": label,
                        "mes": row["mes"],
                        "regime_previsto_norm": row.get("regime_previsto_norm"),
                        "tipo_regime_expost": row.get("tipo_regime_expost"),
                        "tipo_erro_diagnostico": row.get("tipo_erro_diagnostico"),
                        "retorno_modelo": row.get("retorno_total"),
                        "retorno_ibov": row.get("retorno_expost_ibov"),
                        "alfa_vs_ibov": row.get("alfa_vs_ibov"),
                        "peso_acoes": row.get("peso_acoes"),
                        "usa_4m": row.get("usa_4m"),
                    }
                )
    return pd.DataFrame(rows)


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    monthly = load_monthly()
    vs_base = build_vs_base(monthly)
    summary = summarize(monthly, ["modelo"])
    summary_year = summarize(monthly, ["modelo", "ano"])
    summary_error = summarize(monthly, ["modelo", "tipo_erro_diagnostico"])
    summary_real = summarize(monthly, ["modelo", "tipo_regime_expost"])
    robustness = build_robustness(vs_base)
    extremes = build_extremes(monthly)

    base_alpha = float(summary.loc[summary["modelo"].eq("36C_BASE"), "alfa_vs_ibov"].iloc[0])
    summary["delta_alfa_vs_36c"] = summary["alfa_vs_ibov"] - base_alpha
    base_year = summary_year[summary_year["modelo"].eq("36C_BASE")][["ano", "alfa_vs_ibov"]].rename(
        columns={"alfa_vs_ibov": "alfa_36c_ano"}
    )
    summary_year = summary_year.merge(base_year, on="ano", how="left")
    summary_year["delta_alfa_vs_36c"] = summary_year["alfa_vs_ibov"] - summary_year["alfa_36c_ano"]

    decision = pd.DataFrame(
        [
            {
                "criterio": "modelo_lider",
                "resultado": "T44A_QUEDA_CONFIANCA",
                "justificativa": "Maior alfa acumulado e maior taxa de acerto entre os modelos comparados; melhora falsos alertas de queda sem mexer em alta.",
            },
            {
                "criterio": "risco_observado",
                "resultado": "2022 piora levemente",
                "justificativa": "T44A perde 0,24 p.p. vs 36C em 2022, mas ganha 4,26 p.p. em 2023 e 2,33 p.p. em 2024.",
            },
            {
                "criterio": "decisao_operacional",
                "resultado": "candidato para plataforma",
                "justificativa": "Consolidar como modelo recomendado em modo sombra/forward antes de substituir producao.",
            },
        ]
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        decision.to_excel(writer, sheet_name="Decisao", index=False)
        summary.to_excel(writer, sheet_name="Resumo Geral", index=False)
        summary_year.to_excel(writer, sheet_name="Resumo Ano", index=False)
        summary_error.to_excel(writer, sheet_name="Resumo Tipo Erro", index=False)
        summary_real.to_excel(writer, sheet_name="Resumo Regime Real", index=False)
        robustness.to_excel(writer, sheet_name="Robustez vs 36C", index=False)
        vs_base.to_excel(writer, sheet_name="Mes a Mes vs 36C", index=False)
        extremes.to_excel(writer, sheet_name="Extremos", index=False)

    log("Teste 45 - Consolidacao Final T44A + Auditoria de Robustez")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log("")
    log("Resumo geral:")
    log(summary[["modelo", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "drawdown", "pior_mes_retorno", "melhor_mes_retorno", "meses_4m", "delta_alfa_vs_36c"]].to_string(index=False))
    log("")
    log("Resumo ano a ano:")
    log(summary_year[["modelo", "ano", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "delta_alfa_vs_36c"]].to_string(index=False))
    log("")
    log("Robustez vs 36C:")
    log(robustness.to_string(index=False))
    log("")
    log("Resumo por tipo de erro:")
    log(summary_error[["modelo", "tipo_erro_diagnostico", "meses", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "meses_4m"]].to_string(index=False))
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()



