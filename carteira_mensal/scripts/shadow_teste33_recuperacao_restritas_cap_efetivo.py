from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import shadow_teste30_recuperacao_candidatas_restricao as t30


OUTPUT_FILE = t30.OUTPUT_FILE.parent / "shadow_teste33_recuperacao_restritas_cap_efetivo.xlsx"
LOG_FILE = t30.LOG_FILE.parent / "shadow_teste33_recuperacao_restritas_cap_efetivo.log"

SCENARIOS_33 = [
    {"name": "base_28d", "risk_mode": "queda_confirmada_28d", "rule": "base", "cap": np.nan},
    {
        "name": "restrita_virada_forca_sem_queda_leve_cap5_capefetivo25",
        "risk_mode": "queda_confirmada_28d",
        "rule": "virada_forca",
        "cap": 0.05,
        "avoid_predicted_buckets": ["queda_leve"],
    },
    {
        "name": "restrita_combinada_31_sem_queda_leve_cap5_capefetivo25",
        "risk_mode": "queda_confirmada_28d",
        "rule": "combinada_31",
        "cap": 0.05,
        "avoid_predicted_buckets": ["queda_leve"],
    },
]


def pct(value: Any) -> str:
    return t30.pct(value)


def lower_series(frame: pd.DataFrame, col: str) -> pd.Series:
    return frame.get(col, pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()


def base_recoverable(frame: pd.DataFrame) -> pd.Series:
    idx = frame.index
    decision = lower_series(frame, "decisao_preliminar_ajustada")
    timing = lower_series(frame, "tipo_timing")
    watch_type = lower_series(frame, "tipo_watchlist")
    watch_reason = lower_series(frame, "motivo_watchlist_qualificada")
    opt_reason = lower_series(frame, "motivo_bloqueio_otimizacao")
    status_reason = lower_series(frame, "motivo_status_para_risco")
    data_reason = lower_series(frame, "motivo_dado_insuficiente")
    rsi = pd.to_numeric(frame.get("rsi", pd.Series(np.nan, index=idx)), errors="coerce")
    has_risk = pd.to_numeric(frame.get("retorno_medio", pd.Series(np.nan, index=idx)), errors="coerce").notna()
    deterioration = frame.apply(t30.t28d.t28b.sh.is_real_deterioration, axis=1)
    fund_block = frame.get("fundamento_bloqueante", pd.Series(False, index=idx)).map(t30.t28d.t28b.sh.to_bool)
    hard_text = opt_reason + " " + status_reason + " " + watch_reason + " " + data_reason
    hard_block = hard_text.str.contains(
        "fundamento|deterioracao|dados_insuficientes|dados insuficientes|sobrecompra_extrema|watchlist_bloqueante|configuracao_manual",
        regex=True,
        na=False,
    )
    extreme_timing = timing.str.contains("esticado_sobrecompra|sobrecompra", na=False) & (rsi >= 75)
    watch_block = watch_type.str.contains("bloqueante", na=False) | watch_reason.str.contains("bloqueante", na=False)
    return decision.eq("candidata_com_restricao") & has_risk & ~deterioration & ~fund_block & ~hard_block & ~extreme_timing & ~watch_block


def recoverable_by_rule(frame: pd.DataFrame, scenario: dict[str, Any]) -> pd.Series:
    if frame.empty or scenario.get("rule") == "base":
        return pd.Series(False, index=frame.index)
    idx = frame.index
    base = base_recoverable(frame)
    note = pd.to_numeric(frame.get("nota_final", pd.Series(np.nan, index=idx)), errors="coerce")
    force = pd.to_numeric(frame.get("forca_relativa_score", pd.Series(np.nan, index=idx)), errors="coerce")
    beta = pd.to_numeric(frame.get("beta", pd.Series(np.nan, index=idx)), errors="coerce")
    cv = pd.to_numeric(frame.get("cv", pd.Series(np.nan, index=idx)), errors="coerce")
    trend = lower_series(frame, "tendencia_mensal")
    bucket = lower_series(frame, "bucket_regime_previsto")
    confirmed = frame.get("queda_confirmada_28d", pd.Series(False, index=idx)).map(t30.t28d.t28b.sh.to_bool)
    force_class = lower_series(frame, "classificacao_forca_relativa")
    force_ok = force.ge(3) | force_class.str.contains("moderada|forte|positiva", na=False)
    avoid_buckets = {str(x).lower() for x in scenario.get("avoid_predicted_buckets", [])}
    bucket_allowed = ~bucket.isin(avoid_buckets) if avoid_buckets else pd.Series(True, index=idx)

    defensiva_queda = base & bucket_allowed & confirmed & bucket.str.contains("queda", na=False) & beta.lt(0.9) & (cv.lt(50) | cv.isna())
    virada_forca = base & bucket_allowed & note.ge(50) & force_ok & trend.str.contains("virada|alta_aceitavel", na=False)

    rule = str(scenario.get("rule", ""))
    if rule == "defensiva_queda_confirmada":
        return defensiva_queda
    if rule == "virada_forca":
        return virada_forca
    if rule == "combinada_31":
        return defensiva_queda | virada_forca
    return pd.Series(False, index=idx)


def recover_restricted_candidates_32(frame: pd.DataFrame, scenario: dict[str, Any]) -> pd.DataFrame:
    if frame.empty or scenario.get("rule") == "base":
        return frame
    out = frame.copy()
    idx = out.index
    cap = float(scenario["cap"])
    mask = recoverable_by_rule(out, scenario)
    if not mask.any():
        out["teste33_recuperada_restricao"] = False
        return out
    for col in [
        "motivo_bloqueio_otimizacao",
        "tipo_bloqueio_otimizacao",
        "penalizacoes_otimizacao",
        "alertas_nao_bloqueantes",
        "shadow_motivos_correcoes",
        "teste33_motivo_recuperacao",
    ]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)
    out["teste33_recuperada_restricao"] = False
    out.loc[mask, "teste33_recuperada_restricao"] = True
    out.loc[mask, "status_para_risco"] = "moderada_para_risco"
    out.loc[mask, "categoria_elegibilidade"] = "elegivel_moderado"
    out.loc[mask, "motivo_bloqueio_otimizacao"] = ""
    out.loc[mask, "tipo_bloqueio_otimizacao"] = ""
    out.loc[mask, "teste33_motivo_recuperacao"] = f"recuperacao restrita evidenciada: {scenario['rule']}; cap={cap:.3f}"
    out.loc[mask, "penalizacoes_otimizacao"] = out.loc[mask, "penalizacoes_otimizacao"].map(
        lambda x: t30.append_token(x, f"teste33_{scenario['rule']}_cap_{cap:.3f}")
    )
    out.loc[mask, "alertas_nao_bloqueantes"] = out.loc[mask, "alertas_nao_bloqueantes"].map(
        lambda x: t30.append_token(x, "candidata_com_restricao_recuperada_por_evidencia_31")
    )
    out.loc[mask, "shadow_motivos_correcoes"] = out.loc[mask, "shadow_motivos_correcoes"].map(
        lambda x: t30.append_token(x, str(scenario["name"]))
    )
    current_cap = pd.to_numeric(out.get("teste25_cap_individual", pd.Series(np.nan, index=idx)), errors="coerce")
    out.loc[mask, "teste25_cap_individual"] = np.where(current_cap.loc[mask].notna(), np.minimum(current_cap.loc[mask], cap), cap)
    return t30.t28d.t28b.t25.recompute_optimization_flags(out)



