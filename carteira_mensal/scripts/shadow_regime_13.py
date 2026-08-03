from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import shadow_backtest_2025 as bt  # noqa: E402
import shadow_consolidada_6meses as cons  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
from utils import load_settings  # noqa: E402

EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
OUTPUT_FILE = EXCEL_DIR / "shadow_regime_13.xlsx"
LOG_FILE = LOG_DIR / "shadow_regime_13.log"
LAMBDA_BETA = 1.5

MONTHS: dict[str, str] = {
    **{f"2024-{m:02d}": f"carteira_historica_2024_{m:02d}.xlsx" for m in range(1, 13)},
    **{f"2025-{m:02d}": f"carteira_historica_2025_{m:02d}.xlsx" for m in range(1, 13)},
    "2026-01": "carteira_recomendada_2026_01_v1.xlsx",
    "2026-02": "carteira_recomendada_2026_02_v4.xlsx",
    "2026-03": "carteira_recomendada_2026_03_v4.xlsx",
    "2026-04": "carteira_recomendada_2026_04_v2.xlsx",
    "2026-05": "carteira_recomendada_2026_05_v3.xlsx",
    "2026-06": "carteira_recomendada_2026_06_v4.xlsx",
}


@dataclass(frozen=True)
class RegimeScenario:
    name: str
    description: str
    classifier: Callable[[dict[str, Any]], tuple[str, str]]


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
    path = EXCEL_DIR / MONTHS[mes]
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def sheet_or_empty(path: Path, sheet: str) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet)
    except Exception:
        return pd.DataFrame()


def regime_value(path: Path, field: str) -> Any:
    frame = sheet_or_empty(path, "Regime Mercado")
    if frame.empty or "campo" not in frame.columns or "valor" not in frame.columns:
        return np.nan
    hit = frame.loc[frame["campo"].astype(str).eq(field), "valor"]
    return hit.iloc[0] if not hit.empty else np.nan


def diag_percent(path: Path, indicador: str) -> float:
    frame = sheet_or_empty(path, "Diagnostico de Mercado")
    if frame.empty or "indicador" not in frame.columns:
        return np.nan
    hit = frame.loc[frame["indicador"].astype(str).str.strip().eq(indicador), "percentual"]
    return float(pd.to_numeric(hit, errors="coerce").dropna().iloc[0]) if not hit.dropna().empty else np.nan


def first_numeric(frame: pd.DataFrame, col: str) -> float:
    if frame.empty or col not in frame.columns:
        return np.nan
    values = pd.to_numeric(frame[col], errors="coerce").dropna()
    return float(values.iloc[0]) if not values.empty else np.nan


def month_audit_inputs(mes: str, expost: pd.DataFrame) -> dict[str, Any]:
    path = workbook_path(mes)
    prelim = cons._read_sheet(path, "Analise Preliminar")
    current_bucket, current_reason, current_info = bt.infer_regime_bucket(path)
    ibov_ret = sh.ibov_return(expost, mes)
    data = {
        "mes": mes,
        "path": path,
        "bucket_atual": current_bucket,
        "motivo_atual": current_reason,
        "ibov_expost": ibov_ret,
        "label_expost": "queda_forte" if pd.notna(ibov_ret) and ibov_ret <= -0.03 else ("queda_leve" if pd.notna(ibov_ret) and ibov_ret < 0 else "alta"),
        "mercado_classificacao_producao": current_info.get("mercado_classificacao_producao", regime_value(path, "mercado_classificacao")),
        "pct_tendencia_favoravel": current_info.get("pct_tendencia_favoravel", np.nan),
        "pct_ativos_positivos_1m": float(pd.to_numeric(pd.Series([regime_value(path, "pct_ativos_positivos_1m")]), errors="coerce").iloc[0]) if pd.notna(regime_value(path, "pct_ativos_positivos_1m")) else diag_percent(path, "ativos com retorno positivo no mes"),
        "pct_ativos_positivos_ano": diag_percent(path, "ativos com retorno positivo no ano"),
        "pct_mm9_maior_mm21": diag_percent(path, "ativos com MM9 > MM21"),
        "pct_preco_acima_mm50": diag_percent(path, "ativos com preco acima da MM50"),
        "pct_rsi_50_70": diag_percent(path, "ativos com RSI entre 50 e 70"),
        "rsi_ibov": float(pd.to_numeric(pd.Series([regime_value(path, "rsi_ibov_data_base")]), errors="coerce").iloc[0]) if pd.notna(regime_value(path, "rsi_ibov_data_base")) else np.nan,
        "bollinger_ibov": str(regime_value(path, "bollinger_ibov_data_base") or "").lower(),
        "subtipo_favoravel_producao": str(regime_value(path, "subtipo_mercado_favoravel") or ""),
        "ibov_ret_1m_formacao": first_numeric(prelim, "retorno_1m_ibov"),
        "ibov_ret_4m_formacao": first_numeric(prelim, "retorno_4m_ibov"),
        "ibov_ret_ytd_formacao": first_numeric(prelim, "retorno_ytd_ibov"),
    }
    return data


