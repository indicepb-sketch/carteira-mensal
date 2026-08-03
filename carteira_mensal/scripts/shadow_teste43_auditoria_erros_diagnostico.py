from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_42 = EXCEL_DIR / "shadow_teste42_falso_alerta_queda_4m.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste43_auditoria_erros_diagnostico.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste43_auditoria_erros_diagnostico.log"


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


def direcao_prevista(regime: Any) -> str:
    return "alta" if str(regime).strip().lower() == "alta" else "queda"


def direcao_real(ret_ibov: Any) -> str:
    value = pd.to_numeric(ret_ibov, errors="coerce")
    if pd.isna(value):
        return "indefinido"
    return "alta" if float(value) >= 0.0 else "queda"


def tipo_erro(prev: str, real: str) -> str:
    if prev == "alta" and real == "alta":
        return "acerto_ofensivo"
    if prev == "alta" and real == "queda":
        return "falso_positivo_alta"
    if prev == "queda" and real == "queda":
        return "acerto_defensivo"
    if prev == "queda" and real == "alta":
        return "falso_alerta_queda"
    return "indefinido"


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
                "meses": int(len(group)),
                "retorno_modelo_composto": ret_model,
                "retorno_ibov_composto": ret_ibov,
                "alfa_composto": ret_model - ret_ibov,
                "retorno_modelo_medio": float(pd.to_numeric(group["retorno_total"], errors="coerce").mean()),
                "retorno_ibov_medio": float(pd.to_numeric(group["retorno_expost_ibov"], errors="coerce").mean()),
                "alfa_medio": float(pd.to_numeric(group["alfa_vs_ibov"], errors="coerce").mean()),
                "taxa_bateu_ibov": float(pd.to_numeric(group["bateu_ibov"], errors="coerce").mean()),
                "drawdown_modelo": max_drawdown(group["retorno_total"]),
                "peso_acoes_medio": float(pd.to_numeric(group["peso_acoes"], errors="coerce").mean()),
                "peso_cdi_medio": float(pd.to_numeric(group["peso_cdi"], errors="coerce").mean()),
                "nota_media_formacao": float(pd.to_numeric(group["nota_media_formacao"], errors="coerce").mean()),
                "n_ativos_medio": float(pd.to_numeric(group["n_ativos_acoes_formacao"], errors="coerce").mean()),
                "beta_medio_formacao": float(pd.to_numeric(group["beta_carteira_formacao"], errors="coerce").mean()),
                "queda_confirmada_pct": float(pd.to_numeric(group["queda_confirmada_28d"], errors="coerce").mean()),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def load_monthly() -> pd.DataFrame:
    if not INPUT_42.exists():
        raise FileNotFoundError(INPUT_42)
    df = pd.read_excel(INPUT_42, sheet_name="Mes a Mes")
    df["mes"] = df["mes"].astype(str).str[:7]
    df["ano"] = df["mes"].str[:4]
    df["direcao_prevista"] = df["regime_previsto_norm"].map(direcao_prevista)
    df["direcao_real_ibov"] = df["retorno_expost_ibov"].map(direcao_real)
    df["tipo_erro_diagnostico"] = [
        tipo_erro(prev, real) for prev, real in zip(df["direcao_prevista"], df["direcao_real_ibov"])
    ]
    df["diagnostico_acertou_direcao"] = df["direcao_prevista"].eq(df["direcao_real_ibov"])
    return df


