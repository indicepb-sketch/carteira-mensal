from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import shadow_teste28d_queda_confirmada as t28d


OUTPUT_FILE = t28d.OUTPUT_FILE.parent / "shadow_teste30_recuperacao_candidatas_restricao.xlsx"
LOG_FILE = t28d.LOG_FILE.parent / "shadow_teste30_recuperacao_candidatas_restricao.log"

SCENARIOS_30 = [
    {"name": "base_28d", "risk_mode": "queda_confirmada_28d", "recover": False, "cap": np.nan, "min_nota": np.nan},
    {"name": "recupera_restricao_cap5_nota50", "risk_mode": "queda_confirmada_28d", "recover": True, "cap": 0.05, "min_nota": 50.0},
    {"name": "recupera_restricao_cap7_5_nota50", "risk_mode": "queda_confirmada_28d", "recover": True, "cap": 0.075, "min_nota": 50.0},
    {"name": "recupera_restricao_cap5_nota60", "risk_mode": "queda_confirmada_28d", "recover": True, "cap": 0.05, "min_nota": 60.0},
]


def pct(value: Any) -> str:
    return t28d.t28b.pct(value)


def append_token(text: Any, token: str) -> str:
    return t28d.t28b.sh.append_token(text, token)


def lower_series(frame: pd.DataFrame, col: str) -> pd.Series:
    return frame.get(col, pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()


def is_recoverable_restricted(frame: pd.DataFrame, min_nota: float) -> pd.Series:
    if frame.empty:
        return pd.Series(False, index=frame.index)
    idx = frame.index
    decision = lower_series(frame, "decisao_preliminar_ajustada")
    note = pd.to_numeric(frame.get("nota_final", pd.Series(np.nan, index=idx)), errors="coerce")
    force = pd.to_numeric(frame.get("forca_relativa_score", pd.Series(np.nan, index=idx)), errors="coerce")
    force_class = lower_series(frame, "classificacao_forca_relativa")
    timing = lower_series(frame, "tipo_timing")
    watch_type = lower_series(frame, "tipo_watchlist")
    watch_reason = lower_series(frame, "motivo_watchlist_qualificada")
    opt_reason = lower_series(frame, "motivo_bloqueio_otimizacao")
    status_reason = lower_series(frame, "motivo_status_para_risco")
    data_reason = lower_series(frame, "motivo_dado_insuficiente")
    rsi = pd.to_numeric(frame.get("rsi", pd.Series(np.nan, index=idx)), errors="coerce")
    has_risk = pd.to_numeric(frame.get("retorno_medio", pd.Series(np.nan, index=idx)), errors="coerce").notna()
    deterioration = frame.apply(t28d.t28b.sh.is_real_deterioration, axis=1)
    fund_block = frame.get("fundamento_bloqueante", pd.Series(False, index=idx)).map(t28d.t28b.sh.to_bool)

    hard_text = opt_reason + " " + status_reason + " " + watch_reason + " " + data_reason
    hard_block = hard_text.str.contains(
        "fundamento|deterioracao|dados_insuficientes|dados insuficientes|sobrecompra_extrema|watchlist_bloqueante|configuracao_manual",
        regex=True,
        na=False,
    )
    extreme_timing = timing.str.contains("esticado_sobrecompra|sobrecompra", na=False) & (rsi >= 75)
    watch_block = watch_type.str.contains("bloqueante", na=False) | watch_reason.str.contains("bloqueante", na=False)
    force_ok = (force >= 3) | force_class.str.contains("moderada|forte|positiva", na=False)

    return (
        decision.eq("candidata_com_restricao")
        & note.ge(float(min_nota))
        & force_ok
        & has_risk
        & ~deterioration
        & ~fund_block
        & ~hard_block
        & ~extreme_timing
        & ~watch_block
    )


def recover_restricted_candidates(frame: pd.DataFrame, scenario: dict[str, Any]) -> pd.DataFrame:
    if frame.empty or not scenario.get("recover"):
        return frame
    out = frame.copy()
    idx = out.index
    cap = float(scenario["cap"])
    min_nota = float(scenario["min_nota"])
    mask = is_recoverable_restricted(out, min_nota)
    if not mask.any():
        out["teste30_recuperada_restricao"] = False
        return out
    for col in [
        "motivo_bloqueio_otimizacao",
        "tipo_bloqueio_otimizacao",
        "penalizacoes_otimizacao",
        "alertas_nao_bloqueantes",
        "shadow_motivos_correcoes",
        "teste30_motivo_recuperacao",
    ]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)
    out["teste30_recuperada_restricao"] = False
    out.loc[mask, "teste30_recuperada_restricao"] = True
    out.loc[mask, "status_para_risco"] = "moderada_para_risco"
    out.loc[mask, "categoria_elegibilidade"] = "elegivel_moderado"
    out.loc[mask, "motivo_bloqueio_otimizacao"] = ""
    out.loc[mask, "tipo_bloqueio_otimizacao"] = ""
    out.loc[mask, "teste30_motivo_recuperacao"] = (
        f"candidata_com_restricao recuperada; nota>={min_nota:.0f}; forca moderada/positiva; "
        "sem deterioracao real; sem bloqueio fundamental/dados/sobrecompra extrema"
    )
    out.loc[mask, "penalizacoes_otimizacao"] = out.loc[mask, "penalizacoes_otimizacao"].map(
        lambda x: append_token(x, f"teste30_recuperacao_restricao_cap_{cap:.3f}")
    )
    out.loc[mask, "alertas_nao_bloqueantes"] = out.loc[mask, "alertas_nao_bloqueantes"].map(
        lambda x: append_token(x, "candidata_com_restricao_recuperada_com_teto")
    )
    out.loc[mask, "shadow_motivos_correcoes"] = out.loc[mask, "shadow_motivos_correcoes"].map(
        lambda x: append_token(x, str(scenario["name"]))
    )
    current_cap = pd.to_numeric(out.get("teste25_cap_individual", pd.Series(np.nan, index=idx)), errors="coerce")
    out.loc[mask, "teste25_cap_individual"] = np.where(current_cap.loc[mask].notna(), np.minimum(current_cap.loc[mask], cap), cap)
    return t28d.t28b.t25.recompute_optimization_flags(out)


