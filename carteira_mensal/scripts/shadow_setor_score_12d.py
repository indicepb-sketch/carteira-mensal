from __future__ import annotations

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

EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
VOL_FILE = EXCEL_DIR / "estudo_volatilidade_historica.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_setor_score_12d.xlsx"
LOG_FILE = LOG_DIR / "shadow_setor_score_12d.log"
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
class Scenario:
    name: str
    strength: str
    description: str
    dynamic_sector_cap: bool = False


SCENARIOS = [
    Scenario("baseline_consolidado", "none", "Sem ajuste por regime setorial."),
    Scenario("setor_score_medio_12b", "medium", "Score setorial medio do Teste 12B, sem cap rigido."),
    Scenario("setor_gradual_suave_12d", "gradual_light", "Penalizacao gradual suave: setor ruim pesa mais apenas quando o ativo tambem esta fraco."),
    Scenario("setor_gradual_medio_12d", "gradual_medium", "Penalizacao gradual media: setor ruim + ativo fraco reduz mais o score, sem cortar por quantidade."),
]
CURRENT_MES = ""
CURRENT_SCENARIO = SCENARIOS[0]
SECTOR_REGIME: pd.DataFrame = pd.DataFrame()
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


def norm_ticker(value: Any) -> str:
    ticker = str(value).strip().upper()
    if not ticker or ticker == "NAN":
        return ""
    return ticker if ticker.endswith(".SA") else f"{ticker}.SA"


