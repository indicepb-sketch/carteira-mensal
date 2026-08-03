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
OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_exposicao_por_regime.xlsx"
LOG_FILE = ROOT / "output" / "excel" / "shadow_exposicao_por_regime.log"

SCENARIOS = {
    "BASELINE_100": {
        "descricao": "100% investido em todos os regimes",
        "alta": 1.00,
        "queda_leve": 1.00,
        "queda_forte": 1.00,
        "oportunidade": 1.00,
    },
    "EXPOSICAO_SIMPLES": {
        "descricao": "alta 100%; queda leve 70%; queda forte 50%; oportunidade 100%",
        "alta": 1.00,
        "queda_leve": 0.70,
        "queda_forte": 0.50,
        "oportunidade": 1.00,
    },
    "EXPOSICAO_DEFENSIVA": {
        "descricao": "alta 100%; queda leve 60%; queda forte 30%; oportunidade 100%",
        "alta": 1.00,
        "queda_leve": 0.60,
        "queda_forte": 0.30,
        "oportunidade": 1.00,
    },
    "EXPOSICAO_MODERADA": {
        "descricao": "alta 100%; queda leve 80%; queda forte 60%; oportunidade 100%",
        "alta": 1.00,
        "queda_leve": 0.80,
        "queda_forte": 0.60,
        "oportunidade": 1.00,
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
    if mes in {"2026-01", "2026-02"}:
        return "alta"
    if mes in {"2026-03", "2026-04"}:
        return "queda_leve"
    if mes == "2026-05":
        return "queda_forte"
    if mes == "2026-06":
        return "oportunidade"
    return "indefinido"


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


def run_consolidated_portfolios(base_settings: dict) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for mes in cons.MONTHS_6:
        path = sh.workbook_path(mes)
        results[mes] = sh.run_free_size_for_month(
            mes,
            path,
            copy.deepcopy(base_settings),
            lambda_beta=cons.LAMBDA_BETA_CONSOLIDADO,
            downturn_signal="SINAL_A_DEFENSIVO",
        )
    return results


def scaled_portfolio(portfolio: pd.DataFrame, exposure: float) -> pd.DataFrame:
    if portfolio.empty:
        return portfolio
    out = portfolio.copy()
    out["peso_recomendado_original_100pct"] = pd.to_numeric(out["peso_recomendado"], errors="coerce")
    out["peso_final_original_100pct"] = pd.to_numeric(out.get("peso_final", out["peso_recomendado"]), errors="coerce")
    out["peso_recomendado"] = out["peso_recomendado_original_100pct"] * float(exposure)
    out["peso_final"] = out["peso_recomendado"]
    out["exposicao_investida"] = float(exposure)
    out["peso_caixa"] = 1.0 - float(exposure)
    return out


def detail_rows(results: dict[str, dict[str, Any]], expost: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for scenario, cfg in SCENARIOS.items():
        for mes, result in results.items():
            bucket = regime_bucket(mes)
            exposure = float(cfg.get(bucket, 1.0))
            original_portfolio = result.get("portfolio", pd.DataFrame())
            portfolio = scaled_portfolio(original_portfolio, exposure)
            ret_100 = sh.portfolio_expost_return(original_portfolio, expost, mes)
            ret_scaled = sh.portfolio_expost_return(portfolio, expost, mes)
            ibov = sh.ibov_return(expost, mes)
            rows.append(
                {
                    "cenario": scenario,
                    "descricao": cfg["descricao"],
                    "mes": mes,
                    "bucket_regime": bucket,
                    "exposicao_investida": exposure,
                    "peso_caixa": 1.0 - exposure,
                    "retorno_carteira_100pct": ret_100,
                    "retorno_carteira_com_exposicao": ret_scaled,
                    "retorno_ibov": ibov,
                    "alfa": ret_scaled - ibov if pd.notna(ret_scaled) and pd.notna(ibov) else np.nan,
                    "tickers_pesos_100pct": sh.format_weights(sh.weights_map(original_portfolio)),
                    "tickers_pesos_exposicao": sh.format_weights(sh.weights_map(portfolio)),
                }
            )
    return rows


def summary_rows(details: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario, group in details.groupby("cenario"):
        for label, months in {
            "ACUMULADO_6_MESES": list(cons.MONTHS_6.keys()),
            "ALTAS_JAN_FEV": ["2026-01", "2026-02"],
            "QUEDAS_MAR_MAI": ["2026-03", "2026-04", "2026-05"],
            "QUEDA_FORTE_MAI": ["2026-05"],
            "JUN_OPORTUNIDADE": ["2026-06"],
        }.items():
            subset = group[group["mes"].isin(months)]
            ret = compound(subset["retorno_carteira_com_exposicao"])
            ibov = compound(subset["retorno_ibov"])
            rows.append(
                {
                    "cenario": scenario,
                    "descricao": SCENARIOS[scenario]["descricao"],
                    "grupo": label,
                    "retorno_carteira": ret,
                    "retorno_ibov": ibov,
                    "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
                }
            )
    out = pd.DataFrame(rows)
    base = out[out["cenario"].eq("BASELINE_100")][["grupo", "alfa"]].rename(columns={"alfa": "alfa_baseline"})
    out = out.merge(base, on="grupo", how="left")
    out["delta_alfa_vs_baseline"] = out["alfa"] - out["alfa_baseline"]
    return out


def portfolio_rows(results: dict[str, dict[str, Any]]) -> pd.DataFrame:
    frames = []
    for scenario, cfg in SCENARIOS.items():
        for mes, result in results.items():
            exposure = float(cfg.get(regime_bucket(mes), 1.0))
            portfolio = scaled_portfolio(result.get("portfolio", pd.DataFrame()), exposure)
            if portfolio.empty:
                frames.append(pd.DataFrame([{"cenario": scenario, "mes": mes, "ticker": ""}]))
                continue
            frame = portfolio.copy()
            frame.insert(0, "mes", mes)
            frame.insert(0, "cenario", scenario)
            frames.append(frame)
            frames.append(
                pd.DataFrame(
                    [
                        {
                            "cenario": scenario,
                            "mes": mes,
                            "ticker": "CAIXA",
                            "peso_recomendado": 1.0 - exposure,
                            "peso_final": 1.0 - exposure,
                            "exposicao_investida": exposure,
                            "peso_caixa": 1.0 - exposure,
                        }
                    ]
                )
            )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def validation_rows(details: pd.DataFrame, portfolios: pd.DataFrame, expost: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in details.iterrows():
        scenario = row["cenario"]
        mes = row["mes"]
        p = portfolios[(portfolios["cenario"].eq(scenario)) & (portfolios["mes"].eq(mes)) & (~portfolios["ticker"].eq("CAIXA"))].copy()
        manual = sh.portfolio_expost_return(p, expost, mes)
        rows.append(
            {
                "cenario": scenario,
                "mes": mes,
                "retorno_reportado": row["retorno_carteira_com_exposicao"],
                "retorno_manual_peso_x_ativo": manual,
                "diferenca_retorno": manual - row["retorno_carteira_com_exposicao"] if pd.notna(manual) else np.nan,
                "soma_pesos_ativos": pd.to_numeric(p.get("peso_recomendado", pd.Series(dtype=float)), errors="coerce").sum(),
                "peso_caixa": row["peso_caixa"],
                "soma_total": pd.to_numeric(p.get("peso_recomendado", pd.Series(dtype=float)), errors="coerce").sum() + row["peso_caixa"],
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
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
        all_pass = all_pass and passed
        log(f"  {mes}: {'PASSOU' if passed else 'NAO PASSOU'} - {detail}")

    if not all_pass:
        with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
            pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
        log("ANCORA FALHOU. Teste interrompido.")
        return

    install_consolidated_hooks()
    log("Rodando carteira consolidada base 100% e simulando exposicao/caixa por regime.")
    results = run_consolidated_portfolios(base_settings)
    details = pd.DataFrame(detail_rows(results, expost))
    summary = summary_rows(details)
    portfolios = portfolio_rows(results)
    validation = validation_rows(details, portfolios, expost)

    log("RESUMO ACUMULADO:")
    for _, row in summary[summary["grupo"].eq("ACUMULADO_6_MESES")].iterrows():
        log(
            f"  {row['cenario']}: retorno={pct(row['retorno_carteira'])} IBOV={pct(row['retorno_ibov'])} "
            f"alfa={pct(row['alfa'])} delta_vs_baseline={pct(row['delta_alfa_vs_baseline'])}"
        )
    log("DETALHE MENSAL:")
    for _, row in details.iterrows():
        log(
            f"  {row['cenario']} {row['mes']}: regime={row['bucket_regime']} exposicao={pct(row['exposicao_investida'])} "
            f"ret={pct(row['retorno_carteira_com_exposicao'])} IBOV={pct(row['retorno_ibov'])} alfa={pct(row['alfa'])}"
        )
    max_diff = validation["diferenca_retorno"].abs().max() if not validation.empty else np.nan
    log(f"VALIDACAO RETORNO: maior diferenca peso x ativo = {max_diff:.10f}")

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        summary.to_excel(writer, sheet_name="resumo_por_cenario", index=False)
        details.to_excel(writer, sheet_name="detalhe_mes_cenario", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras_com_caixa", index=False)
        validation.to_excel(writer, sheet_name="validacao_retorno", index=False)

    log(f"Arquivo gerado: {OUTPUT_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
