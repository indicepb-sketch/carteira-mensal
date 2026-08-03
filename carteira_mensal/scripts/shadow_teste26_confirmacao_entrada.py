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
OUTPUT_FILE = EXCEL_DIR / "shadow_teste26_confirmacao_entrada.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_teste26_confirmacao_entrada.log"

LAMBDA_BETA = 1.5
BASE_NEGATIVE_MEAN_POLICY = "qualificado_alta"
BASE_NEGATIVE_MEAN_CAP = 0.075

SCENARIOS = [
    {"name": "t25_cap7_5_base", "confirmation": "none"},
    {"name": "confirmacao_suave_cap", "confirmation": "soft_cap"},
    {"name": "confirmacao_leve_bloqueia_0de3", "confirmation": "block_zero_cap_one"},
    {"name": "confirmacao_estrita", "confirmation": "strict"},
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
        "cenario_teste26": str(group["cenario_teste26"].iloc[0]),
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


def confirmation_score(row: pd.Series) -> tuple[int, str]:
    price = pd.to_numeric(pd.Series([row.get("preco_atual", np.nan)]), errors="coerce").iloc[0]
    mm9 = pd.to_numeric(pd.Series([row.get("mm9", np.nan)]), errors="coerce").iloc[0]
    mm21 = pd.to_numeric(pd.Series([row.get("mm21", np.nan)]), errors="coerce").iloc[0]
    rsi = pd.to_numeric(pd.Series([row.get("rsi", np.nan)]), errors="coerce").iloc[0]
    checks = {
        "preco_acima_mm9": pd.notna(price) and pd.notna(mm9) and price > mm9,
        "mm9_maior_mm21": pd.notna(mm9) and pd.notna(mm21) and mm9 > mm21,
        "rsi_45_70": pd.notna(rsi) and 45 <= rsi <= 70,
    }
    return int(sum(checks.values())), "; ".join(k for k, v in checks.items() if v)


def set_cap(out: pd.DataFrame, mask: pd.Series, cap: float) -> None:
    if "teste25_cap_individual" not in out.columns:
        out["teste25_cap_individual"] = np.nan
    current = pd.to_numeric(out["teste25_cap_individual"], errors="coerce")
    out.loc[mask, "teste25_cap_individual"] = np.where(current.loc[mask].notna(), np.minimum(current.loc[mask], cap), cap)


def apply_confirmation_policy(frame: pd.DataFrame, mode: str) -> pd.DataFrame:
    if frame.empty or mode == "none":
        return frame
    out = frame.copy()
    scores = out.apply(lambda row: confirmation_score(row), axis=1)
    out["teste26_confirmacao_score"] = [x[0] for x in scores]
    out["teste26_confirmacao_sinais"] = [x[1] for x in scores]
    out["teste26_confirmacao_regra"] = mode
    for col in ["motivo_bloqueio_otimizacao", "tipo_bloqueio_otimizacao", "penalizacoes_otimizacao", "alertas_nao_bloqueantes"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)

    permitted = out.get("liberado_para_otimizacao", pd.Series(False, index=out.index)).map(sh.to_bool)
    score0 = permitted & out["teste26_confirmacao_score"].eq(0)
    score1 = permitted & out["teste26_confirmacao_score"].eq(1)

    if mode == "soft_cap":
        set_cap(out, score0, 0.05)
        set_cap(out, score1, 0.075)
        out.loc[score0 | score1, "penalizacoes_otimizacao"] = out.loc[score0 | score1, "penalizacoes_otimizacao"].map(
            lambda x: sh.append_token(x, "confirmacao_tecnica_fraca_cap_reduzido")
        )
    elif mode == "block_zero_cap_one":
        set_cap(out, score1, 0.075)
        out.loc[score1, "penalizacoes_otimizacao"] = out.loc[score1, "penalizacoes_otimizacao"].map(
            lambda x: sh.append_token(x, "confirmacao_tecnica_parcial_cap_7_5")
        )
        out.loc[score0, "motivo_bloqueio_otimizacao"] = out.loc[score0, "motivo_bloqueio_otimizacao"].map(
            lambda x: sh.append_token(x, "bloqueio_por_confirmacao_tecnica_0_de_3")
        )
        out.loc[score0, "tipo_bloqueio_otimizacao"] = out.loc[score0, "tipo_bloqueio_otimizacao"].map(
            lambda x: sh.append_token(x, "bloqueio_timing")
        )
    elif mode == "strict":
        set_cap(out, score1, 0.05)
        out.loc[score1, "penalizacoes_otimizacao"] = out.loc[score1, "penalizacoes_otimizacao"].map(
            lambda x: sh.append_token(x, "confirmacao_tecnica_parcial_cap_5")
        )
        out.loc[score0, "motivo_bloqueio_otimizacao"] = out.loc[score0, "motivo_bloqueio_otimizacao"].map(
            lambda x: sh.append_token(x, "bloqueio_por_confirmacao_tecnica_0_de_3")
        )
        out.loc[score0, "tipo_bloqueio_otimizacao"] = out.loc[score0, "tipo_bloqueio_otimizacao"].map(
            lambda x: sh.append_token(x, "bloqueio_timing")
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
        return apply_confirmation_policy(out, str(scenario["confirmation"]))

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
                    "cenario_teste26": scenario["name"],
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
                cand["cenario_teste26"] = scenario["name"]
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
            port["cenario_teste26"] = mode
            port["mes"] = mes
            frames.append(port)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def confirmation_audit(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty or "teste26_confirmacao_score" not in candidates.columns:
        return pd.DataFrame()
    cols = [
        "cenario_teste26",
        "mes",
        "ticker",
        "setor",
        "bucket_regime_previsto",
        "teste26_confirmacao_score",
        "teste26_confirmacao_sinais",
        "teste26_confirmacao_regra",
        "teste25_cap_individual",
        "preco_atual",
        "mm9",
        "mm21",
        "rsi",
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
    summary = pd.DataFrame([summarize(g) for _, g in monthly.groupby("cenario_teste26", sort=False)])
    by_regime = pd.DataFrame(
        [summarize(g) | {"tipo_regime_expost": keys[1]} for keys, g in monthly.groupby(["cenario_teste26", "tipo_regime_expost"], sort=False)]
    )
    baseline = monthly[monthly["cenario_teste26"].eq("t25_cap7_5_base")][["mes", "retorno_modelo", "alfa_modelo", "tickers_pesos"]]
    compare = monthly[~monthly["cenario_teste26"].eq("t25_cap7_5_base")].merge(
        baseline,
        on="mes",
        how="left",
        suffixes=("", "_base"),
    )
    compare["delta_retorno_vs_base"] = compare["retorno_modelo"] - compare["retorno_modelo_base"]
    compare["delta_alfa_vs_base"] = compare["alfa_modelo"] - compare["alfa_modelo_base"]
    portfolios = portfolio_rows(results_by_mode)
    audit = confirmation_audit(candidates)
    validation = monthly.copy()
    if not portfolios.empty:
        sums = portfolios.groupby(["cenario_teste26", "mes"])["peso_recomendado"].sum().reset_index(name="soma_pesos")
        validation = validation.merge(sums, on=["cenario_teste26", "mes"], how="left")
        validation["pesos_ok"] = validation["soma_pesos"].sub(1.0).abs() < 0.0001

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        by_regime.to_excel(writer, sheet_name="por_regime_real", index=False)
        compare.to_excel(writer, sheet_name="comparativo_vs_base", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes_bruto", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras", index=False)
        audit.to_excel(writer, sheet_name="auditoria_confirmacao", index=False)
        validation.to_excel(writer, sheet_name="validacao", index=False)

    log("Teste 26 - Confirmacao de Entrada Para Candidatas Fortes")
    log("Base: Teste 25 cap 7,5%. Confirmacao: preco>MM9, MM9>MM21, RSI entre 45 e 70.")
    for _, row in summary.iterrows():
        log(
            f"  {row['cenario_teste26']}: retorno={pct(row['retorno_carteira'])}; IBOV={pct(row['retorno_ibov'])}; "
            f"alfa={pct(row['alfa_composto'])}; bateu={int(row['meses_bateu_ibov'])}/{int(row['meses'])}; "
            f"drawdown={pct(row['drawdown_carteira'])}"
        )
    base = summary[summary["cenario_teste26"].eq("t25_cap7_5_base")].iloc[0]
    for scenario in [s["name"] for s in SCENARIOS if s["name"] != "t25_cap7_5_base"]:
        row = summary[summary["cenario_teste26"].eq(scenario)].iloc[0]
        log(f"  Delta {scenario} vs base: alfa={pct(row['alfa_composto'] - base['alfa_composto'])}; retorno={pct(row['retorno_carteira'] - base['retorno_carteira'])}; taxa_acerto_delta={row['taxa_meses_bateu_ibov'] - base['taxa_meses_bateu_ibov']:.2%}")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
