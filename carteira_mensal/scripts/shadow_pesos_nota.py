from __future__ import annotations

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

OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_pesos_nota.xlsx"
LOG_FILE = ROOT / "output" / "excel" / "shadow_pesos_nota.log"

COMPOSITIONS = {
    "ATUAL": {"tendencia": 30.0, "timing": 20.0, "fundamentos": 20.0, "setor": 10.0, "risco": 20.0},
    "MAIS_FUND": {"tendencia": 20.0, "timing": 20.0, "fundamentos": 30.0, "setor": 10.0, "risco": 20.0},
    "MENOS_RISCO": {"tendencia": 30.0, "timing": 20.0, "fundamentos": 30.0, "setor": 10.0, "risco": 10.0},
    "MAIS_TECNICO": {"tendencia": 40.0, "timing": 20.0, "fundamentos": 15.0, "setor": 10.0, "risco": 15.0},
    "EQUILIBRADA": {"tendencia": 25.0, "timing": 20.0, "fundamentos": 25.0, "setor": 10.0, "risco": 20.0},
}
BASE_CAPS = {"tendencia": 30.0, "timing": 20.0, "fundamentos": 20.0, "setor": 10.0, "risco": 20.0}
CURRENT_COMPOSITION = "ATUAL"


def rescale_scores(scored: pd.DataFrame, composition: str) -> pd.DataFrame:
    out = scored.copy()
    caps = COMPOSITIONS[composition]
    component_cols = {
        "tendencia": "score_tendencia",
        "timing": "score_timing",
        "fundamentos": "score_fundamentos",
        "setor": "score_setor",
        "risco": "score_risco",
    }
    total = pd.Series(0.0, index=out.index)
    for name, col in component_cols.items():
        raw = pd.to_numeric(out.get(col, pd.Series(0.0, index=out.index)), errors="coerce").fillna(0.0)
        scaled = raw / BASE_CAPS[name] * caps[name]
        out[f"shadow_{col}_original"] = raw
        out[f"shadow_{col}_reescalado"] = scaled
        total = total + scaled
    penalty_cv = pd.to_numeric(out.get("penalidade_cv", pd.Series(0.0, index=out.index)), errors="coerce").fillna(0.0)
    penalty_timing = pd.to_numeric(out.get("penalidade_timing", pd.Series(0.0, index=out.index)), errors="coerce").fillna(0.0)
    priority_penalty = pd.to_numeric(out.get("penalidade_prioridade_otimizacao", pd.Series(0.0, index=out.index)), errors="coerce").fillna(0.0)
    out["nota_final_original_shadow"] = pd.to_numeric(out.get("nota_final", pd.Series(np.nan, index=out.index)), errors="coerce")
    out["score_prioridade_original_shadow"] = pd.to_numeric(out.get("score_prioridade_otimizacao", pd.Series(np.nan, index=out.index)), errors="coerce")
    out["nota_final"] = (total - penalty_cv - penalty_timing).clip(lower=0.0, upper=100.0)
    out["score_prioridade_otimizacao"] = (out["nota_final"] - priority_penalty).clip(lower=0.0, upper=100.0)
    out["shadow_composicao_nota"] = composition
    out["shadow_pesos_nota"] = "; ".join(f"{k}={v:.0f}" for k, v in caps.items())
    return out.sort_values(["score_prioridade_otimizacao", "nota_final"], ascending=[False, False]).reset_index(drop=True)


def make_score_assets_wrapper(original_score_assets):
    def wrapped(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
        scored = original_score_assets(frame, settings)
        return rescale_scores(scored, CURRENT_COMPOSITION)

    return wrapped


def row_with_expost(mes: str, path: Path, result: dict[str, Any], expost: pd.DataFrame, composition: str) -> dict[str, Any]:
    row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=True)
    ret = sh.portfolio_expost_return(result.get("portfolio", pd.DataFrame()), expost, mes)
    ibov = sh.ibov_return(expost, mes)
    row["composicao_nota"] = composition
    row["pesos_nota"] = "; ".join(f"{k}={v:.0f}" for k, v in COMPOSITIONS[composition].items())
    row["grupo_regime"] = sf.classify_month_group(mes)
    row["retorno_expost_carteira"] = ret
    row["retorno_expost_ibov"] = ibov
    row["alfa_expost"] = ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan
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
    for comp, group in base.groupby("composicao_nota", sort=False):
        for _, r in group.sort_values("mes").iterrows():
            out.append(r.to_dict())
        out.append({
            "composicao_nota": comp,
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
                "composicao_nota": comp,
                "mes": f"ACUMULADO_{label.upper()}",
                "grupo_regime": label,
                "retorno_expost_carteira": compounded(sub["retorno_expost_carteira"]),
                "retorno_expost_ibov": compounded(sub["retorno_expost_ibov"]),
                "alfa_expost": compounded(sub["retorno_expost_carteira"]) - compounded(sub["retorno_expost_ibov"]),
            })
    summary = pd.DataFrame(out)
    key_rows = summary[summary["mes"].astype(str).str.startswith("ACUMULADO")].copy()
    if not key_rows.empty:
        atual = key_rows[key_rows["composicao_nota"].eq("ATUAL")].set_index("mes")["alfa_expost"]
        summary["diff_alfa_vs_atual"] = summary.apply(
            lambda r: r.get("alfa_expost", np.nan) - atual.get(r.get("mes"), np.nan) if str(r.get("mes", "")).startswith("ACUMULADO") else np.nan,
            axis=1,
        )
    return summary


