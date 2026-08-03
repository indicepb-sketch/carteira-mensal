from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import shadow_consolidada_6meses as cons  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
from utils import load_settings  # noqa: E402
from main import _market_breadth_rows, _market_classification, classify_favorable_market_subtype  # noqa: E402

MONTHS_2025 = {f"2025-{m:02d}": f"carteira_historica_2025_{m:02d}.xlsx" for m in range(1, 13)}
OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_backtest_2025.xlsx"
LOG_FILE = ROOT / "output" / "excel" / "shadow_backtest_2025.log"
LAMBDA_BETA = 1.5
EXPOSURE_DEFENSIVE = {"alta": 1.00, "oportunidade": 1.00, "queda_leve": 0.60, "queda_forte": 0.30, "indefinido": 1.00}


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def workbook_path(mes: str) -> Path:
    path = ROOT / "output" / "excel" / MONTHS_2025[mes]
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def infer_regime_bucket(path: Path) -> tuple[str, str, dict[str, Any]]:
    prelim = cons._read_sheet(path, "Analise Preliminar")
    info: dict[str, Any] = {}
    if prelim.empty:
        return "alta", "fallback: Analise Preliminar ausente", info

    rows, total, favorable, pct = _market_breadth_rows(prelim)
    market_class, pct_calc = _market_classification(favorable, total)
    info.update({
        "metodologia": "src.main._market_breadth_rows + _market_classification",
        "total_ativos": total,
        "ativos_tendencia_favoravel": favorable,
        "pct_tendencia_favoravel": pct_calc,
        "mercado_classificacao_producao": market_class,
    })
    for row in rows:
        indicador = str(row.get("indicador", "")).strip()
        if indicador:
            info[indicador] = row.get("percentual", np.nan)

    if market_class == "mercado favoravel":
        subtype = classify_favorable_market_subtype(prelim, pd.DataFrame(), market_class, load_settings())
        info.update(subtype)
        sub = str(subtype.get("subtipo_mercado_favoravel", ""))
        reason = str(subtype.get("motivo_subtipo_mercado_favoravel", ""))
        if "oportunidade" in sub:
            return "oportunidade", f"{market_class}; {sub}; {reason}", info
        return "alta", f"{market_class}; {sub}; {reason}", info
    if market_class == "mercado seletivo":
        return "queda_leve", f"{market_class}; tendencia favoravel {pct_calc:.1%}", info
    return "queda_forte", f"{market_class}; tendencia favoravel {pct_calc:.1%}", info

def beta_profile_for_bucket(bucket: str) -> dict[str, Any]:
    if bucket == "oportunidade":
        return {"beta_target_subtipo": "favoravel_oportunidade", "beta_target": 1.15, "beta_target_min": 1.05, "beta_target_max": 1.30, "beta_target_reason": "backtest 2025: oportunidade/favoravel"}
    if bucket == "alta":
        return {"beta_target_subtipo": "favoravel_amplo", "beta_target": 1.10, "beta_target_min": 1.00, "beta_target_max": 1.20, "beta_target_reason": "backtest 2025: alta/favoravel"}
    if bucket == "queda_leve":
        return {"beta_target_subtipo": "favoravel_estreitando", "beta_target": 0.95, "beta_target_min": 0.85, "beta_target_max": 1.05, "beta_target_reason": "backtest 2025: queda leve/seletivo"}
    return {"beta_target_subtipo": "cansado", "beta_target": 0.75, "beta_target_min": 0.65, "beta_target_max": 0.90, "beta_target_reason": "backtest 2025: queda forte/fraco"}


def make_profiles(regimes: dict[str, tuple[str, str]]):
    def beta_target_profile(path: Path, settings: dict) -> dict:
        base = dict(_ORIGINAL_BETA_TARGET_PROFILE(path, settings))
        match = re.search(r"(20\d{2})_(\d{2})", path.name)
        mes = f"{match.group(1)}-{match.group(2)}" if match else ""
        bucket = regimes.get(mes, ("alta", ""))[0]
        base.update(beta_profile_for_bucket(bucket))
        return base

    def downturn_profile(path: Path, settings: dict) -> dict:
        base = dict(_ORIGINAL_DOWNTURN_PROFILE(path, settings))
        match = re.search(r"(20\d{2})_(\d{2})", path.name)
        mes = f"{match.group(1)}-{match.group(2)}" if match else ""
        bucket, reason = regimes.get(mes, ("alta", ""))
        subtype = "alta" if bucket in {"alta", "oportunidade"} else ("queda_forte" if bucket == "queda_forte" else "queda_leve_lateral")
        base["subtipo_queda"] = subtype
        base["motivo_subtipo_queda"] = reason
        return base

    return beta_target_profile, downturn_profile


