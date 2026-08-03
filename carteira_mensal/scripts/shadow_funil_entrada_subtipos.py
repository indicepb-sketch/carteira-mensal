from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import shadow_consolidada_6meses as cons
import shadow_simulacao as sh
from utils import load_settings


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_funil_entrada_subtipos.xlsx"
LOG_FILE = ROOT / "output" / "excel" / "shadow_funil_entrada_subtipos.log"

SCENARIOS = {
    "BASELINE": "funil atual",
    "PENALIZA_TARDIA": "classifica candidata limpa tardia e reduz score/teto",
    "LIBERA_FORMACAO": "libera quase candidatas 2-de-3 como entrada em formacao",
    "TARDIA_E_FORMACAO": "penaliza tardia e libera entrada em formacao",
}
CURRENT_SCENARIO = "BASELINE"


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def num(row: pd.Series, col: str, default: float = np.nan) -> float:
    try:
        value = pd.to_numeric(pd.Series([row.get(col, default)]), errors="coerce").iloc[0]
        return float(value) if pd.notna(value) else default
    except Exception:
        return default


def extra_enrich(frame: pd.DataFrame, path: Path) -> pd.DataFrame:
    if frame.empty or "ticker" not in frame.columns:
        return frame
    out = frame.copy()
    wanted = [
        "ticker",
        "decisao_preliminar_ajustada",
        "motivo_decisao_preliminar",
        "tendencia_mensal",
        "leitura_forca_relativa_mensal",
        "qualidade_fundamentalista",
        "classificacao_fundamentalista_setorial",
        "tipo_timing",
        "distancia_banda_superior_pct",
        "distancia_banda_inferior_pct",
        "rsi",
        "retorno_acumulado_1m",
        "retorno_acumulado_4m",
        "bloqueada_entrada_esticada",
        "fundamento_bloqueante",
        "roe",
        "margem_liquida",
        "pl_atual",
    ]
    for sheet in ("Analise Preliminar", "Otimizacao", "Candidatas Risco"):
        src = sh.read_sheet(path, sheet)
        if src.empty or "ticker" not in src.columns:
            continue
        cols = [c for c in wanted if c in src.columns]
        if len(cols) <= 1:
            continue
        lookup = src[cols].drop_duplicates("ticker").set_index("ticker")
        for col in lookup.columns:
            values = out["ticker"].map(lookup[col])
            if col not in out.columns:
                out[col] = values
            else:
                out[col] = out[col].where(out[col].notna(), values)
    return out


def custom_candidate_loader(original_loader):
    def wrapped(path: Path, settings: dict | None = None) -> pd.DataFrame:
        if CURRENT_SCENARIO == "BASELINE":
            return original_loader(path, settings)
        candidates = sh.read_sheet(path, "Otimizacao")
        if candidates.empty:
            candidates = original_loader(path, settings)
        candidates = candidates.drop_duplicates("ticker").copy() if "ticker" in candidates.columns else candidates
        candidates = sh.enrich_candidate_input(candidates, path, include_downturn_cols=True)
        candidates = extra_enrich(candidates, path)
        return candidates

    return wrapped


