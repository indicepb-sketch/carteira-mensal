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
OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_beta_fraco_condicional.xlsx"
LOG_FILE = ROOT / "output" / "excel" / "shadow_beta_fraco_condicional.log"


SCENARIOS = {
    "BASELINE_1_5": {
        "descricao": "beta-alvo consolidado atual, lambda=1.5 em todos os meses",
        "alta": 1.5,
        "queda": 1.5,
        "jun": 1.5,
    },
    "FRACO_GLOBAL_0_5": {
        "descricao": "beta-alvo mais fraco, lambda=0.5 em todos os meses",
        "alta": 0.5,
        "queda": 0.5,
        "jun": 0.5,
    },
    "SEM_BETA_0_0": {
        "descricao": "beta-alvo desligado na objetivo, lambda=0.0 em todos os meses",
        "alta": 0.0,
        "queda": 0.0,
        "jun": 0.0,
    },
    "COND_ALTA_FRACA_QUEDA_ATUAL": {
        "descricao": "lambda=0.5 em altas/jun; lambda=1.5 em quedas",
        "alta": 0.5,
        "queda": 1.5,
        "jun": 0.5,
    },
    "COND_ALTA_SEM_BETA_QUEDA_ATUAL": {
        "descricao": "lambda=0.0 em altas/jun; lambda=1.5 em quedas",
        "alta": 0.0,
        "queda": 1.5,
        "jun": 0.0,
    },
}


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def regime_bucket(mes: str) -> str:
    if mes in ("2026-01", "2026-02"):
        return "alta"
    if mes == "2026-06":
        return "jun"
    return "queda"


def lambda_for_month(scenario: str, mes: str) -> float:
    bucket = regime_bucket(mes)
    return float(SCENARIOS[scenario][bucket])


