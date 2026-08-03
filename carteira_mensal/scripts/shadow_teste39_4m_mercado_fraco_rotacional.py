from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_38 = EXCEL_DIR / "shadow_teste38_sensibilidade_janela_retorno.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste39_4m_mercado_fraco_rotacional.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste39_4m_mercado_fraco_rotacional.log"


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


def summarize(monthly: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, group in monthly.groupby(group_cols, dropna=False, sort=False):
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
                "meses_bateu_ibov": int(pd.to_numeric(group["alfa_vs_ibov"], errors="coerce").gt(0).sum()),
                "taxa_acerto": float(pd.to_numeric(group["alfa_vs_ibov"], errors="coerce").gt(0).mean()),
                "drawdown": max_drawdown(group["retorno_total"]),
                "peso_acoes_medio": float(pd.to_numeric(group["peso_acoes"], errors="coerce").mean()),
                "maior_peso": float(pd.to_numeric(group["maior_peso"], errors="coerce").max()),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def build_conditional(monthly38: pd.DataFrame, portfolio38: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = monthly38[monthly38["cenario_t38"].astype(str).eq("BASE_T36C")].copy()
    j4 = monthly38[monthly38["cenario_t38"].astype(str).eq("JANELA_4M")].copy()
    j4_idx = j4.set_index("mes")

    selected_rows = []
    for _, row in base.iterrows():
        mes = str(row["mes"])
        regime = str(row.get("regime_previsto_norm", "")).strip().lower()
        use_4m = regime in {"queda_leve", "queda_forte"}
        src = j4_idx.loc[mes].copy() if use_4m and mes in j4_idx.index else row.copy()
        src["mes"] = mes
        src["cenario_t39"] = "T39_4M_MERCADO_FRACO_ROTACIONAL"
        src["cenario_origem_t39"] = "JANELA_4M" if use_4m else "BASE_T36C"
        src["regra_t39"] = "usa_4m_em_queda_prevista" if use_4m else "mantem_base_em_alta_prevista"
        selected_rows.append(src)

    conditional_monthly = pd.DataFrame(selected_rows)
    conditional_monthly["alfa_vs_ibov"] = conditional_monthly["retorno_total"] - conditional_monthly["retorno_expost_ibov"]
    conditional_monthly["bateu_ibov"] = conditional_monthly["alfa_vs_ibov"] > 0

    parts = []
    for _, row in conditional_monthly.iterrows():
        src = str(row["cenario_origem_t39"])
        mes = str(row["mes"])
        chunk = portfolio38[(portfolio38["cenario_t38"].astype(str).eq(src)) & (portfolio38["mes"].astype(str).eq(mes))].copy()
        chunk["cenario_t39"] = "T39_4M_MERCADO_FRACO_ROTACIONAL"
        chunk["cenario_origem_t39"] = src
        chunk["regra_t39"] = str(row["regra_t39"])
        parts.append(chunk)
    conditional_portfolio = pd.concat(parts, ignore_index=True)

    baseline_monthly = base.copy()
    baseline_monthly["cenario_t39"] = "BASE_T36C"
    baseline_monthly["cenario_origem_t39"] = "BASE_T36C"
    baseline_monthly["regra_t39"] = "baseline"
    baseline_portfolio = portfolio38[portfolio38["cenario_t38"].astype(str).eq("BASE_T36C")].copy()
    baseline_portfolio["cenario_t39"] = "BASE_T36C"
    baseline_portfolio["cenario_origem_t39"] = "BASE_T36C"
    baseline_portfolio["regra_t39"] = "baseline"

    monthly = pd.concat([baseline_monthly, conditional_monthly], ignore_index=True, sort=False)
    portfolio = pd.concat([baseline_portfolio, conditional_portfolio], ignore_index=True, sort=False)
    return monthly, portfolio


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    if not INPUT_38.exists():
        raise FileNotFoundError(f"Rode o Teste 38 antes: {INPUT_38}")

    monthly38 = pd.read_excel(INPUT_38, sheet_name="Mes a Mes")
    portfolio38 = pd.read_excel(INPUT_38, sheet_name="Carteiras")
    audit38 = pd.read_excel(INPUT_38, sheet_name="Auditoria Sinais")

    monthly, portfolio = build_conditional(monthly38, portfolio38)
    summary = summarize(monthly, ["cenario_t39"])
    summary_year = summarize(monthly.assign(ano=monthly["mes"].astype(str).str[:4]), ["cenario_t39", "ano"])
    summary_pred_regime = summarize(monthly, ["cenario_t39", "regime_previsto_norm"])
    summary_real_regime = summarize(monthly, ["cenario_t39", "tipo_regime_expost"])

    base_alpha = float(summary.loc[summary["cenario_t39"].eq("BASE_T36C"), "alfa_vs_ibov"].iloc[0])
    summary["delta_alfa_vs_base"] = summary["alfa_vs_ibov"] - base_alpha

    base_year = summary_year[summary_year["cenario_t39"].eq("BASE_T36C")][["ano", "alfa_vs_ibov"]].rename(columns={"alfa_vs_ibov": "alfa_base_ano"})
    summary_year = summary_year.merge(base_year, on="ano", how="left")
    summary_year["delta_alfa_vs_base"] = summary_year["alfa_vs_ibov"] - summary_year["alfa_base_ano"]

    validation = portfolio.groupby(["cenario_t39", "mes"], as_index=False).agg(
        soma_pesos=("peso_efetivo_carteira_total", "sum"),
        contribuicao_total=("contribuicao_retorno_total", "sum"),
        maior_peso=("peso_efetivo_carteira_total", "max"),
        n_linhas=("ticker", "count"),
    )
    validation = validation.merge(monthly[["cenario_t39", "mes", "retorno_total", "retorno_expost_ibov", "alfa_vs_ibov"]], on=["cenario_t39", "mes"], how="left")
    validation["diferenca_contribuicao_vs_retorno"] = validation["contribuicao_total"] - validation["retorno_total"]
    validation["pesos_fecham_100"] = validation["soma_pesos"].sub(1.0).abs().lt(1e-8)
    validation["retorno_consistente"] = validation["diferenca_contribuicao_vs_retorno"].abs().lt(1e-8)

    switched = monthly[monthly["cenario_t39"].eq("T39_4M_MERCADO_FRACO_ROTACIONAL") & monthly["cenario_origem_t39"].eq("JANELA_4M")].copy()
    comparison = monthly.pivot_table(index="mes", columns="cenario_t39", values=["retorno_total", "alfa_vs_ibov"], aggfunc="first")
    comparison.columns = [f"{a}_{b}" for a, b in comparison.columns]
    comparison = comparison.reset_index()
    if {"alfa_vs_ibov_BASE_T36C", "alfa_vs_ibov_T39_4M_MERCADO_FRACO_ROTACIONAL"}.issubset(comparison.columns):
        comparison["delta_alfa_t39_vs_base"] = comparison["alfa_vs_ibov_T39_4M_MERCADO_FRACO_ROTACIONAL"] - comparison["alfa_vs_ibov_BASE_T36C"]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo Geral", index=False)
        summary_year.to_excel(writer, sheet_name="Resumo Ano", index=False)
        summary_pred_regime.to_excel(writer, sheet_name="Resumo Regime Previsto", index=False)
        summary_real_regime.to_excel(writer, sheet_name="Resumo Regime Real", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        comparison.to_excel(writer, sheet_name="Comparativo Mensal", index=False)
        switched.to_excel(writer, sheet_name="Meses Usando 4M", index=False)
        portfolio.to_excel(writer, sheet_name="Carteiras", index=False)
        audit38[audit38["cenario_t38"].astype(str).eq("JANELA_4M")].to_excel(writer, sheet_name="Auditoria Sinal 4M", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)

    log("Teste 39 - Retorno 4M Apenas em Mercado Fraco/Rotacional")
    log("Regra: usa JANELA_4M quando regime previsto e queda_leve/queda_forte; caso contrario mantem BASE_T36C.")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log("")
    log("Resumo geral:")
    log(summary[["cenario_t39", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "drawdown", "delta_alfa_vs_base"]].to_string(index=False))
    log("")
    log("Resumo por ano:")
    log(summary_year[["cenario_t39", "ano", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "delta_alfa_vs_base"]].to_string(index=False))
    log("")
    log(f"Meses em que a regra trocou para 4M: {len(switched)}")
    log(switched[["mes", "regime_previsto_norm", "tipo_regime_expost", "retorno_total", "retorno_expost_ibov", "alfa_vs_ibov"]].to_string(index=False))
    invalid = validation[(~validation["pesos_fecham_100"]) | (~validation["retorno_consistente"])]
    log("")
    log(f"Validacao: {'OK' if invalid.empty else 'FALHAS=' + str(len(invalid))}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()

