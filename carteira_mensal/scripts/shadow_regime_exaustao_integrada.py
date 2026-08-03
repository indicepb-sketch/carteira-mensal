from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "excel"


def compound(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").fillna(0.0)
    result = 1.0
    for value in values:
        result *= 1.0 + float(value)
    return result - 1.0


def load_base_2025() -> pd.DataFrame:
    path = OUT / "shadow_backtest_2025.xlsx"
    df = pd.read_excel(path, sheet_name="mes_a_mes")
    return pd.DataFrame(
        {
            "mes": df["mes"].astype(str),
            "origem": path.name,
            "bucket_regime_base": df["bucket_regime"],
            "retorno_carteira_100pct": df["retorno_expost_sombra_100pct"],
            "retorno_ibov": df["retorno_expost_ibov"],
            "exposicao_base_teste5": df["exposicao_defensiva"],
            "retorno_base_teste5": df["retorno_expost_sombra_defensivo"],
            "carteira_100pct": df["tickers_pesos_sombra"],
        }
    )


def load_base_2026() -> pd.DataFrame:
    path = OUT / "shadow_exposicao_por_regime.xlsx"
    df = pd.read_excel(path, sheet_name="detalhe_mes_cenario")
    df = df[df["cenario"].eq("EXPOSICAO_DEFENSIVA")].copy()
    return pd.DataFrame(
        {
            "mes": df["mes"].astype(str),
            "origem": path.name,
            "bucket_regime_base": df["bucket_regime"],
            "retorno_carteira_100pct": df["retorno_carteira_100pct"],
            "retorno_ibov": df["retorno_ibov"],
            "exposicao_base_teste5": df["exposicao_investida"],
            "retorno_base_teste5": df["retorno_carteira_com_exposicao"],
            "carteira_100pct": df["tickers_pesos_100pct"],
        }
    )


def load_exhaustion() -> pd.DataFrame:
    path = OUT / "shadow_detector_exaustao.xlsx"
    diag = pd.read_excel(path, sheet_name="diagnostico_exaustao")
    cols = [
        "mes",
        "market_class_producao",
        "bucket_regime",
        "exaustao_score",
        "sinais_exaustao",
        "pct_tendencia_favoravel",
        "pct_ativos_positivos_1m",
        "pct_mm9_maior_mm21",
        "pct_preco_acima_mm50",
        "rsi_mediano",
        "retorno_1m_mediano",
        "retorno_4m_mediano",
    ]
    return diag[cols].rename(
        columns={
            "bucket_regime": "bucket_regime_diagnostico",
            "market_class_producao": "market_class_diagnostico",
        }
    )


def integrated_exposure(row: pd.Series) -> tuple[float, str]:
    base = float(row["exposicao_base_teste5"])
    score = int(row.get("exaustao_score", 0) or 0)
    diag_bucket = str(row.get("bucket_regime_diagnostico", "")).lower()
    base_bucket = str(row.get("bucket_regime_base", "")).lower()

    favorable_by_diag = diag_bucket == "alta" or "favoravel" in str(
        row.get("market_class_diagnostico", "")
    ).lower()
    favorable_by_base = base_bucket in {"alta", "oportunidade"}

    if score >= 5 and (favorable_by_diag or favorable_by_base) and base > 0.6:
        return 0.6, "exaustao forte em mercado favoravel: corta de 100% para 60%"
    if score >= 5 and (favorable_by_diag or favorable_by_base) and base <= 0.6:
        return base, "exaustao forte confirmada, mas exposicao base ja era defensiva"
    return base, "mantem exposicao base do Teste 5"


def build() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = pd.concat([load_base_2025(), load_base_2026()], ignore_index=True)
    diag = load_exhaustion()
    detail = base.merge(diag, on="mes", how="left").sort_values("mes")

    exposures = detail.apply(integrated_exposure, axis=1, result_type="expand")
    detail["exposicao_teste8"] = exposures[0]
    detail["motivo_exposicao_teste8"] = exposures[1]
    detail["peso_caixa_teste8"] = 1.0 - detail["exposicao_teste8"]
    detail["retorno_teste8"] = (
        detail["retorno_carteira_100pct"] * detail["exposicao_teste8"]
    )
    detail["alfa_teste8"] = detail["retorno_teste8"] - detail["retorno_ibov"]
    detail["delta_vs_teste5"] = detail["retorno_teste8"] - detail["retorno_base_teste5"]

    scenarios = []
    for name, ret_col in [
        ("BASELINE_100", "retorno_carteira_100pct"),
        ("TESTE5_EXPOSICAO_DEFENSIVA", "retorno_base_teste5"),
        ("TESTE8_REGIME_EXAUSTAO_CONSERVADORA", "retorno_teste8"),
    ]:
        for group, mask in [
            ("TOTAL_18_MESES", pd.Series(True, index=detail.index)),
            ("2025", detail["mes"].str.startswith("2025")),
            ("2026_JAN_JUN", detail["mes"].str.startswith("2026")),
            ("ALTA_OU_OPORTUNIDADE", detail["bucket_regime_base"].isin(["alta", "oportunidade"])),
            ("QUEDA_LEVE", detail["bucket_regime_base"].eq("queda_leve")),
            ("QUEDA_FORTE", detail["bucket_regime_base"].eq("queda_forte")),
            ("EXAUSTAO_FORTE_SCORE_GE5", detail["exaustao_score"].fillna(0).ge(5)),
        ]:
            part = detail[mask].copy()
            if part.empty:
                continue
            ret = compound(part[ret_col])
            ibov = compound(part["retorno_ibov"])
            scenarios.append(
                {
                    "cenario": name,
                    "grupo": group,
                    "meses": ", ".join(part["mes"].tolist()),
                    "retorno_carteira": ret,
                    "retorno_ibov": ibov,
                    "alfa": ret - ibov,
                    "n_meses": len(part),
                }
            )
    summary = pd.DataFrame(scenarios)

    validation = detail[
        [
            "mes",
            "bucket_regime_base",
            "bucket_regime_diagnostico",
            "exposicao_base_teste5",
            "exaustao_score",
            "exposicao_teste8",
            "retorno_carteira_100pct",
            "retorno_teste8",
            "retorno_ibov",
            "alfa_teste8",
            "delta_vs_teste5",
            "motivo_exposicao_teste8",
        ]
    ].copy()
    validation["validacao_retorno"] = (
        validation["retorno_carteira_100pct"] * validation["exposicao_teste8"]
        - validation["retorno_teste8"]
    )

    return summary, detail, validation


def main() -> None:
    summary, detail, validation = build()
    out = OUT / "shadow_regime_exaustao_integrada.xlsx"
    log = OUT / "shadow_regime_exaustao_integrada.log"
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        detail.to_excel(writer, sheet_name="detalhe_mes", index=False)
        validation.to_excel(writer, sheet_name="validacao", index=False)

    total = summary[summary["grupo"].eq("TOTAL_18_MESES")].copy()
    lines = ["Teste 8 - Regime + Exaustao Conservadora Integrada"]
    for _, row in total.iterrows():
        lines.append(
            f"{row['cenario']}: retorno={row['retorno_carteira']:.2%} | "
            f"IBOV={row['retorno_ibov']:.2%} | alfa={row['alfa']:.2%}"
        )
    changed = detail[
        detail["exposicao_teste8"].sub(detail["exposicao_base_teste5"]).abs().gt(1e-9)
        | detail["delta_vs_teste5"].abs().gt(1e-8)
    ]
    lines.append(f"Meses alterados vs Teste 5: {len(changed)}")
    for _, row in changed.iterrows():
        lines.append(
            f"  {row['mes']}: exp {row['exposicao_base_teste5']:.0%} -> "
            f"{row['exposicao_teste8']:.0%}; score={int(row['exaustao_score'])}; "
            f"delta retorno={row['delta_vs_teste5']:.2%}"
        )
    lines.append(f"Arquivo gerado: {out}")
    lines.append(f"Log gerado: {log}")
    log.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()