def classify_entry_subtype(row: pd.Series) -> tuple[str, str]:
    trend = str(row.get("tendencia_mensal", ""))
    timing = str(row.get("tipo_timing", ""))
    rel = str(row.get("leitura_forca_relativa_mensal", ""))
    qual = str(row.get("qualidade_fundamentalista", ""))
    decision = str(row.get("decisao_preliminar_ajustada", ""))
    rsi = num(row, "rsi")
    dist_upper = num(row, "distancia_banda_superior_pct")
    ret_1m = num(row, "retorno_acumulado_1m")
    trend_ok = trend in {"alta_forte_mensal", "alta_aceitavel_ou_virada"}
    timing_ok = timing in {"timing_favoravel_tendencia", "timing_favoravel_com_alerta"}
    rel_ok = rel in {"forte_no_mes", "positiva_no_mes"}
    fund_ok = qual in {"otima", "boa", "aceitavel"} and not sh.to_bool(row.get("fundamento_bloqueante", False))
    clean = decision == "candidata_para_risco" or (trend_ok and timing_ok and rel_ok and fund_ok)
    late_flags = []
    if pd.notna(rsi) and rsi >= 65:
        late_flags.append("rsi>=65")
    if pd.notna(ret_1m) and ret_1m >= 0.08:
        late_flags.append("retorno_1m>=8pct")
    if pd.notna(dist_upper) and dist_upper <= 0.05:
        late_flags.append("perto_banda_superior")
    if "esticado" in timing.lower() or sh.to_bool(row.get("bloqueada_entrada_esticada", False)):
        late_flags.append("timing_esticado")
    if clean and not late_flags and (pd.isna(rsi) or 50 <= rsi <= 65):
        return "entrada_saudavel", "candidata limpa sem sinais fortes de entrada tardia"
    if clean and late_flags:
        return "entrada_tardia", "; ".join(late_flags)
    ok_count = int(trend_ok) + int(timing_ok) + int(rel_ok)
    if fund_ok and ok_count >= 2 and not late_flags:
        return "entrada_em_formacao", f"{ok_count}_de_3_sinais_tecnicos_sem_entrada_tardia"
    if fund_ok and ok_count >= 2 and late_flags:
        return "entrada_em_formacao_tardia", f"{ok_count}_de_3_sinais_mas_tardia: " + "; ".join(late_flags)
    return "fora_do_teste_funil", "nao atende subtipo testado"


