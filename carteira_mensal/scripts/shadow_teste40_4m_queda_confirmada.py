from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_38 = EXCEL_DIR / "shadow_teste38_sensibilidade_janela_retorno.xlsx"
INPUT_36 = EXCEL_DIR / "shadow_teste36_exposicao_regime_2.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste40_4m_queda_confirmada.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste40_4m_queda_confirmada.log"
BASE_SCENARIO = "T36C_QUALIDADE"


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
    if vals.empty:
        return np.nan
    equity = (1.0 + vals).cumprod()
    return float((equity / equity.cummax() - 1.0).min())


def summarize(monthly: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, group in monthly.groupby(group_cols, dropna=False, sort=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        ret = compound(group["retorno_total"])
        ibov = compound(group["retorno_expost_ibov"])
        row.update(
            {
                "meses": len(group),
                "retorno_modelo": ret,
                "retorno_ibov": ibov,
                "alfa_vs_ibov": ret - ibov,
                "meses_bateu_ibov": int(pd.to_numeric(group["alfa_vs_ibov"], errors="coerce").gt(0).sum()),
                "taxa_acerto": float(pd.to_numeric(group["alfa_vs_ibov"], errors="coerce").gt(0).mean()),
                "drawdown": max_drawdown(group["retorno_total"]),
                "peso_acoes_medio": float(pd.to_numeric(group["peso_acoes"], errors="coerce").mean()),
                "maior_peso": float(pd.to_numeric(group["maior_peso"], errors="coerce").max()),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def load_queda_confirmada() -> pd.DataFrame:
    t36 = pd.read_excel(INPUT_36, sheet_name="Mes a Mes")
    t36 = t36[t36["cenario_teste36"].astype(str).eq(BASE_SCENARIO)].copy()
    t36["mes"] = t36["mes"].astype(str)
    cols = ["mes", "queda_confirmada_28d", "bucket_regime_previsto"]
    return t36[[c for c in cols if c in t36.columns]].drop_duplicates("mes")


def build_conditional(monthly38: pd.DataFrame, portfolio38: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    confirm = load_queda_confirmada()
    base = monthly38[monthly38["cenario_t38"].astype(str).eq("BASE_T36C")].copy()
    j4 = monthly38[monthly38["cenario_t38"].astype(str).eq("JANELA_4M")].copy()
    base = base.merge(confirm, on="mes", how="left")
    j4 = j4.merge(confirm, on="mes", how="left")
    j4_idx = j4.set_index("mes")

    selected_rows: list[pd.Series] = []
    for _, row in base.iterrows():
        mes = str(row["mes"])
        regime = str(row.get("regime_previsto_norm", "")).strip().lower()
        confirmed = bool(row.get("queda_confirmada_28d", False))
        use_4m = regime in {"queda_leve", "queda_forte"} and confirmed
        src = j4_idx.loc[mes].copy() if use_4m and mes in j4_idx.index else row.copy()
        src["mes"] = mes
        src["cenario_t40"] = "T40_4M_QUEDA_CONFIRMADA"
        src["cenario_origem_t40"] = "JANELA_4M" if use_4m else "BASE_T36C"
        src["regra_t40"] = "usa_4m_queda_prevista_e_confirmada" if use_4m else "mantem_base_sem_confirmacao"
        src["queda_confirmada_28d"] = confirmed
        selected_rows.append(src)
    conditional_monthly = pd.DataFrame(selected_rows)
    conditional_monthly["alfa_vs_ibov"] = conditional_monthly["retorno_total"] - conditional_monthly["retorno_expost_ibov"]
    conditional_monthly["bateu_ibov"] = conditional_monthly["alfa_vs_ibov"] > 0

    parts = []
    for _, row in conditional_monthly.iterrows():
        src = str(row["cenario_origem_t40"])
        mes = str(row["mes"])
        chunk = portfolio38[(portfolio38["cenario_t38"].astype(str).eq(src)) & (portfolio38["mes"].astype(str).eq(mes))].copy()
        chunk["cenario_t40"] = "T40_4M_QUEDA_CONFIRMADA"
        chunk["cenario_origem_t40"] = src
        chunk["regra_t40"] = str(row["regra_t40"])
        chunk["queda_confirmada_28d"] = bool(row.get("queda_confirmada_28d", False))
        parts.append(chunk)
    conditional_portfolio = pd.concat(parts, ignore_index=True)

    baseline_monthly = base.copy()
    baseline_monthly["cenario_t40"] = "BASE_T36C"
    baseline_monthly["cenario_origem_t40"] = "BASE_T36C"
    baseline_monthly["regra_t40"] = "baseline"
    baseline_portfolio = portfolio38[portfolio38["cenario_t38"].astype(str).eq("BASE_T36C")].copy()
    baseline_portfolio["cenario_t40"] = "BASE_T36C"
    baseline_portfolio["cenario_origem_t40"] = "BASE_T36C"
    baseline_portfolio["regra_t40"] = "baseline"
    baseline_portfolio = baseline_portfolio.merge(confirm, on="mes", how="left")

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
    if not INPUT_36.exists():
        raise FileNotFoundError(f"Arquivo base T36 nao encontrado: {INPUT_36}")

    monthly38 = pd.read_excel(INPUT_38, sheet_name="Mes a Mes")
    portfolio38 = pd.read_excel(INPUT_38, sheet_name="Carteiras")
    audit38 = pd.read_excel(INPUT_38, sheet_name="Auditoria Sinais")
    monthly, portfolio = build_conditional(monthly38, portfolio38)

    summary = summarize(monthly, ["cenario_t40"])
    summary_year = summarize(monthly.assign(ano=monthly["mes"].astype(str).str[:4]), ["cenario_t40", "ano"])
    summary_pred_regime = summarize(monthly, ["cenario_t40", "regime_previsto_norm"])
    summary_real_regime = summarize(monthly, ["cenario_t40", "tipo_regime_expost"])

    base_alpha = float(summary.loc[summary["cenario_t40"].eq("BASE_T36C"), "alfa_vs_ibov"].iloc[0])
    summary["delta_alfa_vs_base"] = summary["alfa_vs_ibov"] - base_alpha
    base_year = summary_year[summary_year["cenario_t40"].eq("BASE_T36C")][["ano", "alfa_vs_ibov"]].rename(columns={"alfa_vs_ibov": "alfa_base_ano"})
    summary_year = summary_year.merge(base_year, on="ano", how="left")
    summary_year["delta_alfa_vs_base"] = summary_year["alfa_vs_ibov"] - summary_year["alfa_base_ano"]

    validation = portfolio.groupby(["cenario_t40", "mes"], as_index=False).agg(
        soma_pesos=("peso_efetivo_carteira_total", "sum"),
        contribuicao_total=("contribuicao_retorno_total", "sum"),
        maior_peso=("peso_efetivo_carteira_total", "max"),
        n_linhas=("ticker", "count"),
    )
    validation = validation.merge(monthly[["cenario_t40", "mes", "retorno_total", "retorno_expost_ibov", "alfa_vs_ibov"]], on=["cenario_t40", "mes"], how="left")
    validation["diferenca_contribuicao_vs_retorno"] = validation["contribuicao_total"] - validation["retorno_total"]
    validation["pesos_fecham_100"] = validation["soma_pesos"].sub(1.0).abs().lt(1e-8)
    validation["retorno_consistente"] = validation["diferenca_contribuicao_vs_retorno"].abs().lt(1e-8)

    switched = monthly[monthly["cenario_t40"].eq("T40_4M_QUEDA_CONFIRMADA") & monthly["cenario_origem_t40"].eq("JANELA_4M")].copy()
    comparison = monthly.pivot_table(index="mes", columns="cenario_t40", values=["retorno_total", "alfa_vs_ibov"], aggfunc="first")
    comparison.columns = [f"{a}_{b}" for a, b in comparison.columns]
    comparison = comparison.reset_index()
    if {"alfa_vs_ibov_BASE_T36C", "alfa_vs_ibov_T40_4M_QUEDA_CONFIRMADA"}.issubset(comparison.columns):
        comparison["delta_alfa_t40_vs_base"] = comparison["alfa_vs_ibov_T40_4M_QUEDA_CONFIRMADA"] - comparison["alfa_vs_ibov_BASE_T36C"]

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

    log("Teste 40 - Retorno 4M Apenas com Queda Confirmada")
    log("Regra: usa JANELA_4M somente quando regime previsto e queda_leve/queda_forte E queda_confirmada_28d=True; caso contrario mantem BASE_T36C.")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log("")
    log("Resumo geral:")
    log(summary[["cenario_t40", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "drawdown", "delta_alfa_vs_base"]].to_string(index=False))
    log("")
    log("Resumo por ano:")
    log(summary_year[["cenario_t40", "ano", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "delta_alfa_vs_base"]].to_string(index=False))
    log("")
    log(f"Meses em que a regra trocou para 4M: {len(switched)}")
    log(switched[["mes", "regime_previsto_norm", "queda_confirmada_28d", "tipo_regime_expost", "retorno_total", "retorno_expost_ibov", "alfa_vs_ibov"]].to_string(index=False))
    invalid = validation[(~validation["pesos_fecham_100"]) | (~validation["retorno_consistente"])]
    log("")
    log(f"Validacao: {'OK' if invalid.empty else 'FALHAS=' + str(len(invalid))}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
