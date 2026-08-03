from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for item in (str(SRC), str(SCRIPTS)):
    if item not in sys.path:
        sys.path.insert(0, item)

from utils import load_settings  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
import shadow_consolidada_6meses as sc  # noqa: E402
import shadow_forca_relativa_continua as sf  # noqa: E402


OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_sem_fundamentos.xlsx"
LOG_FILE = ROOT / "output" / "excel" / "shadow_sem_fundamentos.log"

SCENARIOS = {
    "BASELINE": {
        "remove_score_fund": False,
        "disable_veto_fund": False,
        "descricao": "score_fundamentos ativo + veto fundamental ativo",
    },
    "SEM_SCORE_FUND": {
        "remove_score_fund": True,
        "disable_veto_fund": False,
        "descricao": "score_fundamentos removido da nota; veto fundamental mantido",
    },
    "SEM_VETO": {
        "remove_score_fund": False,
        "disable_veto_fund": True,
        "descricao": "score_fundamentos mantido; veto fundamental desligado",
    },
    "SEM_NADA": {
        "remove_score_fund": True,
        "disable_veto_fund": True,
        "descricao": "score_fundamentos removido + veto fundamental desligado",
    },
}

CURRENT_SCENARIO = "BASELINE"


def _pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def _num(frame: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(default, index=frame.index)
    return pd.to_numeric(frame[col], errors="coerce")


def bad_fundamental_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(False, index=frame.index)
    roe = _num(frame, "roe")
    margem = _num(frame, "margem_liquida")
    pl = _num(frame, "pl_atual", _num(frame, "p_l_atual"))
    block = frame.get("fundamento_bloqueante", pd.Series(False, index=frame.index)).map(sh.to_bool)
    return block | roe.lt(0) | margem.lt(0) | pl.lt(0)


def bad_fundamental_reason(row: pd.Series) -> str:
    reasons: list[str] = []
    if sh.to_bool(row.get("fundamento_bloqueante", False)):
        reasons.append("fundamento_bloqueante")
    for col, label in [("roe", "ROE<0"), ("margem_liquida", "margem_liquida<0"), ("pl_atual", "P/L<0")]:
        value = sh.to_float(row.get(col), np.nan)
        if pd.notna(value) and value < 0:
            reasons.append(label)
    return "; ".join(reasons)


def remove_fundamental_tokens(text: Any) -> str:
    return sh.remove_tokens(
        text,
        [
            "fundamento",
            "fundamentalista",
            "deterioracao",
            "deterioração",
            "roe<0",
            "roe_negativo",
            "margem_liq<0",
            "margem_liquida_negativa",
            "margem_liquida<0",
            "p/l<0",
            "pl_negativo",
            "bloqueio_por_fundamento_bloqueante",
        ],
    )


def clear_fundamental_veto(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    mask = bad_fundamental_mask(out)
    out["shadow_fundamento_ruim_original"] = mask
    out["shadow_motivo_fundamento_ruim_original"] = out.apply(bad_fundamental_reason, axis=1)
    if not mask.any():
        return out

    for col in ["motivo_bloqueio_otimizacao", "tipo_bloqueio_otimizacao", "motivo_fundamento_bloqueante"]:
        if col in out.columns:
            out.loc[mask, col] = out.loc[mask, col].map(remove_fundamental_tokens).fillna("")
    if "fundamento_bloqueante" in out.columns:
        out.loc[mask, "fundamento_bloqueante"] = False
    if "risco_fundamentalista_mensal" in out.columns:
        out.loc[mask, "risco_fundamentalista_mensal"] = "veto_fundamental_desligado_shadow"
    if "qualidade_fundamentalista" in out.columns:
        out.loc[mask & out["qualidade_fundamentalista"].fillna("").astype(str).str.lower().eq("critica"), "qualidade_fundamentalista"] = "fraca"

    reason = out.get("motivo_bloqueio_otimizacao", pd.Series("", index=out.index)).fillna("").astype(str).str.strip()
    status = out.get("status_para_risco", pd.Series("", index=out.index)).fillna("").astype(str)
    category = out.get("categoria_elegibilidade", pd.Series("", index=out.index)).fillna("").astype(str)
    ret_ok = out.get("retorno_medio", pd.Series(np.nan, index=out.index)).notna()
    pure_fundamental_release = mask & reason.eq("")
    out.loc[pure_fundamental_release & ~status.isin(["aprovada_para_risco", "moderada_para_risco"]), "status_para_risco"] = "moderada_para_risco"
    out.loc[pure_fundamental_release & ~category.isin(["elegivel_forte", "elegivel_moderado"]), "categoria_elegibilidade"] = "elegivel_moderado"
    status2 = out.get("status_para_risco", pd.Series("", index=out.index)).fillna("").astype(str).isin(["aprovada_para_risco", "moderada_para_risco"])
    category2 = out.get("categoria_elegibilidade", pd.Series("", index=out.index)).fillna("").astype(str).isin(["elegivel_forte", "elegivel_moderado"])
    out["bloqueado_otimizacao"] = reason.ne("")
    out["liberado_para_otimizacao"] = (~out["bloqueado_otimizacao"].map(sh.to_bool)) & status2 & category2 & ret_ok
    out.loc[pure_fundamental_release, "shadow_motivos_correcoes"] = out.loc[pure_fundamental_release].get("shadow_motivos_correcoes", pd.Series("", index=out.loc[pure_fundamental_release].index)).map(
        lambda value: sh.append_token(value, "veto_fundamental_desligado_shadow")
    )
    return out


def rescore_without_fundamentals(scored: pd.DataFrame) -> pd.DataFrame:
    if scored.empty:
        return scored
    out = scored.copy()
    non_fund_components = ["score_tendencia", "score_timing", "score_setor", "score_risco"]
    for col in ["score_fundamentos", *non_fund_components, "penalidade_cv", "penalidade_timing", "penalidade_prioridade_otimizacao"]:
        if col not in out.columns:
            out[col] = 0.0
    out["score_fundamentos_original_shadow"] = pd.to_numeric(out["score_fundamentos"], errors="coerce").fillna(0.0)
    out["nota_final_original_shadow"] = pd.to_numeric(out.get("nota_final", pd.Series(np.nan, index=out.index)), errors="coerce")
    out["score_prioridade_original_shadow"] = pd.to_numeric(out.get("score_prioridade_otimizacao", pd.Series(np.nan, index=out.index)), errors="coerce")

    scale = 100.0 / 80.0
    total = pd.Series(0.0, index=out.index)
    for col in non_fund_components:
        raw = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
        out[f"{col}_sem_score_fund_shadow"] = raw * scale
        total = total + out[f"{col}_sem_score_fund_shadow"]

    cv_pen = pd.to_numeric(out["penalidade_cv"], errors="coerce").fillna(0.0)
    timing_pen = pd.to_numeric(out["penalidade_timing"], errors="coerce").fillna(0.0)
    priority_pen = pd.to_numeric(out["penalidade_prioridade_otimizacao"], errors="coerce").fillna(0.0)
    out["score_fundamentos"] = 0.0
    out["nota_final"] = (total - cv_pen - timing_pen).clip(lower=0.0, upper=100.0)
    out["score_prioridade_otimizacao"] = (out["nota_final"] - priority_pen).clip(lower=0.0, upper=100.0)
    out["shadow_score_fundamentos_removido"] = True
    return out.sort_values(["score_prioridade_otimizacao", "nota_final"], ascending=[False, False]).reset_index(drop=True)


def make_score_assets_wrapper(original_score_assets):
    def wrapped(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
        scored = original_score_assets(frame, settings)
        if SCENARIOS[CURRENT_SCENARIO]["remove_score_fund"]:
            scored = rescore_without_fundamentals(scored)
        else:
            scored = scored.copy()
            scored["shadow_score_fundamentos_removido"] = False
        scored["cenario_fundamentos"] = CURRENT_SCENARIO
        return scored

    return wrapped


def make_apply_shadow_fixes_wrapper(original_apply):
    def wrapped(frame: pd.DataFrame, regime: str) -> pd.DataFrame:
        out = original_apply(frame, regime)
        if SCENARIOS[CURRENT_SCENARIO]["disable_veto_fund"]:
            out = clear_fundamental_veto(out)
        return out

    return wrapped


def make_d3_wrapper(original_d3):
    extended = sc.make_extended_d3(original_d3)

    def wrapped(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
        out = extended(frame, settings)
        if SCENARIOS[CURRENT_SCENARIO]["disable_veto_fund"]:
            out = clear_fundamental_veto(out)
        return out

    return wrapped


def make_loader_wrapper(original_loader):
    baseline_loader = sf.make_extended_load_candidate_input(original_loader)

    def wrapped(path: Path, settings: dict | None = None) -> pd.DataFrame:
        base = baseline_loader(path, settings)
        if settings is None or not SCENARIOS[CURRENT_SCENARIO]["disable_veto_fund"]:
            return base

        profile = settings.get("_runtime_downturn_profile", {}) or {}
        subtype = str(profile.get("subtipo_queda", "")).lower()
        beta_subtype = str(settings.get("_runtime_beta_target_subtipo", "")).lower()
        momentum_subtypes = {"favoravel_oportunidade", "favoravel_cansado", "favoravel_esticado", "favoravel_amplo"}
        if subtype != "alta" or beta_subtype not in momentum_subtypes:
            return clear_fundamental_veto(base)

        prelim = sh.read_sheet(path, "Analise Preliminar")
        if prelim.empty or "ticker" not in prelim.columns:
            return clear_fundamental_veto(base)
        prelim = sh.enrich_candidate_input(prelim.drop_duplicates("ticker").copy(), path, include_downturn_cols=False)
        base_tickers = set(base.get("ticker", pd.Series(dtype=str)).astype(str)) if not base.empty else set()
        mask = ~prelim["ticker"].astype(str).isin(base_tickers)
        mask &= sf.technical_d3_prelim_mask(prelim) | bad_fundamental_mask(prelim)
        if "retorno_medio" in prelim.columns:
            mask &= sf.numeric_col(prelim, "retorno_medio", np.nan).notna()
        if "beta" in prelim.columns:
            mask &= sf.numeric_col(prelim, "beta", np.nan).notna()
        extra = prelim[mask].copy()
        if not extra.empty:
            extra["shadow_d3_extendida_adicionada_do_preliminar"] = True
            extra["motivo_bloqueio_original_d3"] = extra.get("motivo_bloqueio_otimizacao", pd.Series("preliminar", index=extra.index)).fillna("preliminar")
            extra["bloqueado_otimizacao"] = False
            extra["liberado_para_otimizacao"] = True
            extra["tipo_bloqueio_otimizacao"] = ""
            extra["motivo_bloqueio_otimizacao"] = "relaxado_shadow_sem_veto_fundamental_ou_d3"
            extra["liberado_por_d3"] = True
            extra["d3_extendida_subtipo_original"] = beta_subtype
            extra["d3_extendida_sinal_ativo"] = "V3_MOMENTUM"
            combined = pd.concat([base, extra], ignore_index=True, sort=False) if not base.empty else extra
            return clear_fundamental_veto(combined.drop_duplicates("ticker", keep="first").reset_index(drop=True))
        return clear_fundamental_veto(base)

    return wrapped


def row_with_expost(mes: str, path: Path, result: dict[str, Any], expost: pd.DataFrame, scenario: str) -> dict[str, Any]:
    row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=True)
    row["cenario_fundamentos"] = scenario
    row["descricao_cenario"] = SCENARIOS[scenario]["descricao"]
    row["grupo_regime"] = sf.classify_month_group(mes)
    row["remove_score_fund"] = SCENARIOS[scenario]["remove_score_fund"]
    row["disable_veto_fund"] = SCENARIOS[scenario]["disable_veto_fund"]
    return row


def summary_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    base = pd.DataFrame(rows)
    if base.empty:
        return base
    out = []
    for scenario, group in base.groupby("cenario_fundamentos", sort=False):
        for _, row in group.sort_values("mes").iterrows():
            out.append(row.to_dict())
        total_ret = _compound(group["retorno_expost_sombra"])
        total_ibov = _compound(group["retorno_expost_ibov"])
        out.append(
            {
                "cenario_fundamentos": scenario,
                "descricao_cenario": SCENARIOS[scenario]["descricao"],
                "mes": "ACUMULADO_6_MESES",
                "grupo_regime": "total",
                "retorno_expost_sombra": total_ret,
                "retorno_expost_ibov": total_ibov,
                "alfa_sombra": total_ret - total_ibov if pd.notna(total_ret) and pd.notna(total_ibov) else np.nan,
            }
        )
        for label in ["alta", "baixa", "jun_oportunidade"]:
            sub = group[group["grupo_regime"].eq(label)]
            if sub.empty:
                continue
            sub_ret = _compound(sub["retorno_expost_sombra"])
            sub_ibov = _compound(sub["retorno_expost_ibov"])
            out.append(
                {
                    "cenario_fundamentos": scenario,
                    "descricao_cenario": SCENARIOS[scenario]["descricao"],
                    "mes": f"ACUMULADO_{label.upper()}",
                    "grupo_regime": label,
                    "retorno_expost_sombra": sub_ret,
                    "retorno_expost_ibov": sub_ibov,
                    "alfa_sombra": sub_ret - sub_ibov if pd.notna(sub_ret) and pd.notna(sub_ibov) else np.nan,
                }
            )
    summary = pd.DataFrame(out)
    key = summary[summary["mes"].astype(str).str.startswith("ACUMULADO")]
    baseline = key[key["cenario_fundamentos"].eq("BASELINE")].set_index("mes")["alfa_sombra"] if not key.empty else pd.Series(dtype=float)
    summary["diff_alfa_vs_baseline"] = summary.apply(
        lambda row: row.get("alfa_sombra", np.nan) - baseline.get(row.get("mes"), np.nan) if str(row.get("mes", "")).startswith("ACUMULADO") else np.nan,
        axis=1,
    )
    return summary


def portfolio_rows(results_by_scenario: dict[str, dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario, results in results_by_scenario.items():
        for mes, result in results.items():
            portfolio = result.get("portfolio", pd.DataFrame())
            if portfolio.empty:
                continue
            panel = expost[expost["mes"].astype(str).eq(mes)].set_index("ticker")
            for _, item in portfolio.iterrows():
                ticker = str(item.get("ticker", ""))
                weight = sh.to_float(item.get("peso_recomendado", item.get("peso_final", 0.0)), 0.0)
                ret = panel.loc[ticker, "retorno_realizado_periodo"] if ticker in panel.index and "retorno_realizado_periodo" in panel else np.nan
                out = item.to_dict()
                out.update(
                    {
                        "cenario_fundamentos": scenario,
                        "mes": mes,
                        "peso": weight,
                        "retorno_expost_ativo": ret,
                        "contribuicao_retorno": weight * ret if pd.notna(ret) else np.nan,
                        "fundamento_ruim_real": bool(bad_fundamental_mask(pd.DataFrame([item])).iloc[0]),
                        "motivo_fundamento_ruim": bad_fundamental_reason(item),
                    }
                )
                rows.append(out)
    return pd.DataFrame(rows)


def bad_fundamentals_entered(results_by_scenario: dict[str, dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario, results in results_by_scenario.items():
        if not SCENARIOS[scenario]["disable_veto_fund"]:
            continue
        for mes, result in results.items():
            portfolio = result.get("portfolio", pd.DataFrame())
            if portfolio.empty:
                continue
            mask = bad_fundamental_mask(portfolio)
            if not mask.any():
                continue
            panel = expost[expost["mes"].astype(str).eq(mes)].set_index("ticker")
            for _, item in portfolio[mask].iterrows():
                ticker = str(item.get("ticker", ""))
                weight = sh.to_float(item.get("peso_recomendado", item.get("peso_final", 0.0)), 0.0)
                ret = panel.loc[ticker, "retorno_realizado_periodo"] if ticker in panel.index and "retorno_realizado_periodo" in panel else np.nan
                ibov = sh.ibov_return(expost, mes)
                rows.append(
                    {
                        "cenario_fundamentos": scenario,
                        "mes": mes,
                        "ticker": ticker,
                        "setor": item.get("setor", ""),
                        "peso": weight,
                        "retorno_expost_ativo": ret,
                        "retorno_ibov": ibov,
                        "alfa_individual_vs_ibov": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
                        "roe": item.get("roe", np.nan),
                        "margem_liquida": item.get("margem_liquida", np.nan),
                        "pl_atual": item.get("pl_atual", np.nan),
                        "motivo_fundamento_ruim": bad_fundamental_reason(item),
                        "nota_final": item.get("nota_final", np.nan),
                        "score_fundamentos": item.get("score_fundamentos", np.nan),
                        "qualidade_fundamentalista": item.get("qualidade_fundamentalista", ""),
                    }
                )
    return pd.DataFrame(rows)


def validation_rows(results_by_scenario: dict[str, dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for scenario, results in results_by_scenario.items():
        df = pd.DataFrame(sh.free_size_validation_rows(results, expost))
        if df.empty:
            continue
        df.insert(0, "cenario_fundamentos", scenario)
        real_bad = []
        for mes, result in results.items():
            portfolio = result.get("portfolio", pd.DataFrame())
            real_bad.append(bool((not portfolio.empty) and bad_fundamental_mask(portfolio).any()))
        if len(real_bad) == len(df):
            df["tem_fundamento_ruim_real_calculo_local"] = real_bad
        frames.append(df)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def write_workbook(
    anchor_rows: list[dict[str, Any]],
    scenario_rows: list[dict[str, Any]],
    anchor_results: dict[str, Any],
    results_by_scenario: dict[str, dict[str, Any]],
    expost: pd.DataFrame,
) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        summary_rows(scenario_rows).to_excel(writer, sheet_name="resumo_4_cenarios", index=False)
        portfolio_rows(results_by_scenario, expost).to_excel(writer, sheet_name="carteiras_por_cenario", index=False)
        bad_fundamentals_entered(results_by_scenario, expost).to_excel(writer, sheet_name="acoes_lixo_que_entraram", index=False)
        validation_rows(results_by_scenario, expost).to_excel(writer, sheet_name="validacao_retorno", index=False)
        pd.DataFrame(scenario_rows).to_excel(writer, sheet_name="shadow_vs_real_detalhe", index=False)
        pd.DataFrame([{"cenario_fundamentos": key, **value} for key, value in SCENARIOS.items()]).to_excel(writer, sheet_name="cenarios", index=False)
        sc.run_weight_unit_tests().to_excel(writer, sheet_name="teste_unitario_pesos", index=False)
        for scenario, results in results_by_scenario.items():
            for mes, result in results.items():
                result.get("portfolio", pd.DataFrame()).to_excel(writer, sheet_name=f"{scenario[:18]}_{mes[-2:]}"[:31], index=False)
        for mes, result in anchor_results.items():
            result.get("portfolio", pd.DataFrame()).to_excel(writer, sheet_name=f"anchor_{mes[-2:]}", index=False)


def main() -> None:
    global CURRENT_SCENARIO

    sh.MONTHS = sc.MONTHS_6
    logs: list[str] = []

    def log(message: str) -> None:
        print(message)
        logs.append(message)

    base_settings = load_settings()
    expost = sc.load_expost_6(sc.MONTHS_6)
    log("Datas/retornos ex-post carregados:")
    for mes in sc.MONTHS_6:
        log(f"  {mes}: linhas={len(expost[expost['mes'].astype(str).eq(mes)])} | IBOV={_pct(sh.ibov_return(expost, mes))}")

    anchor_rows: list[dict[str, Any]] = []
    anchor_results: dict[str, Any] = {}
    all_pass = True
    log("TESTE-ANCORA (config sombra desligada):")
    for mes in sc.MONTHS_6:
        path = sh.workbook_path(mes)
        result = sh.run_optimizer_for_month(
            mes,
            path,
            base_settings,
            shadow_fixes=False,
            enable_partial_portfolio=False,
            enable_beta_target=False,
            enable_objetivo_retorno=False,
            enable_composicao_ampliada=False,
        )
        passed, detail = sh.anchor_passed_for_month(mes, path, result.get("portfolio", pd.DataFrame()), result.get("metrics", {}))
        row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=False)
        row["anchor_passou"] = passed
        row["anchor_detalhe"] = detail
        anchor_rows.append(row)
        anchor_results[mes] = result
        all_pass = all_pass and passed
        log(f"  {mes}: {'PASSOU' if passed else 'FALHOU'} - {detail}")

    if not all_pass:
        write_workbook(anchor_rows, [], anchor_results, {}, expost)
        LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
        raise SystemExit("Ancora falhou; teste abortado.")

    original_build = sh.build_free_size_portfolio
    original_d3 = sh.technical_veto_to_penalty_in_opportunity
    original_beta_profile = sh.beta_target_profile
    original_downturn_profile = sh.downturn_regime_profile
    original_loader = sh.load_candidate_input
    original_score_assets = sh.score_assets
    original_apply_shadow_fixes = sh.apply_shadow_fixes
    original_is_real_deterioration = sh.is_real_deterioration
    original_has_fundamental_deterioration = sh.has_fundamental_deterioration_in_portfolio

    sh.build_free_size_portfolio = sc.consolidated_build_free_size_portfolio
    sh.technical_veto_to_penalty_in_opportunity = make_d3_wrapper(original_d3)
    sh.beta_target_profile = sf.consolidated_beta_target_profile_factory(original_beta_profile)
    sh.downturn_regime_profile = sf.consolidated_downturn_profile_factory(original_downturn_profile)
    sh.load_candidate_input = make_loader_wrapper(original_loader)
    sh.score_assets = make_score_assets_wrapper(original_score_assets)
    sh.apply_shadow_fixes = make_apply_shadow_fixes_wrapper(original_apply_shadow_fixes)

    scenario_rows: list[dict[str, Any]] = []
    results_by_scenario: dict[str, dict[str, Any]] = {}
    try:
        for scenario in SCENARIOS:
            CURRENT_SCENARIO = scenario
            if SCENARIOS[scenario]["disable_veto_fund"]:
                sh.is_real_deterioration = lambda row: False
                sh.has_fundamental_deterioration_in_portfolio = lambda portfolio: False
            else:
                sh.is_real_deterioration = original_is_real_deterioration
                sh.has_fundamental_deterioration_in_portfolio = original_has_fundamental_deterioration
            results_by_scenario[scenario] = {}
            log(f"CENARIO {scenario}: {SCENARIOS[scenario]['descricao']}")
            for mes in sc.MONTHS_6:
                path = sh.workbook_path(mes)
                result = sh.run_free_size_for_month(
                    mes,
                    path,
                    base_settings,
                    lambda_beta=sc.LAMBDA_BETA_CONSOLIDADO,
                    downturn_signal="SINAL_A_DEFENSIVO",
                )
                results_by_scenario[scenario][mes] = result
                row = row_with_expost(mes, path, result, expost, scenario)
                scenario_rows.append(row)
                portfolio = result.get("portfolio", pd.DataFrame())
                bad_count = int(bad_fundamental_mask(portfolio).sum()) if not portfolio.empty else 0
                log(
                    f"  {mes}: status={row.get('status_sombra')} | "
                    f"ret={_pct(row.get('retorno_expost_sombra'))} | IBOV={_pct(row.get('retorno_expost_ibov'))} | "
                    f"alfa={_pct(row.get('alfa_sombra'))} | beta={row.get('beta_carteira_sombra', np.nan):.2f} | "
                    f"fund_ruim_na_carteira={bad_count} | pesos={row.get('tickers_pesos_sombra', '')}"
                )
    finally:
        sh.build_free_size_portfolio = original_build
        sh.technical_veto_to_penalty_in_opportunity = original_d3
        sh.beta_target_profile = original_beta_profile
        sh.downturn_regime_profile = original_downturn_profile
        sh.load_candidate_input = original_loader
        sh.score_assets = original_score_assets
        sh.apply_shadow_fixes = original_apply_shadow_fixes
        sh.is_real_deterioration = original_is_real_deterioration
        sh.has_fundamental_deterioration_in_portfolio = original_has_fundamental_deterioration
        CURRENT_SCENARIO = "BASELINE"

    summary = summary_rows(scenario_rows)
    log("RESUMO ACUMULADO POR CENARIO:")
    for _, row in summary[summary["mes"].astype(str).str.startswith("ACUMULADO")].iterrows():
        log(
            f"  {row.get('cenario_fundamentos')} {row.get('mes')}: "
            f"ret={_pct(row.get('retorno_expost_sombra'))} | IBOV={_pct(row.get('retorno_expost_ibov'))} | "
            f"alfa={_pct(row.get('alfa_sombra'))} | diff_vs_baseline={_pct(row.get('diff_alfa_vs_baseline'))}"
        )

    bad_entries = bad_fundamentals_entered(results_by_scenario, expost)
    if bad_entries.empty:
        log("ACOES COM FUNDAMENTO RUIM QUE ENTRARAM: nenhuma nos cenarios sem veto.")
    else:
        log("ACOES COM FUNDAMENTO RUIM QUE ENTRARAM:")
        for _, row in bad_entries.iterrows():
            log(
                f"  {row.get('cenario_fundamentos')} {row.get('mes')} {row.get('ticker')}: "
                f"peso={_pct(row.get('peso'))} ret={_pct(row.get('retorno_expost_ativo'))} "
                f"alfa_ind={_pct(row.get('alfa_individual_vs_ibov'))} motivo={row.get('motivo_fundamento_ruim')}"
            )

    validation = validation_rows(results_by_scenario, expost)
    if not validation.empty:
        max_diff = pd.to_numeric(validation.get("diferenca_retorno", pd.Series(dtype=float)), errors="coerce").abs().max()
        log(f"VALIDACAO RETORNO: maior diferenca peso x retorno = {max_diff:.10f}")
        if (pd.to_numeric(validation.get("maior_peso_individual", pd.Series(dtype=float)), errors="coerce") > 0.250001).any():
            log("REGRESSAO: algum cenario teve acao acima de 25%.")
        if validation.get("violou_max_2_por_setor", pd.Series(dtype=bool)).fillna(False).any():
            log("REGRESSAO: algum cenario violou maximo de 2 ativos por setor.")

    write_workbook(anchor_rows, scenario_rows, anchor_results, results_by_scenario, expost)
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
