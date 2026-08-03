from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import shadow_teste28b_modulacao_risco_regime as t28b
import shadow_teste28c_risco_so_quedas as t28c


OUTPUT_FILE = t28b.EXCEL_DIR / "shadow_teste28d_queda_confirmada.xlsx"
LOG_FILE = t28b.LOG_FILE.parent / "shadow_teste28d_queda_confirmada.log"

SCENARIOS_28D = [
    {"name": "t25_cap7_5_base", "risk_mode": "base"},
    {"name": "modulacao_defensiva_so_quedas_28c", "risk_mode": "defensiva_so_quedas_28c"},
    {"name": "modulacao_queda_confirmada_28d", "risk_mode": "queda_confirmada_28d"},
]


def build_regimes_and_confidence(expost: pd.DataFrame) -> tuple[dict[str, tuple[str, str]], pd.DataFrame]:
    audit_inputs = pd.DataFrame([t28b.r16.month_audit_inputs(mes, expost) for mes in t28b.r16.MONTHS])
    rows: list[dict[str, Any]] = []
    regimes: dict[str, tuple[str, str]] = {}
    for _, row in audit_inputs.iterrows():
        data = row.to_dict()
        bucket, reason = t28b.r16.mm50_only(data)
        mes = str(data["mes"])
        beta_signal = str(data.get("sinal_beta_risk", "risk_off"))
        mm_signal = str(data.get("sinal_mm50_risk", "risk_off"))
        mm = float(data.get("pct_mm50_gt_mm100", np.nan))
        bucket_norm = str(bucket).lower()
        if bucket_norm == "queda_leve":
            confirmed = beta_signal == "risk_off" and pd.notna(mm) and mm >= 0.70
            reason_confirm = "queda_leve_confirmada_beta_risk_off_e_mm50>=70%"
        elif bucket_norm == "queda_forte":
            confirmed = beta_signal == "risk_off" or (pd.notna(mm) and mm >= 0.75)
            reason_confirm = "queda_forte_confirmada_beta_risk_off_ou_mm50>=75%"
        else:
            confirmed = False
            reason_confirm = "regime_nao_e_queda"
        regimes[mes] = (bucket, reason)
        rows.append(
            {
                **data,
                "bucket_regime_previsto": bucket,
                "motivo_regime": reason,
                "queda_confirmada_28d": bool(confirmed),
                "motivo_confirmacao_28d": reason_confirm,
                "tipo_regime_expost": t28b.realized_bucket(float(data.get("ibov_expost", np.nan)))
                if mes != "2026-06"
                else "jun_oportunidade",
            }
        )
    return regimes, pd.DataFrame(rows)


def make_policy_28d(confidence: pd.DataFrame):
    confirmed_by_month = {
        str(row["mes"]): bool(row["queda_confirmada_28d"])
        for _, row in confidence.iterrows()
    }
    current: dict[str, str] = {"mes": ""}

    def set_current_month(mes: str) -> None:
        current["mes"] = mes

    def policy(frame: pd.DataFrame, mode: str, bucket: str) -> pd.DataFrame:
        if mode == "defensiva_so_quedas_28c":
            return t28c.apply_risk_regime_policy_28c(frame, "defensiva_so_quedas", bucket)
        if mode != "queda_confirmada_28d":
            return t28b.apply_risk_regime_policy(frame, mode, bucket)
        if not confirmed_by_month.get(current["mes"], False):
            return frame
        return t28b.apply_risk_regime_policy(frame, "defensiva", bucket)

    return policy, set_current_month


