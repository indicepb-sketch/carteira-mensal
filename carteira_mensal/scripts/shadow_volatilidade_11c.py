from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import shadow_backtest_2025 as bt  # noqa: E402
import shadow_consolidada_6meses as cons  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
from utils import load_settings  # noqa: E402

OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_volatilidade_11c.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_volatilidade_11c.log"
VOL_FILE = ROOT / "output" / "excel" / "estudo_volatilidade_historica.xlsx"
LAMBDA_BETA = 1.5
VOL_THRESHOLD = 1.5
NEGATIVE_RETURN_THRESHOLD = 0.0

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
class Scenario:
    name: str
    penalty_factor: float = 1.0
    use_weight_cap: bool = False
    description: str = ""


SCENARIOS = [
    Scenario("baseline_consolidado", 1.0, False, "Sem ajuste por volatilidade/retorno."),
    Scenario("alerta_combo", 1.0, False, "Apenas alerta quando vol_ratio_21 > 1.5 e ha retorno/sinal recente negativo."),
    Scenario("penalizacao_leve_combo", 0.90, False, "Gatilho combinado reduz o sinal V3 em 10%."),
    Scenario("penalizacao_forte_combo", 0.75, False, "Gatilho combinado reduz o sinal V3 em 25%."),
    Scenario("teto_10pct_combo", 1.0, True, "Gatilho combinado limita peso individual a 10%."),
]

CURRENT_MES = ""
CURRENT_SCENARIO = SCENARIOS[0]
VOL_MAP: dict[tuple[str, str], dict[str, Any]] = {}
ORIGINAL_BUILD = None
ORIGINAL_D3 = None
ORIGINAL_LOADER = None
ORIGINAL_BETA_PROFILE = None
ORIGINAL_DOWNTURN_PROFILE = None


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
    path = ROOT / "output" / "excel" / MONTHS[mes]
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def load_vol_map() -> dict[tuple[str, str], dict[str, Any]]:
    if not VOL_FILE.exists():
        raise FileNotFoundError(f"Rode primeiro o Teste 11: {VOL_FILE}")
    xls = pd.ExcelFile(VOL_FILE)
    sheet = "Detalhe Ativo Mes" if "Detalhe Ativo Mes" in xls.sheet_names else "detalhe_por_ativo_mes"
    detail = pd.read_excel(VOL_FILE, sheet_name=sheet)
    required = {"mes", "ticker", "vol_ratio_21"}
    missing = required - set(detail.columns)
    if missing:
        raise ValueError(f"Aba detalhe_por_ativo_mes sem colunas: {sorted(missing)}")
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in detail.iterrows():
        mes = str(row.get("mes", "")).strip()
        ticker = str(row.get("ticker", "")).strip().upper()
        if not mes or not ticker:
            continue
        out[(mes, ticker)] = {
            "vol_ratio_21": row.get("vol_ratio_21", np.nan),
            "vol_bucket_21": row.get("bucket_vol_ratio_21", ""),
            "vol_21": row.get("vol_21", np.nan),
            "vol_hist_mediana_21": row.get("vol_hist_mediana_21", np.nan),
        }
    return out