def portfolio_rows(results_by_comp: dict[str, dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for comp, results in results_by_comp.items():
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
                    "composicao_nota": comp,
                    "mes": mes,
                    "ticker": ticker,
                    "peso": peso,
                    "retorno_expost_ativo": ret_asset,
                    "contribuicao_retorno": peso * ret_asset if pd.notna(ret_asset) else np.nan,
                    "setor": r.get("setor", ""),
                    "beta": r.get("beta", np.nan),
                    "nota_final": r.get("nota_final", np.nan),
                    "nota_final_original_shadow": r.get("nota_final_original_shadow", np.nan),
                    "score_prioridade_otimizacao": r.get("score_prioridade_otimizacao", np.nan),
                    "score_prioridade_original_shadow": r.get("score_prioridade_original_shadow", np.nan),
                    "score_tendencia": r.get("score_tendencia", np.nan),
                    "score_timing": r.get("score_timing", np.nan),
                    "score_fundamentos": r.get("score_fundamentos", np.nan),
                    "score_setor": r.get("score_setor", np.nan),
                    "score_risco": r.get("score_risco", np.nan),
                    "shadow_score_tendencia_reescalado": r.get("shadow_score_tendencia_reescalado", np.nan),
                    "shadow_score_timing_reescalado": r.get("shadow_score_timing_reescalado", np.nan),
                    "shadow_score_fundamentos_reescalado": r.get("shadow_score_fundamentos_reescalado", np.nan),
                    "shadow_score_setor_reescalado": r.get("shadow_score_setor_reescalado", np.nan),
                    "shadow_score_risco_reescalado": r.get("shadow_score_risco_reescalado", np.nan),
                    "forca_relativa_score": r.get("forca_relativa_score", np.nan),
                    "sinal_v3_ajustado_beta": r.get("sinal_v3_ajustado_beta_tamanho_livre", np.nan),
                })
    return pd.DataFrame(rows)


