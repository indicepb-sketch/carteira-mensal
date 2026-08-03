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
from utils import load_settings  # noqa: E402

EXCEL_DIR = ROOT / "output" / "excel"
INPUT_16 = EXCEL_DIR / "shadow_regime_16_risk_on_off.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste24_relaxar_retorno_medio_alta.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_teste24_relaxar_retorno_medio_alta.log"

SCENARIO_NAME = "risk_on_off_mm50"
LAMBDA_BETA = 1.5


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
        "cenario": str(group["cenario_teste24"].iloc[0]),
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


def reblock_negative_mean_return(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    idx = out.index
    original = pd.to_numeric(
        out.get("retorno_medio_original_shadow", out.get("retorno_medio", pd.Series(np.nan, index=idx))),
        errors="coerce",
    )
    deterioration = out.apply(sh.is_real_deterioration, axis=1)
    mask = (original <= 0) & ~deterioration
    if not mask.any():
        return out
    out.loc[mask, "retorno_medio"] = original.loc[mask]
    for col in ["motivo_bloqueio_otimizacao", "tipo_bloqueio_otimizacao", "penalizacoes_otimizacao", "alertas_nao_bloqueantes", "shadow_motivos_correcoes"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)
    out.loc[mask, "motivo_bloqueio_otimizacao"] = out.loc[mask, "motivo_bloqueio_otimizacao"].map(
        lambda x: sh.append_token(x, "bloqueio_por_retorno_medio_negativo")
    )
    out.loc[mask, "tipo_bloqueio_otimizacao"] = out.loc[mask, "tipo_bloqueio_otimizacao"].map(
        lambda x: sh.append_token(x, "bloqueio_risco")
    )
    out.loc[mask, "shadow_motivos_correcoes"] = out.loc[mask, "shadow_motivos_correcoes"].map(
        lambda x: sh.append_token(x, label)
    )
    if "liberado_por_d3" in out.columns:
        out.loc[mask, "liberado_por_d3"] = False
    reason = out.get("motivo_bloqueio_otimizacao", pd.Series("", index=idx)).fillna("").astype(str).str.strip()
    status_ok = out.get("status_para_risco", pd.Series("", index=idx)).isin(["aprovada_para_risco", "moderada_para_risco"])
    category_ok = out.get("categoria_elegibilidade", pd.Series("", index=idx)).isin(["elegivel_forte", "elegivel_moderado"])
    has_risk = out.get("retorno_medio", pd.Series(np.nan, index=idx)).notna()
    out["bloqueado_otimizacao"] = reason.ne("")
    out["liberado_para_otimizacao"] = (~out["bloqueado_otimizacao"]) & status_ok & category_ok & has_risk
    return out


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


def run_mode(mode: str, regimes: dict[str, tuple[str, str]], expost: pd.DataFrame, base_settings: dict) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], pd.DataFrame]:
    current: dict[str, str] = {"mes": "", "bucket": ""}
    original_apply = sh.apply_shadow_fixes
    original_d3 = sh.technical_veto_to_penalty_in_opportunity

    def should_reblock() -> bool:
        if mode == "retorno_medio_rigido":
            return True
        if mode == "relaxa_retorno_medio_so_alta":
            return current["bucket"] != "alta"
        return False

    def apply_wrapper(frame: pd.DataFrame, regime: str) -> pd.DataFrame:
        out = original_apply(frame, regime)
        if should_reblock():
            out = reblock_negative_mean_return(out, f"{mode}_rebloqueio_apos_apply")
        return out

    extended_d3 = cons.make_extended_d3(original_d3)

    def d3_wrapper(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
        out = extended_d3(frame, settings)
        if should_reblock():
            out = reblock_negative_mean_return(out, f"{mode}_rebloqueio_apos_d3")
        return out

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
            row = {
                "cenario_teste24": mode,
                "mes": mes,
                "bucket_regime_previsto": current["bucket"],
                "motivo_regime": regimes[mes][1],
                "tipo_regime_expost": realized_bucket(ibov) if mes != "2026-06" else "jun_oportunidade",
                "exposicao_modelo": exposure,
                "peso_defensivo": 1.0 - exposure,
                "retorno_100_acoes": ret100,
                "retorno_modelo": ret_model,
                "retorno_expost_ibov": ibov,
                "alfa_modelo": ret_model - ibov if pd.notna(ret_model) else np.nan,
                "status_carteira": result.get("metrics", {}).get("status_carteira", ""),
                "n_ativos": len(result.get("portfolio", pd.DataFrame())),
                "tickers_pesos": sh.format_weights(sh.weights_map(result.get("portfolio", pd.DataFrame()))),
            }
            rows.append(row)
            cand = result.get("candidates", pd.DataFrame()).copy()
            if not cand.empty:
                cand["cenario_teste24"] = mode
                cand["mes"] = mes
                cand["bucket_regime_previsto"] = current["bucket"]
                candidates_frames.append(cand)
    finally:
        sh.apply_shadow_fixes = original_apply
        sh.technical_veto_to_penalty_in_opportunity = original_d3
    return pd.DataFrame(rows), results, pd.concat(candidates_frames, ignore_index=True, sort=False) if candidates_frames else pd.DataFrame()


def portfolio_rows(results_by_mode: dict[str, dict[str, dict[str, Any]]]) -> pd.DataFrame:
    frames = []
    for mode, by_month in results_by_mode.items():
        for mes, result in by_month.items():
            port = result.get("portfolio", pd.DataFrame()).copy()
            if port.empty:
                continue
            port["cenario_teste24"] = mode
            port["mes"] = mes
            frames.append(port)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


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
    sh.build_free_size_portfolio = cons.consolidated_build_free_size_portfolio
    sh.beta_target_profile, sh.downturn_regime_profile = r16.profile_patch(regimes)

    try:
        strict_rows, strict_results, strict_candidates = run_mode("retorno_medio_rigido", regimes, expost, base_settings)
        relax_rows, relax_results, relax_candidates = run_mode("relaxa_retorno_medio_so_alta", regimes, expost, base_settings)
    finally:
        sh.build_free_size_portfolio = original_build
        sh.load_candidate_input = original_loader
        sh.beta_target_profile = original_beta_profile
        sh.downturn_regime_profile = original_downturn_profile

    monthly = pd.concat([strict_rows, relax_rows], ignore_index=True, sort=False)
    summary = pd.DataFrame([summarize(g) for _, g in monthly.groupby("cenario_teste24", sort=False)])
    by_regime = pd.DataFrame([summarize(g) | {"tipo_regime_expost": keys[1]} for keys, g in monthly.groupby(["cenario_teste24", "tipo_regime_expost"], sort=False)])
    by_pred = pd.DataFrame([summarize(g) | {"bucket_regime_previsto": keys[1]} for keys, g in monthly.groupby(["cenario_teste24", "bucket_regime_previsto"], sort=False)])

    wide = monthly.pivot(index="mes", columns="cenario_teste24", values=["retorno_modelo", "alfa_modelo", "retorno_100_acoes"])
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    wide = wide.reset_index()
    monthly_compare = monthly[monthly["cenario_teste24"].eq("relaxa_retorno_medio_so_alta")].merge(
        monthly[monthly["cenario_teste24"].eq("retorno_medio_rigido")][["mes", "retorno_modelo", "alfa_modelo", "tickers_pesos"]],
        on="mes",
        how="left",
        suffixes=("_relaxado", "_rigido"),
    )
    monthly_compare["delta_retorno_relaxado_vs_rigido"] = monthly_compare["retorno_modelo_relaxado"] - monthly_compare["retorno_modelo_rigido"]
    monthly_compare["delta_alfa_relaxado_vs_rigido"] = monthly_compare["alfa_modelo_relaxado"] - monthly_compare["alfa_modelo_rigido"]

    candidates = pd.concat([strict_candidates, relax_candidates], ignore_index=True, sort=False)
    changed_rows = []
    if not candidates.empty:
        cand_cols = ["mes", "ticker", "cenario_teste24", "bucket_regime_previsto", "retorno_medio_original_shadow", "retorno_medio", "liberado_para_otimizacao", "bloqueado_otimizacao", "motivo_bloqueio_otimizacao", "shadow_motivos_correcoes", "nota_final", "forca_relativa_score", "beta", "setor"]
        small = candidates[[c for c in cand_cols if c in candidates.columns]].copy()
        for (mes, ticker), group in small.groupby(["mes", "ticker"], sort=False):
            if set(group["cenario_teste24"]) != {"retorno_medio_rigido", "relaxa_retorno_medio_so_alta"}:
                continue
            a = group.set_index("cenario_teste24")
            strict_ok = sh.to_bool(a.at["retorno_medio_rigido", "liberado_para_otimizacao"]) if "liberado_para_otimizacao" in a.columns else False
            relax_ok = sh.to_bool(a.at["relaxa_retorno_medio_so_alta", "liberado_para_otimizacao"]) if "liberado_para_otimizacao" in a.columns else False
            if strict_ok != relax_ok:
                row = a.loc["relaxa_retorno_medio_so_alta"].to_dict()
                row["liberado_no_rigido"] = strict_ok
                row["liberado_no_relaxado"] = relax_ok
                changed_rows.append(row)
    changed = pd.DataFrame(changed_rows)

    portfolios = portfolio_rows({"retorno_medio_rigido": strict_results, "relaxa_retorno_medio_so_alta": relax_results})
    validation = monthly.copy()
    validation["ok_retorno"] = validation["retorno_modelo"].notna()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        by_regime.to_excel(writer, sheet_name="por_regime_real", index=False)
        by_pred.to_excel(writer, sheet_name="por_regime_previsto", index=False)
        monthly_compare.to_excel(writer, sheet_name="mes_a_mes_comparativo", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes_bruto", index=False)
        changed.to_excel(writer, sheet_name="ativos_liberados_pela_regra", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras", index=False)
        validation.to_excel(writer, sheet_name="validacao", index=False)

    log("Teste 24 - Relaxar retorno_medio_negativo em regime de alta")
    log("Comparacao: retorno_medio_rigido vs relaxa_retorno_medio_so_alta, com exposicao 100/50/20.")
    for _, row in summary.iterrows():
        log(
            f"  {row['cenario']}: retorno={pct(row['retorno_carteira'])}; IBOV={pct(row['retorno_ibov'])}; "
            f"alfa={pct(row['alfa_composto'])}; bateu={int(row['meses_bateu_ibov'])}/{int(row['meses'])}"
        )
    rel = summary[summary["cenario"].eq("relaxa_retorno_medio_so_alta")].iloc[0]
    rig = summary[summary["cenario"].eq("retorno_medio_rigido")].iloc[0]
    log(f"Delta relaxado vs rigido: alfa={pct(rel['alfa_composto'] - rig['alfa_composto'])}; retorno={pct(rel['retorno_carteira'] - rig['retorno_carteira'])}")
    if not changed.empty:
        high_changed = changed[changed["bucket_regime_previsto"].eq("alta")]
        log(f"Ativos liberados pela regra em meses de alta: {len(high_changed)} linhas ticker-mes")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