def attach_volatility(frame: pd.DataFrame, mes: str) -> pd.DataFrame:
    if frame.empty or "ticker" not in frame.columns:
        return frame
    out = frame.copy()
    keys = out["ticker"].astype(str).str.upper()
    out["vol_ratio_21"] = [VOL_MAP.get((mes, ticker), {}).get("vol_ratio_21", np.nan) for ticker in keys]
    out["vol_bucket_21"] = [VOL_MAP.get((mes, ticker), {}).get("vol_bucket_21", "") for ticker in keys]
    out["vol_21"] = [VOL_MAP.get((mes, ticker), {}).get("vol_21", np.nan) for ticker in keys]
    out["vol_hist_mediana_21"] = [VOL_MAP.get((mes, ticker), {}).get("vol_hist_mediana_21", np.nan) for ticker in keys]
    vol_high = pd.to_numeric(out["vol_ratio_21"], errors="coerce") > VOL_THRESHOLD
    ret1 = pd.to_numeric(out.get("retorno_acumulado_1m", pd.Series(np.nan, index=out.index)), errors="coerce")
    ret4 = pd.to_numeric(out.get("retorno_acumulado_4m", pd.Series(np.nan, index=out.index)), errors="coerce")
    retmed = pd.to_numeric(out.get("retorno_medio", pd.Series(np.nan, index=out.index)), errors="coerce")
    tendencia = out.get("tendencia_mensal", pd.Series("", index=out.index)).fillna("").astype(str).str.lower()
    timing = out.get("tipo_timing", pd.Series("", index=out.index)).fillna("").astype(str).str.lower()
    leitura_forca = out.get("leitura_forca_relativa_mensal", pd.Series("", index=out.index)).fillna("").astype(str).str.lower()
    class_forca = out.get("classificacao_forca_relativa", pd.Series("", index=out.index)).fillna("").astype(str).str.lower()
    force_score = pd.to_numeric(out.get("forca_relativa_score", pd.Series(np.nan, index=out.index)), errors="coerce")

    out["alerta_volatilidade_anormal_21"] = vol_high
    out["vol11c_retorno_1m_negativo"] = ret1 < NEGATIVE_RETURN_THRESHOLD
    out["vol11c_retorno_4m_negativo"] = ret4 < NEGATIVE_RETURN_THRESHOLD
    out["vol11c_retorno_medio_negativo"] = retmed < NEGATIVE_RETURN_THRESHOLD
    out["vol11c_sinal_tecnico_negativo"] = (
        tendencia.str.contains("fraca|descarte|baixa", regex=True)
        | timing.str.contains("fraco", regex=True)
    )
    out["vol11c_forca_relativa_fraca"] = (
        leitura_forca.str.contains("fraca", regex=True)
        | class_forca.str.contains("fraca", regex=True)
        | (force_score <= 1)
    )
    out["alerta_vol_negativo_11c"] = vol_high & (
        out["vol11c_retorno_1m_negativo"]
        | out["vol11c_retorno_4m_negativo"]
        | out["vol11c_retorno_medio_negativo"]
        | (out["vol11c_sinal_tecnico_negativo"] & out["vol11c_forca_relativa_fraca"])
    )
    motivos = []
    for _, row in out.iterrows():
        parts = []
        if bool(row.get("alerta_volatilidade_anormal_21", False)):
            parts.append("vol_ratio_21_acima_1_5")
        if bool(row.get("vol11c_retorno_1m_negativo", False)):
            parts.append("retorno_1m_negativo")
        if bool(row.get("vol11c_retorno_4m_negativo", False)):
            parts.append("retorno_4m_negativo")
        if bool(row.get("vol11c_retorno_medio_negativo", False)):
            parts.append("retorno_medio_negativo")
        if bool(row.get("vol11c_sinal_tecnico_negativo", False)):
            parts.append("sinal_tecnico_negativo")
        if bool(row.get("vol11c_forca_relativa_fraca", False)):
            parts.append("forca_relativa_fraca")
        motivos.append("; ".join(parts))
    out["motivo_alerta_vol_negativo_11c"] = motivos
    return out


def redistribute_with_individual_caps(weights: pd.Series, caps: pd.Series) -> pd.Series:
    values = pd.to_numeric(weights, errors="coerce").fillna(0).clip(lower=0).astype(float)
    caps = pd.to_numeric(caps.reindex(values.index), errors="coerce").fillna(0.25).clip(lower=0.0).astype(float)
    if values.sum() <= 0:
        return values
    values = values / values.sum()
    capped = pd.Series(0.0, index=values.index, dtype=float)
    free = pd.Series(True, index=values.index)
    remaining = 1.0
    base = values.copy()
    for _ in range(len(values) + 3):
        if not free.any():
            break
        free_base = base[free].clip(lower=0)
        if free_base.sum() <= 0:
            free_base = pd.Series(1.0, index=free_base.index)
        alloc = remaining * free_base / free_base.sum()
        over = alloc > caps[free] + 1e-12
        if not over.any():
            capped.loc[free] = alloc
            remaining = 0.0
            break
        over_idx = alloc[over].index
        capped.loc[over_idx] = caps.loc[over_idx]
        free.loc[over_idx] = False
        remaining = 1.0 - capped.sum()
        if remaining <= 1e-12:
            remaining = 0.0
            break
    if remaining > 1e-10 and free.any():
        room = (caps[free] - capped[free]).clip(lower=0)
        if room.sum() > 0:
            capped.loc[free] += remaining * room / room.sum()
    if capped.sum() > 0:
        capped = capped / capped.sum()
    return capped