def alpha_summary(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    out = []
    for scenario, group in df.groupby("cenario"):
        for label, meses in {
            "ACUMULADO_6_MESES": list(cons.MONTHS_6.keys()),
            "ALTAS_JAN_FEV": ["2026-01", "2026-02"],
            "QUEDAS_MAR_MAI": ["2026-03", "2026-04", "2026-05"],
            "JUN_OPORTUNIDADE": ["2026-06"],
        }.items():
            subset = group[group["mes"].isin(meses)]
            ret = compound(subset["retorno_expost_sombra"])
            ibov = compound(subset["retorno_expost_ibov"])
            out.append(
                {
                    "cenario": scenario,
                    "descricao": SCENARIOS[scenario]["descricao"],
                    "grupo": label,
                    "retorno_sombra": ret,
                    "retorno_ibov": ibov,
                    "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
                }
            )
    summary = pd.DataFrame(out)
    base = summary[summary["cenario"].eq("BASELINE_1_5")][["grupo", "alfa"]].rename(columns={"alfa": "alfa_baseline"})
    summary = summary.merge(base, on="grupo", how="left")
    summary["delta_alfa_vs_baseline"] = summary["alfa"] - summary["alfa_baseline"]
    return summary


def portfolio_rows(results: dict[tuple[str, str], dict[str, Any]]) -> pd.DataFrame:
    frames = []
    for (scenario, mes), result in results.items():
        portfolio = result.get("portfolio", pd.DataFrame())
        if portfolio.empty:
            frames.append(pd.DataFrame([{"cenario": scenario, "mes": mes, "ticker": "", "peso_recomendado": np.nan}]))
            continue
        frame = portfolio.copy()
        frame.insert(0, "mes", mes)
        frame.insert(0, "cenario", scenario)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def validation_rows(results: dict[tuple[str, str], dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    rows = []
    by_scenario: dict[str, dict[str, Any]] = {}
    for (scenario, mes), result in results.items():
        by_scenario.setdefault(scenario, {})[mes] = result
    for scenario, scenario_results in by_scenario.items():
        frame = pd.DataFrame(sh.free_size_validation_rows(scenario_results, expost))
        if not frame.empty:
            frame.insert(0, "cenario", scenario)
            rows.append(frame)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def install_consolidated_hooks() -> None:
    original_downturn_profile = sh.downturn_regime_profile
    original_beta_target_profile = sh.beta_target_profile
    original_d3 = sh.technical_veto_to_penalty_in_opportunity

    sh.build_free_size_portfolio = cons.consolidated_build_free_size_portfolio
    sh.technical_veto_to_penalty_in_opportunity = cons.make_extended_d3(original_d3)

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
            mes_key,
            (base.get("subtipo_queda", ""), base.get("motivo_subtipo_queda", "")),
        )
        base["subtipo_queda"] = subtype
        base["motivo_subtipo_queda"] = reason
        return base

    sh.beta_target_profile = consolidated_beta_target_profile
    sh.downturn_regime_profile = consolidated_downturn_profile


def main() -> None:
    sh.MONTHS = cons.MONTHS_6
    logs: list[str] = []

    def log(message: str) -> None:
        print(message)
        logs.append(message)

    base_settings = load_settings()
    expost = cons.load_expost_6(cons.MONTHS_6)

    log("TESTE-ANCORA: flags sombra desligadas")
    anchor_rows: list[dict[str, Any]] = []
    anchor_results: dict[str, Any] = {}
    all_pass = True
    for mes in cons.MONTHS_6:
        path = sh.workbook_path(mes)
        result = sh.run_optimizer_for_month(
            mes,
            path,
            base_settings,
            shadow_fixes=False,
            enable_partial_portfolio=False,
            enable_beta_target=False,
            enable_objetivo_retorno=False,
        )
        passed, detail = sh.anchor_passed_for_month(mes, path, result["portfolio"], result["metrics"])
        row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=False)
        row["anchor_passou"] = passed
        row["anchor_detalhe"] = detail
        anchor_rows.append(row)
        anchor_results[mes] = result
        all_pass = all_pass and passed
        log(f"  {mes}: {'PASSOU' if passed else 'NAO PASSOU'} - {detail}")

    if not all_pass:
        with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
            pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
        log("ANCORA FALHOU. Teste interrompido.")
        return

    install_consolidated_hooks()

    rows: list[dict[str, Any]] = []
    results: dict[tuple[str, str], dict[str, Any]] = {}
    log("Rodando Teste 3: beta-alvo fraco/condicional.")
    for scenario, meta in SCENARIOS.items():
        log(f"CENARIO {scenario}: {meta['descricao']}")
        for mes in cons.MONTHS_6:
            lam = lambda_for_month(scenario, mes)
            path = sh.workbook_path(mes)
            result = sh.run_free_size_for_month(
                mes,
                path,
                copy.deepcopy(base_settings),
                lambda_beta=lam,
                downturn_signal="SINAL_A_DEFENSIVO",
            )
            results[(scenario, mes)] = result
            row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=True)
            row["cenario"] = scenario
            row["descricao_cenario"] = meta["descricao"]
            row["lambda_beta_teste"] = lam
            row["grupo_regime"] = regime_bucket(mes)
            rows.append(row)
            log(
                f"  {mes}: lambda={lam:.1f} sinal={row['sinal_quedas_aplicado']} "
                f"beta_alvo={row['beta_target']:.2f} beta={row['beta_carteira_sombra']:.2f} "
                f"ret={pct(row['retorno_expost_sombra'])} IBOV={pct(row['retorno_expost_ibov'])} "
                f"alfa={pct(row['alfa_sombra'])} pesos={row['tickers_pesos_sombra']}"
            )

    summary = alpha_summary(rows)
    validation = validation_rows(results, expost)
    max_diff = validation["diferenca_retorno"].abs().max() if not validation.empty and "diferenca_retorno" in validation else np.nan

    log("RESUMO ACUMULADO:")
    for _, row in summary.iterrows():
        if row["grupo"] == "ACUMULADO_6_MESES":
            log(
                f"  {row['cenario']}: retorno={pct(row['retorno_sombra'])} "
                f"IBOV={pct(row['retorno_ibov'])} alfa={pct(row['alfa'])} "
                f"delta_vs_baseline={pct(row['delta_alfa_vs_baseline'])}"
            )
    log(f"VALIDACAO RETORNO: maior diferenca peso x ativo = {max_diff:.10f}")

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        summary.to_excel(writer, sheet_name="resumo_por_cenario", index=False)
        pd.DataFrame(rows).to_excel(writer, sheet_name="detalhe_mes_cenario", index=False)
        portfolio_rows(results).to_excel(writer, sheet_name="carteiras_por_cenario", index=False)
        validation.to_excel(writer, sheet_name="validacao_retorno", index=False)

    log(f"Arquivo gerado: {OUTPUT_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