def is_overbought(d: dict[str, Any]) -> bool:
    return (pd.notna(d.get("rsi_ibov")) and float(d["rsi_ibov"]) >= 75) or "sobrecompra" in str(d.get("bollinger_ibov", ""))


def current_classifier(d: dict[str, Any]) -> tuple[str, str]:
    return str(d["bucket_atual"]), str(d["motivo_atual"])


def exhaustion_simple(d: dict[str, Any]) -> tuple[str, str]:
    bucket, reason = current_classifier(d)
    pos = d.get("pct_ativos_positivos_1m", np.nan)
    if bucket in {"alta", "oportunidade"} and is_overbought(d) and (pd.isna(pos) or pos < 0.70):
        return "queda_leve", f"exaustao simples: IBOV sobrecomprado e amplitude positiva 1m <70% ({pos:.1%})"
    return bucket, "mantem atual: " + reason


def two_layer(d: dict[str, Any]) -> tuple[str, str]:
    trend = d.get("pct_tendencia_favoravel", np.nan)
    pos = d.get("pct_ativos_positivos_1m", np.nan)
    above50 = d.get("pct_preco_acima_mm50", np.nan)
    if pd.notna(trend) and trend < 0.20:
        return "queda_forte", f"duas camadas: tendencia favoravel <20% ({trend:.1%})"
    if pd.notna(above50) and above50 < 0.35:
        return "queda_forte", f"duas camadas: preco acima MM50 <35% ({above50:.1%})"
    if pd.notna(trend) and trend < 0.40:
        return "queda_leve", f"duas camadas: tendencia favoravel <40% ({trend:.1%})"
    if pd.notna(pos) and pos < 0.40:
        return "queda_leve", f"duas camadas: amplitude positiva 1m <40% ({pos:.1%})"
    if is_overbought(d) and (pd.isna(pos) or pos < 0.70):
        return "queda_leve", f"duas camadas: sobrecompra com amplitude positiva 1m insuficiente ({pos:.1%})"
    if "oportunidade" in str(d.get("subtipo_favoravel_producao", "")):
        return "oportunidade", "duas camadas: oportunidade mantida"
    return "alta", "duas camadas: tendencia e amplitude confirmadas"