def recompute_metrics(portfolio: pd.DataFrame, covariance: pd.DataFrame, settings: dict, metrics: dict[str, Any]) -> dict[str, Any]:
    out = dict(metrics)
    if portfolio.empty:
        return out
    tickers = portfolio["ticker"].astype(str).tolist()
    w = pd.to_numeric(portfolio["peso_recomendado"], errors="coerce").fillna(0).to_numpy(float)
    cov = covariance.reindex(index=tickers, columns=tickers).fillna(0).to_numpy(float)
    mean_returns = pd.to_numeric(portfolio.get("retorno_medio", pd.Series(0, index=portfolio.index)), errors="coerce").fillna(0).to_numpy(float)
    betas = pd.to_numeric(portfolio.get("beta", pd.Series(1.0, index=portfolio.index)), errors="coerce").fillna(1.0).to_numpy(float)
    port_ret = sh.opt.portfolio_return(w, mean_returns)
    port_risk = sh.opt.portfolio_risk(w, cov)
    beta = sh.opt.portfolio_beta(w, betas)
    rf_daily = (1 + float(settings.get("risk_free_rate", {}).get("annual_rate", 0.0))) ** (1 / 252) - 1
    out.update(
        {
            "retorno_carteira": port_ret,
            "risco_carteira": port_risk,
            "cv_carteira": port_risk / port_ret if port_ret > 0 else np.nan,
            "beta_carteira": beta,
            "sharpe": (port_ret - rf_daily) / port_risk if port_risk > 0 else np.nan,
            "maior_peso_individual": float(portfolio["peso_recomendado"].max()),
            "concentracao_por_setor": portfolio.groupby("setor")["peso_recomendado"].sum().to_dict() if "setor" in portfolio else {},
            "acoes_por_setor": portfolio["setor"].value_counts().to_dict() if "setor" in portfolio else {},
            "volatilidade_11c_recalculou_metricas": True,
        }
    )
    return out