def load_sector_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name in ["carteira_recomendada_2026_06_v4.xlsx", "carteira_recomendada_2026_01_v1.xlsx", "carteira_recomendada_2026_02_v4.xlsx"]:
        path = ROOT / "output" / "excel" / name
        if not path.exists():
            continue
        try:
            frame = pd.read_excel(path, sheet_name="Analise Preliminar")
        except Exception:
            continue
        if "ticker" not in frame or "setor" not in frame:
            continue
        for _, row in frame[["ticker", "setor"]].dropna().iterrows():
            ticker = str(row["ticker"]).strip().upper()
            sector = str(row["setor"]).strip()
            if ticker and sector and sector.lower() not in {"nan", "nao mapeado", "não mapeado"}:
                mapping.setdefault(ticker, sector)
    return mapping


def patch_sector_enrichment(sector_map: dict[str, str]):
    original_loader = sh.load_candidate_input

    def wrapped(path: Path, settings: dict) -> pd.DataFrame:
        frame = original_loader(path, settings)
        if frame.empty or "ticker" not in frame.columns:
            return frame
        current = frame.get("setor", pd.Series("", index=frame.index)).astype(str).str.strip().str.lower()
        missing = current.isin(["", "nan", "nao mapeado", "não mapeado"])
        mapped = frame["ticker"].astype(str).str.upper().map(sector_map)
        frame.loc[missing & mapped.notna(), "setor"] = mapped[missing & mapped.notna()]
        return frame

    sh.load_candidate_input = wrapped
    return original_loader


def portfolio_rows(results: dict[str, Any]) -> pd.DataFrame:
    frames = []
    for mes, result in results.items():
        port = result.get("portfolio", pd.DataFrame())
        if port.empty:
            frames.append(pd.DataFrame([{"mes": mes, "ticker": "", "peso_recomendado": 0.0}]))
            continue
        df = port.copy()
        df.insert(0, "mes", mes)
        frames.append(df)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def apply_exposure(row: dict[str, Any], bucket: str) -> dict[str, Any]:
    exposure = EXPOSURE_DEFENSIVE.get(bucket, 1.0)
    out = dict(row)
    ret = row.get("retorno_expost_sombra", np.nan)
    ibov = row.get("retorno_expost_ibov", np.nan)
    out["bucket_regime"] = bucket
    out["exposicao_defensiva"] = exposure
    out["peso_caixa"] = 1.0 - exposure
    out["retorno_expost_sombra_100pct"] = ret
    out["retorno_expost_sombra_defensivo"] = ret * exposure if pd.notna(ret) else np.nan
    out["alfa_sombra_defensivo"] = out["retorno_expost_sombra_defensivo"] - ibov if pd.notna(out["retorno_expost_sombra_defensivo"]) and pd.notna(ibov) else np.nan
    return out