def conservative_turn(d: dict[str, Any]) -> tuple[str, str]:
    trend = d.get("pct_tendencia_favoravel", np.nan)
    pos = d.get("pct_ativos_positivos_1m", np.nan)
    rsi_ok = d.get("pct_rsi_50_70", np.nan)
    ibov_1m = d.get("ibov_ret_1m_formacao", np.nan)
    if pd.notna(trend) and trend < 0.25:
        return "queda_forte", f"conservador: tendencia favoravel <25% ({trend:.1%})"
    if pd.notna(pos) and pos < 0.35:
        return "queda_forte", f"conservador: amplitude positiva 1m <35% ({pos:.1%})"
    if is_overbought(d):
        return "queda_leve", "conservador: IBOV em sobrecompra vira queda_leve/cautela"
    if pd.notna(ibov_1m) and ibov_1m < 0 and (pd.isna(pos) or pos < 0.50):
        return "queda_leve", f"conservador: IBOV 1m negativo ({ibov_1m:.1%}) com amplitude fraca ({pos:.1%})"
    if pd.notna(rsi_ok) and rsi_ok < 0.35 and pd.notna(pos) and pos < 0.50:
        return "queda_leve", f"conservador: poucos RSI saudaveis ({rsi_ok:.1%}) e amplitude fraca ({pos:.1%})"
    if "oportunidade" in str(d.get("subtipo_favoravel_producao", "")):
        return "oportunidade", "conservador: oportunidade mantida"
    return "alta", "conservador: sem sinais de exaustao/queda"


SCENARIOS = [
    RegimeScenario("regime_atual", "Classificacao atual inferida das planilhas/producao.", current_classifier),
    RegimeScenario("exaustao_simples", "Alta sobrecomprada com amplitude 1m <70% vira queda_leve.", exhaustion_simple),
    RegimeScenario("duas_camadas", "Camada 1 tendencia/amplitude; camada 2 exaustao por sobrecompra.", two_layer),
    RegimeScenario("conservador_virada", "Versao mais cautelosa: toda sobrecompra vira queda_leve.", conservative_turn),
]


def profile_patch(regimes: dict[str, tuple[str, str]]):
    def beta_target_profile(path: Path, settings: dict) -> dict:
        base = dict(ORIGINAL_BETA_TARGET_PROFILE(path, settings))
        match = re.search(r"(20\d{2})_(\d{2})", path.name)
        mes = f"{match.group(1)}-{match.group(2)}" if match else ""
        bucket = regimes.get(mes, ("alta", ""))[0]
        base.update(bt.beta_profile_for_bucket(bucket))
        return base

    def downturn_profile(path: Path, settings: dict) -> dict:
        base = dict(ORIGINAL_DOWNTURN_PROFILE(path, settings))
        match = re.search(r"(20\d{2})_(\d{2})", path.name)
        mes = f"{match.group(1)}-{match.group(2)}" if match else ""
        bucket, reason = regimes.get(mes, ("alta", ""))
        subtype = "alta" if bucket in {"alta", "oportunidade"} else ("queda_forte" if bucket == "queda_forte" else "queda_leve_lateral")
        base["subtipo_queda"] = subtype
        base["motivo_subtipo_queda"] = reason
        return base
    return beta_target_profile, downturn_profile


ORIGINAL_BUILD = None
ORIGINAL_D3 = None
ORIGINAL_LOADER = None
ORIGINAL_BETA_TARGET_PROFILE = None
ORIGINAL_DOWNTURN_PROFILE = None



