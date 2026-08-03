
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
import shadow_consolidado_14_13b as t14  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
from utils import load_settings  # noqa: E402

EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
OUTPUT_FILE = EXCEL_DIR / "shadow_regime_16_risk_on_off.xlsx"
LOG_FILE = LOG_DIR / "shadow_regime_16_risk_on_off.log"
LAMBDA_BETA = 1.5

# Limires observados no estudo de assertividade Jan/2024-Jun/2026.
# med_beta alto capturou meses risk-on; amplitude estrutural muito alta (MM50>MM100)
# sinalizou risco de regime maduro/virada, por isso <= threshold vira risk-on.
BETA_RISK_ON_THRESHOLD = 1.077681
MM50_RISK_ON_MAX_THRESHOLD = 0.684615
MM50_RISK_OFF_STRONG_THRESHOLD = 0.73
BETA_RISK_OFF_STRONG_THRESHOLD = 1.00

MONTHS: dict[str, str] = dict(t14.MONTHS)


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


def direction(bucket: Any) -> str:
    text = str(bucket).lower()
    return "alta" if text in {"alta", "oportunidade", "risk_on"} or "alta" in text or "oportunidade" in text else "queda"


def realized_bucket(ret: float) -> str:
    if pd.isna(ret):
        return "indefinido"
    if ret >= 0:
        return "alta"
    if ret <= -0.03:
        return "queda_forte"
    return "queda_leve"


def workbook_path(mes: str) -> Path:
    path = EXCEL_DIR / MONTHS[mes]
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def safe_bool(series: pd.Series) -> pd.Series:
    if series.empty:
        return series.astype(bool)
    return series.map(lambda v: str(v).strip().lower() in {"true", "1", "sim", "yes"})


def extra_risk_on_off_metrics(path: Path) -> dict[str, Any]:
    prelim = cons._read_sheet(path, "Analise Preliminar")
    if prelim.empty:
        return {
            "med_beta": np.nan,
            "pct_mm50_gt_mm100": np.nan,
            "n_beta_validos": 0,
            "n_mm50_mm100_validos": 0,
        }
    beta = to_num(prelim.get("beta", pd.Series(dtype=float))).dropna()
    if "mm50_maior_mm100" in prelim.columns:
        mm_bool = safe_bool(prelim["mm50_maior_mm100"])
        valid_mm = prelim["mm50_maior_mm100"].notna()
    else:
        mm50 = to_num(prelim.get("mm50_semanal", prelim.get("mm50", pd.Series(dtype=float))))
        mm100 = to_num(prelim.get("mm100_semanal", prelim.get("mm100", pd.Series(dtype=float))))
        valid_mm = mm50.notna() & mm100.notna()
        mm_bool = mm50 > mm100
    return {
        "med_beta": float(beta.median()) if not beta.empty else np.nan,
        "pct_mm50_gt_mm100": float(mm_bool[valid_mm].mean()) if valid_mm.any() else np.nan,
        "n_beta_validos": int(beta.shape[0]),
        "n_mm50_mm100_validos": int(valid_mm.sum()),
    }


def month_audit_inputs(mes: str, expost: pd.DataFrame) -> dict[str, Any]:
    data = dict(t14.month_audit_inputs(mes, expost))
    data.update(extra_risk_on_off_metrics(workbook_path(mes)))
    beta = data.get("med_beta", np.nan)
    mm = data.get("pct_mm50_gt_mm100", np.nan)
    data["sinal_beta_risk"] = "risk_on" if pd.notna(beta) and beta >= BETA_RISK_ON_THRESHOLD else "risk_off"
    data["sinal_mm50_risk"] = "risk_on" if pd.notna(mm) and mm <= MM50_RISK_ON_MAX_THRESHOLD else "risk_off"
    data["threshold_beta_risk_on"] = BETA_RISK_ON_THRESHOLD
    data["threshold_pct_mm50_gt_mm100_risk_on_max"] = MM50_RISK_ON_MAX_THRESHOLD
    return data


def bucket_from_signals(d: dict[str, Any], beta_signal: str, mm_signal: str, base_bucket: str | None = None) -> tuple[str, str]:
    if beta_signal == "risk_on" and mm_signal == "risk_on":
        return "alta", f"risk-on confirmado: beta_mediano={d.get('med_beta'):.4f} >= {BETA_RISK_ON_THRESHOLD:.4f}; pct_MM50>MM100={d.get('pct_mm50_gt_mm100'):.1%} <= {MM50_RISK_ON_MAX_THRESHOLD:.1%}"
    beta = d.get("med_beta", np.nan)
    mm = d.get("pct_mm50_gt_mm100", np.nan)
    strong_off = (pd.notna(beta) and beta < BETA_RISK_OFF_STRONG_THRESHOLD) or (pd.notna(mm) and mm > MM50_RISK_OFF_STRONG_THRESHOLD)
    bucket = "queda_forte" if strong_off else "queda_leve"
    return bucket, f"risk-off confirmado: beta_mediano={pct(beta) if False else beta:.4f}; pct_MM50>MM100={mm:.1%}; bucket={bucket}"