def build_with_volatility_overlay(scored: pd.DataFrame, covariance: pd.DataFrame, settings: dict) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    scenario = CURRENT_SCENARIO
    adjusted = attach_volatility(scored, CURRENT_MES)
    if scenario.penalty_factor < 1.0 and not adjusted.empty:
        mask = adjusted["alerta_vol_negativo_11c"].fillna(False).astype(bool)
        for col in ("_shadow_objetivo_sinal_norm", "shadow_tamanho_livre_sinal_v3", "score_prioridade_otimizacao"):
            if col in adjusted.columns:
                numeric_col = pd.to_numeric(adjusted[col], errors="coerce").fillna(0).astype(float)
                adjusted[f"{col}_antes_vol11c"] = numeric_col
                adjusted[col] = numeric_col
                adjusted.loc[mask, col] = numeric_col.loc[mask] * scenario.penalty_factor
        adjusted["vol11c_penalizacao_sinal"] = np.where(mask, 1.0 - scenario.penalty_factor, 0.0)
    else:
        adjusted["vol11c_penalizacao_sinal"] = 0.0

    portfolio, metrics, audit = cons.consolidated_build_free_size_portfolio(adjusted, covariance, settings)
    audit = attach_volatility(audit, CURRENT_MES)
    metrics["vol11c_cenario"] = scenario.name
    metrics["vol11c_descricao"] = scenario.description
    metrics["vol11c_threshold"] = VOL_THRESHOLD
    metrics["vol11c_penalty_factor"] = scenario.penalty_factor

    if not portfolio.empty:
        portfolio = attach_volatility(portfolio, CURRENT_MES)
        portfolio["vol11c_peso_antes_ajuste"] = portfolio["peso_recomendado"]
        if scenario.use_weight_cap:
            high_vol = portfolio["alerta_vol_negativo_11c"].fillna(False).astype(bool)
            caps = pd.Series(0.25, index=portfolio.index)
            caps.loc[high_vol] = 0.10
            new_weights = redistribute_with_individual_caps(portfolio["peso_recomendado"], caps)
            portfolio["vol11c_teto_individual"] = caps
            portfolio["vol11c_cap_10pct_aplicado"] = high_vol
            portfolio["peso_recomendado"] = new_weights.to_numpy(float)
            portfolio["peso_final"] = portfolio["peso_recomendado"]
            metrics["vol11c_cap_10pct_ativos"] = int(high_vol.sum())
            metrics = recompute_metrics(portfolio, covariance, settings, metrics)
        else:
            portfolio["vol11c_teto_individual"] = 0.25
            portfolio["vol11c_cap_10pct_aplicado"] = False
        metrics["vol11c_ativos_alerta_na_carteira"] = int(portfolio["alerta_vol_negativo_11c"].fillna(False).astype(bool).sum())
        metrics["vol11c_peso_alerta_na_carteira"] = float(portfolio.loc[portfolio["alerta_vol_negativo_11c"].fillna(False).astype(bool), "peso_recomendado"].sum())
    return portfolio, metrics, audit


def infer_all_regimes() -> dict[str, tuple[str, str]]:
    regimes = {}
    for mes in MONTHS:
        regimes[mes] = bt.infer_regime_bucket(workbook_path(mes))[:2]
    return regimes


def patch_runtime(regimes: dict[str, tuple[str, str]]) -> None:
    global ORIGINAL_BUILD, ORIGINAL_D3, ORIGINAL_LOADER, ORIGINAL_BETA_PROFILE, ORIGINAL_DOWNTURN_PROFILE
    ORIGINAL_BUILD = sh.build_free_size_portfolio
    ORIGINAL_D3 = sh.technical_veto_to_penalty_in_opportunity
    ORIGINAL_LOADER = bt.patch_sector_enrichment(bt.load_sector_map())
    ORIGINAL_BETA_PROFILE = sh.beta_target_profile
    ORIGINAL_DOWNTURN_PROFILE = sh.downturn_regime_profile
    bt._ORIGINAL_BETA_TARGET_PROFILE = ORIGINAL_BETA_PROFILE
    bt._ORIGINAL_DOWNTURN_PROFILE = ORIGINAL_DOWNTURN_PROFILE
    sh.MONTHS = MONTHS
    sh.build_free_size_portfolio = build_with_volatility_overlay
    sh.technical_veto_to_penalty_in_opportunity = cons.make_extended_d3(ORIGINAL_D3)
    sh.beta_target_profile, sh.downturn_regime_profile = bt.make_profiles(regimes)


def restore_runtime() -> None:
    if ORIGINAL_BUILD is not None:
        sh.build_free_size_portfolio = ORIGINAL_BUILD
    if ORIGINAL_D3 is not None:
        sh.technical_veto_to_penalty_in_opportunity = ORIGINAL_D3
    if ORIGINAL_LOADER is not None:
        sh.load_candidate_input = ORIGINAL_LOADER
    if ORIGINAL_BETA_PROFILE is not None:
        sh.beta_target_profile = ORIGINAL_BETA_PROFILE
    if ORIGINAL_DOWNTURN_PROFILE is not None:
        sh.downturn_regime_profile = ORIGINAL_DOWNTURN_PROFILE


