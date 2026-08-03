from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
import sys

for folder in (SRC, SCRIPTS):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import shadow_backtest_2025 as bt  # noqa: E402
import shadow_consolidada_6meses as cons  # noqa: E402
import shadow_regime_16_risk_on_off as r16  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
import shadow_teste25_relaxamento_qualificado as t25  # noqa: E402
from utils import load_settings  # noqa: E402

EXCEL_DIR = ROOT / "output" / "excel"
INPUT_16 = EXCEL_DIR / "shadow_regime_16_risk_on_off.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste27b_peso_conviccao.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_teste27b_peso_conviccao.log"

LAMBDA_BETA = 1.5
BASE_NEGATIVE_MEAN_POLICY = "qualificado_alta"
BASE_NEGATIVE_MEAN_CAP = 0.075

SCENARIOS = [
    {"name": "t25_cap7_5_base", "conviction": "base"},
    {"name": "conviccao_cap_conservador", "conviction": "cap"},
    {"name": "conviccao_remove_baixa", "conviction": "remove_baixa"},
]


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def max_drawdown(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").fillna(0.0)
    if vals.empty:
        return np.nan
    equity = (1.0 + vals).cumprod()
    return float((equity / equity.cummax() - 1.0).min())


def realized_bucket(ret: float) -> str:
    if pd.isna(ret):
        return "indefinido"
    if ret >= 0:
        return "alta"
    if ret <= -0.03:
        return "queda_forte"
    return "queda_leve"


def exposure_100_50_20(bucket: str) -> float:
    bucket = str(bucket).lower()
    if bucket in {"alta", "oportunidade", "jun_oportunidade"}:
        return 1.0
    if bucket == "queda_leve":
        return 0.50
    if bucket == "queda_forte":
        return 0.20
    return 1.0


def summarize(group: pd.DataFrame) -> dict[str, Any]:
    ret = pd.to_numeric(group["retorno_modelo"], errors="coerce")
    ibov = pd.to_numeric(group["retorno_expost_ibov"], errors="coerce")
    alpha = pd.to_numeric(group["alfa_modelo"], errors="coerce")
    return {
        "cenario_teste27b": str(group["cenario_teste27b"].iloc[0]),
        "meses": int(len(group)),
        "retorno_carteira": compound(ret),
        "retorno_ibov": compound(ibov),
        "alfa_composto": compound(ret) - compound(ibov),
        "meses_bateu_ibov": int((alpha > 0).sum()),
        "taxa_meses_bateu_ibov": float((alpha > 0).mean()) if len(alpha) else np.nan,
        "pior_alfa_mensal": float(alpha.min()) if alpha.notna().any() else np.nan,
        "melhor_alfa_mensal": float(alpha.max()) if alpha.notna().any() else np.nan,
        "drawdown_carteira": max_drawdown(ret),
        "exposicao_media": float(pd.to_numeric(group["exposicao_modelo"], errors="coerce").mean()),
    }


def classify_conviction(row: pd.Series) -> str:
    nota = pd.to_numeric(pd.Series([row.get("nota_final", np.nan)]), errors="coerce").iloc[0]
    forca = pd.to_numeric(pd.Series([row.get("forca_relativa_score", np.nan)]), errors="coerce").iloc[0]
    if pd.isna(nota) or pd.isna(forca):
        return "dados_insuficientes"
    if nota >= 80 and forca >= 4:
        return "conviccao_muito_alta"
    if nota >= 70 and forca >= 4:
        return "conviccao_alta"
    if nota >= 60 and forca >= 3:
        return "conviccao_media_alta"
    if nota >= 50 or forca >= 3:
        return "conviccao_media"
    return "conviccao_baixa"


def set_cap(out: pd.DataFrame, mask: pd.Series, cap: float) -> None:
    if "teste25_cap_individual" not in out.columns:
        out["teste25_cap_individual"] = np.nan
    current = pd.to_numeric(out["teste25_cap_individual"], errors="coerce")
    out.loc[mask, "teste25_cap_individual"] = np.where(current.loc[mask].notna(), np.minimum(current.loc[mask], cap), cap)


def apply_conviction_policy(frame: pd.DataFrame, mode: str) -> pd.DataFrame:
    if frame.empty or mode == "base":
        return frame
    out = frame.copy()
    out["teste27b_conviccao_grupo"] = out.apply(classify_conviction, axis=1)
    out["teste27b_regra"] = mode
    for col in ["motivo_bloqueio_otimizacao", "tipo_bloqueio_otimizacao", "penalizacoes_otimizacao", "alertas_nao_bloqueantes"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)

    permitted = out.get("liberado_para_otimizacao", pd.Series(False, index=out.index)).map(sh.to_bool)
    groups = out["teste27b_conviccao_grupo"].astype(str)

    # Conservative caps derived from Test 27A: the best alpha contribution came from 5%-7.5% weights,
    # while 10%-15% was negative. This keeps only very high conviction above 7.5%.
    set_cap(out, permitted & groups.eq("conviccao_muito_alta"), 0.10)
    set_cap(out, permitted & groups.eq("conviccao_alta"), 0.075)
    set_cap(out, permitted & groups.eq("conviccao_media_alta"), 0.05)
    set_cap(out, permitted & groups.eq("conviccao_media"), 0.05)
    set_cap(out, permitted & groups.eq("conviccao_baixa"), 0.025)

    penalized = permitted & groups.isin(["conviccao_baixa", "conviccao_media", "conviccao_media_alta", "conviccao_alta", "conviccao_muito_alta"])
    out.loc[penalized, "penalizacoes_otimizacao"] = out.loc[penalized, "penalizacoes_otimizacao"].map(
        lambda x: sh.append_token(x, "teto_por_conviccao_teste27b")
    )

    if mode == "remove_baixa":
        low = permitted & groups.eq("conviccao_baixa")
        out.loc[low, "motivo_bloqueio_otimizacao"] = out.loc[low, "motivo_bloqueio_otimizacao"].map(
            lambda x: sh.append_token(x, "bloqueio_por_conviccao_baixa_teste27b")
        )
        out.loc[low, "tipo_bloqueio_otimizacao"] = out.loc[low, "tipo_bloqueio_otimizacao"].map(
            lambda x: sh.append_token(x, "bloqueio_prioridade")
        )
    return t25.recompute_optimization_flags(out)


def result_return(result: dict[str, Any], expost: pd.DataFrame, mes: str) -> float:
    portfolio = result.get("portfolio", pd.DataFrame())
    if portfolio.empty:
        return np.nan
    month = expost[expost["mes"].astype(str).eq(mes)].set_index("ticker")
    total = 0.0
    has = False
    for ticker, weight in sh.weights_map(portfolio).items():
        if ticker in month.index and pd.notna(month.at[ticker, "retorno_realizado_periodo"]):
            total += float(weight) * float(month.at[ticker, "retorno_realizado_periodo"])
            has = True
    return total if has else np.nan


def run_mode(scenario: dict[str, Any], regimes: dict[str, tuple[str, str]], expost: pd.DataFrame, base_settings: dict) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], pd.DataFrame]:
    current: dict[str, str] = {"mes": "", "bucket": ""}
    original_apply = sh.apply_shadow_fixes
    original_d3 = sh.technical_veto_to_penalty_in_opportunity

    def enforce(frame: pd.DataFrame) -> pd.DataFrame:
        out = t25.enforce_negative_mean_policy(
            frame,
            BASE_NEGATIVE_MEAN_POLICY,
            current["bucket"],
            str(scenario["name"]),
            BASE_NEGATIVE_MEAN_CAP,
        )
        return apply_conviction_policy(out, str(scenario["conviction"]))

    def apply_wrapper(frame: pd.DataFrame, regime: str) -> pd.DataFrame:
        return enforce(original_apply(frame, regime))

    extended_d3 = cons.make_extended_d3(original_d3)

    def d3_wrapper(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
        return enforce(extended_d3(frame, settings))

    sh.apply_shadow_fixes = apply_wrapper
    sh.technical_veto_to_penalty_in_opportunity = d3_wrapper
    rows: list[dict[str, Any]] = []
    results: dict[str, dict[str, Any]] = {}
    candidates_frames: list[pd.DataFrame] = []
    try:
        for mes in r16.MONTHS:
            current["mes"] = mes
            current["bucket"] = regimes[mes][0]
            result = sh.run_free_size_for_month(
                mes,
                r16.workbook_path(mes),
                base_settings,
                lambda_beta=LAMBDA_BETA,
                downturn_signal="SINAL_A_DEFENSIVO",
            )
            results[mes] = result
            ibov = float(expost[expost["mes"].astype(str).eq(mes)]["retorno_ibov_periodo"].dropna().iloc[0])
            ret100 = result_return(result, expost, mes)
            exposure = exposure_100_50_20(current["bucket"])
            ret_model = ret100 * exposure if pd.notna(ret100) else np.nan
            metrics = result.get("metrics", {})
            rows.append(
                {
                    "cenario_teste27b": scenario["name"],
                    "mes": mes,
                    "bucket_regime_previsto": current["bucket"],
                    "motivo_regime": regimes[mes][1],
                    "tipo_regime_expost": realized_bucket(ibov) if mes != "2026-06" else "jun_oportunidade",
                    "exposicao_modelo": exposure,
                    "retorno_100_acoes": ret100,
                    "retorno_modelo": ret_model,
                    "retorno_expost_ibov": ibov,
                    "alfa_modelo": ret_model - ibov if pd.notna(ret_model) else np.nan,
                    "status_carteira": metrics.get("status_carteira", ""),
                    "n_ativos": len(result.get("portfolio", pd.DataFrame())),
                    "beta_carteira": metrics.get("beta_carteira", np.nan),
                    "tickers_pesos": sh.format_weights(sh.weights_map(result.get("portfolio", pd.DataFrame()))),
                }
            )
            cand = result.get("candidates", pd.DataFrame()).copy()
            if not cand.empty:
                cand["cenario_teste27b"] = scenario["name"]
                cand["mes"] = mes
                cand["bucket_regime_previsto"] = current["bucket"]
                candidates_frames.append(cand)
    finally:
        sh.apply_shadow_fixes = original_apply
        sh.technical_veto_to_penalty_in_opportunity = original_d3
    candidates = pd.concat(candidates_frames, ignore_index=True, sort=False) if candidates_frames else pd.DataFrame()
    return pd.DataFrame(rows), results, candidates


def portfolio_rows(results_by_mode: dict[str, dict[str, dict[str, Any]]]) -> pd.DataFrame:
    frames = []
    for mode, by_month in results_by_mode.items():
        for mes, result in by_month.items():
            port = result.get("portfolio", pd.DataFrame()).copy()
            if port.empty:
                continue
            port["cenario_teste27b"] = mode
            port["mes"] = mes
            frames.append(port)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def caps_audit(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    cols = [
        "cenario_teste27b",
        "mes",
        "ticker",
        "setor",
        "bucket_regime_previsto",
        "teste27b_conviccao_grupo",
        "teste25_cap_individual",
        "nota_final",
        "forca_relativa_score",
        "liberado_para_otimizacao",
        "motivo_bloqueio_otimizacao",
        "penalizacoes_otimizacao",
    ]
    return candidates[[c for c in cols if c in candidates.columns]].copy()


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    if not INPUT_16.exists():
        raise FileNotFoundError(INPUT_16)

    base_settings = load_settings()
    sh.MONTHS = r16.MONTHS
    expost = pd.read_excel(INPUT_16, sheet_name="expost_universo")
    audit_inputs = pd.DataFrame([r16.month_audit_inputs(mes, expost) for mes in r16.MONTHS])
    regimes: dict[str, tuple[str, str]] = {}
    for _, row in audit_inputs.iterrows():
        bucket, reason = r16.mm50_only(row.to_dict())
        regimes[str(row["mes"])] = (bucket, reason)

    original_build = sh.build_free_size_portfolio
    original_loader = bt.patch_sector_enrichment(bt.load_sector_map())
    original_beta_profile = sh.beta_target_profile
    original_downturn_profile = sh.downturn_regime_profile
    r16.ORIGINAL_BETA_TARGET_PROFILE = original_beta_profile
    r16.ORIGINAL_DOWNTURN_PROFILE = original_downturn_profile
    sh.build_free_size_portfolio = t25.build_free_size_portfolio_with_qualified_caps
    sh.beta_target_profile, sh.downturn_regime_profile = r16.profile_patch(regimes)

    all_rows: list[pd.DataFrame] = []
    all_candidates: list[pd.DataFrame] = []
    results_by_mode: dict[str, dict[str, dict[str, Any]]] = {}
    try:
        for scenario in SCENARIOS:
            rows, results, candidates = run_mode(scenario, regimes, expost, base_settings)
            all_rows.append(rows)
            all_candidates.append(candidates)
            results_by_mode[str(scenario["name"])] = results
    finally:
        sh.build_free_size_portfolio = original_build
        sh.load_candidate_input = original_loader
        sh.beta_target_profile = original_beta_profile
        sh.downturn_regime_profile = original_downturn_profile

    monthly = pd.concat(all_rows, ignore_index=True, sort=False)
    candidates = pd.concat(all_candidates, ignore_index=True, sort=False) if all_candidates else pd.DataFrame()
    summary = pd.DataFrame([summarize(g) for _, g in monthly.groupby("cenario_teste27b", sort=False)])
    by_regime = pd.DataFrame(
        [summarize(g) | {"tipo_regime_expost": keys[1]} for keys, g in monthly.groupby(["cenario_teste27b", "tipo_regime_expost"], sort=False)]
    )
    baseline = monthly[monthly["cenario_teste27b"].eq("t25_cap7_5_base")][["mes", "retorno_modelo", "alfa_modelo", "tickers_pesos"]]
    compare = monthly[~monthly["cenario_teste27b"].eq("t25_cap7_5_base")].merge(
        baseline,
        on="mes",
        how="left",
        suffixes=("", "_base"),
    )
    compare["delta_retorno_vs_base"] = compare["retorno_modelo"] - compare["retorno_modelo_base"]
    compare["delta_alfa_vs_base"] = compare["alfa_modelo"] - compare["alfa_modelo_base"]
    portfolios = portfolio_rows(results_by_mode)
    audit = caps_audit(candidates)
    validation = monthly.copy()
    if not portfolios.empty:
        sums = portfolios.groupby(["cenario_teste27b", "mes"])["peso_recomendado"].sum().reset_index(name="soma_pesos")
        maxw = portfolios.groupby(["cenario_teste27b", "mes"])["peso_recomendado"].max().reset_index(name="maior_peso")
        validation = validation.merge(sums, on=["cenario_teste27b", "mes"], how="left").merge(maxw, on=["cenario_teste27b", "mes"], how="left")
        validation["pesos_ok"] = validation["soma_pesos"].sub(1.0).abs() < 0.0001

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        by_regime.to_excel(writer, sheet_name="por_regime_real", index=False)
        compare.to_excel(writer, sheet_name="comparativo_vs_base", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes_bruto", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras", index=False)
        audit.to_excel(writer, sheet_name="auditoria_caps_conviccao", index=False)
        validation.to_excel(writer, sheet_name="validacao", index=False)

    log("Teste 27B - Peso por Conviccao com Teto Conservador")
    log("Base: Teste 25 cap 7,5%. Caps: muito alta 10%, alta 7,5%, media/media-alta 5%, baixa 2,5%.")
    for _, row in summary.iterrows():
        log(
            f"  {row['cenario_teste27b']}: retorno={pct(row['retorno_carteira'])}; IBOV={pct(row['retorno_ibov'])}; "
            f"alfa={pct(row['alfa_composto'])}; bateu={int(row['meses_bateu_ibov'])}/{int(row['meses'])}; "
            f"drawdown={pct(row['drawdown_carteira'])}"
        )
    base = summary[summary["cenario_teste27b"].eq("t25_cap7_5_base")].iloc[0]
    for scenario in [s["name"] for s in SCENARIOS if s["name"] != "t25_cap7_5_base"]:
        row = summary[summary["cenario_teste27b"].eq(scenario)].iloc[0]
        log(f"  Delta {scenario} vs base: alfa={pct(row['alfa_composto'] - base['alfa_composto'])}; retorno={pct(row['retorno_carteira'] - base['retorno_carteira'])}; taxa_acerto_delta={row['taxa_meses_bateu_ibov'] - base['taxa_meses_bateu_ibov']:.2%}")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