def baseline_13b(d: dict[str, Any]) -> tuple[str, str]:
    return t14.anti_false_positive_conservative(d)


def beta_only(d: dict[str, Any]) -> tuple[str, str]:
    beta_signal = str(d.get("sinal_beta_risk", "risk_off"))
    beta = d.get("med_beta", np.nan)
    if beta_signal == "risk_on":
        return "alta", f"beta-only risk-on: beta mediano {beta:.4f} >= {BETA_RISK_ON_THRESHOLD:.4f}"
    bucket = "queda_forte" if pd.notna(beta) and beta < BETA_RISK_OFF_STRONG_THRESHOLD else "queda_leve"
    return bucket, f"beta-only risk-off: beta mediano {beta:.4f} < {BETA_RISK_ON_THRESHOLD:.4f}"


def mm50_only(d: dict[str, Any]) -> tuple[str, str]:
    mm_signal = str(d.get("sinal_mm50_risk", "risk_off"))
    mm = d.get("pct_mm50_gt_mm100", np.nan)
    if mm_signal == "risk_on":
        return "alta", f"MM50/MM100 risk-on: pct MM50>MM100 {mm:.1%} <= {MM50_RISK_ON_MAX_THRESHOLD:.1%}"
    bucket = "queda_forte" if pd.notna(mm) and mm > MM50_RISK_OFF_STRONG_THRESHOLD else "queda_leve"
    return bucket, f"MM50/MM100 risk-off: pct MM50>MM100 {mm:.1%} > {MM50_RISK_ON_MAX_THRESHOLD:.1%}"


def majority_vote(d: dict[str, Any]) -> tuple[str, str]:
    b13, r13 = baseline_13b(d)
    votes = [direction(b13), "alta" if d.get("sinal_beta_risk") == "risk_on" else "queda", "alta" if d.get("sinal_mm50_risk") == "risk_on" else "queda"]
    high_votes = votes.count("alta")
    if high_votes >= 2:
        return "alta", f"voto majoritario risk-on ({votes}); 13B={b13}; {r13}"
    bucket = "queda_forte" if b13 == "queda_forte" or (pd.notna(d.get("med_beta")) and d.get("med_beta") < BETA_RISK_OFF_STRONG_THRESHOLD) or (pd.notna(d.get("pct_mm50_gt_mm100")) and d.get("pct_mm50_gt_mm100") > MM50_RISK_OFF_STRONG_THRESHOLD) else "queda_leve"
    return bucket, f"voto majoritario risk-off ({votes}); 13B={b13}; {r13}"


def two_way_confirm(d: dict[str, Any]) -> tuple[str, str]:
    b13, r13 = baseline_13b(d)
    beta_dir = "alta" if d.get("sinal_beta_risk") == "risk_on" else "queda"
    mm_dir = "alta" if d.get("sinal_mm50_risk") == "risk_on" else "queda"
    base_dir = direction(b13)
    if base_dir == "alta" and beta_dir == "queda" and mm_dir == "queda":
        bucket = "queda_forte" if (pd.notna(d.get("med_beta")) and d.get("med_beta") < BETA_RISK_OFF_STRONG_THRESHOLD) or (pd.notna(d.get("pct_mm50_gt_mm100")) and d.get("pct_mm50_gt_mm100") > MM50_RISK_OFF_STRONG_THRESHOLD) else "queda_leve"
        return bucket, f"13B rebaixado por confirmacao dupla risk-off: beta={d.get('med_beta'):.4f}; pct_MM50>MM100={d.get('pct_mm50_gt_mm100'):.1%}; antes={b13}"
    if base_dir == "queda" and beta_dir == "alta" and mm_dir == "alta":
        return "alta", f"13B elevado por confirmacao dupla risk-on: beta={d.get('med_beta'):.4f}; pct_MM50>MM100={d.get('pct_mm50_gt_mm100'):.1%}; antes={b13}"
    return b13, f"13B mantido; confirmacao insuficiente para troca. beta={beta_dir}; mm50={mm_dir}; {r13}"