def validation_rows(results_by_comp: dict[str, dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for comp, results in results_by_comp.items():
        df = pd.DataFrame(sh.free_size_validation_rows(results, expost))
        if not df.empty:
            df.insert(0, "composicao_nota", comp)
            frames.append(df)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def changed_weights(results_by_comp: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    atual = results_by_comp.get("ATUAL", {})
    for comp, results in results_by_comp.items():
        if comp == "ATUAL":
            continue
        for mes in sc.MONTHS_6:
            a = atual.get(mes, {}).get("portfolio", pd.DataFrame())
            b = results.get(mes, {}).get("portfolio", pd.DataFrame())
            aw = a.set_index("ticker")["peso_recomendado"] if not a.empty and "ticker" in a else pd.Series(dtype=float)
            bw = b.set_index("ticker")["peso_recomendado"] if not b.empty and "ticker" in b else pd.Series(dtype=float)
            for ticker in sorted(set(aw.index.astype(str)) | set(bw.index.astype(str))):
                delta = float(bw.get(ticker, 0.0) - aw.get(ticker, 0.0))
                if abs(delta) > 1e-8:
                    rows.append({"composicao_nota": comp, "mes": mes, "ticker": ticker, "peso_atual": float(aw.get(ticker, 0.0)), "peso_alternativo": float(bw.get(ticker, 0.0)), "delta_peso": delta})
    return pd.DataFrame(rows)


def write_workbook(anchor_rows: list[dict[str, Any]], scenario_rows: list[dict[str, Any]], anchor_results: dict[str, Any], results_by_comp: dict[str, dict[str, Any]], expost: pd.DataFrame, unit_df: pd.DataFrame) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        build_summary(scenario_rows).to_excel(writer, sheet_name="resumo_por_composicao", index=False)
        portfolio_rows(results_by_comp, expost).to_excel(writer, sheet_name="carteiras_por_composicao", index=False)
        validation_rows(results_by_comp, expost).to_excel(writer, sheet_name="validacao_retorno", index=False)
        changed_weights(results_by_comp).to_excel(writer, sheet_name="diferenca_pesos", index=False)
        pd.DataFrame([{"composicao_nota": k, **v} for k, v in COMPOSITIONS.items()]).to_excel(writer, sheet_name="hipoteses_testadas", index=False)
        unit_df.to_excel(writer, sheet_name="teste_unitario_pesos", index=False)
        for mes, result in anchor_results.items():
            result.get("portfolio", pd.DataFrame()).to_excel(writer, sheet_name=f"ancora_{mes[-2:]}", index=False)
        for comp, results in results_by_comp.items():
            for mes, result in results.items():
                result.get("portfolio", pd.DataFrame()).to_excel(writer, sheet_name=f"{comp[:10]}_{mes[-2:]}"[:31], index=False)


def main() -> None:
    global CURRENT_COMPOSITION
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
        result = sh.run_optimizer_for_month(mes, path, base_settings, shadow_fixes=False, enable_partial_portfolio=False, enable_beta_target=False, enable_objetivo_retorno=False, enable_composicao_ampliada=False)
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
    original_score_assets = sh.score_assets

    sh.build_free_size_portfolio = sc.consolidated_build_free_size_portfolio
    sh.technical_veto_to_penalty_in_opportunity = sc.make_extended_d3(original_d3)
    sh.beta_target_profile = sf.consolidated_beta_target_profile_factory(original_beta_profile)
    sh.downturn_regime_profile = sf.consolidated_downturn_profile_factory(original_downturn_profile)
    sh.load_candidate_input = sf.make_extended_load_candidate_input(original_loader)
    sh.score_assets = make_score_assets_wrapper(original_score_assets)

    unit_df = sc.run_weight_unit_tests()
    scenario_rows: list[dict[str, Any]] = []
    results_by_comp: dict[str, dict[str, Any]] = {}
    try:
        for comp in COMPOSITIONS:
            CURRENT_COMPOSITION = comp
            results_by_comp[comp] = {}
            log(f"COMPOSICAO {comp}: {COMPOSITIONS[comp]}")
            for mes in sc.MONTHS_6:
                path = sh.workbook_path(mes)
                result = sh.run_free_size_for_month(mes, path, base_settings, lambda_beta=sc.LAMBDA_BETA_CONSOLIDADO, downturn_signal="SINAL_A_DEFENSIVO")
                results_by_comp[comp][mes] = result
                row = row_with_expost(mes, path, result, expost, comp)
                scenario_rows.append(row)
                port = result.get("portfolio", pd.DataFrame())
                tickers = ",".join(port.get("ticker", pd.Series(dtype=str)).astype(str).tolist()) if not port.empty else ""
                weights = ",".join([f"{x:.1%}" for x in pd.to_numeric(port.get("peso_recomendado", pd.Series(dtype=float)), errors="coerce").fillna(0)]) if not port.empty else ""
                log(f"  {mes}: grupo={row.get('grupo_regime')} ret={row.get('retorno_expost_carteira', np.nan):.2%} ibov={row.get('retorno_expost_ibov', np.nan):.2%} alfa={row.get('alfa_expost', np.nan):.2%} tickers={tickers} pesos={weights}")
    finally:
        sh.build_free_size_portfolio = original_build
        sh.technical_veto_to_penalty_in_opportunity = original_d3
        sh.beta_target_profile = original_beta_profile
        sh.downturn_regime_profile = original_downturn_profile
        sh.load_candidate_input = original_loader
        sh.score_assets = original_score_assets
        CURRENT_COMPOSITION = "ATUAL"

    summary = build_summary(scenario_rows)
    log("RESUMO ACUMULADO:")
    for _, r in summary[summary["mes"].astype(str).str.startswith("ACUMULADO")].iterrows():
        log(f"  {r.get('composicao_nota')} {r.get('mes')}: alfa={r.get('alfa_expost', np.nan):.2%} diff_vs_atual={r.get('diff_alfa_vs_atual', np.nan):.2%}")

    write_workbook(anchor_rows, scenario_rows, anchor_results, results_by_comp, expost, unit_df)
    logs.append(f"Arquivo gerado: {OUTPUT_FILE}")
    logs.append(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    print(f"Arquivo gerado: {OUTPUT_FILE}")
    print(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
