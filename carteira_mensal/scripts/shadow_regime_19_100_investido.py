from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_16 = ROOT / "output" / "excel" / "shadow_regime_16_risk_on_off.xlsx"
INPUT_18A = ROOT / "output" / "excel" / "shadow_forward_18a_julho_regime17.xlsx"
OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_regime_19_100_investido.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_regime_19_100_investido.log"

SCENARIOS = [
    "13b_conservador",
    "risk_on_off_mm50",
    "risk_on_off_voto",
    "risk_on_off_confirmacao",
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


def max_drawdown(returns: pd.Series) -> float:
    vals = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    if vals.empty:
        return np.nan
    equity = (1.0 + vals).cumprod()
    dd = equity / equity.cummax() - 1.0
    return float(dd.min())


def summarize(group: pd.DataFrame, ret_col: str, alpha_col: str, label: str) -> dict[str, Any]:
    ret = pd.to_numeric(group[ret_col], errors="coerce")
    ibov = pd.to_numeric(group["retorno_expost_ibov"], errors="coerce")
    alpha = pd.to_numeric(group[alpha_col], errors="coerce")
    return {
        "cenario": str(group["cenario"].iloc[0]),
        "modo_exposicao": label,
        "meses": int(len(group)),
        "retorno_carteira": compound(ret),
        "retorno_ibov": compound(ibov),
        "alfa_composto": compound(ret) - compound(ibov),
        "meses_bateu_ibov": int((alpha > 0).sum()),
        "taxa_meses_bateu_ibov": float((alpha > 0).mean()) if len(alpha) else np.nan,
        "pior_alfa_mensal": float(alpha.min()) if alpha.notna().any() else np.nan,
        "melhor_alfa_mensal": float(alpha.max()) if alpha.notna().any() else np.nan,
        "drawdown_carteira": max_drawdown(ret),
        "drawdown_relativo_vs_ibov": max_drawdown(ret - ibov),
    }


def add_realized_regime(frame: pd.DataFrame) -> pd.DataFrame:
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


def summarize_by(frame: pd.DataFrame, by: str, ret_col: str, alpha_col: str, label: str) -> pd.DataFrame:
    rows = []
    for keys, group in frame.groupby(["cenario", by], sort=False):
        row = summarize(group, ret_col, alpha_col, label)
        row[by] = keys[1]
        row["meses_lista"] = ", ".join(group["mes"].astype(str).tolist())
        rows.append(row)
    return pd.DataFrame(rows)


def monthly_delta(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["retorno_100_investido"] = pd.to_numeric(out["retorno_expost_sombra"], errors="coerce")
    out["alfa_100_investido"] = out["retorno_100_investido"] - pd.to_numeric(out["retorno_expost_ibov"], errors="coerce")
    out["delta_ret_100_vs_defensivo"] = out["retorno_100_investido"] - pd.to_numeric(out["retorno_expost_sombra_defensivo"], errors="coerce")
    out["delta_alfa_100_vs_defensivo"] = out["alfa_100_investido"] - pd.to_numeric(out["alfa_sombra_defensivo"], errors="coerce")
    cols = [
        "cenario",
        "mes",
        "bucket_regime",
        "tipo_regime_expost",
        "exposicao_defensiva",
        "retorno_100_investido",
        "retorno_expost_sombra_defensivo",
        "retorno_expost_ibov",
        "alfa_100_investido",
        "alfa_sombra_defensivo",
        "delta_ret_100_vs_defensivo",
        "delta_alfa_100_vs_defensivo",
        "tickers_pesos_sombra",
    ]
    return out[[c for c in cols if c in out.columns]]


def july_100pct_summary() -> pd.DataFrame:
    if not INPUT_18A.exists():
        return pd.DataFrame()
    partial = pd.read_excel(INPUT_18A, sheet_name="Ativos Parcial")
    if partial.empty:
        return pd.DataFrame()
    rows = []
    for scenario, group in partial.groupby("cenario", sort=False):
        stocks = group[~group["ticker"].astype(str).str.upper().isin(["CDI", "CAIXA"])].copy()
        weight_sum = pd.to_numeric(stocks["peso_recomendado"], errors="coerce").sum()
        if weight_sum > 0:
            weights_100 = pd.to_numeric(stocks["peso_recomendado"], errors="coerce") / weight_sum
            ret_100 = (weights_100 * pd.to_numeric(stocks["retorno_periodo"], errors="coerce")).sum()
        else:
            ret_100 = np.nan
        ibov = pd.read_excel(INPUT_18A, sheet_name="Resumo Parcial")
        ibov_val = pd.to_numeric(ibov.loc[ibov["cenario"].eq(scenario), "retorno_ibov_parcial"], errors="coerce")
        ibov_ret = float(ibov_val.iloc[0]) if not ibov_val.empty else np.nan
        rows.append(
            {
                "cenario": scenario,
                "data_avaliacao": str(stocks["data_avaliacao"].dropna().astype(str).max()) if "data_avaliacao" in stocks else "",
                "retorno_julho_parcial_100_acoes": ret_100,
                "retorno_ibov_parcial": ibov_ret,
                "alfa_parcial_100_vs_ibov": ret_100 - ibov_ret if pd.notna(ret_100) and pd.notna(ibov_ret) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    logs: list[str] = []

    def log(message: str) -> None:
        print(message, flush=True)
        logs.append(message)

    if not INPUT_16.exists():
        raise FileNotFoundError(INPUT_16)
    details = pd.read_excel(INPUT_16, sheet_name="mes_a_mes")
    details = details[details["cenario"].isin(SCENARIOS)].copy()
    details = add_realized_regime(details)
    monthly = monthly_delta(details)

    summary_100 = pd.DataFrame([summarize(g, "retorno_100_investido", "alfa_100_investido", "100pct_acoes") for _, g in monthly.groupby("cenario", sort=False)])
    summary_def = pd.DataFrame([summarize(g, "retorno_expost_sombra_defensivo", "alfa_sombra_defensivo", "exposicao_defensiva") for _, g in monthly.groupby("cenario", sort=False)])
    compare = summary_100.merge(
        summary_def,
        on="cenario",
        suffixes=("_100pct", "_defensivo"),
    )
    compare["delta_alfa_100_vs_defensivo"] = compare["alfa_composto_100pct"] - compare["alfa_composto_defensivo"]
    compare["delta_retorno_100_vs_defensivo"] = compare["retorno_carteira_100pct"] - compare["retorno_carteira_defensivo"]
    compare["delta_drawdown_100_vs_defensivo"] = compare["drawdown_carteira_100pct"] - compare["drawdown_carteira_defensivo"]

    by_year_100 = summarize_by(monthly.assign(ano=monthly["mes"].astype(str).str.slice(0, 4)), "ano", "retorno_100_investido", "alfa_100_investido", "100pct_acoes")
    by_regime_100 = summarize_by(monthly, "tipo_regime_expost", "retorno_100_investido", "alfa_100_investido", "100pct_acoes")
    july = july_100pct_summary()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        compare.to_excel(writer, sheet_name="comparativo_100_vs_def", index=False)
        summary_100.to_excel(writer, sheet_name="resumo_100_investido", index=False)
        by_year_100.to_excel(writer, sheet_name="100_por_ano", index=False)
        by_regime_100.to_excel(writer, sheet_name="100_por_regime", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes_100", index=False)
        july.to_excel(writer, sheet_name="julho_parcial_100", index=False)

    log("Teste - modelos 100% investidos")
    log("Resumo 100% em acoes:")
    for _, row in summary_100.iterrows():
        log(
            f"  {row['cenario']}: retorno={pct(row['retorno_carteira'])}; "
            f"IBOV={pct(row['retorno_ibov'])}; alfa={pct(row['alfa_composto'])}; "
            f"bateu={row['meses_bateu_ibov']}/{row['meses']}; pior_mes={pct(row['pior_alfa_mensal'])}; "
            f"drawdown={pct(row['drawdown_carteira'])}"
        )
    log("Comparacao 100% vs defensivo:")
    for _, row in compare.iterrows():
        log(f"  {row['cenario']}: delta_alfa={pct(row['delta_alfa_100_vs_defensivo'])}; delta_drawdown={pct(row['delta_drawdown_100_vs_defensivo'])}")
    if not july.empty:
        log("Julho parcial 100% em acoes:")
        for _, row in july.iterrows():
            log(f"  {row['cenario']}: retorno={pct(row['retorno_julho_parcial_100_acoes'])}; IBOV={pct(row['retorno_ibov_parcial'])}; alfa={pct(row['alfa_parcial_100_vs_ibov'])}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