SCENARIOS = [
    RegimeScenario("13b_conservador", "Baseline Teste 14: diagnostico em duas camadas com filtro anti-falso-positivo.", baseline_13b),
    RegimeScenario("risk_on_off_beta", "Camada risk-on/risk-off usando somente beta mediano do universo.", beta_only),
    RegimeScenario("risk_on_off_mm50", "Camada risk-on/risk-off usando somente pct de ativos com MM50 > MM100.", mm50_only),
    RegimeScenario("risk_on_off_voto", "Voto majoritario: 13B + beta mediano + MM50/MM100 estrutural.", majority_vote),
    RegimeScenario("risk_on_off_confirmacao", "13B troca de regime apenas quando beta e MM50/MM100 concordam contra ele.", two_way_confirm),
]


def scenario_order() -> dict[str, int]:
    return {s.name: i for i, s in enumerate(SCENARIOS)}


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


def portfolio_rows(results: dict[tuple[str, str], dict[str, Any]]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
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


def summarize(rows: pd.DataFrame, only_2026: bool = False) -> pd.DataFrame:
    frame = rows.copy()
    if only_2026:
        frame = frame[frame["mes"].astype(str).str.startswith("2026")]
    out: list[dict[str, Any]] = []
    order = scenario_order()
    for scenario, group in frame.groupby("cenario", sort=False):
        ret = compound(group["retorno_expost_sombra_defensivo"])
        ibov = compound(group["retorno_expost_ibov"])
        out.append({
            "cenario": scenario,
            "periodo": "2026" if only_2026 else "2024_2026_06",
            "retorno_carteira": ret,
            "retorno_ibov": ibov,
            "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
            "meses_alfa_positivo": int((group["alfa_sombra_defensivo"] > 0).sum()),
            "meses_queda_classificados_como_alta": int(((group["bucket_regime"].isin(["alta", "oportunidade"])) & (group["retorno_expost_ibov"] < 0)).sum()),
            "_ordem": order.get(scenario, 999),
        })
    base = next((row for row in out if row["cenario"] == "13b_conservador"), None)
    base_ret = base["retorno_carteira"] if base else np.nan
    for row in out:
        row["delta_retorno_vs_13b"] = row["retorno_carteira"] - base_ret if pd.notna(row["retorno_carteira"]) and pd.notna(base_ret) else np.nan
    return pd.DataFrame(out).sort_values("_ordem").drop(columns="_ordem") if out else pd.DataFrame()


def summarize_by_realized_regime(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    frame = rows.copy()
    frame["tipo_regime_expost"] = [t14.regime_type(m, r) for m, r in zip(frame["mes"], frame["retorno_expost_ibov"])]
    out: list[dict[str, Any]] = []
    order = scenario_order()
    for (scenario, tipo), group in frame.groupby(["cenario", "tipo_regime_expost"], sort=False):
        ret = compound(group["retorno_expost_sombra_defensivo"])
        ibov = compound(group["retorno_expost_ibov"])
        out.append({
            "cenario": scenario,
            "tipo_regime_expost": tipo,
            "meses": ", ".join(group["mes"].astype(str).tolist()),
            "retorno_carteira": ret,
            "retorno_ibov": ibov,
            "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
            "meses_alfa_positivo": int((group["alfa_sombra_defensivo"] > 0).sum()),
            "_ordem": order.get(scenario, 999),
        })
    return pd.DataFrame(out).sort_values(["_ordem", "tipo_regime_expost"]).drop(columns="_ordem") if out else pd.DataFrame()


def regime_accuracy(audit: pd.DataFrame) -> pd.DataFrame:
    if audit.empty:
        return pd.DataFrame()
    frame = audit.copy()
    frame["direcao_prevista"] = frame["bucket_recalibrado"].map(direction)
    frame["direcao_real"] = frame["label_expost"].map(direction)
    frame["acerto_direcional"] = frame["direcao_prevista"].eq(frame["direcao_real"])
    frame["acerto_exato"] = frame["bucket_recalibrado"].replace({"oportunidade": "alta"}).eq(frame["label_expost"].replace({"oportunidade": "alta"}))
    rows = []
    order = scenario_order()
    for scenario, group in frame.groupby("cenario", sort=False):
        rows.append({
            "cenario": scenario,
            "meses": int(len(group)),
            "acertos_exatos": int(group["acerto_exato"].sum()),
            "taxa_acerto_exato": float(group["acerto_exato"].mean()),
            "acertos_direcionais": int(group["acerto_direcional"].sum()),
            "taxa_acerto_direcional": float(group["acerto_direcional"].mean()),
            "acerto_direcional_2024": float(group[group["mes"].astype(str).str.startswith("2024")]["acerto_direcional"].mean()),
            "acerto_direcional_2025": float(group[group["mes"].astype(str).str.startswith("2025")]["acerto_direcional"].mean()),
            "acerto_direcional_2026": float(group[group["mes"].astype(str).str.startswith("2026")]["acerto_direcional"].mean()),
            "_ordem": order.get(scenario, 999),
        })
    return pd.DataFrame(rows).sort_values("_ordem").drop(columns="_ordem")


def confusion_table(audit: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for scenario, group in audit.groupby("cenario", sort=False):
        ct = pd.crosstab(group["bucket_recalibrado"], group["label_expost"], dropna=False).reset_index().rename(columns={"bucket_recalibrado": "bucket_previsto"})
        ct.insert(0, "cenario", scenario)
        frames.append(ct)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def validation_rows(results: dict[tuple[str, str], dict[str, Any]], expost: pd.DataFrame, details: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(t14.validation_rows_by_scenario(results, expost, details))


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    log("Teste 16 - Diagnostico de Regime com Camada Risk-On/Risk-Off")
    log("Modo sombra; producao intacta; compara 13B contra beta mediano, MM50/MM100 e confirmacoes hibridas.")
    base_settings = load_settings()
    sh.MONTHS = MONTHS
    expost = pd.concat([cons._month_expost_from_workbook(mes, workbook_path(mes)) for mes in MONTHS], ignore_index=True, sort=False)
    audit_inputs = pd.DataFrame([month_audit_inputs(mes, expost) for mes in MONTHS])

    log("Sinais risk-on/risk-off por mes:")
    for _, row in audit_inputs.iterrows():
        log(f"  {row['mes']}: IBOV={pct(row['ibov_expost'])} | beta_med={row.get('med_beta', np.nan):.4f} => {row.get('sinal_beta_risk')} | pct_MM50>MM100={pct(row.get('pct_mm50_gt_mm100'))} => {row.get('sinal_mm50_risk')}")

    scenario_regime_rows: list[dict[str, Any]] = []
    scenario_rows: list[dict[str, Any]] = []
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
                row_out = {**row.to_dict(), "cenario": scenario.name, "bucket_recalibrado": bucket, "motivo_recalibrado": reason}
                row_out["direcao_prevista"] = direction(bucket)
                row_out["direcao_real"] = direction(row_out["label_expost"])
                scenario_regime_rows.append(row_out)
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
                row["motivo_regime_16"] = regimes[mes][1]
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
    summary_2026 = summarize(details, only_2026=True)
    summary_by_regime = summarize_by_realized_regime(details)
    accuracy = regime_accuracy(regime_audit)
    confusion = confusion_table(regime_audit)
    validation = validation_rows(all_results, expost, details)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo_cenarios", index=False)
        summary_2026.to_excel(writer, sheet_name="resumo_2026", index=False)
        accuracy.to_excel(writer, sheet_name="assertividade_regime", index=False)
        summary_by_regime.to_excel(writer, sheet_name="resumo_por_tipo_regime", index=False)
        details.to_excel(writer, sheet_name="mes_a_mes", index=False)
        regime_audit.to_excel(writer, sheet_name="auditoria_regime", index=False)
        confusion.to_excel(writer, sheet_name="matriz_regime_vs_ibov", index=False)
        portfolio_rows(all_results).to_excel(writer, sheet_name="carteiras", index=False)
        validation.to_excel(writer, sheet_name="validacao_retorno", index=False)
        expost.to_excel(writer, sheet_name="expost_universo", index=False)

    log("\nResumo acumulado:")
    for _, row in summary.iterrows():
        log(f"  {row['cenario']}: retorno={pct(row['retorno_carteira'])} | IBOV={pct(row['retorno_ibov'])} | alfa={pct(row['alfa'])} | delta_vs_13B={pct(row['delta_retorno_vs_13b'])}")
    log("\nAssertividade direcional:")
    for _, row in accuracy.iterrows():
        log(f"  {row['cenario']}: direcional={pct(row['taxa_acerto_direcional'])} | exato={pct(row['taxa_acerto_exato'])} | 2024={pct(row['acerto_direcional_2024'])} | 2025={pct(row['acerto_direcional_2025'])} | 2026={pct(row['acerto_direcional_2026'])}")
    if not validation.empty:
        max_diff_raw = pd.to_numeric(validation["diferenca_retorno_bruto"], errors="coerce").abs().max()
        max_diff_def = pd.to_numeric(validation["diferenca_retorno_defensivo"], errors="coerce").abs().max()
        log(f"Validacao retorno bruto: maior diferenca peso x retorno = {max_diff_raw:.10f}")
        log(f"Validacao retorno defensivo: maior diferenca exposicao x retorno = {max_diff_def:.10f}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