def main() -> None:
    logs: list[str] = []

    def log(message: str) -> None:
        print(message, flush=True)
        logs.append(message)

    missing = [str(workbook_path(mes)) for mes in MONTHS_2025 if not workbook_path(mes).exists()]
    if missing:
        raise FileNotFoundError("Arquivos historicos ausentes: " + "; ".join(missing))

    sh.MONTHS = MONTHS_2025
    base_settings = load_settings()
    expost = pd.concat([cons._month_expost_from_workbook(mes, workbook_path(mes)) for mes in MONTHS_2025], ignore_index=True, sort=False)
    regimes = {mes: infer_regime_bucket(workbook_path(mes))[:2] for mes in MONTHS_2025}
    log("Regimes inferidos para 2025:")
    for mes, (bucket, reason) in regimes.items():
        log(f"  {mes}: {bucket} | {reason} | IBOV={pct(sh.ibov_return(expost, mes))}")

    global _ORIGINAL_BETA_TARGET_PROFILE, _ORIGINAL_DOWNTURN_PROFILE
    _ORIGINAL_BETA_TARGET_PROFILE = sh.beta_target_profile
    _ORIGINAL_DOWNTURN_PROFILE = sh.downturn_regime_profile
    original_build = sh.build_free_size_portfolio
    original_d3 = sh.technical_veto_to_penalty_in_opportunity
    original_loader = patch_sector_enrichment(load_sector_map())

    sh.build_free_size_portfolio = cons.consolidated_build_free_size_portfolio
    sh.technical_veto_to_penalty_in_opportunity = cons.make_extended_d3(original_d3)
    sh.beta_target_profile, sh.downturn_regime_profile = make_profiles(regimes)

    shadow_rows = []
    shadow_results = {}
    for mes in MONTHS_2025:
        bucket = regimes[mes][0]
        result = sh.run_free_size_for_month(mes, workbook_path(mes), base_settings, lambda_beta=LAMBDA_BETA, downturn_signal="SINAL_A_DEFENSIVO")
        row = sh.build_summary_row(mes, workbook_path(mes), result, expost, shadow_fixes=True)
        row["cenario"] = "CONSOLIDADA_100"
        row = apply_exposure(row, bucket)
        shadow_rows.append(row)
        shadow_results[mes] = result
        log(f"{mes}: bucket={bucket} | sinal={row.get('sinal_quedas_aplicado')} | acoes={len(result.get('portfolio', pd.DataFrame()))} | beta={row.get('beta_carteira_sombra', np.nan):.2f} | ret100={pct(row.get('retorno_expost_sombra_100pct'))} | ret_def={pct(row.get('retorno_expost_sombra_defensivo'))} | IBOV={pct(row.get('retorno_expost_ibov'))} | alfa_def={pct(row.get('alfa_sombra_defensivo'))}")

    validation = pd.DataFrame(sh.free_size_validation_rows(shadow_results, expost))
    details = pd.DataFrame(shadow_rows)
    summary = pd.DataFrame([
        {"cenario": "CONSOLIDADA_100", "retorno_carteira": compound(details["retorno_expost_sombra_100pct"]), "retorno_ibov": compound(details["retorno_expost_ibov"]), "alfa": compound(details["retorno_expost_sombra_100pct"]) - compound(details["retorno_expost_ibov"])},
        {"cenario": "CONSOLIDADA_EXPOSICAO_DEFENSIVA", "retorno_carteira": compound(details["retorno_expost_sombra_defensivo"]), "retorno_ibov": compound(details["retorno_expost_ibov"]), "alfa": compound(details["retorno_expost_sombra_defensivo"]) - compound(details["retorno_expost_ibov"])},
    ])
    group_rows = []
    for bucket, group in details.groupby("bucket_regime"):
        group_rows.append({"bucket_regime": bucket, "meses": ", ".join(group["mes"].astype(str)), "retorno_100pct": compound(group["retorno_expost_sombra_100pct"]), "retorno_defensivo": compound(group["retorno_expost_sombra_defensivo"]), "retorno_ibov": compound(group["retorno_expost_ibov"]), "alfa_100pct": compound(group["retorno_expost_sombra_100pct"]) - compound(group["retorno_expost_ibov"]), "alfa_defensivo": compound(group["retorno_expost_sombra_defensivo"]) - compound(group["retorno_expost_ibov"])})

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        details.to_excel(writer, sheet_name="mes_a_mes", index=False)
        pd.DataFrame(group_rows).to_excel(writer, sheet_name="por_regime", index=False)
        portfolio_rows(shadow_results).to_excel(writer, sheet_name="carteiras_por_mes", index=False)
        validation.to_excel(writer, sheet_name="validacao_retorno", index=False)
        expost.to_excel(writer, sheet_name="expost_universo", index=False)

    sh.build_free_size_portfolio = original_build
    sh.technical_veto_to_penalty_in_opportunity = original_d3
    sh.beta_target_profile = _ORIGINAL_BETA_TARGET_PROFILE
    sh.downturn_regime_profile = _ORIGINAL_DOWNTURN_PROFILE
    sh.load_candidate_input = original_loader

    log("Resumo:")
    for _, row in summary.iterrows():
        log(f"  {row['cenario']}: retorno={pct(row['retorno_carteira'])} | IBOV={pct(row['retorno_ibov'])} | alfa={pct(row['alfa'])}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()

