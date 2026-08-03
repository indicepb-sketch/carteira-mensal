from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for p in (str(SRC), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from utils import load_settings  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
import shadow_consolidada_6meses as sc  # noqa: E402
import shadow_forca_relativa_continua as sf  # noqa: E402

OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_camada_c_alta.xlsx"
LOG_FILE = ROOT / "output" / "excel" / "shadow_camada_c_alta.log"

SCENARIOS = {
    "BASELINE_C_ATIVA": "baseline consolidado atual, Camada C ativa",
    "C_AFROUXADA_ALTA": "Camada C afrouxada somente nos meses de alta",
}
CURRENT_SCENARIO = "BASELINE_C_ATIVA"
CURRENT_MES = ""


def is_high_month(mes: str) -> bool:
    subtype, _reason = sc.CONSOLIDATED_SIGNAL_PROFILE.get(mes, ("", ""))
    return str(subtype).lower() == "alta"


def append_token(text: Any, token: str) -> str:
    parts = [p.strip() for p in str(text or "").split(";") if p and p.strip()]
    if token not in parts:
        parts.append(token)
    return "; ".join(parts)


def remove_tokens(text: Any, tokens: list[str]) -> str:
    raw = str(text or "")
    parts = [p.strip() for p in raw.split(";") if p and p.strip()]
    out = []
    for part in parts:
        lower = part.lower()
        if any(tok.lower() in lower for tok in tokens):
            continue
        out.append(part)
    return "; ".join(out)


def relax_layer_c_if_needed(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or CURRENT_SCENARIO != "C_AFROUXADA_ALTA" or not is_high_month(CURRENT_MES):
        return frame
    out = frame.copy()
    for col in [
        "alerta_realizacao_pos_rali",
        "penalizacao_realizacao_pos_rali",
        "motivos_alerta_realizacao_pos_rali",
        "motivo_peso_maximo_reduzido",
        "peso_maximo_timing_com_alerta",
        "penalizacoes_otimizacao",
        "alertas_nao_bloqueantes",
        "qualidade_do_timing",
    ]:
        if col not in out.columns:
            out[col] = np.nan if col.startswith("peso_") else ""

    alert = out["alerta_realizacao_pos_rali"].map(sh.to_bool).fillna(False)
    motivo = out["motivo_peso_maximo_reduzido"].fillna("").astype(str).str.lower()
    reason = out["motivos_alerta_realizacao_pos_rali"].fillna("").astype(str).str.lower()
    penalty = out["penalizacoes_otimizacao"].fillna("").astype(str).str.lower()
    mask = alert | motivo.str.contains("realizacao_pos_rali", na=False) | reason.str.contains("rali|rally|banda superior|rsi", regex=True, na=False) | penalty.str.contains("realizacao_pos_rali", na=False)
    if not mask.any():
        out["camada_c_afrouxada_alta"] = False
        return out

    out["camada_c_afrouxada_alta"] = False
    out.loc[mask, "camada_c_afrouxada_alta"] = True
    out.loc[mask, "alerta_realizacao_pos_rali"] = False
    out.loc[mask, "penalizacao_realizacao_pos_rali"] = False
    out.loc[mask, "motivos_alerta_realizacao_pos_rali"] = ""
    out.loc[mask, "motivo_peso_maximo_reduzido"] = out.loc[mask, "motivo_peso_maximo_reduzido"].map(lambda x: remove_tokens(x, ["realizacao_pos_rali", "rali", "rally"]))
    out.loc[mask, "penalizacoes_otimizacao"] = out.loc[mask, "penalizacoes_otimizacao"].map(lambda x: remove_tokens(x, ["penalizacao_realizacao_pos_rali", "realizacao_pos_rali"]))
    out.loc[mask, "alertas_nao_bloqueantes"] = out.loc[mask, "alertas_nao_bloqueantes"].map(lambda x: remove_tokens(x, ["alerta_realizacao_pos_rali", "realizacao_pos_rali"]))
    # This cap is the mechanical expression of the post-rally alert in the high-regime layer.
    cap_reason = motivo.str.contains("realizacao_pos_rali", na=False) | alert
    out.loc[mask & cap_reason, "peso_maximo_timing_com_alerta"] = np.nan
    quality_is_alert = out["qualidade_do_timing"].fillna("").astype(str).eq("timing_com_alerta")
    out.loc[mask & quality_is_alert, "qualidade_do_timing"] = "timing_saudavel"
    out.loc[mask, "shadow_motivos_correcoes"] = out.loc[mask].get("shadow_motivos_correcoes", pd.Series("", index=out.loc[mask].index)).map(lambda x: append_token(x, "camada_c_realizacao_pos_rali_afrouxada_alta"))
    return out


def make_layer_c_apply_shadow_fixes(original_apply):
    def wrapped(frame: pd.DataFrame, regime: str) -> pd.DataFrame:
        fixed = original_apply(frame, regime)
        return relax_layer_c_if_needed(fixed)

    return wrapped


def row_with_expost(mes: str, path: Path, result: dict[str, Any], expost: pd.DataFrame, cenario: str) -> dict[str, Any]:
    row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=True)
    ret = sh.portfolio_expost_return(result.get("portfolio", pd.DataFrame()), expost, mes)
    ibov = sh.ibov_return(expost, mes)
    row["cenario"] = cenario
    row["descricao_cenario"] = SCENARIOS[cenario]
    row["grupo_regime"] = sf.classify_month_group(mes)
    row["retorno_expost_carteira"] = ret
    row["retorno_expost_ibov"] = ibov
    row["alfa_expost"] = ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan
    cand = result.get("candidates", pd.DataFrame())
    if not cand.empty:
        row["qtd_camada_c_afrouxada"] = int(cand.get("camada_c_afrouxada_alta", pd.Series(False, index=cand.index)).map(sh.to_bool).fillna(False).sum())
        row["tickers_camada_c_afrouxada"] = ", ".join(cand.loc[cand.get("camada_c_afrouxada_alta", pd.Series(False, index=cand.index)).map(sh.to_bool).fillna(False), "ticker"].astype(str).tolist()) if "ticker" in cand else ""
    else:
        row["qtd_camada_c_afrouxada"] = 0
        row["tickers_camada_c_afrouxada"] = ""
    return row


def compounded(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def build_summary(rows: list[dict[str, Any]]) -> pd.DataFrame:
    base = pd.DataFrame(rows)
    if base.empty:
        return base
    out = []
    for cenario, group in base.groupby("cenario", sort=False):
        for _, r in group.sort_values("mes").iterrows():
            out.append(r.to_dict())
        out.append({
            "cenario": cenario,
            "mes": "ACUMULADO_6_MESES",
            "grupo_regime": "total",
            "retorno_expost_carteira": compounded(group["retorno_expost_carteira"]),
            "retorno_expost_ibov": compounded(group["retorno_expost_ibov"]),
            "alfa_expost": compounded(group["retorno_expost_carteira"]) - compounded(group["retorno_expost_ibov"]),
        })
        for label in ["alta", "baixa", "jun_oportunidade"]:
            sub = group[group["grupo_regime"].eq(label)]
            if sub.empty:
                continue
            out.append({
                "cenario": cenario,
                "mes": f"ACUMULADO_{label.upper()}",
                "grupo_regime": label,
                "retorno_expost_carteira": compounded(sub["retorno_expost_carteira"]),
                "retorno_expost_ibov": compounded(sub["retorno_expost_ibov"]),
                "alfa_expost": compounded(sub["retorno_expost_carteira"]) - compounded(sub["retorno_expost_ibov"]),
            })
    summary = pd.DataFrame(out)
    # Add baseline delta for monthly rows.
    month_rows = summary[~summary["mes"].astype(str).str.startswith("ACUMULADO")].copy()
    if not month_rows.empty:
        pivot = month_rows.pivot(index="mes", columns="cenario", values="alfa_expost")
        if {"BASELINE_C_ATIVA", "C_AFROUXADA_ALTA"}.issubset(pivot.columns):
            delta = (pivot["C_AFROUXADA_ALTA"] - pivot["BASELINE_C_ATIVA"]).rename("delta_alfa_vs_baseline")
            summary = summary.merge(delta, left_on="mes", right_index=True, how="left")
    return summary


def portfolio_rows(results_by_scenario: dict[str, dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cenario, results in results_by_scenario.items():
        for mes, result in results.items():
            portfolio = result.get("portfolio", pd.DataFrame())
            if portfolio.empty:
                continue
            panel = expost[expost["mes"].astype(str).eq(mes)].set_index("ticker")
            for _, r in portfolio.iterrows():
                ticker = str(r.get("ticker", ""))
                ret_asset = panel.loc[ticker, "retorno_realizado_periodo"] if ticker in panel.index and "retorno_realizado_periodo" in panel else np.nan
                peso = float(pd.to_numeric(pd.Series([r.get("peso_recomendado", r.get("peso_final", 0.0))]), errors="coerce").fillna(0).iloc[0])
                rows.append({
                    "cenario": cenario,
                    "mes": mes,
                    "ticker": ticker,
                    "peso": peso,
                    "retorno_expost_ativo": ret_asset,
                    "contribuicao_retorno": peso * ret_asset if pd.notna(ret_asset) else np.nan,
                    "setor": r.get("setor", ""),
                    "beta": r.get("beta", np.nan),
                    "nota_final": r.get("nota_final", np.nan),
                    "score_prioridade_otimizacao": r.get("score_prioridade_otimizacao", np.nan),
                    "forca_relativa_score": r.get("forca_relativa_score", np.nan),
                    "alerta_realizacao_pos_rali": r.get("alerta_realizacao_pos_rali", False),
                    "camada_c_afrouxada_alta": r.get("camada_c_afrouxada_alta", False),
                    "peso_maximo_timing_com_alerta": r.get("peso_maximo_timing_com_alerta", np.nan),
                    "peso_maximo_permitido_ativo": r.get("peso_maximo_permitido_ativo", np.nan),
                    "sinal_v3_ajustado_beta": r.get("sinal_v3_ajustado_beta_tamanho_livre", np.nan),
                    "peso_antes_teto": r.get("peso_antes_teto_tamanho_livre", np.nan),
                    "teto_aplicado": r.get("teto_tamanho_livre_aplicado", False),
                })
    return pd.DataFrame(rows)


def validation_rows(results_by_scenario: dict[str, dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for cenario, results in results_by_scenario.items():
        df = pd.DataFrame(sh.free_size_validation_rows(results, expost))
        if not df.empty:
            df.insert(0, "cenario", cenario)
            frames.append(df)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def changed_assets_table(results_by_scenario: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    base = results_by_scenario.get("BASELINE_C_ATIVA", {})
    relaxed = results_by_scenario.get("C_AFROUXADA_ALTA", {})
    for mes in sc.MONTHS_6:
        b = base.get(mes, {}).get("portfolio", pd.DataFrame())
        r = relaxed.get(mes, {}).get("portfolio", pd.DataFrame())
        if b.empty and r.empty:
            continue
        bw = b.set_index("ticker")["peso_recomendado"] if not b.empty and "ticker" in b else pd.Series(dtype=float)
        rw = r.set_index("ticker")["peso_recomendado"] if not r.empty and "ticker" in r else pd.Series(dtype=float)
        tickers = sorted(set(bw.index.astype(str)) | set(rw.index.astype(str)))
        for ticker in tickers:
            rows.append({
                "mes": mes,
                "ticker": ticker,
                "peso_baseline": float(bw.get(ticker, 0.0)),
                "peso_c_afrouxada": float(rw.get(ticker, 0.0)),
                "delta_peso": float(rw.get(ticker, 0.0) - bw.get(ticker, 0.0)),
            })
    return pd.DataFrame(rows)


def write_workbook(anchor_rows: list[dict[str, Any]], scenario_rows: list[dict[str, Any]], anchor_results: dict[str, Any], results_by_scenario: dict[str, dict[str, Any]], expost: pd.DataFrame, unit_df: pd.DataFrame) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        build_summary(scenario_rows).to_excel(writer, sheet_name="resumo_2_cenarios", index=False)
        portfolio_rows(results_by_scenario, expost).to_excel(writer, sheet_name="carteiras_por_mes", index=False)
        validation_rows(results_by_scenario, expost).to_excel(writer, sheet_name="validacao_retorno", index=False)
        changed_assets_table(results_by_scenario).to_excel(writer, sheet_name="diferenca_pesos", index=False)
        unit_df.to_excel(writer, sheet_name="teste_unitario_pesos", index=False)
        for mes, result in anchor_results.items():
            result.get("portfolio", pd.DataFrame()).to_excel(writer, sheet_name=f"ancora_{mes[-2:]}", index=False)
        for cenario, results in results_by_scenario.items():
            prefix = "base" if cenario == "BASELINE_C_ATIVA" else "relaxC"
            for mes, result in results.items():
                result.get("portfolio", pd.DataFrame()).to_excel(writer, sheet_name=f"{prefix}_{mes[-2:]}"[:31], index=False)


def main() -> None:
    global CURRENT_SCENARIO, CURRENT_MES
    sh.MONTHS = sc.MONTHS_6
    logs: list[str] = []

    def log(message: str) -> None:
        print(message)
        logs.append(message)

    base_settings = load_settings()
    expost = sc.load_expost_6(sc.MONTHS_6)
    log("Datas/retornos ex-post carregados:")
    for mes in sc.MONTHS_6:
        log(f"  {mes}: linhas={len(expost[expost['mes'].astype(str).eq(mes)])} IBOV={sh.ibov_return(expost, mes):.2%}")

    anchor_rows: list[dict[str, Any]] = []
    anchor_results: dict[str, Any] = {}
    all_pass = True
    log("TESTE-ANCORA (flags sombra desligadas):")
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
        write_workbook(anchor_rows, [], anchor_results, {}, expost, pd.DataFrame())
        LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
        raise SystemExit("Ancora falhou; teste abortado.")

    original_build = sh.build_free_size_portfolio
    original_d3 = sh.technical_veto_to_penalty_in_opportunity
    original_beta_profile = sh.beta_target_profile
    original_downturn_profile = sh.downturn_regime_profile
    original_loader = sh.load_candidate_input
    original_apply_fixes = sh.apply_shadow_fixes

    sh.build_free_size_portfolio = sc.consolidated_build_free_size_portfolio
    sh.technical_veto_to_penalty_in_opportunity = sc.make_extended_d3(original_d3)
    sh.beta_target_profile = sf.consolidated_beta_target_profile_factory(original_beta_profile)
    sh.downturn_regime_profile = sf.consolidated_downturn_profile_factory(original_downturn_profile)
    sh.load_candidate_input = sf.make_extended_load_candidate_input(original_loader)
    sh.apply_shadow_fixes = make_layer_c_apply_shadow_fixes(original_apply_fixes)

    unit_df = sc.run_weight_unit_tests()
    scenario_rows: list[dict[str, Any]] = []
    results_by_scenario: dict[str, dict[str, Any]] = {}
    try:
        for cenario, desc in SCENARIOS.items():
            CURRENT_SCENARIO = cenario
            results_by_scenario[cenario] = {}
            log(f"CENARIO {cenario}: {desc}")
            for mes in sc.MONTHS_6:
                CURRENT_MES = mes
                path = sh.workbook_path(mes)
                result = sh.run_free_size_for_month(
                    mes,
                    path,
                    base_settings,
                    lambda_beta=sc.LAMBDA_BETA_CONSOLIDADO,
                    downturn_signal="SINAL_A_DEFENSIVO",
                )
                results_by_scenario[cenario][mes] = result
                row = row_with_expost(mes, path, result, expost, cenario)
                scenario_rows.append(row)
                port = result.get("portfolio", pd.DataFrame())
                tickers = ",".join(port.get("ticker", pd.Series(dtype=str)).astype(str).tolist()) if not port.empty else ""
                weights = ",".join([f"{x:.1%}" for x in pd.to_numeric(port.get("peso_recomendado", pd.Series(dtype=float)), errors="coerce").fillna(0)]) if not port.empty else ""
                log(
                    f"  {mes}: grupo={row.get('grupo_regime')} relaxC={row.get('qtd_camada_c_afrouxada', 0)} "
                    f"ret={row.get('retorno_expost_carteira', np.nan):.2%} ibov={row.get('retorno_expost_ibov', np.nan):.2%} alfa={row.get('alfa_expost', np.nan):.2%} "
                    f"tickers={tickers} pesos={weights}"
                )
    finally:
        sh.build_free_size_portfolio = original_build
        sh.technical_veto_to_penalty_in_opportunity = original_d3
        sh.beta_target_profile = original_beta_profile
        sh.downturn_regime_profile = original_downturn_profile
        sh.load_candidate_input = original_loader
        sh.apply_shadow_fixes = original_apply_fixes
        CURRENT_MES = ""
        CURRENT_SCENARIO = "BASELINE_C_ATIVA"

    summary = build_summary(scenario_rows)
    log("RESUMO:")
    for _, r in summary[summary["mes"].astype(str).str.startswith("ACUMULADO")].iterrows():
        log(f"  {r.get('cenario')} {r.get('mes')}: ret={r.get('retorno_expost_carteira', np.nan):.2%} ibov={r.get('retorno_expost_ibov', np.nan):.2%} alfa={r.get('alfa_expost', np.nan):.2%}")
    # Regression check: low months must be identical.
    detail = pd.DataFrame(scenario_rows)
    pivot = detail.pivot(index="mes", columns="cenario", values="retorno_expost_carteira")
    if {"BASELINE_C_ATIVA", "C_AFROUXADA_ALTA"}.issubset(pivot.columns):
        for mes in ["2026-03", "2026-04", "2026-05"]:
            diff = float(pivot.loc[mes, "C_AFROUXADA_ALTA"] - pivot.loc[mes, "BASELINE_C_ATIVA"])
            log(f"CHECK_QUEDA_IDENTICA {mes}: delta_retorno={diff:.8f}")

    write_workbook(anchor_rows, scenario_rows, anchor_results, results_by_scenario, expost, unit_df)
    logs.append(f"Arquivo gerado: {OUTPUT_FILE}")
    logs.append(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    print(f"Arquivo gerado: {OUTPUT_FILE}")
    print(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
