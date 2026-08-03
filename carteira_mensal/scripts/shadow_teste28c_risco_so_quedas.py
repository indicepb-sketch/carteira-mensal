from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste28c_risco_so_quedas.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_teste28c_risco_so_quedas.log"

import shadow_teste28b_modulacao_risco_regime as t28b  # noqa: E402


SCENARIOS_28C = [
    {"name": "t25_cap7_5_base", "risk_mode": "base"},
    {"name": "modulacao_defensiva_so_quedas", "risk_mode": "defensiva_so_quedas"},
]

ORIGINAL_POLICY_28B = t28b.apply_risk_regime_policy


def apply_risk_regime_policy_28c(frame: pd.DataFrame, mode: str, bucket: str) -> pd.DataFrame:
    if mode != "defensiva_so_quedas":
        return ORIGINAL_POLICY_28B(frame, mode, bucket)
    if str(bucket).lower() not in {"queda_leve", "queda_forte"}:
        return frame
    return ORIGINAL_POLICY_28B(frame, "defensiva", bucket)


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    if not t28b.INPUT_16.exists():
        raise FileNotFoundError(t28b.INPUT_16)

    base_settings = t28b.load_settings()
    t28b.sh.MONTHS = t28b.r16.MONTHS
    expost = pd.read_excel(t28b.INPUT_16, sheet_name="expost_universo")
    audit_inputs = pd.DataFrame([t28b.r16.month_audit_inputs(mes, expost) for mes in t28b.r16.MONTHS])
    regimes: dict[str, tuple[str, str]] = {}
    for _, row in audit_inputs.iterrows():
        bucket, reason = t28b.r16.mm50_only(row.to_dict())
        regimes[str(row["mes"])] = (bucket, reason)

    original_policy = t28b.apply_risk_regime_policy
    original_build = t28b.sh.build_free_size_portfolio
    original_loader = t28b.bt.patch_sector_enrichment(t28b.bt.load_sector_map())
    original_beta_profile = t28b.sh.beta_target_profile
    original_downturn_profile = t28b.sh.downturn_regime_profile
    t28b.r16.ORIGINAL_BETA_TARGET_PROFILE = original_beta_profile
    t28b.r16.ORIGINAL_DOWNTURN_PROFILE = original_downturn_profile
    t28b.apply_risk_regime_policy = apply_risk_regime_policy_28c
    t28b.sh.build_free_size_portfolio = t28b.t25.build_free_size_portfolio_with_qualified_caps
    t28b.sh.beta_target_profile, t28b.sh.downturn_regime_profile = t28b.r16.profile_patch(regimes)

    all_rows: list[pd.DataFrame] = []
    all_candidates: list[pd.DataFrame] = []
    results_by_mode: dict[str, dict] = {}
    try:
        for scenario in SCENARIOS_28C:
            rows, results, candidates = t28b.run_mode(scenario, regimes, expost, base_settings)
            all_rows.append(rows)
            all_candidates.append(candidates)
            results_by_mode[str(scenario["name"])] = results
    finally:
        t28b.apply_risk_regime_policy = original_policy
        t28b.sh.build_free_size_portfolio = original_build
        t28b.sh.load_candidate_input = original_loader
        t28b.sh.beta_target_profile = original_beta_profile
        t28b.sh.downturn_regime_profile = original_downturn_profile

    monthly = pd.concat(all_rows, ignore_index=True, sort=False)
    candidates = pd.concat(all_candidates, ignore_index=True, sort=False) if all_candidates else pd.DataFrame()
    summary = pd.DataFrame([t28b.summarize(g) for _, g in monthly.groupby("cenario_teste28b", sort=False)])
    summary = summary.rename(columns={"cenario_teste28b": "cenario_teste28c"})
    by_regime = pd.DataFrame(
        [
            t28b.summarize(g) | {"tipo_regime_expost": keys[1]}
            for keys, g in monthly.groupby(["cenario_teste28b", "tipo_regime_expost"], sort=False)
        ]
    ).rename(columns={"cenario_teste28b": "cenario_teste28c"})
    baseline = monthly[monthly["cenario_teste28b"].eq("t25_cap7_5_base")][["mes", "retorno_modelo", "alfa_modelo", "tickers_pesos"]]
    compare = monthly[~monthly["cenario_teste28b"].eq("t25_cap7_5_base")].merge(
        baseline,
        on="mes",
        how="left",
        suffixes=("", "_base"),
    )
    compare["delta_retorno_vs_base"] = compare["retorno_modelo"] - compare["retorno_modelo_base"]
    compare["delta_alfa_vs_base"] = compare["alfa_modelo"] - compare["alfa_modelo_base"]
    portfolios = t28b.portfolio_rows(results_by_mode).rename(columns={"cenario_teste28b": "cenario_teste28c"})
    audit = t28b.caps_audit(candidates).rename(columns={"cenario_teste28b": "cenario_teste28c"})
    validation = monthly.copy()
    if not portfolios.empty:
        sums = portfolios.groupby(["cenario_teste28c", "mes"])["peso_recomendado"].sum().reset_index(name="soma_pesos")
        maxw = portfolios.groupby(["cenario_teste28c", "mes"])["peso_recomendado"].max().reset_index(name="maior_peso")
        validation = validation.rename(columns={"cenario_teste28b": "cenario_teste28c"})
        validation = validation.merge(sums, on=["cenario_teste28c", "mes"], how="left").merge(maxw, on=["cenario_teste28c", "mes"], how="left")
        validation["pesos_ok"] = validation["soma_pesos"].sub(1.0).abs() < 0.0001

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        by_regime.to_excel(writer, sheet_name="por_regime_real", index=False)
        compare.to_excel(writer, sheet_name="comparativo_vs_base", index=False)
        monthly.rename(columns={"cenario_teste28b": "cenario_teste28c"}).to_excel(writer, sheet_name="mes_a_mes_bruto", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras", index=False)
        audit.to_excel(writer, sheet_name="auditoria_caps_risco", index=False)
        validation.to_excel(writer, sheet_name="validacao", index=False)

    log("Teste 28C - Modulacao Defensiva de Risco somente em Quedas")
    log("Base: Teste 25 cap 7,5%. ModulaÃ§Ã£o defensiva aplicada apenas em bucket previsto queda_leve/queda_forte.")
    for _, row in summary.iterrows():
        log(
            f"  {row['cenario_teste28c']}: retorno={t28b.pct(row['retorno_carteira'])}; IBOV={t28b.pct(row['retorno_ibov'])}; "
            f"alfa={t28b.pct(row['alfa_composto'])}; bateu={int(row['meses_bateu_ibov'])}/{int(row['meses'])}; "
            f"drawdown={t28b.pct(row['drawdown_carteira'])}"
        )
    base = summary[summary["cenario_teste28c"].eq("t25_cap7_5_base")].iloc[0]
    row = summary[summary["cenario_teste28c"].eq("modulacao_defensiva_so_quedas")].iloc[0]
    log(f"  Delta 28C vs base: alfa={t28b.pct(row['alfa_composto'] - base['alfa_composto'])}; retorno={t28b.pct(row['retorno_carteira'] - base['retorno_carteira'])}; taxa_acerto_delta={row['taxa_meses_bateu_ibov'] - base['taxa_meses_bateu_ibov']:.2%}")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()