def capped_proportional_weights_by_asset_strict(signal: pd.Series, caps: pd.Series, floor: float = 0.01) -> pd.Series:
    """Distribui proporcionalmente sem violar caps. Se os caps nao fecham 100%, nao normaliza acima deles."""
    values = pd.to_numeric(signal, errors="coerce").fillna(0).clip(lower=0).astype(float)
    caps = pd.to_numeric(caps.reindex(values.index), errors="coerce").fillna(1.0).clip(lower=0).astype(float)
    if values.empty:
        return values
    if float(caps.sum()) <= 1.0 + 1e-12:
        return caps.copy()
    values = values + max(float(floor), 0.0)
    capped = pd.Series(0.0, index=values.index, dtype=float)
    free = pd.Series(True, index=values.index)
    remaining = 1.0
    for _ in range(len(values) + 2):
        if not free.any() or remaining <= 1e-12:
            break
        denom = float(values[free].sum())
        alloc = pd.Series(remaining / int(free.sum()), index=values[free].index) if denom <= 0 else remaining * values[free] / denom
        over = alloc > caps[free] + 1e-12
        if not over.any():
            capped.loc[free] = alloc
            remaining = 0.0
            break
        over_idx = alloc[over].index
        capped.loc[over_idx] = caps.loc[over_idx]
        free.loc[over_idx] = False
        remaining = 1.0 - float(capped.sum())
    return capped.clip(upper=caps)

