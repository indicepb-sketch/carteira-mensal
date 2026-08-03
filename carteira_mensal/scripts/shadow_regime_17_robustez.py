from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = ROOT / "output" / "excel" / "shadow_regime_16_risk_on_off.xlsx"
OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_regime_17_robustez.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_regime_17_robustez.log"

FOCUS_SCENARIOS = [
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


def max_drawdown(series: pd.Series) -> float:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    running_max = vals.cummax()
    dd = vals / running_max - 1.0
    return float(dd.min())


def scenario_metrics(group: pd.DataFrame, baseline_by_month: pd.DataFrame) -> dict[str, Any]:
    g = group.sort_values("mes").copy()
    ret = pd.to_numeric(g["retorno_expost_sombra_defensivo"], errors="coerce")
    ibov = pd.to_numeric(g["retorno_expost_ibov"], errors="coerce")
    alpha = pd.to_numeric(g["alfa_sombra_defensivo"], errors="coerce")
    equity = (1.0 + ret.fillna(0)).cumprod()
    ibov_equity = (1.0 + ibov.fillna(0)).cumprod()
    relative_equity = equity / ibov_equity

    merged = g[["mes", "bucket_regime"]].merge(
        baseline_by_month[["mes", "bucket_regime"]].rename(columns={"bucket_regime": "bucket_13b"}),
        on="mes",
        how="left",
    )
    changed = merged["bucket_regime"].astype(str).ne(merged["bucket_13b"].astype(str))

    return {
        "cenario": str(g["cenario"].iloc[0]),
        "meses": int(len(g)),
        "retorno_carteira": compound(ret),
        "retorno_ibov": compound(ibov),
        "alfa_composto": compound(ret) - compound(ibov),
        "alfa_medio_mensal": float(alpha.mean()) if alpha.notna().any() else np.nan,
        "mediana_alfa_mensal": float(alpha.median()) if alpha.notna().any() else np.nan,
        "meses_bateu_ibov": int((alpha > 0).sum()),
        "taxa_meses_bateu_ibov": float((alpha > 0).mean()) if len(alpha) else np.nan,
        "pior_alfa_mensal": float(alpha.min()) if alpha.notna().any() else np.nan,
        "melhor_alfa_mensal": float(alpha.max()) if alpha.notna().any() else np.nan,
        "vol_alfa_mensal": float(alpha.std(ddof=0)) if alpha.notna().any() else np.nan,
        "drawdown_carteira": max_drawdown(equity),
        "drawdown_relativo_vs_ibov": max_drawdown(relative_equity),
        "n_mudancas_regime_vs_13b": int(changed.sum()),
        "pct_mudancas_regime_vs_13b": float(changed.mean()) if len(changed) else np.nan,
        "meses_mudados_vs_13b": ", ".join(merged.loc[changed, "mes"].astype(str).tolist()),
    }


def yearly_metrics(details: pd.DataFrame, baseline_by_month: pd.DataFrame) -> pd.DataFrame:
    rows = []
    frame = details.copy()
    frame["ano"] = frame["mes"].astype(str).str.slice(0, 4)
    for (scenario, year), group in frame.groupby(["cenario", "ano"], sort=False):
        row = scenario_metrics(group, baseline_by_month)
        row["ano"] = year
        rows.append(row)
    return pd.DataFrame(rows)


def regime_metrics(details: pd.DataFrame, baseline_by_month: pd.DataFrame) -> pd.DataFrame:
    rows = []
    frame = details.copy()
    if "tipo_regime_expost" not in frame.columns:
        frame["tipo_regime_expost"] = np.select(
            [
                pd.to_numeric(frame["retorno_expost_ibov"], errors="coerce") >= 0,
                pd.to_numeric(frame["retorno_expost_ibov"], errors="coerce") <= -0.03,
            ],
            ["alta", "queda_forte"],
            default="queda_leve",
        )
    for (scenario, regime), group in frame.groupby(["cenario", "tipo_regime_expost"], sort=False):
        row = scenario_metrics(group, baseline_by_month)
        row["tipo_regime_expost"] = regime
        row["meses_lista"] = ", ".join(group["mes"].astype(str).tolist())
        rows.append(row)
    return pd.DataFrame(rows)


def monthly_delta(details: pd.DataFrame) -> pd.DataFrame:
    baseline = details[details["cenario"].eq("13b_conservador")][
        ["mes", "bucket_regime", "retorno_expost_sombra_defensivo", "alfa_sombra_defensivo"]
    ].rename(
        columns={
            "bucket_regime": "bucket_13b",
            "retorno_expost_sombra_defensivo": "retorno_13b",
            "alfa_sombra_defensivo": "alfa_13b",
        }
    )
    out = details.merge(baseline, on="mes", how="left")
    out["mudou_regime_vs_13b"] = out["bucket_regime"].astype(str).ne(out["bucket_13b"].astype(str))
    out["delta_retorno_vs_13b"] = pd.to_numeric(out["retorno_expost_sombra_defensivo"], errors="coerce") - pd.to_numeric(out["retorno_13b"], errors="coerce")
    out["delta_alfa_vs_13b"] = pd.to_numeric(out["alfa_sombra_defensivo"], errors="coerce") - pd.to_numeric(out["alfa_13b"], errors="coerce")
    cols = [
        "cenario",
        "mes",
        "bucket_regime",
        "bucket_13b",
        "mudou_regime_vs_13b",
        "retorno_expost_sombra_defensivo",
        "retorno_expost_ibov",
        "alfa_sombra_defensivo",
        "retorno_13b",
        "alfa_13b",
        "delta_retorno_vs_13b",
        "delta_alfa_vs_13b",
        "tickers_pesos_sombra",
        "motivo_regime_16",
    ]
    return out[[c for c in cols if c in out.columns]]


def decision_table(summary: pd.DataFrame, by_year: pd.DataFrame, by_regime: pd.DataFrame) -> pd.DataFrame:
    rows = []
    base = summary[summary["cenario"].eq("13b_conservador")].iloc[0]
    for _, row in summary.iterrows():
        scenario = row["cenario"]
        y = by_year[by_year["cenario"].eq(scenario)]
        r = by_regime[by_regime["cenario"].eq(scenario)]
        alpha_2024 = float(y.loc[y["ano"].eq("2024"), "alfa_composto"].iloc[0]) if (y["ano"].eq("2024")).any() else np.nan
        alpha_2025 = float(y.loc[y["ano"].eq("2025"), "alfa_composto"].iloc[0]) if (y["ano"].eq("2025")).any() else np.nan
        alpha_2026 = float(y.loc[y["ano"].eq("2026"), "alfa_composto"].iloc[0]) if (y["ano"].eq("2026")).any() else np.nan
        high_alpha = float(r.loc[r["tipo_regime_expost"].eq("alta"), "alfa_composto"].iloc[0]) if (r["tipo_regime_expost"].eq("alta")).any() else np.nan
        down = r[r["tipo_regime_expost"].astype(str).str.contains("queda", na=False)]
        down_alpha = compound(pd.Series(dtype=float))
        if not down.empty:
            # Approximation for reading: sum is more stable here because each row is already a compound over different subsets.
            down_alpha = float(down["alfa_composto"].sum())
        rows.append({
            "cenario": scenario,
            "delta_alfa_total_vs_13b": row["alfa_composto"] - base["alfa_composto"],
            "delta_taxa_meses_bateu_vs_13b": row["taxa_meses_bateu_ibov"] - base["taxa_meses_bateu_ibov"],
            "delta_pior_mes_vs_13b": row["pior_alfa_mensal"] - base["pior_alfa_mensal"],
            "delta_drawdown_relativo_vs_13b": row["drawdown_relativo_vs_ibov"] - base["drawdown_relativo_vs_ibov"],
            "alfa_2024": alpha_2024,
            "alfa_2025": alpha_2025,
            "alfa_2026": alpha_2026,
            "alfa_altas": high_alpha,
            "alfa_quedas_soma_subgrupos": down_alpha,
            "n_mudancas_regime_vs_13b": row["n_mudancas_regime_vs_13b"],
            "leitura": "",
        })
    out = pd.DataFrame(rows)
    for idx, row in out.iterrows():
        if row["cenario"] == "13b_conservador":
            out.at[idx, "leitura"] = "baseline atual"
        elif row["delta_alfa_total_vs_13b"] > 0 and row["delta_pior_mes_vs_13b"] >= -0.01 and row["alfa_2026"] >= 0:
            out.at[idx, "leitura"] = "candidata forte: melhora alfa sem piorar muito o pior mes e preserva 2026"
        elif row["delta_alfa_total_vs_13b"] > 0:
            out.at[idx, "leitura"] = "candidata com ressalva: melhora alfa, mas exige leitura de estabilidade"
        else:
            out.at[idx, "leitura"] = "nao recomendada: nao melhora o baseline"
    return out


def main() -> None:
    logs: list[str] = []

    def log(message: str) -> None:
        print(message, flush=True)
        logs.append(message)

    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Arquivo do Teste 16 nao encontrado: {INPUT_FILE}")

    details = pd.read_excel(INPUT_FILE, sheet_name="mes_a_mes")
    audit = pd.read_excel(INPUT_FILE, sheet_name="auditoria_regime")
    validation16 = pd.read_excel(INPUT_FILE, sheet_name="validacao_retorno")
    details = details[details["cenario"].isin(FOCUS_SCENARIOS)].copy()
    audit = audit[audit["cenario"].isin(FOCUS_SCENARIOS)].copy()
    details["tipo_regime_expost"] = np.select(
        [
            details["mes"].astype(str).eq("2026-06"),
            pd.to_numeric(details["retorno_expost_ibov"], errors="coerce") >= 0,
            pd.to_numeric(details["retorno_expost_ibov"], errors="coerce") <= -0.03,
        ],
        ["jun_oportunidade", "alta", "queda_forte"],
        default="queda_leve",
    )

    baseline_by_month = details[details["cenario"].eq("13b_conservador")][["mes", "bucket_regime"]].copy()
    summary = pd.DataFrame([scenario_metrics(g, baseline_by_month) for _, g in details.groupby("cenario", sort=False)])
    by_year = yearly_metrics(details, baseline_by_month)
    by_regime = regime_metrics(details, baseline_by_month)
    monthly = monthly_delta(details)
    decision = decision_table(summary, by_year, by_regime)

    accuracy = pd.read_excel(INPUT_FILE, sheet_name="assertividade_regime")
    accuracy = accuracy[accuracy["cenario"].isin(FOCUS_SCENARIOS)].copy()
    confusion = pd.read_excel(INPUT_FILE, sheet_name="matriz_regime_vs_ibov")
    confusion = confusion[confusion["cenario"].isin(FOCUS_SCENARIOS)].copy()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        decision.to_excel(writer, sheet_name="decisao_recomendada", index=False)
        summary.to_excel(writer, sheet_name="robustez_geral", index=False)
        by_year.to_excel(writer, sheet_name="robustez_por_ano", index=False)
        by_regime.to_excel(writer, sheet_name="robustez_por_regime", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes_delta", index=False)
        accuracy.to_excel(writer, sheet_name="assertividade_regime", index=False)
        confusion.to_excel(writer, sheet_name="matriz_regime_vs_ibov", index=False)
        audit.to_excel(writer, sheet_name="auditoria_regime", index=False)
        validation16[validation16["cenario"].isin(FOCUS_SCENARIOS)].to_excel(writer, sheet_name="validacao_retorno_t16", index=False)

    log("Teste 17 - Robustez da Camada Risk-On/Risk-Off")
    log(f"Fonte: {INPUT_FILE}")
    log("Resumo geral:")
    for _, row in summary.iterrows():
        log(
            f"  {row['cenario']}: alfa={pct(row['alfa_composto'])} | "
            f"meses_bateu={row['meses_bateu_ibov']}/{row['meses']} ({pct(row['taxa_meses_bateu_ibov'])}) | "
            f"pior_mes={pct(row['pior_alfa_mensal'])} | dd_rel={pct(row['drawdown_relativo_vs_ibov'])} | "
            f"mudancas_vs_13b={row['n_mudancas_regime_vs_13b']}"
        )
    log("Decisao:")
    for _, row in decision.iterrows():
        log(f"  {row['cenario']}: {row['leitura']} | delta_alfa_vs_13b={pct(row['delta_alfa_total_vs_13b'])}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