def apply_entry_funnel_scenario(frame: pd.DataFrame, regime: str) -> pd.DataFrame:
    out = ORIGINAL_APPLY_SHADOW_FIXES(frame, regime)
    if CURRENT_SCENARIO == "BASELINE" or out.empty:
        out["subtipo_entrada_teste4"] = ""
        out["motivo_subtipo_entrada_teste4"] = ""
        return out

    out = extra_enrich(out, CURRENT_PATH)
    classified = out.apply(classify_entry_subtype, axis=1, result_type="expand")
    classified.columns = ["subtipo_entrada_teste4", "motivo_subtipo_entrada_teste4"]
    out = pd.concat([out, classified], axis=1)

    reason = out.get("motivo_bloqueio_otimizacao", pd.Series("", index=out.index)).fillna("").astype(str)
    deterioration = out.apply(sh.is_real_deterioration, axis=1) | out.get("fundamento_bloqueante", pd.Series(False, index=out.index)).map(sh.to_bool)
    allowed_by_data = out.get("retorno_medio", pd.Series(np.nan, index=out.index)).notna()
    already_free = out.get("liberado_para_otimizacao", pd.Series(False, index=out.index)).map(sh.to_bool)

    if CURRENT_SCENARIO in {"PENALIZA_TARDIA", "TARDIA_E_FORMACAO"}:
        tardia = out["subtipo_entrada_teste4"].eq("entrada_tardia")
        out.loc[tardia, "penalizacoes_otimizacao"] = out.loc[tardia, "penalizacoes_otimizacao"].map(
            lambda x: sh.append_token(x, "teste4_penalizacao_entrada_tardia")
        )
        base_score = pd.to_numeric(out.get("score_prioridade_otimizacao", out.get("nota_final", pd.Series(0, index=out.index))), errors="coerce").fillna(0)
        out.loc[tardia, "score_prioridade_otimizacao"] = base_score.loc[tardia] - 20.0
        if "peso_maximo_timing_com_alerta" not in out.columns:
            out["peso_maximo_timing_com_alerta"] = np.nan
        current_cap = pd.to_numeric(out.loc[tardia, "peso_maximo_timing_com_alerta"], errors="coerce")
        out.loc[tardia, "peso_maximo_timing_com_alerta"] = np.minimum(current_cap.fillna(0.10), 0.10)
        out.loc[tardia, "shadow_motivos_correcoes"] = out.loc[tardia, "shadow_motivos_correcoes"].map(
            lambda x: sh.append_token(x, "teste4_entrada_tardia_cap10")
        )

    if CURRENT_SCENARIO in {"LIBERA_FORMACAO", "TARDIA_E_FORMACAO"}:
        formacao = out["subtipo_entrada_teste4"].eq("entrada_em_formacao") & ~deterioration & allowed_by_data
        # Remove somente bloqueios tecnicos/timing/forca. Bloqueios fundamentais ficam.
        technical_tokens = [
            "watchlist", "timing", "entrada esticada", "entrada_esticada", "sobrecompra",
            "tendencia tecnica negativa", "tendencia_tecnica_negativa", "forca_relativa_fraca",
            "bloqueio_por_forca_relativa_fraca", "bloqueio_por_tendencia_mensal_desfavoravel",
        ]
        out.loc[formacao, "motivo_bloqueio_otimizacao"] = out.loc[formacao, "motivo_bloqueio_otimizacao"].map(
            lambda x: sh.remove_tokens(x, technical_tokens)
        )
        out.loc[formacao, "tipo_bloqueio_otimizacao"] = out.loc[formacao, "tipo_bloqueio_otimizacao"].map(
            lambda x: sh.remove_tokens(x, ["bloqueio_tecnico", "watchlist"])
        )
        out.loc[formacao, "penalizacoes_otimizacao"] = out.loc[formacao, "penalizacoes_otimizacao"].map(
            lambda x: sh.append_token(x, "teste4_entrada_em_formacao_penalizada")
        )
        if "peso_maximo_timing_com_alerta" not in out.columns:
            out["peso_maximo_timing_com_alerta"] = np.nan
        current_cap = pd.to_numeric(out.loc[formacao, "peso_maximo_timing_com_alerta"], errors="coerce")
        out.loc[formacao, "peso_maximo_timing_com_alerta"] = np.minimum(current_cap.fillna(0.12), 0.12)
        out.loc[formacao, "status_para_risco"] = "moderada_para_risco"
        out.loc[formacao, "categoria_elegibilidade"] = "elegivel_moderado"
        out.loc[formacao, "shadow_motivos_correcoes"] = out.loc[formacao, "shadow_motivos_correcoes"].map(
            lambda x: sh.append_token(x, "teste4_quase_candidata_2de3_liberada")
        )

    final_reason = out.get("motivo_bloqueio_otimizacao", pd.Series("", index=out.index)).fillna("").astype(str).str.strip()
    status_ok = out.get("status_para_risco", pd.Series("", index=out.index)).isin(["aprovada_para_risco", "moderada_para_risco"])
    category_ok = out.get("categoria_elegibilidade", pd.Series("", index=out.index)).isin(["elegivel_forte", "elegivel_moderado"])
    out["bloqueado_otimizacao"] = final_reason.ne("")
    out["liberado_para_otimizacao"] = ((~out["bloqueado_otimizacao"]) & status_ok & category_ok & allowed_by_data) | already_free
    return out


def install_consolidated_hooks() -> None:
    original_downturn_profile = sh.downturn_regime_profile
    original_beta_target_profile = sh.beta_target_profile
    original_d3 = sh.technical_veto_to_penalty_in_opportunity
    original_loader = sh.load_candidate_input

    sh.build_free_size_portfolio = cons.consolidated_build_free_size_portfolio
    sh.technical_veto_to_penalty_in_opportunity = cons.make_extended_d3(original_d3)
    sh.apply_shadow_fixes = apply_entry_funnel_scenario
    sh.load_candidate_input = custom_candidate_loader(original_loader)

    def consolidated_beta_target_profile(path: Path, settings: dict) -> dict:
        base = dict(original_beta_target_profile(path, settings))
        match = re.search(r"2026_(\d{2})", path.name)
        mes_key = f"2026-{match.group(1)}" if match else ""
        base.update(cons.CONSOLIDATED_BETA_PROFILE.get(mes_key, {}))
        return base

    def consolidated_downturn_profile(path: Path, settings: dict) -> dict:
        base = dict(original_downturn_profile(path, settings))
        match = re.search(r"2026_(\d{2})", path.name)
        mes_key = f"2026-{match.group(1)}" if match else ""
        subtype, reason = cons.CONSOLIDATED_SIGNAL_PROFILE.get(
            mes_key, (base.get("subtipo_queda", ""), base.get("motivo_subtipo_queda", ""))
        )
        base["subtipo_queda"] = subtype
        base["motivo_subtipo_queda"] = reason
        return base

    sh.beta_target_profile = consolidated_beta_target_profile
    sh.downturn_regime_profile = consolidated_downturn_profile