def run_mode_28d(
    scenario: dict[str, Any],
    regimes: dict[str, tuple[str, str]],
    expost: pd.DataFrame,
    base_settings: dict,
    confidence: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], pd.DataFrame]:
    policy, set_current_month = make_policy_28d(confidence)
    current: dict[str, str] = {"bucket": ""}
    original_apply = t28b.sh.apply_shadow_fixes
    original_d3 = t28b.sh.technical_veto_to_penalty_in_opportunity

    def enforce(frame: pd.DataFrame) -> pd.DataFrame:
        out = t28b.t25.enforce_negative_mean_policy(
            frame,
            t28b.BASE_NEGATIVE_MEAN_POLICY,
            current["bucket"],
            str(scenario["name"]),
            t28b.BASE_NEGATIVE_MEAN_CAP,
        )
        return policy(out, str(scenario["risk_mode"]), current["bucket"])

    def apply_wrapper(frame: pd.DataFrame, regime: str) -> pd.DataFrame:
        return enforce(original_apply(frame, regime))

    extended_d3 = t28b.cons.make_extended_d3(original_d3)

    def d3_wrapper(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
        return enforce(extended_d3(frame, settings))

    t28b.sh.apply_shadow_fixes = apply_wrapper
    t28b.sh.technical_veto_to_penalty_in_opportunity = d3_wrapper
    rows: list[dict[str, Any]] = []
    results: dict[str, dict[str, Any]] = {}
    candidates_frames: list[pd.DataFrame] = []
    try:
        for mes in t28b.r16.MONTHS:
            set_current_month(mes)
            current["bucket"] = regimes[mes][0]
            result = t28b.sh.run_free_size_for_month(
                mes,
                t28b.r16.workbook_path(mes),
                base_settings,
                lambda_beta=t28b.LAMBDA_BETA,
                downturn_signal="SINAL_A_DEFENSIVO",
            )
            results[mes] = result
            ibov = float(expost[expost["mes"].astype(str).eq(mes)]["retorno_ibov_periodo"].dropna().iloc[0])
            ret100 = t28b.result_return(result, expost, mes)
            exposure = t28b.exposure_100_50_20(current["bucket"])
            ret_model = ret100 * exposure if pd.notna(ret100) else np.nan
            metrics = result.get("metrics", {})
            rows.append(
                {
                    "cenario_teste28d": scenario["name"],
                    "mes": mes,
                    "bucket_regime_previsto": current["bucket"],
                    "motivo_regime": regimes[mes][1],
                    "queda_confirmada_28d": bool(
                        confidence.loc[confidence["mes"].astype(str).eq(mes), "queda_confirmada_28d"].iloc[0]
                    ),
                    "tipo_regime_expost": t28b.realized_bucket(ibov) if mes != "2026-06" else "jun_oportunidade",
                    "exposicao_modelo": exposure,
                    "retorno_100_acoes": ret100,
                    "retorno_modelo": ret_model,
                    "retorno_expost_ibov": ibov,
                    "alfa_modelo": ret_model - ibov if pd.notna(ret_model) else np.nan,
                    "status_carteira": metrics.get("status_carteira", ""),
                    "n_ativos": len(result.get("portfolio", pd.DataFrame())),
                    "beta_carteira": metrics.get("beta_carteira", np.nan),
                    "tickers_pesos": t28b.sh.format_weights(t28b.sh.weights_map(result.get("portfolio", pd.DataFrame()))),
                }
            )
            cand = result.get("candidates", pd.DataFrame()).copy()
            if not cand.empty:
                cand["cenario_teste28d"] = scenario["name"]
                cand["mes"] = mes
                cand["bucket_regime_previsto"] = current["bucket"]
                cand["queda_confirmada_28d"] = bool(
                    confidence.loc[confidence["mes"].astype(str).eq(mes), "queda_confirmada_28d"].iloc[0]
                )
                candidates_frames.append(cand)
    finally:
        t28b.sh.apply_shadow_fixes = original_apply
        t28b.sh.technical_veto_to_penalty_in_opportunity = original_d3
    candidates = pd.concat(candidates_frames, ignore_index=True, sort=False) if candidates_frames else pd.DataFrame()
    return pd.DataFrame(rows), results, candidates


def summarize_by_regime(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    renamed = monthly.rename(columns={"cenario_teste28d": "cenario_teste28b"})
    for keys, group in renamed.groupby(["cenario_teste28b", "tipo_regime_expost"], sort=False):
        row = t28b.summarize(group)
        row["cenario_teste28d"] = row.pop("cenario_teste28b")
        row["tipo_regime_expost"] = keys[1]
        rows.append(row)
    return pd.DataFrame(rows)


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
    regimes, confidence = build_regimes_and_confidence(expost)

    original_build = t28b.sh.build_free_size_portfolio
    original_loader = t28b.bt.patch_sector_enrichment(t28b.bt.load_sector_map())
    original_beta_profile = t28b.sh.beta_target_profile
    original_downturn_profile = t28b.sh.downturn_regime_profile
    t28b.r16.ORIGINAL_BETA_TARGET_PROFILE = original_beta_profile
    t28b.r16.ORIGINAL_DOWNTURN_PROFILE = original_downturn_profile
    t28b.sh.build_free_size_portfolio = t28b.t25.build_free_size_portfolio_with_qualified_caps
    t28b.sh.beta_target_profile, t28b.sh.downturn_regime_profile = t28b.r16.profile_patch(regimes)

    all_rows: list[pd.DataFrame] = []
    all_candidates: list[pd.DataFrame] = []
    results_by_mode: dict[str, dict[str, dict[str, Any]]] = {}
    try:
        for scenario in SCENARIOS_28D:
            rows, results, candidates = run_mode_28d(scenario, regimes, expost, base_settings, confidence)
            all_rows.append(rows)
            all_candidates.append(candidates)
            results_by_mode[str(scenario["name"])] = results
    finally:
        t28b.sh.build_free_size_portfolio = original_build
        t28b.sh.load_candidate_input = original_loader
        t28b.sh.beta_target_profile = original_beta_profile
        t28b.sh.downturn_regime_profile = original_downturn_profile

    monthly = pd.concat(all_rows, ignore_index=True, sort=False)
    candidates = pd.concat(all_candidates, ignore_index=True, sort=False) if all_candidates else pd.DataFrame()
    summary = pd.DataFrame(
        [
            t28b.summarize(g.rename(columns={"cenario_teste28d": "cenario_teste28b"}))
            | {"cenario_teste28d": scenario}
            for scenario, g in monthly.groupby("cenario_teste28d", sort=False)
        ]
    ).drop(columns=["cenario_teste28b"], errors="ignore")
    by_regime = summarize_by_regime(monthly)
    baseline = monthly[monthly["cenario_teste28d"].eq("t25_cap7_5_base")][["mes", "retorno_modelo", "alfa_modelo", "tickers_pesos"]]
    compare = monthly[~monthly["cenario_teste28d"].eq("t25_cap7_5_base")].merge(
        baseline,
        on="mes",
        how="left",
        suffixes=("", "_base"),
    )
    compare["delta_retorno_vs_base"] = compare["retorno_modelo"] - compare["retorno_modelo_base"]
    compare["delta_alfa_vs_base"] = compare["alfa_modelo"] - compare["alfa_modelo_base"]
    portfolios = t28b.portfolio_rows(results_by_mode).rename(columns={"cenario_teste28b": "cenario_teste28d"})
    audit = t28b.caps_audit(candidates).rename(columns={"cenario_teste28b": "cenario_teste28d"})
    validation = monthly.copy()
    if not portfolios.empty:
        sums = portfolios.groupby(["cenario_teste28d", "mes"])["peso_recomendado"].sum().reset_index(name="soma_pesos")
        maxw = portfolios.groupby(["cenario_teste28d", "mes"])["peso_recomendado"].max().reset_index(name="maior_peso")
        validation = validation.merge(sums, on=["cenario_teste28d", "mes"], how="left").merge(maxw, on=["cenario_teste28d", "mes"], how="left")
        validation["pesos_ok"] = validation["soma_pesos"].sub(1.0).abs() < 0.0001

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        by_regime.to_excel(writer, sheet_name="por_regime_real", index=False)
        compare.to_excel(writer, sheet_name="comparativo_vs_base", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes_bruto", index=False)
        confidence.to_excel(writer, sheet_name="auditoria_confirmacao_28d", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras", index=False)
        audit.to_excel(writer, sheet_name="auditoria_caps_risco", index=False)
        validation.to_excel(writer, sheet_name="validacao", index=False)

    log("Teste 28D - Modulacao defensiva de risco apenas em queda confirmada")
    log("Base: Teste 25 cap 7,5%; compara 28C contra 28D com confirmacao adicional de queda.")
    log("Regra 28D: queda_leve exige beta risk-off e MM50>MM100 >= 70%; queda_forte exige beta risk-off ou MM50>MM100 >= 75%.")
    for _, row in summary.iterrows():
        log(
            f"  {row['cenario_teste28d']}: retorno={t28b.pct(row['retorno_carteira'])}; "
            f"IBOV={t28b.pct(row['retorno_ibov'])}; alfa={t28b.pct(row['alfa_composto'])}; "
            f"bateu={int(row['meses_bateu_ibov'])}/{int(row['meses'])}; drawdown={t28b.pct(row['drawdown_carteira'])}"
        )
    base = summary[summary["cenario_teste28d"].eq("t25_cap7_5_base")].iloc[0]
    for scenario in ["modulacao_defensiva_so_quedas_28c", "modulacao_queda_confirmada_28d"]:
        row = summary[summary["cenario_teste28d"].eq(scenario)].iloc[0]
        log(
            f"  Delta {scenario} vs base: alfa={t28b.pct(row['alfa_composto'] - base['alfa_composto'])}; "
            f"retorno={t28b.pct(row['retorno_carteira'] - base['retorno_carteira'])}; "
            f"taxa_acerto_delta={row['taxa_meses_bateu_ibov'] - base['taxa_meses_bateu_ibov']:.2%}"
        )
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()


