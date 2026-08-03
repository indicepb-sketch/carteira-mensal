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
OUTPUT_FILE = EXCEL_DIR / "shadow_teste25_relaxamento_qualificado.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_teste25_relaxamento_qualificado.log"

LAMBDA_BETA = 1.5

SCENARIOS = [
    {"name": "retorno_medio_rigido", "policy": "rigido", "qualified_cap": np.nan},
    {"name": "relaxa_amplo_so_alta", "policy": "amplo_alta", "qualified_cap": np.nan},
    {"name": "relaxa_qualificado_cap5", "policy": "qualificado_alta", "qualified_cap": 0.05},
    {"name": "relaxa_qualificado_cap7_5", "policy": "qualificado_alta", "qualified_cap": 0.075},
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
        "cenario_teste25": str(group["cenario_teste25"].iloc[0]),
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


def is_qualified_negative_mean(row: pd.Series) -> bool:
    if sh.is_real_deterioration(row):
        return False
    if sh.to_bool(row.get("fundamento_bloqueante", False)):
        return False
    nota = pd.to_numeric(pd.Series([row.get("nota_final", np.nan)]), errors="coerce").iloc[0]
    forca = pd.to_numeric(pd.Series([row.get("forca_relativa_score", np.nan)]), errors="coerce").iloc[0]
    watch_type = str(row.get("tipo_watchlist", "")).lower()
    motivo = str(row.get("motivo_watchlist_qualificada", "")) + " " + str(row.get("motivo_bloqueio_otimizacao", ""))
    if "bloqueante" in watch_type or "watchlist_bloqueante" in motivo.lower():
        return False
    if pd.isna(nota) or nota < 60:
        return False
    if pd.isna(forca) or forca < 3:
        return False
    return True


def recompute_optimization_flags(out: pd.DataFrame) -> pd.DataFrame:
    idx = out.index
    reason = out.get("motivo_bloqueio_otimizacao", pd.Series("", index=idx)).fillna("").astype(str).str.strip()
    status_ok = out.get("status_para_risco", pd.Series("", index=idx)).isin(["aprovada_para_risco", "moderada_para_risco"])
    category_ok = out.get("categoria_elegibilidade", pd.Series("", index=idx)).isin(["elegivel_forte", "elegivel_moderado"])
    has_risk = out.get("retorno_medio", pd.Series(np.nan, index=idx)).notna()
    out["bloqueado_otimizacao"] = reason.ne("")
    out["liberado_para_otimizacao"] = (~out["bloqueado_otimizacao"]) & status_ok & category_ok & has_risk
    return out


def enforce_negative_mean_policy(frame: pd.DataFrame, policy: str, bucket: str, label: str, qualified_cap: float | None) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    idx = out.index
    original = pd.to_numeric(
        out.get("retorno_medio_original_shadow", out.get("retorno_medio", pd.Series(np.nan, index=idx))),
        errors="coerce",
    )
    deterioration = out.apply(sh.is_real_deterioration, axis=1)
    neg_mask = (original <= 0) & ~deterioration
    qualified = pd.Series(False, index=idx)
    if policy == "qualificado_alta" and bucket == "alta":
        qualified = out.apply(is_qualified_negative_mean, axis=1) & neg_mask
    elif policy == "amplo_alta" and bucket == "alta":
        qualified = neg_mask

    should_reblock = neg_mask & ~qualified
    if not neg_mask.any():
        return out

    for col in [
        "motivo_bloqueio_otimizacao",
        "tipo_bloqueio_otimizacao",
        "penalizacoes_otimizacao",
        "alertas_nao_bloqueantes",
        "shadow_motivos_correcoes",
        "teste25_status_relaxamento",
        "teste25_motivo_qualificacao",
    ]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)

    if should_reblock.any():
        out.loc[should_reblock, "retorno_medio"] = original.loc[should_reblock]
        out.loc[should_reblock, "motivo_bloqueio_otimizacao"] = out.loc[should_reblock, "motivo_bloqueio_otimizacao"].map(
            lambda x: sh.append_token(x, "bloqueio_por_retorno_medio_negativo")
        )
        out.loc[should_reblock, "tipo_bloqueio_otimizacao"] = out.loc[should_reblock, "tipo_bloqueio_otimizacao"].map(
            lambda x: sh.append_token(x, "bloqueio_risco")
        )
        out.loc[should_reblock, "shadow_motivos_correcoes"] = out.loc[should_reblock, "shadow_motivos_correcoes"].map(
            lambda x: sh.append_token(x, f"{label}_rebloqueio_retorno_medio")
        )
        out.loc[should_reblock, "teste25_status_relaxamento"] = "rebloqueado"

    if qualified.any():
        out.loc[qualified, "shadow_motivos_correcoes"] = out.loc[qualified, "shadow_motivos_correcoes"].map(
            lambda x: sh.append_token(x, f"{label}_retorno_medio_negativo_relaxado")
        )
        out.loc[qualified, "penalizacoes_otimizacao"] = out.loc[qualified, "penalizacoes_otimizacao"].map(
            lambda x: sh.append_token(x, "retorno_medio_negativo_com_qualidade")
        )
        out.loc[qualified, "teste25_status_relaxamento"] = "relaxado_qualificado" if policy == "qualificado_alta" else "relaxado_amplo"
        out.loc[qualified, "teste25_motivo_qualificacao"] = "nota>=60; forca_relativa>=3; sem deterioracao real; sem watchlist bloqueante"
        if qualified_cap is not None and pd.notna(qualified_cap):
            out.loc[qualified, "teste25_cap_individual"] = float(qualified_cap)
    return recompute_optimization_flags(out)