def workbook_path(mes: str) -> Path:
    path = EXCEL_DIR / MONTHS[mes]
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def load_detail() -> pd.DataFrame:
    xls = pd.ExcelFile(VOL_FILE)
    sheet = "Detalhe Ativo Mes" if "Detalhe Ativo Mes" in xls.sheet_names else "detalhe_por_ativo_mes"
    df = pd.read_excel(VOL_FILE, sheet_name=sheet)
    df["mes"] = df["mes"].astype(str)
    df["ticker"] = df["ticker"].map(norm_ticker)
    df["setor"] = df.get("setor", "").fillna("Nao mapeado").astype(str).str.strip()
    df.loc[df["setor"].isin(["", "nan", "None", "Nao mapeado", "nÃ£o mapeado"]), "setor"] = np.nan
    sector_map = build_sector_map()
    missing = df["setor"].isna()
    df.loc[missing, "setor"] = df.loc[missing, "ticker"].map(sector_map)
    df["setor"] = df["setor"].fillna("Nao mapeado")
    for col in ["retorno_realizado_periodo", "retorno_ibov_periodo", "retorno_relativo_vs_ibov", "bateu_ibov"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[df["mes"].isin(MONTHS)].copy()


def build_sector_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in sorted(EXCEL_DIR.glob("carteira_recomendada_2026_*_v*.xlsx")):
        try:
            frame = pd.read_excel(path, sheet_name="Analise Preliminar", usecols=lambda c: c in ["ticker", "setor"])
        except Exception:
            continue
        for _, row in frame.dropna(subset=["ticker"]).iterrows():
            ticker = norm_ticker(row.get("ticker"))
            sector = str(row.get("setor", "")).strip()
            if ticker and sector and sector.lower() not in {"nan", "nao mapeado", "nÃ£o mapeado"}:
                mapping.setdefault(ticker, sector)
    return mapping


def build_expost(detail: pd.DataFrame) -> pd.DataFrame:
    cols = ["mes", "ticker", "retorno_realizado_periodo", "retorno_ibov_periodo", "retorno_relativo_vs_ibov", "bateu_ibov"]
    return detail[[c for c in cols if c in detail.columns]].drop_duplicates(["mes", "ticker"], keep="first").copy()


def sector_month_stats(detail: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (mes, setor), group in detail.groupby(["mes", "setor"], dropna=False):
        ret = group["retorno_realizado_periodo"].dropna()
        if ret.empty:
            continue
        ibov = group["retorno_ibov_periodo"].dropna()
        ibov_ret = float(ibov.iloc[0]) if not ibov.empty else np.nan
        rows.append(
            {
                "mes": mes,
                "setor": setor,
                "n_ativos_setor": int(group["ticker"].nunique()),
                "retorno_mediano_setor": float(ret.median()),
                "retorno_medio_setor": float(ret.mean()),
                "retorno_ibov_periodo": ibov_ret,
                "alfa_mediano_setor_vs_ibov": float(ret.median() - ibov_ret) if pd.notna(ibov_ret) else np.nan,
                "pct_ativos_bateram_ibov": float((group["retorno_realizado_periodo"] > group["retorno_ibov_periodo"]).mean()),
            }
        )
    return pd.DataFrame(rows).sort_values(["mes", "setor"])


def classify_sector(alpha_1m: float, alpha_3m: float, alpha_6m: float, pct_3m: float) -> str:
    if pd.isna(alpha_3m) and pd.isna(alpha_6m):
        return "setor_neutro_sem_historico"
    a1 = 0 if pd.isna(alpha_1m) else alpha_1m
    a3 = 0 if pd.isna(alpha_3m) else alpha_3m
    a6 = 0 if pd.isna(alpha_6m) else alpha_6m
    p3 = 0.5 if pd.isna(pct_3m) else pct_3m
    if a3 >= 0.015 and a6 >= 0 and p3 >= 0.67:
        return "setor_lider"
    if a3 >= 0.005 and a6 >= -0.005 and p3 >= 0.50:
        return "setor_favoravel"
    if a1 <= -0.03 and a3 <= -0.015 and p3 <= 0.33:
        return "setor_em_deterioracao"
    if a3 <= -0.01 and a6 <= 0:
        return "setor_fraco"
    return "setor_neutro"


def factor_for_class(label: str, strength: str) -> float:
    profiles = {
        "none": {
            "setor_lider": 1.00,
            "setor_favoravel": 1.00,
            "setor_neutro": 1.00,
            "setor_neutro_sem_historico": 1.00,
            "setor_fraco": 1.00,
            "setor_em_deterioracao": 1.00,
        },
        "light": {
            "setor_lider": 1.08,
            "setor_favoravel": 1.04,
            "setor_neutro": 1.00,
            "setor_neutro_sem_historico": 1.00,
            "setor_fraco": 0.96,
            "setor_em_deterioracao": 0.90,
        },
        "medium": {
            "setor_lider": 1.15,
            "setor_favoravel": 1.07,
            "setor_neutro": 1.00,
            "setor_neutro_sem_historico": 1.00,
            "setor_fraco": 0.92,
            "setor_em_deterioracao": 0.82,
        },
        "gradual_light": {
            "setor_lider": 1.08,
            "setor_favoravel": 1.04,
            "setor_neutro": 1.00,
            "setor_neutro_sem_historico": 1.00,
            "setor_fraco": 0.98,
            "setor_em_deterioracao": 0.94,
        },
        "gradual_medium": {
            "setor_lider": 1.10,
            "setor_favoravel": 1.05,
            "setor_neutro": 1.00,
            "setor_neutro_sem_historico": 1.00,
            "setor_fraco": 0.95,
            "setor_em_deterioracao": 0.90,
        },
    }
    return profiles.get(strength, profiles["medium"]).get(label, 1.0)


def build_sector_regime_table(sector_month: pd.DataFrame) -> pd.DataFrame:
    months = sorted(MONTHS)
    sectors = sorted(sector_month["setor"].dropna().unique())
    rows = []
    for mes in months:
        prev_months = [m for m in months if m < mes]
        for sector in sectors:
            prev = sector_month[(sector_month["setor"].eq(sector)) & (sector_month["mes"].isin(prev_months))]
            last1 = prev.tail(1)
            last3 = prev.tail(3)
            last6 = prev.tail(6)
            alpha_1m = float(last1["alfa_mediano_setor_vs_ibov"].mean()) if not last1.empty else np.nan
            alpha_3m = float(last3["alfa_mediano_setor_vs_ibov"].mean()) if not last3.empty else np.nan
            alpha_6m = float(last6["alfa_mediano_setor_vs_ibov"].mean()) if not last6.empty else np.nan
            pct_3m = float((last3["alfa_mediano_setor_vs_ibov"] > 0).mean()) if not last3.empty else np.nan
            regime = classify_sector(alpha_1m, alpha_3m, alpha_6m, pct_3m)
            rows.append(
                {
                    "mes": mes,
                    "setor": sector,
                    "meses_historico_disponiveis": int(prev["mes"].nunique()),
                    "alfa_setor_1m_anterior": alpha_1m,
                    "alfa_setor_3m_media": alpha_3m,
                    "alfa_setor_6m_media": alpha_6m,
                    "pct_3m_setor_bateu_ibov": pct_3m,
                    "regime_setorial_recente": regime,
                    "fator_leve": factor_for_class(regime, "light"),
                    "fator_medio": factor_for_class(regime, "medium"),
                    "fator_gradual_suave": factor_for_class(regime, "gradual_light"),
                    "fator_gradual_medio": factor_for_class(regime, "gradual_medium"),
                }
            )
    return pd.DataFrame(rows)


def attach_sector_regime(frame: pd.DataFrame, mes: str, scenario: Scenario) -> pd.DataFrame:
    if frame.empty or "setor" not in frame.columns:
        return frame
    out = frame.copy()
    regime = SECTOR_REGIME[SECTOR_REGIME["mes"].eq(mes)].copy()
    cols = [
        "setor",
        "regime_setorial_recente",
        "alfa_setor_1m_anterior",
        "alfa_setor_3m_media",
        "alfa_setor_6m_media",
        "pct_3m_setor_bateu_ibov",
        "fator_leve",
        "fator_medio",
        "fator_gradual_suave",
        "fator_gradual_medio",
    ]
    stale_cols = [c for c in cols if c != "setor" and c in out.columns]
    stale_cols += [c for c in out.columns if c.endswith("_x") or c.endswith("_y")]
    if stale_cols:
        out = out.drop(columns=list(dict.fromkeys(stale_cols)), errors="ignore")
    out = out.merge(regime[cols], on="setor", how="left")
    factor_map = {"medium": "fator_medio", "gradual_light": "fator_gradual_suave", "gradual_medium": "fator_gradual_medio"}
    factor_col = factor_map.get(scenario.strength, "fator_leve")
    out["fator_regime_setorial_12b"] = 1.0 if scenario.strength == "none" else pd.to_numeric(out.get(factor_col, pd.Series(1.0, index=out.index)), errors="coerce").fillna(1.0)
    out["cenario_regime_setorial_12b"] = scenario.name
    return out


def apply_dynamic_sector_cap(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    if frame.empty or "setor" not in frame.columns or "regime_setorial_recente" not in frame.columns:
        return frame, []
    out = frame.copy()
    reasons: list[str] = []
    signal_col = "_shadow_objetivo_sinal_norm" if "_shadow_objetivo_sinal_norm" in out.columns else "score_prioridade_otimizacao"
    out["_setor12d_rank_signal"] = pd.to_numeric(out.get(signal_col, pd.Series(0, index=out.index)), errors="coerce").fillna(0)
    out["setor12d_max_acoes_setor"] = np.where(
        out["regime_setorial_recente"].astype(str).isin(["setor_fraco", "setor_em_deterioracao"]),
        1,
        2,
    )
    keep_indexes = []
    dropped = []
    for sector, group in out.groupby("setor", dropna=False):
        limit = int(group["setor12d_max_acoes_setor"].min()) if not group.empty else 2
        ordered = group.sort_values(["_setor12d_rank_signal", "nota_final"], ascending=[False, False])
        keep_indexes.extend(ordered.head(limit).index.tolist())
        for _, row in ordered.iloc[limit:].iterrows():
            dropped.append(f"{row.get('ticker')}:{sector}:{row.get('regime_setorial_recente')}")
    capped = out.loc[sorted(keep_indexes)].copy()
    capped["setor12d_removido_por_cap_setorial"] = False
    if dropped:
        reasons.append("removidos_por_cap_setorial=" + ", ".join(dropped))
    return capped, reasons


def redistribute_with_caps(weights: pd.Series, caps: pd.Series) -> pd.Series:
    values = pd.to_numeric(weights, errors="coerce").fillna(0).clip(lower=0).astype(float)
    caps = pd.to_numeric(caps.reindex(values.index), errors="coerce").fillna(0.25).clip(lower=0.0).astype(float)
    if values.sum() <= 0:
        return values
    values = values / values.sum()
    capped = pd.Series(0.0, index=values.index, dtype=float)
    free = pd.Series(True, index=values.index)
    remaining = 1.0
    for _ in range(len(values) + 3):
        if not free.any():
            break
        base = values[free]
        if base.sum() <= 0:
            base = pd.Series(1.0, index=base.index)
        alloc = remaining * base / base.sum()
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


def recompute_portfolio_metrics(portfolio: pd.DataFrame, covariance: pd.DataFrame, settings: dict, metrics: dict[str, Any]) -> dict[str, Any]:
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
    out.update({
        "retorno_carteira": port_ret,
        "risco_carteira": port_risk,
        "cv_carteira": port_risk / port_ret if port_ret > 0 else np.nan,
        "beta_carteira": beta,
        "sharpe": (port_ret - rf_daily) / port_risk if port_risk > 0 else np.nan,
        "maior_peso_individual": float(portfolio["peso_recomendado"].max()),
        "concentracao_por_setor": portfolio.groupby("setor")["peso_recomendado"].sum().to_dict() if "setor" in portfolio else {},
        "acoes_por_setor": portfolio["setor"].value_counts().to_dict() if "setor" in portfolio else {},
        "setor12d_metricas_recalculadas": True,
    })
    return out


def apply_gradual_asset_context(frame: pd.DataFrame, base_factor: pd.Series, scenario: Scenario) -> pd.Series:
    if scenario.strength not in {"gradual_light", "gradual_medium"} or frame.empty:
        return base_factor
    out = pd.to_numeric(base_factor, errors="coerce").fillna(1.0).astype(float).copy()
    regime = frame.get("regime_setorial_recente", pd.Series("", index=frame.index)).astype(str)
    weak_sector = regime.isin(["setor_fraco", "setor_em_deterioracao"])

    def numeric_col(*names: str) -> pd.Series:
        for name in names:
            if name in frame.columns:
                return pd.to_numeric(frame[name], errors="coerce")
        return pd.Series(np.nan, index=frame.index)

    ret_1m = numeric_col("retorno_acumulado_1m", "retorno_1m", "valorizacao_mes")
    ret_4m = numeric_col("retorno_acumulado_4m", "retorno_4m")
    rel_1m = numeric_col("retorno_1m_relativo_ibov")
    rel_4m = numeric_col("retorno_4m_relativo_ibov")
    vol_ratio = numeric_col("vol_ratio_21", "volatilidade_ratio_21", "vol_ratio")

    weakness = pd.Series(0, index=frame.index, dtype=float)
    weakness += (ret_1m < 0).fillna(False).astype(float)
    weakness += (ret_4m < 0).fillna(False).astype(float)
    weakness += (rel_1m < 0).fillna(False).astype(float)
    weakness += (rel_4m < 0).fillna(False).astype(float)
    weakness += ((vol_ratio > 1.5) & (ret_1m < 0)).fillna(False).astype(float)
    weakness = weakness.clip(upper=3)

    step = 0.04 if scenario.strength == "gradual_light" else 0.07
    contextual = 1.0 - (step * weakness)
    out.loc[weak_sector] = out.loc[weak_sector] * contextual.loc[weak_sector]
    out = out.clip(lower=0.70, upper=1.15)
    return out
def build_with_sector_overlay(scored: pd.DataFrame, covariance: pd.DataFrame, settings: dict) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    scenario = CURRENT_SCENARIO
    adjusted = attach_sector_regime(scored, CURRENT_MES, scenario)
    if scenario.strength != "none" and not adjusted.empty:
        factor = pd.to_numeric(adjusted["fator_regime_setorial_12b"], errors="coerce").fillna(1.0)
        factor = apply_gradual_asset_context(adjusted, factor, scenario)
        adjusted["fator_regime_setorial_12d_aplicado"] = factor
        for col in ("_shadow_objetivo_sinal_norm", "shadow_tamanho_livre_sinal_v3", "score_prioridade_otimizacao"):
            if col in adjusted.columns:
                numeric_col = pd.to_numeric(adjusted[col], errors="coerce").fillna(0).astype(float)
                adjusted[f"{col}_antes_setor12b"] = numeric_col
                adjusted[col] = numeric_col * factor
    cap_reasons: list[str] = []
    if scenario.dynamic_sector_cap:
        adjusted, cap_reasons = apply_dynamic_sector_cap(adjusted)
    portfolio, metrics, audit = cons.consolidated_build_free_size_portfolio(adjusted, covariance, settings)
    audit = attach_sector_regime(audit, CURRENT_MES, scenario)
    if not portfolio.empty:
        portfolio = attach_sector_regime(portfolio, CURRENT_MES, scenario)
        if scenario.dynamic_sector_cap:
            portfolio["setor12d_peso_antes_cap"] = pd.to_numeric(portfolio["peso_recomendado"], errors="coerce").fillna(0.0)
            is_deteriorating = portfolio["regime_setorial_recente"].astype(str).eq("setor_em_deterioracao")
            caps = pd.Series(0.25, index=portfolio.index, dtype=float)
            caps.loc[is_deteriorating] = 0.10
            portfolio["setor12d_teto_individual"] = caps
            portfolio["setor12d_teto_reduzido"] = is_deteriorating
            portfolio["peso_recomendado"] = redistribute_with_caps(portfolio["peso_recomendado"], caps)
            portfolio["peso_final"] = portfolio["peso_recomendado"]
            metrics = recompute_portfolio_metrics(portfolio, covariance, settings, metrics)
    metrics["setor12d_cenario"] = scenario.name
    metrics["setor12d_descricao"] = scenario.description
    metrics["setor12d_forca"] = scenario.strength
    metrics["setor12d_dynamic_cap"] = scenario.dynamic_sector_cap
    metrics["setor12d_cap_reasons"] = " | ".join(cap_reasons)
    metrics["setor12b_cenario"] = scenario.name
    metrics["setor12b_descricao"] = scenario.description
    metrics["setor12b_forca"] = scenario.strength
    return portfolio, metrics, audit


def infer_all_regimes() -> dict[str, tuple[str, str]]:
    return {mes: bt.infer_regime_bucket(workbook_path(mes))[:2] for mes in MONTHS}


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
    sh.build_free_size_portfolio = build_with_sector_overlay
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


def summarize_by_regime(rows: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (cenario, bucket), group in rows.groupby(["cenario", "bucket_regime"], dropna=False):
        ret = compound(group["retorno_expost_sombra_defensivo"])
        ibov = compound(group["retorno_expost_ibov"])
        out.append({"cenario": cenario, "bucket_regime": bucket, "meses": ", ".join(group["mes"].astype(str)), "retorno_carteira": ret, "retorno_ibov": ibov, "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan})
    return pd.DataFrame(out)


def main() -> None:
    logs: list[str] = []

    def log(message: str) -> None:
        print(message, flush=True)
        logs.append(message)

    global SECTOR_REGIME, CURRENT_MES, CURRENT_SCENARIO
    detail = load_detail()
    expost = build_expost(detail)
    sector_month = sector_month_stats(detail)
    SECTOR_REGIME = build_sector_regime_table(sector_month)
    regimes = infer_all_regimes()
    base_settings = load_settings()

    log("Teste 12D - Score Setorial Gradual Sem Cap Rigido")
    log("Modo sombra; usa somente meses anteriores para classificar o setor de cada mes.")
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
                result = sh.run_free_size_for_month(mes, workbook_path(mes), base_settings, lambda_beta=LAMBDA_BETA, downturn_signal="SINAL_A_DEFENSIVO")
                row = sh.build_summary_row(mes, workbook_path(mes), result, expost, shadow_fixes=True)
                row["cenario"] = scenario.name
                row["descricao_cenario"] = scenario.description
                row = bt.apply_exposure(row, bucket)
                summary_rows.append(row)
                all_results[(scenario.name, mes)] = result
                log(
                    f"{mes}: bucket={bucket} | acoes={len(result.get('portfolio', pd.DataFrame()))} | "
                    f"ret_def={pct(row.get('retorno_expost_sombra_defensivo'))} | "
                    f"IBOV={pct(row.get('retorno_expost_ibov'))} | alfa_def={pct(row.get('alfa_sombra_defensivo'))}"
                )
    finally:
        restore_runtime()

    details = pd.DataFrame(summary_rows)
    baseline_ret = compound(details.loc[details["cenario"].eq("baseline_consolidado"), "retorno_expost_sombra_defensivo"])
    summary = []
    for cenario, group in details.groupby("cenario", sort=False):
        ret = compound(group["retorno_expost_sombra_defensivo"])
        ibov = compound(group["retorno_expost_ibov"])
        summary.append(
            {
                "cenario": cenario,
                "retorno_carteira": ret,
                "retorno_ibov": ibov,
                "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
                "delta_retorno_vs_baseline": ret - baseline_ret if pd.notna(ret) and pd.notna(baseline_ret) else np.nan,
                "n_meses_alfa_positivo": int((group["alfa_sombra_defensivo"] > 0).sum()),
            }
        )
    summary_df = pd.DataFrame(summary)
    validation = []
    for (cenario, mes), result in all_results.items():
        rows = sh.free_size_validation_rows({mes: result}, expost)
        for row in rows:
            row["cenario"] = cenario
            validation.append(row)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="resumo_cenarios", index=False)
        details.to_excel(writer, sheet_name="mes_a_mes", index=False)
        summarize_by_regime(details).to_excel(writer, sheet_name="resumo_por_regime", index=False)
        portfolio_rows(all_results).to_excel(writer, sheet_name="carteiras", index=False)
        pd.DataFrame(validation).to_excel(writer, sheet_name="validacao_retorno", index=False)
        SECTOR_REGIME.to_excel(writer, sheet_name="Regime Setorial", index=False)
        sector_month.to_excel(writer, sheet_name="Setor Mes Expost", index=False)

    log("\nResumo acumulado:")
    for _, row in summary_df.iterrows():
        log(f"  {row['cenario']}: retorno={pct(row['retorno_carteira'])} | IBOV={pct(row['retorno_ibov'])} | alfa={pct(row['alfa'])} | delta_vs_baseline={pct(row['delta_retorno_vs_baseline'])}")
    validation_df = pd.DataFrame(validation)
    if not validation_df.empty:
        max_diff = pd.to_numeric(validation_df["diferenca_retorno"], errors="coerce").abs().max()
        log(f"Validacao retorno: maior diferenca peso x retorno = {max_diff:.10f}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()












