from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import shadow_consolidada_6meses as cons
import shadow_simulacao as sh
from utils import load_settings


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_universo_custom_jan_jun.xlsx"
LOG_FILE = ROOT / "output" / "excel" / "shadow_universo_custom_jan_jun.log"

CUSTOM_TICKERS_RAW = [
    "ABEV3",
    "AGRO3",
    "ARZZ3",
    "B3SA3",
    "BBAS3",
    "BBSE3",
    "BPAC11",
    "BPAN4",
    "BRFS3",
    "BRKM5",
    "CCRO3",
    "CPLE6",
    "CSAN3",
    "CSNA3",
    "CYRE3",
    "EGIE3",
    "EMBR3",
    "ENEV3",
    "EVEN3",
    "EZTC3",
    "FLRY3",
    "GGBR4",
    "GOAU4",
    "GRND3",
    "HAPV3",
    "HYPE3",
    "ITSA4",
    "JBSS3",
    "KLBN11",
    "LREN3",
    "MGLU3",
    "MOVI3",
    "MRFG3",
    "MRVE3",
    "MULT3",
    "NTCO3",
    "PETR4",
    "PRIO3",
    "RADL3",
    "RAIL3",
    "RENT3",
    "SANB11",
    "SBSP3",
    "SEER3",
    "SLCE3",
    "STBP3",
    "SUZB3",
    "TOTS3",
    "UNIP6",
    "USIM5",
    "VALE3",
    "WEGE3",
    "YDUQ3",
]


def normalize_ticker(ticker: str) -> str:
    text = str(ticker).strip().upper()
    if not text:
        return text
    return text if text.endswith(".SA") else f"{text}.SA"