def make_policy_32(confidence: pd.DataFrame, scenario: dict[str, Any]):
    policy_28d, set_current_month = t30.t28d.make_policy_28d(confidence)

    def policy(frame: pd.DataFrame, bucket: str) -> pd.DataFrame:
        out = policy_28d(frame, str(scenario["risk_mode"]), bucket).copy()
        out["bucket_regime_previsto"] = str(bucket)
        return recover_restricted_candidates_32(out, scenario)

    return policy, set_current_month


def run_mode_32(
    scenario: dict[str, Any],
    regimes: dict[str, tuple[str, str]],
    expost: pd.DataFrame,
    base_settings: dict,
    confidence: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], pd.DataFrame]:
    policy, set_current_month = make_policy_32(confidence, scenario)
    current: dict[str, str] = {"bucket": ""}
    original_apply = t30.t28d.t28b.sh.apply_shadow_fixes
    original_d3 = t30.t28d.t28b.sh.technical_veto_to_penalty_in_opportunity

    def enforce(frame: pd.DataFrame) -> pd.DataFrame:
        out = t30.t28d.t28b.t25.enforce_negative_mean_policy(
            frame,
            t30.t28d.t28b.BASE_NEGATIVE_MEAN_POLICY,
            current["bucket"],
            str(scenario["name"]),
            t30.t28d.t28b.BASE_NEGATIVE_MEAN_CAP,
        )
        return policy(out, current["bucket"])

    def apply_wrapper(frame: pd.DataFrame, regime: str) -> pd.DataFrame:
        return enforce(original_apply(frame, regime))

    extended_d3 = t30.t28d.t28b.cons.make_extended_d3(original_d3)

    def d3_wrapper(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
        return enforce(extended_d3(frame, settings))

    t30.t28d.t28b.sh.apply_shadow_fixes = apply_wrapper
    t30.t28d.t28b.sh.technical_veto_to_penalty_in_opportunity = d3_wrapper
    rows: list[dict[str, Any]] = []
    results: dict[str, dict[str, Any]] = {}
    candidates_frames: list[pd.DataFrame] = []
    try:
        for mes in t30.t28d.t28b.r16.MONTHS:
            set_current_month(mes)
            current["bucket"] = regimes[mes][0]
            result = t30.t28d.t28b.sh.run_free_size_for_month(
                mes,
                t30.t28d.t28b.r16.workbook_path(mes),
                base_settings,
                lambda_beta=t30.t28d.t28b.LAMBDA_BETA,
                downturn_signal="SINAL_A_DEFENSIVO",
            )
            results[mes] = result
            ibov = float(expost[expost["mes"].astype(str).eq(mes)]["retorno_ibov_periodo"].dropna().iloc[0])
            ret100 = t30.t28d.t28b.result_return(result, expost, mes)
            exposure = t30.t28d.t28b.exposure_100_50_20(current["bucket"])
            ret_model = ret100 * exposure if pd.notna(ret100) else np.nan
            metrics = result.get("metrics", {})
            cand = result.get("candidates", pd.DataFrame()).copy()
            rec_col = "teste33_recuperada_restricao"
            n_rec = int(cand.get(rec_col, pd.Series(False, index=cand.index)).map(t30.t28d.t28b.sh.to_bool).sum()) if not cand.empty else 0
            port = result.get("portfolio", pd.DataFrame())
            rec_in_port = 0
            if not port.empty and not cand.empty and "ticker" in cand.columns:
                rec_tickers = set(cand.loc[cand.get(rec_col, pd.Series(False, index=cand.index)).map(t30.t28d.t28b.sh.to_bool), "ticker"].astype(str))
                rec_in_port = int(port["ticker"].astype(str).isin(rec_tickers).sum())
            rows.append(
                {
                    "cenario_teste33": scenario["name"],
                    "mes": mes,
                    "bucket_regime_previsto": current["bucket"],
                    "motivo_regime": regimes[mes][1],
                    "queda_confirmada_28d": bool(confidence.loc[confidence["mes"].astype(str).eq(mes), "queda_confirmada_28d"].iloc[0]),
                    "tipo_regime_expost": t30.t28d.t28b.realized_bucket(ibov) if mes != "2026-06" else "jun_oportunidade",
                    "exposicao_modelo": exposure,
                    "retorno_100_acoes": ret100,
                    "retorno_modelo": ret_model,
                    "retorno_expost_ibov": ibov,
                    "alfa_modelo": ret_model - ibov if pd.notna(ret_model) else np.nan,
                    "status_carteira": metrics.get("status_carteira", ""),
                    "n_ativos": len(port),
                    "beta_carteira": metrics.get("beta_carteira", np.nan),
                    "n_recuperadas_restricao_pool": n_rec,
                    "n_recuperadas_restricao_carteira": rec_in_port,
                    "tickers_pesos": t30.t28d.t28b.sh.format_weights(t30.t28d.t28b.sh.weights_map(port)),
                }
            )
            if not cand.empty:
                cand["cenario_teste33"] = scenario["name"]
                cand["mes"] = mes
                cand["bucket_regime_previsto"] = current["bucket"]
                candidates_frames.append(cand)
    finally:
        t30.t28d.t28b.sh.apply_shadow_fixes = original_apply
        t30.t28d.t28b.sh.technical_veto_to_penalty_in_opportunity = original_d3
    candidates = pd.concat(candidates_frames, ignore_index=True, sort=False) if candidates_frames else pd.DataFrame()
    return pd.DataFrame(rows), results, candidates


def summarize(group: pd.DataFrame) -> dict[str, Any]:
    renamed = group.rename(columns={"cenario_teste33": "cenario_teste28b"})
    row = t30.t28d.t28b.summarize(renamed)
    row["cenario_teste33"] = row.pop("cenario_teste28b")
    row["recuperadas_pool_total"] = int(pd.to_numeric(group.get("n_recuperadas_restricao_pool", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    row["recuperadas_carteira_total"] = int(pd.to_numeric(group.get("n_recuperadas_restricao_carteira", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    return row


def recovered_rows(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty or "teste33_recuperada_restricao" not in candidates.columns:
        return pd.DataFrame()
    mask = candidates["teste33_recuperada_restricao"].map(t30.t28d.t28b.sh.to_bool)
    cols = [
        "cenario_teste33", "mes", "ticker", "setor", "bucket_regime_previsto", "decisao_preliminar_ajustada",
        "teste25_cap_individual", "nota_final", "forca_relativa_score", "classificacao_forca_relativa",
        "tipo_timing", "tendencia_mensal", "rsi", "retorno_medio_original_shadow", "retorno_medio",
        "beta", "cv", "qualidade_fundamentalista", "teste33_motivo_recuperacao",
    ]
    return candidates.loc[mask, [c for c in cols if c in candidates.columns]].copy()


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    if not t30.t28d.t28b.INPUT_16.exists():
        raise FileNotFoundError(t30.t28d.t28b.INPUT_16)
    base_settings = t30.t28d.t28b.load_settings()
    t30.t28d.t28b.sh.MONTHS = t30.t28d.t28b.r16.MONTHS
    expost = pd.read_excel(t30.t28d.t28b.INPUT_16, sheet_name="expost_universo")
    regimes, confidence = t30.t28d.build_regimes_and_confidence(expost)

    original_build = t30.t28d.t28b.sh.build_free_size_portfolio
    original_cap_allocator = t30.t28d.t28b.t25.capped_proportional_weights_by_asset
    original_loader = t30.t28d.t28b.bt.patch_sector_enrichment(t30.t28d.t28b.bt.load_sector_map())
    original_beta_profile = t30.t28d.t28b.sh.beta_target_profile
    original_downturn_profile = t30.t28d.t28b.sh.downturn_regime_profile
    t30.t28d.t28b.r16.ORIGINAL_BETA_TARGET_PROFILE = original_beta_profile
    t30.t28d.t28b.r16.ORIGINAL_DOWNTURN_PROFILE = original_downturn_profile
    t30.t28d.t28b.sh.build_free_size_portfolio = t30.t28d.t28b.t25.build_free_size_portfolio_with_qualified_caps
    t30.t28d.t28b.t25.capped_proportional_weights_by_asset = capped_proportional_weights_by_asset_strict
    t30.t28d.t28b.sh.beta_target_profile, t30.t28d.t28b.sh.downturn_regime_profile = t30.t28d.t28b.r16.profile_patch(regimes)

    all_rows: list[pd.DataFrame] = []
    all_candidates: list[pd.DataFrame] = []
    results_by_mode: dict[str, dict[str, dict[str, Any]]] = {}
    try:
        for scenario in SCENARIOS_33:
            rows, results, candidates = run_mode_32(scenario, regimes, expost, base_settings, confidence)
            all_rows.append(rows)
            all_candidates.append(candidates)
            results_by_mode[str(scenario["name"])] = results
    finally:
        t30.t28d.t28b.sh.build_free_size_portfolio = original_build
        t30.t28d.t28b.t25.capped_proportional_weights_by_asset = original_cap_allocator
        t30.t28d.t28b.sh.load_candidate_input = original_loader
        t30.t28d.t28b.sh.beta_target_profile = original_beta_profile
        t30.t28d.t28b.sh.downturn_regime_profile = original_downturn_profile

    monthly = pd.concat(all_rows, ignore_index=True, sort=False)
    candidates = pd.concat(all_candidates, ignore_index=True, sort=False) if all_candidates else pd.DataFrame()
    summary = pd.DataFrame([summarize(g) for _, g in monthly.groupby("cenario_teste33", sort=False)])
    by_regime = pd.DataFrame([summarize(g) | {"tipo_regime_expost": keys[1]} for keys, g in monthly.groupby(["cenario_teste33", "tipo_regime_expost"], sort=False)])
    baseline = monthly[monthly["cenario_teste33"].eq("base_28d")][["mes", "retorno_modelo", "alfa_modelo", "tickers_pesos"]]
    compare = monthly[~monthly["cenario_teste33"].eq("base_28d")].merge(baseline, on="mes", how="left", suffixes=("", "_base28d"))
    compare["delta_retorno_vs_base28d"] = compare["retorno_modelo"] - compare["retorno_modelo_base28d"]
    compare["delta_alfa_vs_base28d"] = compare["alfa_modelo"] - compare["alfa_modelo_base28d"]
    portfolios = t30.portfolio_rows(results_by_mode).rename(columns={"cenario_teste30": "cenario_teste33"})
    recovered = recovered_rows(candidates)
    validation = t30.validation_rows(monthly.rename(columns={"cenario_teste33": "cenario_teste30"}), portfolios.rename(columns={"cenario_teste33": "cenario_teste30"})).rename(columns={"cenario_teste30": "cenario_teste33"})

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        by_regime.to_excel(writer, sheet_name="por_regime_real", index=False)
        compare.to_excel(writer, sheet_name="comparativo_vs_base28d", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes_bruto", index=False)
        recovered.to_excel(writer, sheet_name="recuperadas_restricao", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras", index=False)
        validation.to_excel(writer, sheet_name="validacao", index=False)

    log("Teste 33 - Recuperacao de Restritas com Cap Efetivo e Filtro sem Queda Leve")
    log("Base: 28D. Recupera virada+forca do Teste 31, evitando bucket previsto queda_leve e validando cap efetivo <=25%.")
    for _, row in summary.iterrows():
        log(
            f"  {row['cenario_teste33']}: retorno={pct(row['retorno_carteira'])}; IBOV={pct(row['retorno_ibov'])}; "
            f"alfa={pct(row['alfa_composto'])}; bateu={int(row['meses_bateu_ibov'])}/{int(row['meses'])}; "
            f"drawdown={pct(row['drawdown_carteira'])}; rec_pool={int(row['recuperadas_pool_total'])}; "
            f"rec_cart={int(row['recuperadas_carteira_total'])}"
        )
    base = summary[summary["cenario_teste33"].eq("base_28d")].iloc[0]
    for scenario in [s["name"] for s in SCENARIOS_33 if s["name"] != "base_28d"]:
        row = summary[summary["cenario_teste33"].eq(scenario)].iloc[0]
        log(
            f"  Delta {scenario} vs base_28d: alfa={pct(row['alfa_composto'] - base['alfa_composto'])}; "
            f"retorno={pct(row['retorno_carteira'] - base['retorno_carteira'])}; "
            f"taxa_acerto_delta={row['taxa_meses_bateu_ibov'] - base['taxa_meses_bateu_ibov']:.2%}"
        )
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()