def portfolio_rows(results: dict[tuple[str, str], dict[str, Any]]) -> pd.DataFrame:
    frames = []
    for (scenario, mes), result in results.items():
        portfolio = result.get("portfolio", pd.DataFrame())
        if portfolio.empty:
            frames.append(pd.DataFrame([{"cenario": scenario, "mes": mes, "ticker": ""}]))
            continue
        frame = portfolio.copy()
        frame.insert(0, "mes", mes)
        frame.insert(0, "cenario", scenario)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def funnel_counts(results: dict[tuple[str, str], dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for (scenario, mes), result in results.items():
        cand = result.get("candidates", pd.DataFrame())
        if cand.empty or "subtipo_entrada_teste4" not in cand.columns:
            continue
        counts = cand["subtipo_entrada_teste4"].fillna("").replace("", "sem_classificacao").value_counts()
        for subtype, count in counts.items():
            rows.append({"cenario": scenario, "mes": mes, "subtipo_entrada_teste4": subtype, "quantidade": int(count)})
    return pd.DataFrame(rows)


def summary_rows(results: dict[tuple[str, str], dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (scenario, mes), result in results.items():
        path = sh.workbook_path(mes)
        row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=True)
        row["cenario"] = scenario
        row["descricao"] = SCENARIOS[scenario]
        rows.append(row)
    return pd.DataFrame(rows)


def alpha_summary(details: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario, group in details.groupby("cenario"):
        for label, months in {
            "ACUMULADO_6_MESES": list(cons.MONTHS_6.keys()),
            "ALTAS_JAN_FEV": ["2026-01", "2026-02"],
            "QUEDAS_MAR_MAI": ["2026-03", "2026-04", "2026-05"],
            "JUN_OPORTUNIDADE": ["2026-06"],
        }.items():
            subset = group[group["mes"].isin(months)]
            ret = compound(subset["retorno_expost_sombra"])
            ibov = compound(subset["retorno_expost_ibov"])
            rows.append({
                "cenario": scenario,
                "descricao": SCENARIOS[scenario],
                "grupo": label,
                "retorno_sombra": ret,
                "retorno_ibov": ibov,
                "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
            })
    out = pd.DataFrame(rows)
    base = out[out["cenario"].eq("BASELINE")][["grupo", "alfa"]].rename(columns={"alfa": "alfa_baseline"})
    out = out.merge(base, on="grupo", how="left")
    out["delta_alfa_vs_baseline"] = out["alfa"] - out["alfa_baseline"]
    return out


def validation_rows(results: dict[tuple[str, str], dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    frames = []
    by_scenario: dict[str, dict[str, Any]] = {}
    for (scenario, mes), result in results.items():
        by_scenario.setdefault(scenario, {})[mes] = result
    for scenario, scenario_results in by_scenario.items():
        frame = pd.DataFrame(sh.free_size_validation_rows(scenario_results, expost))
        if not frame.empty:
            frame.insert(0, "cenario", scenario)
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


ORIGINAL_APPLY_SHADOW_FIXES = sh.apply_shadow_fixes
CURRENT_PATH = Path()


def main() -> None:
    global CURRENT_SCENARIO, CURRENT_PATH
    sh.MONTHS = cons.MONTHS_6
    logs: list[str] = []

    def log(message: str) -> None:
        print(message)
        logs.append(message)

    base_settings = load_settings()
    expost = cons.load_expost_6(cons.MONTHS_6)

    log("TESTE-ANCORA: flags sombra desligadas")
    anchor_rows = []
    all_pass = True
    for mes in cons.MONTHS_6:
        path = sh.workbook_path(mes)
        result = sh.run_optimizer_for_month(
            mes, path, base_settings, shadow_fixes=False,
            enable_partial_portfolio=False, enable_beta_target=False, enable_objetivo_retorno=False,
        )
        passed, detail = sh.anchor_passed_for_month(mes, path, result["portfolio"], result["metrics"])
        row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=False)
        row["anchor_passou"] = passed
        row["anchor_detalhe"] = detail
        anchor_rows.append(row)
        all_pass = all_pass and passed
        log(f"  {mes}: {'PASSOU' if passed else 'NAO PASSOU'} - {detail}")

    if not all_pass:
        with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
            pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
        log("ANCORA FALHOU. Teste interrompido.")
        return

    install_consolidated_hooks()
    results: dict[tuple[str, str], dict[str, Any]] = {}
    log("Rodando Teste 4: subtipos de candidata limpa.")
    for scenario in SCENARIOS:
        CURRENT_SCENARIO = scenario
        log(f"CENARIO {scenario}: {SCENARIOS[scenario]}")
        for mes in cons.MONTHS_6:
            CURRENT_PATH = sh.workbook_path(mes)
            result = sh.run_free_size_for_month(
                mes,
                CURRENT_PATH,
                copy.deepcopy(base_settings),
                lambda_beta=cons.LAMBDA_BETA_CONSOLIDADO,
                downturn_signal="SINAL_A_DEFENSIVO",
            )
            results[(scenario, mes)] = result
            row = sh.build_summary_row(mes, CURRENT_PATH, result, expost, shadow_fixes=True)
            cand = result.get("candidates", pd.DataFrame())
            subtype_counts = ""
            if not cand.empty and "subtipo_entrada_teste4" in cand.columns:
                subtype_counts = "; ".join(f"{k}={v}" for k, v in cand["subtipo_entrada_teste4"].fillna("").replace("", "sem_classificacao").value_counts().items())
            log(
                f"  {mes}: ret={pct(row['retorno_expost_sombra'])} IBOV={pct(row['retorno_expost_ibov'])} "
                f"alfa={pct(row['alfa_sombra'])} beta={row['beta_carteira_sombra']:.2f} "
                f"pesos={row['tickers_pesos_sombra']} | subtipos={subtype_counts}"
            )

    details = summary_rows(results, expost)
    summary = alpha_summary(details)
    validation = validation_rows(results, expost)
    max_diff = validation["diferenca_retorno"].abs().max() if not validation.empty and "diferenca_retorno" in validation else np.nan

    log("RESUMO ACUMULADO:")
    for _, row in summary[summary["grupo"].eq("ACUMULADO_6_MESES")].iterrows():
        log(
            f"  {row['cenario']}: retorno={pct(row['retorno_sombra'])} IBOV={pct(row['retorno_ibov'])} "
            f"alfa={pct(row['alfa'])} delta_vs_baseline={pct(row['delta_alfa_vs_baseline'])}"
        )
    log(f"VALIDACAO RETORNO: maior diferenca peso x ativo = {max_diff:.10f}")

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        summary.to_excel(writer, sheet_name="resumo_por_cenario", index=False)
        details.to_excel(writer, sheet_name="detalhe_mes_cenario", index=False)
        portfolio_rows(results).to_excel(writer, sheet_name="carteiras_por_cenario", index=False)
        funnel_counts(results).to_excel(writer, sheet_name="contagem_subtipos", index=False)
        validation.to_excel(writer, sheet_name="validacao_retorno", index=False)

    log(f"Arquivo gerado: {OUTPUT_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