def validation_rows_by_scenario(results: dict[tuple[str, str], dict[str, Any]], expost: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for (scenario, mes), result in results.items():
        portfolio = result.get("portfolio", pd.DataFrame())
        reported = result.get("metrics", {}).get("retorno_expost_sombra", np.nan)
        try:
            calculated = sh.portfolio_expost_return(portfolio, expost, mes)
        except Exception:
            calculated = np.nan
        rows.append({
            "cenario": scenario,
            "mes": mes,
            "retorno_reportado": reported,
            "retorno_calculado_pesos": calculated,
            "diferenca_retorno": reported - calculated if pd.notna(reported) and pd.notna(calculated) else np.nan,
            "n_ativos": int(len(portfolio)) if portfolio is not None else 0,
        })
    return rows
def portfolio_rows(results: dict[tuple[str, str], dict[str, Any]]) -> pd.DataFrame:
    frames = []
    for (scenario, mes), result in results.items():
        portfolio = result.get("portfolio", pd.DataFrame())
        if portfolio.empty:
            frames.append(pd.DataFrame([{"cenario": scenario, "mes": mes, "ticker": "", "peso_recomendado": np.nan}]))
            continue
        df = portfolio.copy()
        df.insert(0, "mes", mes)
        df.insert(0, "cenario", scenario)
        frames.append(df)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def confusion_table(audit: pd.DataFrame, pred_col: str) -> pd.DataFrame:
    if audit.empty:
        return pd.DataFrame()
    return pd.crosstab(audit[pred_col], audit["label_expost"], dropna=False).reset_index().rename(columns={pred_col: "bucket_previsto"})


def summarize(rows: pd.DataFrame) -> pd.DataFrame:
    out = []
    for scenario, group in rows.groupby("cenario"):
        ret = compound(group["retorno_expost_sombra_defensivo"])
        ibov = compound(group["retorno_expost_ibov"])
        out.append({
            "cenario": scenario,
            "retorno_carteira": ret,
            "retorno_ibov": ibov,
            "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
            "meses_alfa_positivo": int((group["alfa_sombra_defensivo"] > 0).sum()),
            "meses_queda_classificados_como_alta": int(((group["bucket_regime"].isin(["alta", "oportunidade"])) & (group["retorno_expost_ibov"] < 0)).sum()),
        })
    base_ret = out[0]["retorno_carteira"] if out else np.nan
    for row in out:
        row["delta_retorno_vs_regime_atual"] = row["retorno_carteira"] - base_ret if pd.notna(row["retorno_carteira"]) and pd.notna(base_ret) else np.nan
    return pd.DataFrame(out)


def summarize_2026(rows: pd.DataFrame) -> pd.DataFrame:
    df = rows[rows["mes"].astype(str).str.startswith("2026")]
    return summarize(df)


def main() -> None:
    logs: list[str] = []
    def log(msg: str) -> None:
        print(msg)
        logs.append(msg)

    log("Teste 13 - Auditoria e Recalibracao do Diagnostico de Regime")
    log("Modo sombra; producao intacta; classificacao usa somente dados da data-base/formacao.")
    base_settings = load_settings()
    expost = pd.concat([cons._month_expost_from_workbook(mes, workbook_path(mes)) for mes in MONTHS], ignore_index=True, sort=False)
    audit_inputs = pd.DataFrame([month_audit_inputs(mes, expost) for mes in MONTHS])

    scenario_regime_rows = []
    scenario_rows = []
    all_results: dict[tuple[str, str], dict[str, Any]] = {}

    global ORIGINAL_BUILD, ORIGINAL_D3, ORIGINAL_LOADER, ORIGINAL_BETA_TARGET_PROFILE, ORIGINAL_DOWNTURN_PROFILE
    ORIGINAL_BUILD = sh.build_free_size_portfolio
    ORIGINAL_D3 = sh.technical_veto_to_penalty_in_opportunity
    ORIGINAL_LOADER = bt.patch_sector_enrichment(bt.load_sector_map())
    ORIGINAL_BETA_TARGET_PROFILE = sh.beta_target_profile
    ORIGINAL_DOWNTURN_PROFILE = sh.downturn_regime_profile

    try:
        for scenario in SCENARIOS:
            log(f"\nCenario: {scenario.name} | {scenario.description}")
            regimes: dict[str, tuple[str, str]] = {}
            for _, row in audit_inputs.iterrows():
                bucket, reason = scenario.classifier(row.to_dict())
                regimes[str(row["mes"])] = (bucket, reason)
                scenario_regime_rows.append({**row.to_dict(), "cenario": scenario.name, "bucket_recalibrado": bucket, "motivo_recalibrado": reason})
                log(f"  {row['mes']}: bucket={bucket} | IBOV={pct(row['ibov_expost'])} | {reason}")

            sh.build_free_size_portfolio = cons.consolidated_build_free_size_portfolio
            sh.technical_veto_to_penalty_in_opportunity = cons.make_extended_d3(ORIGINAL_D3)
            sh.beta_target_profile, sh.downturn_regime_profile = profile_patch(regimes)
            sh.MONTHS = MONTHS

            for mes in MONTHS:
                bucket = regimes[mes][0]
                result = sh.run_free_size_for_month(mes, workbook_path(mes), base_settings, lambda_beta=LAMBDA_BETA, downturn_signal="SINAL_A_DEFENSIVO")
                row = sh.build_summary_row(mes, workbook_path(mes), result, expost, shadow_fixes=True)
                row["cenario"] = scenario.name
                row["descricao_cenario"] = scenario.description
                row["motivo_regime_13"] = regimes[mes][1]
                row = bt.apply_exposure(row, bucket)
                scenario_rows.append(row)
                all_results[(scenario.name, mes)] = result
    finally:
        if ORIGINAL_BUILD is not None:
            sh.build_free_size_portfolio = ORIGINAL_BUILD
        if ORIGINAL_D3 is not None:
            sh.technical_veto_to_penalty_in_opportunity = ORIGINAL_D3
        if ORIGINAL_LOADER is not None:
            sh.load_candidate_input = ORIGINAL_LOADER
        if ORIGINAL_BETA_TARGET_PROFILE is not None:
            sh.beta_target_profile = ORIGINAL_BETA_TARGET_PROFILE
        if ORIGINAL_DOWNTURN_PROFILE is not None:
            sh.downturn_regime_profile = ORIGINAL_DOWNTURN_PROFILE

    details = pd.DataFrame(scenario_rows)
    regime_audit = pd.DataFrame(scenario_regime_rows)
    summary = summarize(details)
    summary_2026 = summarize_2026(details)
    validation = pd.DataFrame(validation_rows_by_scenario(all_results, expost))

    confusion_frames = []
    for scenario, group in regime_audit.groupby("cenario"):
        ct = confusion_table(group, "bucket_recalibrado")
        ct.insert(0, "cenario", scenario)
        confusion_frames.append(ct)
    confusion = pd.concat(confusion_frames, ignore_index=True, sort=False) if confusion_frames else pd.DataFrame()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo_cenarios", index=False)
        summary_2026.to_excel(writer, sheet_name="resumo_2026", index=False)
        details.to_excel(writer, sheet_name="mes_a_mes", index=False)
        regime_audit.to_excel(writer, sheet_name="auditoria_regime", index=False)
        confusion.to_excel(writer, sheet_name="matriz_regime_vs_ibov", index=False)
        portfolio_rows(all_results).to_excel(writer, sheet_name="carteiras", index=False)
        validation.to_excel(writer, sheet_name="validacao_retorno", index=False)
        expost.to_excel(writer, sheet_name="expost_universo", index=False)

    log("\nResumo acumulado:")
    for _, row in summary.iterrows():
        log(f"  {row['cenario']}: retorno={pct(row['retorno_carteira'])} | IBOV={pct(row['retorno_ibov'])} | alfa={pct(row['alfa'])} | quedas_como_alta={int(row['meses_queda_classificados_como_alta'])}")
    log("\nResumo 2026:")
    for _, row in summary_2026.iterrows():
        log(f"  {row['cenario']}: retorno={pct(row['retorno_carteira'])} | IBOV={pct(row['retorno_ibov'])} | alfa={pct(row['alfa'])} | quedas_como_alta={int(row['meses_queda_classificados_como_alta'])}")
    if not validation.empty:
        max_diff = pd.to_numeric(validation["diferenca_retorno"], errors="coerce").abs().max()
        log(f"Validacao retorno: maior diferenca peso x retorno = {max_diff:.10f}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()

