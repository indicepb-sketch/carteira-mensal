from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_16 = ROOT / "output" / "excel" / "shadow_regime_16_risk_on_off.xlsx"
OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_regime_21_exposicao_sem_zerar.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_regime_21_exposicao_sem_zerar.log"

SCENARIOS = [
    "13b_conservador",
    "risk_on_off_mm50",
    "risk_on_off_voto",
    "risk_on_off_confirmacao",
]

PROFILES = [
    {
        "perfil_exposicao": "atual_teste16",
        "descricao": "Exposicao original do Teste 16.",
        "queda_leve": None,
        "queda_forte": None,
    },
    {
        "perfil_exposicao": "100_alta_40_20",
        "descricao": "100% em alta; 40% em queda leve; 20% em queda forte.",
        "queda_leve": 0.40,
        "queda_forte": 0.20,
    },
    {
        "perfil_exposicao": "100_alta_50_20",
        "descricao": "100% em alta; 50% em queda leve; 20% em queda forte.",
        "queda_leve": 0.50,
        "queda_forte": 0.20,
    },
    {
        "perfil_exposicao": "100_alta_60_20",
        "descricao": "100% em alta; 60% em queda leve; 20% em queda forte.",
        "queda_leve": 0.60,
        "queda_forte": 0.20,
    },
    {
        "perfil_exposicao": "100_alta_40_30",
        "descricao": "100% em alta; 40% em queda leve; 30% em queda forte.",
        "queda_leve": 0.40,
        "queda_forte": 0.30,
    },
    {
        "perfil_exposicao": "100_alta_50_30",
        "descricao": "100% em alta; 50% em queda leve; 30% em queda forte.",
        "queda_leve": 0.50,
        "queda_forte": 0.30,
    },
    {
        "perfil_exposicao": "100_alta_60_30",
        "descricao": "100% em alta; 60% em queda leve; 30% em queda forte.",
        "queda_leve": 0.60,
        "queda_forte": 0.30,
    },
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
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


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


def exposure_for_row(row: pd.Series, profile: dict[str, Any]) -> float:
    if profile["perfil_exposicao"] == "atual_teste16":
        return float(row.get("exposicao_defensiva", 1.0))
    bucket = str(row.get("bucket_regime", "")).lower()
    if bucket in {"alta", "jun_oportunidade", "oportunidade"}:
        return 1.0
    if bucket == "queda_forte":
        return float(profile["queda_forte"])
    if bucket == "queda_leve":
        return float(profile["queda_leve"])
    return 1.0


def summarize(group: pd.DataFrame) -> dict[str, Any]:
    ret = pd.to_numeric(group["retorno_modelo"], errors="coerce")
    ibov = pd.to_numeric(group["retorno_expost_ibov"], errors="coerce")
    alpha = pd.to_numeric(group["alfa_modelo"], errors="coerce")
    return {
        "cenario": str(group["cenario"].iloc[0]),
        "perfil_exposicao": str(group["perfil_exposicao"].iloc[0]),
        "descricao": str(group["descricao"].iloc[0]),
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
        "exposicao_media": float(pd.to_numeric(group["exposicao_modelo"], errors="coerce").mean()),
    }


def build_monthly(base: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in base.iterrows():
        for profile in PROFILES:
            exposure = exposure_for_row(row, profile)
            ret_100 = float(row["retorno_expost_sombra_100pct"])
            ibov = float(row["retorno_expost_ibov"])
            ret_model = ret_100 * exposure
            rows.append(
                {
                    "cenario": row["cenario"],
                    "mes": row["mes"],
                    "bucket_regime": row["bucket_regime"],
                    "tipo_regime_expost": row["tipo_regime_expost"],
                    "perfil_exposicao": profile["perfil_exposicao"],
                    "descricao": profile["descricao"],
                    "exposicao_modelo": exposure,
                    "peso_defensivo": 1.0 - exposure,
                    "retorno_100_acoes": ret_100,
                    "retorno_modelo": ret_model,
                    "retorno_expost_ibov": ibov,
                    "alfa_modelo": ret_model - ibov,
                    "tickers_pesos_sombra": row.get("tickers_pesos_sombra", ""),
                }
            )
    return pd.DataFrame(rows)


def summarize_by(monthly: pd.DataFrame, by_cols: list[str]) -> pd.DataFrame:
    rows = []
    for _, group in monthly.groupby(by_cols, sort=False):
        row = summarize(group)
        for col in by_cols:
            row[col] = group[col].iloc[0]
        row["meses_lista"] = ", ".join(group["mes"].astype(str).tolist())
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    logs: list[str] = []

    def log(message: str) -> None:
        print(message, flush=True)
        logs.append(message)

    if not INPUT_16.exists():
        raise FileNotFoundError(INPUT_16)

    base = pd.read_excel(INPUT_16, sheet_name="mes_a_mes")
    base = base[base["cenario"].isin(SCENARIOS)].copy()
    base = add_realized_regime(base)
    base["retorno_expost_sombra_100pct"] = pd.to_numeric(base["retorno_expost_sombra_100pct"], errors="coerce")
    base["retorno_expost_ibov"] = pd.to_numeric(base["retorno_expost_ibov"], errors="coerce")
    base = base.dropna(subset=["retorno_expost_sombra_100pct", "retorno_expost_ibov"])

    monthly = build_monthly(base)
    summary = pd.DataFrame([summarize(g) for _, g in monthly.groupby(["cenario", "perfil_exposicao"], sort=False)])
    by_year = summarize_by(monthly.assign(ano=monthly["mes"].astype(str).str.slice(0, 4)), ["cenario", "perfil_exposicao", "ano"])
    by_regime_realizado = summarize_by(monthly, ["cenario", "perfil_exposicao", "tipo_regime_expost"])
    by_regime_previsto = summarize_by(monthly, ["cenario", "perfil_exposicao", "bucket_regime"])

    control = summary[summary["perfil_exposicao"].eq("atual_teste16")][
        ["cenario", "retorno_carteira", "alfa_composto", "drawdown_carteira"]
    ].rename(
        columns={
            "retorno_carteira": "retorno_carteira_atual",
            "alfa_composto": "alfa_atual",
            "drawdown_carteira": "drawdown_atual",
        }
    )
    compare = summary.merge(control, on="cenario", how="left")
    compare["delta_alfa_vs_atual"] = compare["alfa_composto"] - compare["alfa_atual"]
    compare["delta_retorno_vs_atual"] = compare["retorno_carteira"] - compare["retorno_carteira_atual"]
    compare["delta_drawdown_vs_atual"] = compare["drawdown_carteira"] - compare["drawdown_atual"]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        compare.to_excel(writer, sheet_name="comparativo_perfis", index=False)
        summary.to_excel(writer, sheet_name="resumo", index=False)
        by_year.to_excel(writer, sheet_name="por_ano", index=False)
        by_regime_realizado.to_excel(writer, sheet_name="por_regime_realizado", index=False)
        by_regime_previsto.to_excel(writer, sheet_name="por_regime_previsto", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes", index=False)

    log("Teste 21 - exposicao por regime sem zerar carteira")
    log(f"Meses avaliados: {base['mes'].nunique()} ({base['mes'].min()} a {base['mes'].max()})")
    for scenario in SCENARIOS:
        sub = compare[compare["cenario"].eq(scenario) & ~compare["perfil_exposicao"].eq("atual_teste16")].sort_values("alfa_composto", ascending=False)
        atual = compare[compare["cenario"].eq(scenario) & compare["perfil_exposicao"].eq("atual_teste16")].iloc[0]
        best = sub.iloc[0]
        log("")
        log(f"{scenario}:")
        log(
            f"  atual: retorno={pct(atual['retorno_carteira'])}; alfa={pct(atual['alfa_composto'])}; "
            f"drawdown={pct(atual['drawdown_carteira'])}"
        )
        log(
            f"  melhor sem zerar: {best['perfil_exposicao']} | retorno={pct(best['retorno_carteira'])}; "
            f"alfa={pct(best['alfa_composto'])}; delta_alfa={pct(best['delta_alfa_vs_atual'])}; "
            f"bateu={int(best['meses_bateu_ibov'])}/{int(best['meses'])}; drawdown={pct(best['drawdown_carteira'])}"
        )
    log("")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