def build_impacts(monthly: pd.DataFrame) -> pd.DataFrame:
    base = monthly[monthly["cenario_t42"].eq("BASE_T36C")][
        ["mes", "retorno_total", "alfa_vs_ibov"]
    ].rename(columns={"retorno_total": "retorno_base", "alfa_vs_ibov": "alfa_base"})
    rows = []
    for scenario, group in monthly[~monthly["cenario_t42"].eq("BASE_T36C")].groupby("cenario_t42", sort=False):
        merged = group.merge(base, on="mes", how="left")
        merged["delta_retorno_vs_base"] = merged["retorno_total"] - merged["retorno_base"]
        merged["delta_alfa_vs_base"] = merged["alfa_vs_ibov"] - merged["alfa_base"]
        rows.append(merged)
    return pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    monthly = load_monthly()
    base = monthly[monthly["cenario_t42"].eq("BASE_T36C")].copy()
    impacts = build_impacts(monthly)

    resumo_tipo = summarize(base, ["tipo_erro_diagnostico"])
    resumo_ano_tipo = summarize(base, ["ano", "tipo_erro_diagnostico"])
    resumo_regime_previsto = summarize(base, ["regime_previsto_norm", "direcao_real_ibov"])
    resumo_cenario_tipo = summarize(monthly, ["cenario_t42", "tipo_erro_diagnostico"])

    impacto_resumo = pd.DataFrame()
    if not impacts.empty:
        impacto_resumo = impacts.groupby(["cenario_t42", "tipo_erro_diagnostico"], as_index=False).agg(
            meses=("mes", "count"),
            delta_alfa_medio_vs_base=("delta_alfa_vs_base", "mean"),
            delta_alfa_total_simples_vs_base=("delta_alfa_vs_base", "sum"),
            meses_melhorou_vs_base=("delta_alfa_vs_base", lambda s: int(pd.to_numeric(s, errors="coerce").gt(0).sum())),
            usa_4m_meses=("usa_4m", "sum"),
        )
        impacto_resumo["taxa_melhora_vs_base"] = impacto_resumo["meses_melhorou_vs_base"] / impacto_resumo["meses"]

    matriz_confusao = pd.crosstab(base["direcao_prevista"], base["direcao_real_ibov"], margins=True).reset_index()
    matriz_tipo_erro_ano = pd.crosstab(base["ano"], base["tipo_erro_diagnostico"], margins=True).reset_index()

    piores_erros = base.sort_values("alfa_vs_ibov", ascending=True).head(15)
    melhores_erros = base.sort_values("alfa_vs_ibov", ascending=False).head(15)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        base.to_excel(writer, sheet_name="Mes a Mes Base", index=False)
        resumo_tipo.to_excel(writer, sheet_name="Resumo Tipo Erro", index=False)
        resumo_ano_tipo.to_excel(writer, sheet_name="Resumo Ano Tipo", index=False)
        resumo_regime_previsto.to_excel(writer, sheet_name="Resumo Previsto x Real", index=False)
        resumo_cenario_tipo.to_excel(writer, sheet_name="Resumo Cenarios x Erro", index=False)
        impacto_resumo.to_excel(writer, sheet_name="Impacto Cenarios x Erro", index=False)
        matriz_confusao.to_excel(writer, sheet_name="Matriz Confusao", index=False)
        matriz_tipo_erro_ano.to_excel(writer, sheet_name="Matriz Erro Ano", index=False)
        piores_erros.to_excel(writer, sheet_name="Piores Alfas Base", index=False)
        melhores_erros.to_excel(writer, sheet_name="Melhores Alfas Base", index=False)
        monthly.to_excel(writer, sheet_name="Todos Cenarios Mes", index=False)

    acerto_diag = float(base["diagnostico_acertou_direcao"].mean())
    log("Teste 43 - Auditoria dos Erros do Diagnostico Inicial")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Taxa de acerto direcional do diagnostico inicial: {pct(acerto_diag)}")
    log("")
    log("Resumo por tipo de erro/acerto - BASE_T36C:")
    log(
        resumo_tipo[
            [
                "tipo_erro_diagnostico",
                "meses",
                "retorno_modelo_composto",
                "retorno_ibov_composto",
                "alfa_composto",
                "alfa_medio",
                "taxa_bateu_ibov",
                "peso_acoes_medio",
                "nota_media_formacao",
                "beta_medio_formacao",
            ]
        ].to_string(index=False)
    )
    log("")
    log("Impacto dos cenarios alternativos por tipo de erro:")
    if impacto_resumo.empty:
        log("Sem cenarios alternativos.")
    else:
        log(
            impacto_resumo[
                [
                    "cenario_t42",
                    "tipo_erro_diagnostico",
                    "meses",
                    "delta_alfa_medio_vs_base",
                    "delta_alfa_total_simples_vs_base",
                    "taxa_melhora_vs_base",
                    "usa_4m_meses",
                ]
            ].to_string(index=False)
        )
    log("")
    log("Matriz de confusao direcional:")
    log(matriz_confusao.to_string(index=False))
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