def capped_proportional_weights_by_asset(signal: pd.Series, caps: pd.Series, floor: float = 0.01) -> pd.Series:
    values = pd.to_numeric(signal, errors="coerce").fillna(0).clip(lower=0).astype(float)
    caps = pd.to_numeric(caps.reindex(values.index), errors="coerce").fillna(1.0).clip(lower=0).astype(float)
    if values.empty:
        return values
    values = values + max(float(floor), 0.0)
    if float(caps.sum()) <= 1.0 + 1e-12:
        return caps / caps.sum() if caps.sum() > 0 else pd.Series(0.0, index=values.index)
    capped = pd.Series(0.0, index=values.index, dtype=float)
    free = pd.Series(True, index=values.index)
    remaining = 1.0
    for _ in range(len(values) + 2):
        if not free.any() or remaining <= 1e-12:
            break
        denom = float(values[free].sum())
        if denom <= 0:
            alloc = pd.Series(remaining / int(free.sum()), index=values[free].index)
        else:
            alloc = remaining * values[free] / denom
        over = alloc > caps[free] + 1e-12
        if not over.any():
            capped.loc[free] = alloc
            remaining = 0.0
            break
        over_idx = alloc[over].index
        capped.loc[over_idx] = caps.loc[over_idx]
        free.loc[over_idx] = False
        remaining = 1.0 - float(capped.sum())
    if capped.sum() > 0:
        capped = capped / capped.sum()
    return capped