def build_expost() -> pd.DataFrame:
    xls = pd.ExcelFile(VOL_FILE)
    sheet = "Detalhe Ativo Mes" if "Detalhe Ativo Mes" in xls.sheet_names else "detalhe_por_ativo_mes"
    detail = pd.read_excel(VOL_FILE, sheet_name=sheet)
    cols = [
        "mes",
        "ticker",
        "retorno_realizado_periodo",
        "retorno_ibov_periodo",
        "retorno_relativo_vs_ibov",
        "bateu_ibov",
    ]
    available = [col for col in cols if col in detail.columns]
    expost = detail.loc[detail["mes"].astype(str).isin(MONTHS), available].copy()
    expost["ticker"] = expost["ticker"].astype(str).str.upper()
    return expost.drop_duplicates(["mes", "ticker"], keep="first")


def summarize_by_regime(rows: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (cenario, bucket), group in rows.groupby(["cenario", "bucket_regime"], dropna=False):
        ret = compound(group["retorno_expost_sombra_defensivo"])
        ibov = compound(group["retorno_expost_ibov"])
        out.append(
            {
                "cenario": cenario,
                "bucket_regime": bucket,
                "meses": ", ".join(group["mes"].astype(str).tolist()),
                "retorno_carteira": ret,
                "retorno_ibov": ibov,
                "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
            }
        )
    return pd.DataFrame(out)


def portfolio_rows(results: dict[tuple[str, str], dict[str, Any]]) -> pd.DataFrame:
    frames = []
    for (cenario, mes), result in results.items():
        portfolio = result.get("portfolio", pd.DataFrame())
        if portfolio.empty:
            frames.append(pd.DataFrame([{"cenario": cenario, "mes": mes, "ticker": "", "peso_recomendado": np.nan}]))
            continue
        df = portfolio.copy()
        df.insert(0, "mes", mes)
        df.insert(0, "cenario", cenario)
        frames.append(df)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def candidate_alert_rows(results: dict[tuple[str, str], dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for (cenario, mes), result in results.items():
        candidates = attach_volatility(result.get("candidates", pd.DataFrame()), mes)
        if candidates.empty:
            continue
        weights = result.get("portfolio", pd.DataFrame())
        weight_map = weights.set_index("ticker")["peso_recomendado"].to_dict() if not weights.empty and "ticker" in weights else {}
        mask = candidates["alerta_vol_negativo_11c"].fillna(False).astype(bool)
        cols = [c for c in ["ticker", "nome", "setor", "nota_final", "forca_relativa_score", "score_prioridade_otimizacao", "_shadow_objetivo_sinal_norm", "shadow_tamanho_livre_sinal_v3", "vol_ratio_21", "vol_bucket_21", "vol11c_penalizacao_sinal", "status_para_risco", "tipo_timing", "decisao_preliminar_ajustada"] if c in candidates.columns]
        for _, row in candidates.loc[mask, cols].iterrows():
            out = row.to_dict()
            out["cenario"] = cenario
            out["mes"] = mes
            out["peso_final_sombra"] = weight_map.get(out.get("ticker"), 0.0)
            rows.append(out)
    return pd.DataFrame(rows)


def main() -> None:
    logs: list[str] = []

    def log(message: str) -> None:
        print(message, flush=True)
        logs.append(message)

    global VOL_MAP, CURRENT_MES, CURRENT_SCENARIO
    VOL_MAP = load_vol_map()
    missing = [str(workbook_path(mes)) for mes in MONTHS if not workbook_path(mes).exists()]
    if missing:
        raise FileNotFoundError("Arquivos mensais ausentes: " + "; ".join(missing))

    log("Teste 11C: carteiras sombra com regra de volatilidade anormal.")
    log("Producao nao sera alterada. Julho/2026 fica fora da calibracao; janela usada: 2024-01 a 2026-06.")

    base_settings = load_settings()
    expost = build_expost()
    regimes = infer_all_regimes()
    patch_runtime(regimes)

    summary_rows: list[dict[str, Any]] = []
    all_results: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        for scenario in SCENARIOS:
            CURRENT_SCENARIO = scenario
            log(f"\nCenario: {scenario.name} | {scenario.description}")
            for mes in MONTHS:
                CURRENT_MES = mes
                bucket = regimes[mes][0]
                result = sh.run_free_size_for_month(
                    mes,
                    workbook_path(mes),
                    base_settings,
                    lambda_beta=LAMBDA_BETA,
                    downturn_signal="SINAL_A_DEFENSIVO",
                )
                row = sh.build_summary_row(mes, workbook_path(mes), result, expost, shadow_fixes=True)
                row["cenario"] = scenario.name
                row["descricao_cenario"] = scenario.description
                row = bt.apply_exposure(row, bucket)
                row["n_ativos_alerta_vol_carteira"] = result.get("metrics", {}).get("vol11c_ativos_alerta_na_carteira", 0)
                row["peso_alerta_vol_carteira"] = result.get("metrics", {}).get("vol11c_peso_alerta_na_carteira", 0.0)
                row["cap_10pct_ativos"] = result.get("metrics", {}).get("vol11c_cap_10pct_ativos", 0)
                summary_rows.append(row)
                all_results[(scenario.name, mes)] = result
                log(
                    f"{mes}: bucket={bucket} | acoes={len(result.get('portfolio', pd.DataFrame()))} | "
                    f"ret_def={pct(row.get('retorno_expost_sombra_defensivo'))} | "
                    f"IBOV={pct(row.get('retorno_expost_ibov'))} | alfa_def={pct(row.get('alfa_sombra_defensivo'))} | "
                    f"vol_alerta_peso={pct(row.get('peso_alerta_vol_carteira'))}"
                )
    finally:
        restore_runtime()

    details = pd.DataFrame(summary_rows)
    acumulado_rows = []
    for cenario, group in details.groupby("cenario", sort=False):
        ret = compound(group["retorno_expost_sombra_defensivo"])
        ibov = compound(group["retorno_expost_ibov"])
        base_ret = compound(details.loc[details["cenario"].eq("baseline_consolidado"), "retorno_expost_sombra_defensivo"])
        acumulado_rows.append(
            {
                "cenario": cenario,
                "retorno_carteira": ret,
                "retorno_ibov": ibov,
                "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
                "delta_retorno_vs_baseline": ret - base_ret if pd.notna(ret) and pd.notna(base_ret) else np.nan,
                "meses": len(group),
                "n_meses_alfa_positivo": int((group["alfa_sombra_defensivo"] > 0).sum()),
            }
        )
    acumulado = pd.DataFrame(acumulado_rows)
    por_regime = summarize_by_regime(details)
    validation = []
    for key, result in all_results.items():
        cenario, mes = key
        rows = sh.free_size_validation_rows({mes: result}, expost)
        for row in rows:
            row["cenario"] = cenario
            validation.append(row)
    validation_df = pd.DataFrame(validation)
    portfolios = portfolio_rows(all_results)
    alerts = candidate_alert_rows(all_results)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        acumulado.to_excel(writer, sheet_name="resumo_cenarios", index=False)
        details.to_excel(writer, sheet_name="mes_a_mes", index=False)
        por_regime.to_excel(writer, sheet_name="resumo_por_regime", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras", index=False)
        alerts.to_excel(writer, sheet_name="ativos_vol_alerta", index=False)
        validation_df.to_excel(writer, sheet_name="validacao_retorno", index=False)

    log("\nResumo acumulado defensivo:")
    for _, row in acumulado.iterrows():
        log(
            f"  {row['cenario']}: retorno={pct(row['retorno_carteira'])} | "
            f"IBOV={pct(row['retorno_ibov'])} | alfa={pct(row['alfa'])} | "
            f"delta_vs_baseline={pct(row['delta_retorno_vs_baseline'])}"
        )
    if not validation_df.empty:
        max_diff = pd.to_numeric(validation_df["diferenca_retorno"], errors="coerce").abs().max()
        log(f"Validacao retorno: maior diferenca peso x retorno = {max_diff:.10f}")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()