CUSTOM_TICKERS = {normalize_ticker(t) for t in CUSTOM_TICKERS_RAW}


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def filter_to_custom_universe(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "ticker" not in frame.columns:
        return frame
    out = frame.copy()
    norm = out["ticker"].map(normalize_ticker)
    out = out[norm.isin(CUSTOM_TICKERS)].copy()
    out["ticker"] = out["ticker"].map(normalize_ticker)
    return out


def custom_load_candidate_input(original_loader):
    def wrapped(path: Path, settings: dict | None = None) -> pd.DataFrame:
        frame = original_loader(path, settings)
        return filter_to_custom_universe(frame)

    return wrapped


def universe_coverage(path: Path, mes: str) -> pd.DataFrame:
    rows = []
    sheets = {
        "Analise Preliminar": sh.read_sheet(path, "Analise Preliminar"),
        "Candidatas Risco": sh.read_sheet(path, "Candidatas Risco"),
        "Otimizacao": sh.read_sheet(path, "Otimizacao"),
    }
    for sheet_name, frame in sheets.items():
        if frame.empty or "ticker" not in frame.columns:
            available = set()
        else:
            available = {normalize_ticker(t) for t in frame["ticker"].dropna().astype(str)}
        for ticker in sorted(CUSTOM_TICKERS):
            rows.append(
                {
                    "mes": mes,
                    "aba": sheet_name,
                    "ticker": ticker,
                    "presente": ticker in available,
                }
            )
    return pd.DataFrame(rows)


def portfolio_rows(results: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for mes, result in results.items():
        portfolio = result.get("portfolio", pd.DataFrame())
        if portfolio.empty:
            rows.append({"mes": mes, "status": result.get("metrics", {}).get("status_carteira", ""), "ticker": "", "peso_recomendado": np.nan})
            continue
        frame = portfolio.copy()
        frame.insert(0, "mes", mes)
        rows.append(frame)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def validation_rows(results: dict[str, Any], expost: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(sh.free_size_validation_rows(results, expost))


def summary_rows(results: dict[str, Any], expost: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for mes, result in results.items():
        path = sh.workbook_path(mes)
        row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=True)
        row["cenario"] = "UNIVERSO_CUSTOM_CONSOLIDADA"
        row["universo_custom_total"] = len(CUSTOM_TICKERS)
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df.loc[len(df)] = {
            "mes": "ACUMULADO_JAN_JUN",
            "cenario": "UNIVERSO_CUSTOM_CONSOLIDADA",
            "retorno_expost_sombra": compound(df["retorno_expost_sombra"]),
            "retorno_expost_ibov": compound(df["retorno_expost_ibov"]),
            "alfa_sombra": compound(df["retorno_expost_sombra"]) - compound(df["retorno_expost_ibov"]),
            "universo_custom_total": len(CUSTOM_TICKERS),
        }
    return df


def main() -> None:
    sh.MONTHS = cons.MONTHS_6
    logs: list[str] = []

    def log(message: str) -> None:
        print(message)
        logs.append(message)

    base_settings = load_settings()
    expost = cons.load_expost_6(cons.MONTHS_6)

    log(f"Universo customizado informado: {len(CUSTOM_TICKERS)} tickers.")
    log("Meses: " + ", ".join(cons.MONTHS_6.keys()))

    anchor_rows = []
    anchor_results: dict[str, Any] = {}
    all_pass = True
    log("TESTE-ANCORA original, sem filtro de universo:")
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
        log(f"  {mes}: {'PASSOU' if passed else 'NAO PASSOU'} | {detail}")

    coverage = pd.concat([universe_coverage(sh.workbook_path(mes), mes) for mes in cons.MONTHS_6], ignore_index=True)

    if not all_pass:
        with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
            pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
            coverage.to_excel(writer, sheet_name="cobertura_universo", index=False)
        LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
        log(f"ANCORA FALHOU. Arquivo diagnostico: {OUTPUT_FILE}")
        return

    original_downturn_profile = sh.downturn_regime_profile
    original_beta_target_profile = sh.beta_target_profile
    original_d3 = sh.technical_veto_to_penalty_in_opportunity
    original_loader = sh.load_candidate_input

    sh.build_free_size_portfolio = cons.consolidated_build_free_size_portfolio
    sh.technical_veto_to_penalty_in_opportunity = cons.make_extended_d3(original_d3)

    def consolidated_beta_target_profile(path: Path, settings: dict) -> dict:
        base = dict(original_beta_target_profile(path, settings))
        import re

        match = re.search(r"2026_(\d{2})", path.name)
        mes_key = f"2026-{match.group(1)}" if match else ""
        base.update(cons.CONSOLIDATED_BETA_PROFILE.get(mes_key, {}))
        return base

    def consolidated_downturn_profile(path: Path, settings: dict) -> dict:
        base = dict(original_downturn_profile(path, settings))
        import re

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
    sh.load_candidate_input = custom_load_candidate_input(original_loader)

    shadow_results: dict[str, Any] = {}
    log("Rodando universo customizado com config consolidada.")
    for mes in cons.MONTHS_6:
        path = sh.workbook_path(mes)
        result = sh.run_free_size_for_month(
            mes,
            path,
            copy.deepcopy(base_settings),
            lambda_beta=cons.LAMBDA_BETA_CONSOLIDADO,
            downturn_signal="SINAL_A_DEFENSIVO",
        )
        shadow_results[mes] = result
        row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=True)
        log(
            f"  {mes}: status={row['status_sombra']} | sinal={row['sinal_quedas_aplicado']} | "
            f"beta_alvo={row['beta_target']:.2f} | beta={row['beta_carteira_sombra']:.2f} | "
            f"retorno={pct(row['retorno_expost_sombra'])} | IBOV={pct(row['retorno_expost_ibov'])} | "
            f"alfa={pct(row['alfa_sombra'])} | pesos={row['tickers_pesos_sombra']}"
        )

    summary = summary_rows(shadow_results, expost)
    validation = validation_rows(shadow_results, expost)
    portfolios = portfolio_rows(shadow_results)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        summary.to_excel(writer, sheet_name="resumo_custom", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras_custom", index=False)
        validation.to_excel(writer, sheet_name="validacao_retorno", index=False)
        coverage.to_excel(writer, sheet_name="cobertura_universo", index=False)
        pd.DataFrame({"ticker_custom": sorted(CUSTOM_TICKERS)}).to_excel(writer, sheet_name="universo_custom", index=False)
        cons.regime_summary(summary[summary["mes"].astype(str).str.startswith("2026-")]).to_excel(writer, sheet_name="resumo_por_regime", index=False)
        sh._result_sheets(writer, "shadow_custom", shadow_results)

    accum = summary[summary["mes"].eq("ACUMULADO_JAN_JUN")].iloc[0]
    log(
        f"ACUMULADO JAN-JUN: retorno={pct(accum['retorno_expost_sombra'])} | "
        f"IBOV={pct(accum['retorno_expost_ibov'])} | alfa={pct(accum['alfa_sombra'])}"
    )
    max_diff = validation["diferenca_retorno"].abs().max() if not validation.empty and "diferenca_retorno" in validation else np.nan
    log(f"VALIDACAO RETORNO: maior diferenca peso x ativo = {max_diff:.10f}")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