def build_free_size_portfolio_with_qualified_caps(scored: pd.DataFrame, covariance: pd.DataFrame, settings: dict) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    cfg = sh.free_size_settings(settings)
    audit = scored.copy()
    pool, reasons = sh.selected_free_size_pool(scored, settings)
    min_assets = cfg["min_assets"]
    if len(pool) < min_assets:
        metrics = {
            "status_carteira": "carteira invalida / ativos insuficientes",
            "carteira_valida": False,
            "tamanho_livre_enabled": True,
            "tamanho_livre_numero_aprovadas": len(pool),
            "restricoes_violadas": f"tamanho livre: apenas {len(pool)} acoes aprovadas; minimo {min_assets}",
            "motivo_escolha_final": "; ".join(reasons),
        }
        return pd.DataFrame(), metrics, audit

    pool = pool.copy().reset_index(drop=True)
    signal_v3 = pd.to_numeric(
        pool.get("shadow_tamanho_livre_sinal_v3", pool.get("_shadow_objetivo_sinal_norm", pd.Series(0, index=pool.index))),
        errors="coerce",
    ).fillna(0)
    beta_target = float(settings.get("_runtime_beta_target", np.nan))
    lambda_beta = float(sh.objetivo_retorno_settings(settings).get("lambda_beta", 0.0))
    signal_adjusted = cons.consolidated_beta_adjusted_signal(pool, beta_target, lambda_beta)
    weights_before_cap = signal_adjusted.clip(lower=0) + cfg["signal_floor"]
    weights_before_cap = weights_before_cap / weights_before_cap.sum()
    caps = pd.Series(cfg["individual_cap"], index=pool.index, dtype=float)
    if "teste25_cap_individual" in pool.columns:
        specific = pd.to_numeric(pool["teste25_cap_individual"], errors="coerce")
        caps = caps.where(specific.isna(), np.minimum(caps, specific))
    weights_v3_capped = capped_proportional_weights_by_asset(signal_v3, caps, cfg["signal_floor"])
    w_series = capped_proportional_weights_by_asset(signal_adjusted, caps, cfg["signal_floor"])
    w = w_series.to_numpy(float)

    pool["sinal_v3_original_tamanho_livre"] = signal_v3.to_numpy(float)
    pool["sinal_v3_ajustado_beta_tamanho_livre"] = signal_adjusted.to_numpy(float)
    pool["peso_antes_teto_tamanho_livre"] = weights_before_cap.to_numpy(float)
    pool["peso_v3_sem_beta_tamanho_livre"] = weights_v3_capped.to_numpy(float)
    pool["peso_maximo_permitido_ativo"] = caps.to_numpy(float)
    pool["teto_tamanho_livre_aplicado"] = pool["peso_antes_teto_tamanho_livre"] > caps + 1e-12
    pool["grupo_economico_ou_bloco_risco"] = pool["ticker"].astype(str).map(sh.opt._risk_block_for_ticker)
    pool["peso_recomendado"] = w

    tickers = pool["ticker"].astype(str).tolist()
    cov = covariance.reindex(index=tickers, columns=tickers).fillna(0).to_numpy(float)
    mean_returns = pd.to_numeric(pool.get("retorno_medio", pd.Series(0, index=pool.index)), errors="coerce").fillna(0).to_numpy(float)
    betas = pd.to_numeric(pool.get("beta", pd.Series(1.0, index=pool.index)), errors="coerce").fillna(1.0).to_numpy(float)
    port_ret = sh.opt.portfolio_return(w, mean_returns)
    port_risk = sh.opt.portfolio_risk(w, cov)
    beta = sh.opt.portfolio_beta(w, betas)
    cv = port_risk / port_ret if port_ret > 0 else np.nan
    sectors = pool.get("setor", pd.Series("", index=pool.index)).fillna("Outros")
    sector_counts = sectors.value_counts().to_dict()
    sector_weights = pool.groupby("setor")["peso_recomendado"].sum().to_dict() if "setor" in pool else {}
    max_sector_count = max(sector_counts.values()) if sector_counts else 0
    max_sector_weight = max(sector_weights.values()) if sector_weights else np.nan
    rf_daily = (1 + float(settings.get("risk_free_rate", {}).get("annual_rate", 0.0))) ** (1 / 252) - 1
    sharpe = (port_ret - rf_daily) / port_risk if port_risk > 0 else np.nan
    signal_values = pd.to_numeric(pool.get("_shadow_objetivo_sinal_norm", pd.Series(0, index=pool.index)), errors="coerce").fillna(0).to_numpy(float)
    nota_values = pd.to_numeric(pool.get("nota_final", pool.get("score_prioridade_otimizacao", pd.Series(0, index=pool.index))), errors="coerce").fillna(0).to_numpy(float)
    forca_values = pd.to_numeric(pool.get("forca_relativa_score", pd.Series(0, index=pool.index)), errors="coerce").fillna(0).to_numpy(float)

    metrics = {
        "status_carteira": "valida",
        "carteira_valida": True,
        "tamanho_livre_enabled": True,
        "tamanho_livre_numero_aprovadas": len(pool),
        "tamanho_livre_teto_individual": cfg["individual_cap"],
        "tamanho_livre_minimo_acoes": min_assets,
        "quantidade_acoes": len(pool),
        "ativos_elegiveis": len(pool),
        "retorno_carteira": port_ret,
        "risco_carteira": port_risk,
        "cv_carteira": cv,
        "beta_carteira": beta,
        "sharpe": sharpe,
        "maior_peso_individual": float(pool["peso_recomendado"].max()),
        "maior_peso_setorial": float(max_sector_weight) if pd.notna(max_sector_weight) else np.nan,
        "max_acoes_por_setor": int(max_sector_count),
        "concentracao_por_setor": sector_weights,
        "acoes_por_setor": sector_counts,
        "objetivo_retorno_enabled": True,
        "objetivo_retorno_variant": "V3",
        "objetivo_retorno_lambda_cv": sh.objetivo_retorno_settings(settings)["lambda_cv"],
        "objetivo_retorno_lambda_beta": lambda_beta,
        "objetivo_retorno_sinal_ponderado": float(np.dot(w, signal_values)),
        "tamanho_livre_status_otimizacao_pesos": "v3_proporcional_com_beta_e_caps_qualificados",
        "tamanho_livre_beta_target": beta_target,
        "tamanho_livre_distancia_beta_target": beta - beta_target if pd.notna(beta_target) else np.nan,
        "nota_final_ponderada": float(np.dot(w, nota_values)),
        "forca_relativa_score_ponderada": float(np.dot(w, forca_values)),
        "restricoes_violadas": "",
        "motivo_escolha_final": "; ".join(reasons),
    }
    pool["peso_final"] = pool["peso_recomendado"]
    pool["status_carteira"] = metrics["status_carteira"]
    return pool, metrics, audit


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

    def apply_wrapper(frame: pd.DataFrame, regime: str) -> pd.DataFrame:
        out = original_apply(frame, regime)
        return enforce_negative_mean_policy(
            out,
            str(scenario["policy"]),
            current["bucket"],
            str(scenario["name"]),
            scenario.get("qualified_cap"),
        )

    extended_d3 = cons.make_extended_d3(original_d3)

    def d3_wrapper(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
        out = extended_d3(frame, settings)
        return enforce_negative_mean_policy(
            out,
            str(scenario["policy"]),
            current["bucket"],
            str(scenario["name"]),
            scenario.get("qualified_cap"),
        )

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
                    "cenario_teste25": scenario["name"],
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
                    "status_carteira": metrics.get("status_carteira", ""),
                    "n_ativos": len(result.get("portfolio", pd.DataFrame())),
                    "beta_carteira": metrics.get("beta_carteira", np.nan),
                    "nota_final_ponderada": metrics.get("nota_final_ponderada", np.nan),
                    "forca_relativa_score_ponderada": metrics.get("forca_relativa_score_ponderada", np.nan),
                    "tickers_pesos": sh.format_weights(sh.weights_map(result.get("portfolio", pd.DataFrame()))),
                }
            )
            cand = result.get("candidates", pd.DataFrame()).copy()
            if not cand.empty:
                cand["cenario_teste25"] = scenario["name"]
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
            port["cenario_teste25"] = mode
            port["mes"] = mes
            frames.append(port)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def released_qualified_rows(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    mask = candidates.get("teste25_status_relaxamento", pd.Series("", index=candidates.index)).astype(str).str.contains("relaxado", na=False)
    cols = [
        "cenario_teste25",
        "mes",
        "ticker",
        "setor",
        "bucket_regime_previsto",
        "teste25_status_relaxamento",
        "teste25_cap_individual",
        "nota_final",
        "forca_relativa_score",
        "retorno_medio_original_shadow",
        "retorno_medio",
        "beta",
        "cv",
        "tipo_timing",
        "tipo_watchlist",
        "qualidade_fundamentalista",
        "fundamento_bloqueante",
        "teste25_motivo_qualificacao",
    ]
    return candidates.loc[mask, [c for c in cols if c in candidates.columns]].copy()


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
    sh.build_free_size_portfolio = build_free_size_portfolio_with_qualified_caps
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
    summary = pd.DataFrame([summarize(g) for _, g in monthly.groupby("cenario_teste25", sort=False)])
    by_regime = pd.DataFrame(
        [summarize(g) | {"tipo_regime_expost": keys[1]} for keys, g in monthly.groupby(["cenario_teste25", "tipo_regime_expost"], sort=False)]
    )
    by_pred = pd.DataFrame(
        [summarize(g) | {"bucket_regime_previsto": keys[1]} for keys, g in monthly.groupby(["cenario_teste25", "bucket_regime_previsto"], sort=False)]
    )

    baseline = monthly[monthly["cenario_teste25"].eq("retorno_medio_rigido")][["mes", "retorno_modelo", "alfa_modelo", "tickers_pesos"]]
    compare_frames = []
    for scenario in [s["name"] for s in SCENARIOS if s["name"] != "retorno_medio_rigido"]:
        comp = monthly[monthly["cenario_teste25"].eq(scenario)].merge(
            baseline,
            on="mes",
            how="left",
            suffixes=("", "_rigido"),
        )
        comp["delta_retorno_vs_rigido"] = comp["retorno_modelo"] - comp["retorno_modelo_rigido"]
        comp["delta_alfa_vs_rigido"] = comp["alfa_modelo"] - comp["alfa_modelo_rigido"]
        compare_frames.append(comp)
    monthly_compare = pd.concat(compare_frames, ignore_index=True, sort=False) if compare_frames else pd.DataFrame()

    released = released_qualified_rows(candidates)
    portfolios = portfolio_rows(results_by_mode)
    validation = monthly.copy()
    validation["retorno_ok"] = validation["retorno_modelo"].notna()
    validation["pesos_ok"] = True
    if not portfolios.empty:
        sums = portfolios.groupby(["cenario_teste25", "mes"])["peso_recomendado"].sum().reset_index(name="soma_pesos")
        validation = validation.merge(sums, on=["cenario_teste25", "mes"], how="left")
        validation["pesos_ok"] = validation["soma_pesos"].sub(1.0).abs() < 0.0001

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        by_regime.to_excel(writer, sheet_name="por_regime_real", index=False)
        by_pred.to_excel(writer, sheet_name="por_regime_previsto", index=False)
        monthly_compare.to_excel(writer, sheet_name="comparativo_vs_rigido", index=False)
        monthly.to_excel(writer, sheet_name="mes_a_mes_bruto", index=False)
        released.to_excel(writer, sheet_name="relaxados_qualificados", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras", index=False)
        validation.to_excel(writer, sheet_name="validacao", index=False)

    log("Teste 25 - Relaxamento Qualificado de Retorno Medio Negativo")
    log("Cenarios: rigido, relaxamento amplo so em alta, qualificado cap 5%, qualificado cap 7,5%.")
    for _, row in summary.iterrows():
        log(
            f"  {row['cenario_teste25']}: retorno={pct(row['retorno_carteira'])}; IBOV={pct(row['retorno_ibov'])}; "
            f"alfa={pct(row['alfa_composto'])}; bateu={int(row['meses_bateu_ibov'])}/{int(row['meses'])}; "
            f"drawdown={pct(row['drawdown_carteira'])}"
        )
    rig = summary[summary["cenario_teste25"].eq("retorno_medio_rigido")].iloc[0]
    for scenario in [s["name"] for s in SCENARIOS if s["name"] != "retorno_medio_rigido"]:
        row = summary[summary["cenario_teste25"].eq(scenario)].iloc[0]
        log(f"  Delta {scenario} vs rigido: alfa={pct(row['alfa_composto'] - rig['alfa_composto'])}; retorno={pct(row['retorno_carteira'] - rig['retorno_carteira'])}")
    if not released.empty:
        counts = released.groupby("cenario_teste25")["ticker"].count()
        for scenario, count in counts.items():
            log(f"  Ativos ticker-mes relaxados em {scenario}: {int(count)}")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