def make_policy_30(confidence: pd.DataFrame, scenario: dict[str, Any]):
    policy_28d, set_current_month = t28d.make_policy_28d(confidence)

    def policy(frame: pd.DataFrame, bucket: str) -> pd.DataFrame:
        out = policy_28d(frame, str(scenario["risk_mode"]), bucket)
        return recover_restricted_candidates(out, scenario)

    return policy, set_current_month


def run_mode_30(
    scenario: dict[str, Any],
    regimes: dict[str, tuple[str, str]],
    expost: pd.DataFrame,
    base_settings: dict,
    confidence: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], pd.DataFrame]:
    policy, set_current_month = make_policy_30(confidence, scenario)
    current: dict[str, str] = {"bucket": ""}
    original_apply = t28d.t28b.sh.apply_shadow_fixes
    original_d3 = t28d.t28b.sh.technical_veto_to_penalty_in_opportunity

    def enforce(frame: pd.DataFrame) -> pd.DataFrame:
        out = t28d.t28b.t25.enforce_negative_mean_policy(
            frame,
            t28d.t28b.BASE_NEGATIVE_MEAN_POLICY,
            current["bucket"],
            str(scenario["name"]),
            t28d.t28b.BASE_NEGATIVE_MEAN_CAP,
        )
        return policy(out, current["bucket"])

    def apply_wrapper(frame: pd.DataFrame, regime: str) -> pd.DataFrame:
        return enforce(original_apply(frame, regime))

    extended_d3 = t28d.t28b.cons.make_extended_d3(original_d3)

    def d3_wrapper(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
        return enforce(extended_d3(frame, settings))

    t28d.t28b.sh.apply_shadow_fixes = apply_wrapper
    t28d.t28b.sh.technical_veto_to_penalty_in_opportunity = d3_wrapper
    rows: list[dict[str, Any]] = []
    results: dict[str, dict[str, Any]] = {}
    candidates_frames: list[pd.DataFrame] = []
    try:
        for mes in t28d.t28b.r16.MONTHS:
            set_current_month(mes)
            current["bucket"] = regimes[mes][0]
            result = t28d.t28b.sh.run_free_size_for_month(
                mes,
                t28d.t28b.r16.workbook_path(mes),
                base_settings,
                lambda_beta=t28d.t28b.LAMBDA_BETA,
                downturn_signal="SINAL_A_DEFENSIVO",
            )
            results[mes] = result
            ibov = float(expost[expost["mes"].astype(str).eq(mes)]["retorno_ibov_periodo"].dropna().iloc[0])
            ret100 = t28d.t28b.result_return(result, expost, mes)
            exposure = t28d.t28b.exposure_100_50_20(current["bucket"])
            ret_model = ret100 * exposure if pd.notna(ret100) else np.nan
            metrics = result.get("metrics", {})
            cand = result.get("candidates", pd.DataFrame()).copy()
            n_rec = int(cand.get("teste30_recuperada_restricao", pd.Series(False, index=cand.index)).map(t28d.t28b.sh.to_bool).sum()) if not cand.empty else 0
            port = result.get("portfolio", pd.DataFrame())
            rec_in_port = 0
            if not port.empty and not cand.empty and "ticker" in cand.columns:
                rec_tickers = set(cand.loc[cand.get("teste30_recuperada_restricao", pd.Series(False, index=cand.index)).map(t28d.t28b.sh.to_bool), "ticker"].astype(str))
                rec_in_port = int(port["ticker"].astype(str).isin(rec_tickers).sum())
            rows.append(
                {
                    "cenario_teste30": scenario["name"],
                    "mes": mes,
                    "bucket_regime_previsto": current["bucket"],
                    "motivo_regime": regimes[mes][1],
                    "queda_confirmada_28d": bool(confidence.loc[confidence["mes"].astype(str).eq(mes), "queda_confirmada_28d"].iloc[0]),
                    "tipo_regime_expost": t28d.t28b.realized_bucket(ibov) if mes != "2026-06" else "jun_oportunidade",
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
                    "tickers_pesos": t28d.t28b.sh.format_weights(t28d.t28b.sh.weights_map(port)),
                }
            )
            if not cand.empty:
                cand["cenario_teste30"] = scenario["name"]
                cand["mes"] = mes
                cand["bucket_regime_previsto"] = current["bucket"]
                candidates_frames.append(cand)
    finally:
        t28d.t28b.sh.apply_shadow_fixes = original_apply
        t28d.t28b.sh.technical_veto_to_penalty_in_opportunity = original_d3
    candidates = pd.concat(candidates_frames, ignore_index=True, sort=False) if candidates_frames else pd.DataFrame()
    return pd.DataFrame(rows), results, candidates


def summarize(group: pd.DataFrame) -> dict[str, Any]:
    renamed = group.rename(columns={"cenario_teste30": "cenario_teste28b"})
    row = t28d.t28b.summarize(renamed)
    row["cenario_teste30"] = row.pop("cenario_teste28b")
    row["recuperadas_pool_total"] = int(pd.to_numeric(group.get("n_recuperadas_restricao_pool", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    row["recuperadas_carteira_total"] = int(pd.to_numeric(group.get("n_recuperadas_restricao_carteira", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    return row


def portfolio_rows(results_by_mode: dict[str, dict[str, dict[str, Any]]]) -> pd.DataFrame:
    return t28d.t28b.portfolio_rows(results_by_mode).rename(columns={"cenario_teste28b": "cenario_teste30"})


def recovered_rows(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty or "teste30_recuperada_restricao" not in candidates.columns:
        return pd.DataFrame()
    mask = candidates["teste30_recuperada_restricao"].map(t28d.t28b.sh.to_bool)
    cols = [
        "cenario_teste30",
        "mes",
        "ticker",
        "setor",
        "bucket_regime_previsto",
        "decisao_preliminar_ajustada",
        "status_para_risco",
        "categoria_elegibilidade",
        "teste25_cap_individual",
        "nota_final",
        "forca_relativa_score",
        "classificacao_forca_relativa",
        "tipo_timing",
        "rsi",
        "retorno_medio_original_shadow",
        "retorno_medio",
        "beta",
        "cv",
        "qualidade_fundamentalista",
        "fundamento_bloqueante",
        "teste30_motivo_recuperacao",
    ]
    return candidates.loc[mask, [c for c in cols if c in candidates.columns]].copy()


def validation_rows(monthly: pd.DataFrame, portfolios: pd.DataFrame) -> pd.DataFrame:
    validation = monthly.copy()
    if not portfolios.empty:
        sums = portfolios.groupby(["cenario_teste30", "mes"])["peso_recomendado"].sum().reset_index(name="soma_pesos")
        maxw = portfolios.groupby(["cenario_teste30", "mes"])["peso_recomendado"].max().reset_index(name="maior_peso_acoes")
        validation = validation.merge(sums, on=["cenario_teste30", "mes"], how="left").merge(maxw, on=["cenario_teste30", "mes"], how="left")
        validation["pesos_ok"] = validation["soma_pesos"].sub(1.0).abs() < 0.0001
        validation["maior_peso_efetivo"] = validation["maior_peso_acoes"] * validation["exposicao_modelo"]
    return validation


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    if not t28d.t28b.INPUT_16.exists():
        raise FileNotFoundError(t28d.t28b.INPUT_16)
    base_settings = t28d.t28b.load_settings()
    t28d.t28b.sh.MONTHS = t28d.t28b.r16.MONTHS
    expost = pd.read_excel(t28d.t28b.INPUT_16, sheet_name="expost_universo")
    regimes, confidence = t28d.build_regimes_and_confidence(expost)

    original_build = t28d.t28b.sh.build_free_size_portfolio
    original_loader = t28d.t28b.bt.patch_sector_enrichment(t28d.t28b.bt.load_sector_map())
    original_beta_profile = t28d.t28b.sh.beta_target_profile
    original_downturn_profile = t28d.t28b.sh.downturn_regime_profile
    t28d.t28b.r16.ORIGINAL_BETA_TARGET_PROFILE = original_beta_profile
    t28d.t28b.r16.ORIGINAL_DOWNTURN_PROFILE = original_downturn_profile
    t28d.t28b.sh.build_free_size_portfolio = t28d.t28b.t25.build_free_size_portfolio_with_qualified_caps
    t28d.t28b.sh.beta_target_profile, t28d.t28b.sh.downturn_regime_profile = t28d.t28b.r16.profile_patch(regimes)

    all_rows: list[pd.DataFrame] = []
    all_candidates: list[pd.DataFrame] = []
    results_by_mode: dict[str, dict[str, dict[str, Any]]] = {}
    try:
        for scenario in SCENARIOS_30:
            rows, results, candidates = run_mode_30(scenario, regimes, expost, base_settings, confidence)
            all_rows.append(rows)
            all_candidates.append(candidates)
            results_by_mode[str(scenario["name"])] = results
    finally:
        t28d.t28b.sh.build_free_size_portfolio = original_build
        t28d.t28b.sh.load_candidate_input = original_loader
        t28d.t28b.sh.beta_target_profile = original_beta_profile
        t28d.t28b.sh.downturn_regime_profile = original_downturn_profile

    monthly = pd.concat(all_rows, ignore_index=True, sort=False)
    candidates = pd.concat(all_candidates, ignore_index=True, sort=False) if all_candidates else pd.DataFrame()
    summary = pd.DataFrame([summarize(g) for _, g in monthly.groupby("cenario_teste30", sort=False)])
    by_regime = pd.DataFrame(
        [summarize(g) | {"tipo_regime_expost": keys[1]} for keys, g in monthly.groupby(["cenario_teste30", "tipo_regime_expost"], sort=False)]
    )
    baseline = monthly[monthly["cenario_teste30"].eq("base_28d")][["mes", "retorno_modelo", "alfa_modelo", "tickers_pesos"]]
    compare = monthly[~monthly["cenario_teste30"].eq("base_28d")].merge(
        baseline,
        on="mes",
        how="left",
        suffixes=("", "_base28d"),
    )
    compare["delta_retorno_vs_base28d"] = compare["retorno_modelo"] - compare["retorno_modelo_base28d"]
    compare["delta_alfa_vs_base28d"] = compare["alfa_modelo"] - compare["alfa_modelo_base28d"]
    portfolios = portfolio_rows(results_by_mode)
    recovered = recovered_rows(candidates)
    validation = validation_rows(monthly, portfolios)
    cap_audit = t28d.t28b.caps_audit(candidates).rename(columns={"cenario_teste28b": "cenario_teste30"})

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        by_regime.to_excel(writer, sheet_name="por_regime_real", index=False)
        compare.to_excel(writer, sheet_name="comparativo_vs_base28d", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes_bruto", index=False)
        recovered.to_excel(writer, sheet_name="recuperadas_restricao", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras", index=False)
        cap_audit.to_excel(writer, sheet_name="auditoria_caps", index=False)
        validation.to_excel(writer, sheet_name="validacao", index=False)

    log("Teste 30 - Recuperacao Controlada de Candidatas com Restricao")
    log("Base: 28D. Recupera apenas candidata_com_restricao qualificada, com teto pequeno, sem mexer em producao.")
    for _, row in summary.iterrows():
        log(
            f"  {row['cenario_teste30']}: retorno={pct(row['retorno_carteira'])}; "
            f"IBOV={pct(row['retorno_ibov'])}; alfa={pct(row['alfa_composto'])}; "
            f"bateu={int(row['meses_bateu_ibov'])}/{int(row['meses'])}; "
            f"drawdown={pct(row['drawdown_carteira'])}; "
            f"rec_pool={int(row['recuperadas_pool_total'])}; rec_cart={int(row['recuperadas_carteira_total'])}"
        )
    base = summary[summary["cenario_teste30"].eq("base_28d")].iloc[0]
    for scenario in [s["name"] for s in SCENARIOS_30 if s["name"] != "base_28d"]:
        row = summary[summary["cenario_teste30"].eq(scenario)].iloc[0]
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

