from __future__ import annotations

import math
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
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils import load_settings

import shadow_simulacao as sh


MONTHS_6 = {
    "2026-01": "carteira_recomendada_2026_01_v1.xlsx",
    "2026-02": "carteira_recomendada_2026_02_v4.xlsx",
    "2026-03": "carteira_recomendada_2026_03_v4.xlsx",
    "2026-04": "carteira_recomendada_2026_04_v2.xlsx",
    "2026-05": "carteira_recomendada_2026_05_v3.xlsx",
    "2026-06": "carteira_recomendada_2026_06_v4.xlsx",
}

CONSOLIDATED_SIGNAL_PROFILE = {
    "2026-01": ("alta", "grupo consolidado: alta; usa momentum"),
    "2026-02": ("alta", "grupo consolidado: alta; usa momentum"),
    "2026-03": ("queda_leve_lateral", "grupo consolidado: queda leve; usa defensivo"),
    "2026-04": ("queda_leve_lateral", "grupo consolidado: queda leve; usa defensivo"),
    "2026-05": ("queda_forte", "grupo consolidado: queda forte; usa defensivo"),
    "2026-06": ("alta", "grupo consolidado: oportunidade/favoravel; usa momentum"),
}


CONSOLIDATED_BETA_PROFILE = {
    "2026-01": {"beta_target_subtipo": "favoravel_amplo", "beta_target": 1.10, "beta_target_min": 1.00, "beta_target_max": 1.20, "beta_target_reason": "grupo consolidado: alta; beta-alvo ofensivo"},
    "2026-02": {"beta_target_subtipo": "favoravel_amplo", "beta_target": 1.10, "beta_target_min": 1.00, "beta_target_max": 1.20, "beta_target_reason": "grupo consolidado: alta; beta-alvo ofensivo"},
    "2026-03": {"beta_target_subtipo": "favoravel_estreitando", "beta_target": 0.95, "beta_target_min": 0.85, "beta_target_max": 1.05, "beta_target_reason": "grupo consolidado: queda leve; beta-alvo moderado"},
    "2026-04": {"beta_target_subtipo": "cansado", "beta_target": 0.75, "beta_target_min": 0.65, "beta_target_max": 0.90, "beta_target_reason": "grupo consolidado: queda leve/cansado; beta-alvo defensivo"},
    "2026-05": {"beta_target_subtipo": "cansado", "beta_target": 0.75, "beta_target_min": 0.65, "beta_target_max": 0.90, "beta_target_reason": "grupo consolidado: queda forte; beta-alvo defensivo"},
    "2026-06": {"beta_target_subtipo": "favoravel_oportunidade", "beta_target": 1.15, "beta_target_min": 1.05, "beta_target_max": 1.30, "beta_target_reason": "grupo consolidado: oportunidade/favoravel; beta-alvo ofensivo"},
}
OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_simulacao_consolidada_6meses_v2.xlsx"
LOG_FILE = ROOT / "output" / "excel" / "shadow_simulacao_consolidada_6meses_v2.log"
LAMBDA_BETA_CONSOLIDADO = 1.5


PREVIOUS_ALPHA = {
    "2026-01": -0.0483,
    "2026-02": 0.0194,
    "2026-03": -0.0027,
    "2026-04": -0.0248,
    "2026-05": -0.0258,
    "2026-06": 0.0141,
    "ACUMULADO_6_MESES": -0.0701,
}
REGIME_GROUPS = {
    "altas": ["2026-01", "2026-02"],
    "quedas_leves": ["2026-03", "2026-04"],
    "queda_forte": ["2026-05"],
    "oportunidade": ["2026-06"],
}


def _pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def consolidated_beta_adjusted_signal(pool: pd.DataFrame, beta_target: float, lambda_beta: float) -> pd.Series:
    base = pd.to_numeric(
        pool.get("shadow_tamanho_livre_sinal_v3", pool.get("_shadow_objetivo_sinal_norm", pd.Series(0, index=pool.index))),
        errors="coerce",
    ).fillna(0).clip(lower=0)
    if lambda_beta <= 0 or pd.isna(beta_target) or "beta" not in pool.columns:
        return base
    betas = pd.to_numeric(pool["beta"], errors="coerce").fillna(beta_target)
    beta_affinity = 1.0 / (1.0 + float(lambda_beta) * (betas - float(beta_target)).abs())
    adjusted = base * beta_affinity
    return adjusted.fillna(0).clip(lower=0)


def consolidated_build_free_size_portfolio(scored: pd.DataFrame, covariance: pd.DataFrame, settings: dict) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
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
    signal_adjusted = consolidated_beta_adjusted_signal(pool, beta_target, lambda_beta)
    weights_before_cap = signal_adjusted.clip(lower=0) + cfg["signal_floor"]
    weights_before_cap = weights_before_cap / weights_before_cap.sum()
    weights_v3_capped = sh.capped_proportional_weights(signal_v3, cfg["individual_cap"], cfg["signal_floor"])
    w = sh.capped_proportional_weights(signal_adjusted, cfg["individual_cap"], cfg["signal_floor"]).to_numpy(float)

    pool["sinal_v3_original_tamanho_livre"] = signal_v3.to_numpy(float)
    pool["sinal_v3_ajustado_beta_tamanho_livre"] = signal_adjusted.to_numpy(float)
    pool["peso_antes_teto_tamanho_livre"] = weights_before_cap.to_numpy(float)
    pool["peso_v3_sem_beta_tamanho_livre"] = weights_v3_capped.to_numpy(float)
    pool["peso_maximo_permitido_ativo"] = cfg["individual_cap"]
    pool["teto_tamanho_livre_aplicado"] = pool["peso_antes_teto_tamanho_livre"] > cfg["individual_cap"] + 1e-12
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
        "tamanho_livre_status_otimizacao_pesos": "v3_proporcional_com_modulador_beta_sem_slsqp",
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


def run_weight_unit_tests() -> pd.DataFrame:
    cases = []
    toy3 = pd.Series([0.90, 0.50, 0.20], index=["alto", "medio", "baixo"])
    w3 = sh.capped_proportional_weights(toy3, cap=0.60, floor=0.01)
    for ticker, signal in toy3.items():
        cases.append({"teste": "3_acoes_cap_60", "ativo": ticker, "sinal": signal, "peso_pos_teto": float(w3.loc[ticker])})
    toy6 = pd.Series([1.00, 0.80, 0.60, 0.40, 0.25, 0.10], index=["a1", "a2", "a3", "a4", "a5", "a6"])
    w6 = sh.capped_proportional_weights(toy6, cap=0.25, floor=0.01)
    for ticker, signal in toy6.items():
        cases.append({"teste": "6_acoes_cap_25", "ativo": ticker, "sinal": signal, "peso_pos_teto": float(w6.loc[ticker])})
    return pd.DataFrame(cases)
def _read_sheet(path: Path, sheet: str) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet)
    except Exception:
        return pd.DataFrame()


def _field_value(fields: dict[str, Any], *names: str) -> Any:
    normalized = {str(k).strip().lower(): v for k, v in fields.items()}
    for name in names:
        key = name.strip().lower()
        if key in normalized:
            return normalized[key]
    return np.nan


def _coerce_date(value: Any) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    try:
        return pd.to_datetime(value).normalize()
    except Exception:
        return None


def _ticker_col(df: pd.DataFrame) -> str | None:
    for col in ("ticker_yfinance", "ticker", "Ticker", "ativo", "ticker_original"):
        if col in df.columns:
            return col
    return None


def _normalize_ticker(value: Any) -> str:
    ticker = str(value).strip().upper()
    if not ticker or ticker == "NAN":
        return ""
    if ticker == "^BVSP":
        return ticker
    return ticker if ticker.endswith(".SA") else f"{ticker}.SA"


def _price_at_or_before(series: pd.Series, date: pd.Timestamp) -> float:
    s = series.dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    s = s[s.index <= date]
    return float(s.iloc[-1]) if not s.empty else np.nan


def _download_prices(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    import yfinance as yf

    unique = sorted(set(tickers))
    data = yf.download(
        tickers=unique,
        start=(start - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
        end=(end + pd.Timedelta(days=3)).strftime("%Y-%m-%d"),
        auto_adjust=False,
        progress=False,
        threads=True,
        group_by="ticker",
    )
    return data


def _series_from_download(data: pd.DataFrame, ticker: str) -> pd.Series:
    if data.empty:
        return pd.Series(dtype=float)
    if isinstance(data.columns, pd.MultiIndex):
        if ticker in data.columns.get_level_values(0):
            frame = data[ticker]
            if "Adj Close" in frame.columns:
                return frame["Adj Close"]
            if "Close" in frame.columns:
                return frame["Close"]
        if "Adj Close" in data.columns.get_level_values(0) and ticker in data["Adj Close"].columns:
            return data["Adj Close"][ticker]
    if "Adj Close" in data.columns:
        return data["Adj Close"]
    if "Close" in data.columns:
        return data["Close"]
    return pd.Series(dtype=float)


def _month_expost_from_workbook(mes: str, path: Path) -> pd.DataFrame:
    fields = sh.fields_dict(_read_sheet(path, "Validacao Final"))
    base_fields = sh.fields_dict(_read_sheet(path, "Data Base Carteira"))
    fields.update({k: v for k, v in base_fields.items() if k not in fields or pd.isna(fields[k])})
    start = _coerce_date(
        _field_value(fields, "data_inicio_performance", "Data Inicio Performance", "data_formacao_carteira")
    )
    end = _coerce_date(_field_value(fields, "data_avaliacao_carteira", "Data Avaliacao Carteira"))
    formation = _coerce_date(_field_value(fields, "data_formacao_carteira", "Data Formacao Carteira"))
    if start is None:
        start = formation
    if end is None or start is None:
        raise ValueError(f"Datas de performance ausentes em {path.name}")

    universe = _read_sheet(path, "Universo de Ativos")
    col = _ticker_col(universe)
    if col is None:
        raise ValueError(f"Aba Universo de Ativos sem coluna de ticker em {path.name}")
    tickers = [_normalize_ticker(v) for v in universe[col].dropna().tolist()]
    tickers = [t for t in tickers if t and not re.match(r"^I[A-Z]{3,4}\.SA$", t) and not t.startswith("XFIX")]

    prelim = _read_sheet(path, "Analise Preliminar")
    risco = _read_sheet(path, "Candidatas Risco")
    otimiz = _read_sheet(path, "Otimizacao")

    meta = {}
    for df in (prelim, risco, otimiz):
        if df.empty:
            continue
        tcol = _ticker_col(df)
        if tcol is None:
            continue
        for _, row in df.iterrows():
            ticker = _normalize_ticker(row.get(tcol))
            if not ticker:
                continue
            item = meta.setdefault(ticker, {})
            for key in (
                "nome",
                "setor",
                "nota_final",
                "classificacao_forca_relativa",
                "retorno_acumulado_1m",
                "regime_mercado",
                "subtipo_mercado",
                "status_para_risco",
                "decisao_preliminar_ajustada",
                "motivo_bloqueio_otimizacao",
                "motivo_decisao_preliminar",
                "peso_final",
                "peso_recomendado",
            ):
                if key in row and pd.notna(row.get(key)) and (key not in item or pd.isna(item.get(key))):
                    item[key] = row.get(key)

    prices = _download_prices(tickers + ["^BVSP"], start, end)
    ibov_series = _series_from_download(prices, "^BVSP")
    ibov_start = _price_at_or_before(ibov_series, start)
    ibov_end = _price_at_or_before(ibov_series, end)
    ibov_ret = ibov_end / ibov_start - 1.0 if ibov_start and pd.notna(ibov_start) and pd.notna(ibov_end) else np.nan

    real_weights = sh.weights_map(sh.real_portfolio(path))
    rows = []
    for ticker in tickers:
        series = _series_from_download(prices, ticker)
        p0 = _price_at_or_before(series, start)
        p1 = _price_at_or_before(series, end)
        ret = p1 / p0 - 1.0 if p0 and pd.notna(p0) and pd.notna(p1) else np.nan
        info = meta.get(ticker, {})
        peso = float(real_weights.get(ticker, 0.0))
        status_para_risco = str(info.get("status_para_risco", "")).lower()
        decisao = str(info.get("decisao_preliminar_ajustada", "")).lower()
        bloqueado = str(info.get("motivo_bloqueio_otimizacao", "")).strip()
        if peso > 0:
            status = "selecionada"
        elif "aprovada" in status_para_risco or "moderada" in status_para_risco or "candidata" in decisao:
            status = "aprovada_nao_selecionada"
        elif bloqueado:
            status = "bloqueada"
        else:
            status = "fora_do_funil"
        rows.append(
            {
                "mes": mes,
                "data_formacao": formation,
                "data_inicio_performance": start,
                "data_avaliacao": end,
                "ticker": ticker,
                "nome": info.get("nome", ""),
                "setor": info.get("setor", ""),
                "retorno_realizado_periodo": ret,
                "retorno_ibov_periodo": ibov_ret,
                "retorno_relativo_vs_ibov": ret - ibov_ret if pd.notna(ret) and pd.notna(ibov_ret) else np.nan,
                "bateu_ibov": bool(ret > ibov_ret) if pd.notna(ret) and pd.notna(ibov_ret) else np.nan,
                "status_na_selecao": status,
                "motivo_bloqueio_ou_status": bloqueado or info.get("motivo_decisao_preliminar", ""),
                "peso_final": peso,
                "nota_final": info.get("nota_final", np.nan),
                "classificacao_forca_relativa": info.get("classificacao_forca_relativa", ""),
                "retorno_acumulado_1m": info.get("retorno_acumulado_1m", np.nan),
                "regime_mercado": info.get("regime_mercado", sh.market_regime(path)),
                "subtipo_mercado": info.get("subtipo_mercado", ""),
            }
        )
    return pd.DataFrame(rows)



def make_extended_d3(original_d3):
    def extended_d3(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
        if frame.empty:
            return frame
        profile = settings.get("_runtime_downturn_profile", {}) or {}
        active_signal_is_v3 = profile.get("subtipo_queda", "") == "alta"
        if not active_signal_is_v3:
            return frame
        subtype = str(settings.get("_runtime_beta_target_subtipo", "")).strip().lower()
        allowed = {"favoravel_oportunidade", "favoravel_amplo", "favoravel_esticado", "favoravel_cansado", "cansado", "favoravel_limpo", "favoravel_indefinido"}
        if subtype not in allowed:
            return frame
        patched = dict(settings)
        patched["_runtime_beta_target_subtipo"] = "favoravel_oportunidade"
        out = original_d3(frame, patched)
        if "liberado_por_d3" in out.columns:
            mask = out["liberado_por_d3"].fillna(False).map(sh.to_bool)
            if mask.any():
                out.loc[mask, "d3_extendida_subtipo_original"] = subtype
                out.loc[mask, "d3_extendida_sinal_ativo"] = "V3_MOMENTUM"
                if "shadow_beta_target_motivos" in out.columns:
                    out.loc[mask, "shadow_beta_target_motivos"] = out.loc[mask, "shadow_beta_target_motivos"].fillna("").astype(str).map(
                        lambda txt: sh.append_token(txt, "d3_estendida_v3_favoravel")
                    )
        return out
    return extended_d3
def load_expost_6(months: dict[str, str]) -> pd.DataFrame:
    frames = []
    if sh.EXPOST_FILE.exists():
        try:
            base = pd.read_excel(sh.EXPOST_FILE, sheet_name="Universo Expost")
            frames.append(base[base["mes"].astype(str).isin([m for m in months if m != "2026-01"])].copy())
        except Exception:
            pass
    jan_path = ROOT / "output" / "excel" / months["2026-01"]
    frames.append(_month_expost_from_workbook("2026-01", jan_path))
    expost = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return expost[expost["mes"].astype(str).isin(months.keys())].copy()


def portfolio_detail_rows(results: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for mes, result in results.items():
        metrics = result.get("metrics", {})
        portfolio = result.get("portfolio", pd.DataFrame())
        if portfolio.empty:
            rows.append(
                {
                    "mes": mes,
                    "status_sombra": metrics.get("status_carteira", ""),
                    "ticker": "",
                    "peso_recomendado": np.nan,
                }
            )
            continue
        for _, row in portfolio.iterrows():
            out = row.to_dict()
            out["mes"] = mes
            out["status_sombra"] = metrics.get("status_carteira", "")
            out["subtipo_queda"] = metrics.get("subtipo_queda", "")
            out["sinal_quedas_aplicado"] = metrics.get("sinal_quedas_aplicado", "")
            out["beta_target"] = metrics.get("beta_target", np.nan)
            out["beta_carteira"] = metrics.get("beta_carteira", np.nan)
            rows.append(out)
    return pd.DataFrame(rows)



def d3_liberated_rows(results: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for mes, result in results.items():
        candidates = result.get("candidates", pd.DataFrame())
        portfolio = result.get("portfolio", pd.DataFrame())
        weights = portfolio.set_index("ticker")["peso_recomendado"].to_dict() if not portfolio.empty and "ticker" in portfolio and "peso_recomendado" in portfolio else {}
        if candidates.empty or "liberado_por_d3" not in candidates.columns:
            continue
        mask = candidates["liberado_por_d3"].fillna(False).map(sh.to_bool)
        cols = [c for c in [
            "ticker", "nome", "setor", "status_para_risco", "categoria_elegibilidade", "tipo_timing", "tipo_watchlist",
            "motivo_bloqueio_original_d3", "motivo_bloqueio_otimizacao", "penalizacoes_otimizacao",
            "d3_extendida_subtipo_original", "d3_extendida_sinal_ativo", "nota_final", "forca_relativa_score",
            "retorno_medio", "beta", "roe", "margem_liquida", "pl_atual",
        ] if c in candidates.columns]
        for _, row in candidates.loc[mask, cols].iterrows():
            out = row.to_dict()
            out["mes"] = mes
            out["peso_final_sombra"] = weights.get(out.get("ticker"), 0.0)
            rows.append(out)
    return pd.DataFrame(rows)
def alpha_summary(shadow_rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(shadow_rows)
    rows = []
    for _, row in df.iterrows():
        rows.append(
            {
                "mes": row["mes"],
                "retorno_sombra": row["retorno_expost_sombra"],
                "retorno_ibov": row["retorno_expost_ibov"],
                "alfa": row["alfa_sombra"],
                "status_sombra": row["status_sombra"],
                "subtipo_queda": row["subtipo_queda"],
                "sinal_quedas_aplicado": row["sinal_quedas_aplicado"],
                "beta_target": row["beta_target"],
                "beta_realizado": row["beta_carteira_sombra"],
                "alfa_anterior": PREVIOUS_ALPHA.get(row["mes"], np.nan),
                "delta_alfa_vs_anterior": row["alfa_sombra"] - PREVIOUS_ALPHA.get(row["mes"], np.nan),
            }
        )
    rows.append(
        {
            "mes": "ACUMULADO_6_MESES",
            "retorno_sombra": _compound(df["retorno_expost_sombra"]),
            "retorno_ibov": _compound(df["retorno_expost_ibov"]),
            "alfa": _compound(df["retorno_expost_sombra"]) - _compound(df["retorno_expost_ibov"]),
            "status_sombra": "",
            "subtipo_queda": "",
            "sinal_quedas_aplicado": "",
            "beta_target": np.nan,
            "beta_realizado": np.nan,
            "alfa_anterior": PREVIOUS_ALPHA.get("ACUMULADO_6_MESES", np.nan),
            "delta_alfa_vs_anterior": (_compound(df["retorno_expost_sombra"]) - _compound(df["retorno_expost_ibov"])) - PREVIOUS_ALPHA.get("ACUMULADO_6_MESES", np.nan),
        }
    )
    return pd.DataFrame(rows)


def regime_summary(shadow_rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(shadow_rows).set_index("mes")
    rows = []
    for grupo, meses in REGIME_GROUPS.items():
        subset = df.loc[[m for m in meses if m in df.index]]
        rows.append(
            {
                "tipo_regime": grupo,
                "meses": ", ".join(meses),
                "retorno_sombra_acumulado": _compound(subset["retorno_expost_sombra"]) if not subset.empty else np.nan,
                "retorno_ibov_acumulado": _compound(subset["retorno_expost_ibov"]) if not subset.empty else np.nan,
                "alfa_acumulado": (
                    _compound(subset["retorno_expost_sombra"]) - _compound(subset["retorno_expost_ibov"])
                    if not subset.empty
                    else np.nan
                ),
                "sinais_usados": "; ".join(sorted(set(str(v) for v in subset["sinal_quedas_aplicado"].dropna().tolist()))),
            }
        )
    return pd.DataFrame(rows)


def write_workbook(
    anchor_rows: list[dict[str, Any]],
    shadow_rows: list[dict[str, Any]],
    anchor_results: dict[str, Any],
    shadow_results: dict[str, Any],
    validation_rows: list[dict[str, Any]],
) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        alpha_summary(shadow_rows).to_excel(writer, sheet_name="resumo_alfa_por_mes_e_acumulado", index=False)
        regime_summary(shadow_rows).to_excel(writer, sheet_name="resumo_por_tipo_regime", index=False)
        portfolio_detail_rows(shadow_results).to_excel(writer, sheet_name="carteiras_por_mes", index=False)
        pd.DataFrame(validation_rows).to_excel(writer, sheet_name="validacao_retorno", index=False)
        pd.DataFrame(shadow_rows).to_excel(writer, sheet_name="shadow_vs_real_detalhe", index=False)
        d3_liberated_rows(shadow_results).to_excel(writer, sheet_name="d3_extendida_liberadas", index=False)
        run_weight_unit_tests().to_excel(writer, sheet_name="teste_unitario_pesos", index=False)
        trace_cols = ["ticker", "setor", "beta", "nota_final", "forca_relativa_score", "_shadow_signal_v3_norm", "shadow_tamanho_livre_sinal_v3", "sinal_v3_original_tamanho_livre", "sinal_v3_ajustado_beta_tamanho_livre", "peso_antes_teto_tamanho_livre", "peso_v3_sem_beta_tamanho_livre", "peso_recomendado", "peso_maximo_permitido_ativo", "teto_tamanho_livre_aplicado", "shadow_sinal_quedas_aplicado"]
        trace_frames = []
        for trace_mes in ("2026-01", "2026-06"):
            trace_port = shadow_results.get(trace_mes, {}).get("portfolio", pd.DataFrame())
            if not trace_port.empty:
                trace = trace_port[[c for c in trace_cols if c in trace_port.columns]].copy()
                trace.insert(0, "mes", trace_mes)
                trace_frames.append(trace)
        if trace_frames:
            pd.concat(trace_frames, ignore_index=True).to_excel(writer, sheet_name="trace_pesos_jan_jun", index=False)
        else:
            pd.DataFrame().to_excel(writer, sheet_name="trace_pesos_jan_jun", index=False)
        sh._result_sheets(writer, "anchor", anchor_results)
        sh._result_sheets(writer, "shadow", shadow_results)


def main() -> None:
    sh.MONTHS = MONTHS_6
    logs: list[str] = []

    def log(message: str) -> None:
        print(message)
        logs.append(message)

    base_settings = load_settings()
    expost = load_expost_6(MONTHS_6)
    log("Datas/retornos ex-post carregados:")
    for mes in MONTHS_6:
        month = expost[expost["mes"].astype(str).eq(mes)]
        ibov = sh.ibov_return(expost, mes)
        if not month.empty:
            dates = month.iloc[0]
            log(
                f"  {mes}: inicio={dates.get('data_inicio_performance', '')} | avaliacao={dates.get('data_avaliacao', '')} | IBOV={_pct(ibov)} | ativos={len(month)}"
            )
        else:
            log(f"  {mes}: ex-post ausente")

    log("TESTE-ANCORA: configuracao consolidada desligada")
    anchor_rows: list[dict[str, Any]] = []
    anchor_results: dict[str, Any] = {}
    all_pass = True
    for mes in MONTHS_6:
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

    if not all_pass:
        write_workbook(anchor_rows, [], anchor_results, {}, [])
        LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
        log(f"ANCORA FALHOU. Arquivo diagnostico: {OUTPUT_FILE}")
        log(f"Log: {LOG_FILE}")
        return

    original_downturn_profile = sh.downturn_regime_profile
    original_beta_target_profile = sh.beta_target_profile
    original_d3 = sh.technical_veto_to_penalty_in_opportunity
    sh.build_free_size_portfolio = consolidated_build_free_size_portfolio
    sh.technical_veto_to_penalty_in_opportunity = make_extended_d3(original_d3)

    def consolidated_beta_target_profile(path: Path, settings: dict) -> dict:
        base = dict(original_beta_target_profile(path, settings))
        match = re.search(r"2026_(\d{2})", path.name)
        mes_key = f"2026-{match.group(1)}" if match else ""
        base.update(CONSOLIDATED_BETA_PROFILE.get(mes_key, {}))
        return base

    sh.beta_target_profile = consolidated_beta_target_profile

    unit_df = run_weight_unit_tests()
    log("TESTE UNITARIO PESOS POS-TETO:")
    for _, unit_row in unit_df.iterrows():
        log("  {0} | {1}: sinal={2:.2f} -> peso={3:.2%}".format(unit_row["teste"], unit_row["ativo"], unit_row["sinal"], unit_row["peso_pos_teto"]))

    def consolidated_downturn_profile(path: Path, settings: dict) -> dict:
        base = original_downturn_profile(path, settings)
        match = re.search(r"2026_(\d{2})", path.name)
        mes_key = f"2026-{match.group(1)}" if match else ""
        subtype, reason = CONSOLIDATED_SIGNAL_PROFILE.get(mes_key, (base.get("subtipo_queda", ""), base.get("motivo_subtipo_queda", "")))
        base = dict(base)
        base["subtipo_queda"] = subtype
        base["motivo_subtipo_queda"] = reason
        return base

    sh.downturn_regime_profile = consolidated_downturn_profile

    log(
        "ANCORA PASSOU. Rodando consolidada: tamanho livre + V3 + beta-alvo por regime "
        f"(lambda_beta={LAMBDA_BETA_CONSOLIDADO}) + Sinal A defensivo em quedas."
    )
    shadow_rows: list[dict[str, Any]] = []
    shadow_results: dict[str, Any] = {}
    for mes in MONTHS_6:
        path = sh.workbook_path(mes)
        result = sh.run_free_size_for_month(
            mes,
            path,
            base_settings,
            lambda_beta=LAMBDA_BETA_CONSOLIDADO,
            downturn_signal="SINAL_A_DEFENSIVO",
        )
        row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=True)
        row["cenario_objetivo"] = "CONSOLIDADA_TAMANHO_LIVRE_V3_BETA_REGIME_SINAL_A_QUEDAS"
        row["lambda_beta_consolidado"] = LAMBDA_BETA_CONSOLIDADO
        cand_result = result.get("candidates", pd.DataFrame())
        d3_mask = cand_result.get("liberado_por_d3", pd.Series(False, index=cand_result.index)).fillna(False).map(sh.to_bool) if not cand_result.empty else pd.Series(dtype=bool)
        d3_count = int(d3_mask.sum()) if not d3_mask.empty else 0
        d3_tickers = ", ".join(cand_result.loc[d3_mask, "ticker"].astype(str).tolist()) if d3_count and "ticker" in cand_result else ""
        row["d3_extendida_liberadas"] = d3_count
        row["d3_extendida_tickers"] = d3_tickers
        shadow_rows.append(row)
        shadow_results[mes] = result

        log(
            f"  {mes}: status={row['status_sombra']} | subtipo={row['subtipo_queda']} | "
            f"sinal={row['sinal_quedas_aplicado']} | beta_alvo={row['beta_target']:.2f} | "
            f"beta_real={row['beta_carteira_sombra']:.2f} | pesos={row['tickers_pesos_sombra']} | "
            f"ret={_pct(row['retorno_expost_sombra'])} | IBOV={_pct(row['retorno_expost_ibov'])} | "
            f"alfa={_pct(row['alfa_sombra'])} | d3_liberadas={d3_count}"
        )

    validation_rows = sh.free_size_validation_rows(shadow_results, expost)
    validation = pd.DataFrame(validation_rows)
    if not validation.empty:
        if validation["alguma_acao_acima_25pct"].any():
            log("REGRESSAO: houve acao acima de 25%.")
        if validation["violou_max_2_por_setor"].any():
            log("REGRESSAO: houve violacao de maximo 2 ativos por setor.")
        if validation["tem_deterioracao_fundamental_real"].any():
            log("REGRESSAO: houve ativo com deterioracao fundamental real.")
        if validation["carteira_com_menos_de_5_acoes"].any():
            log("REGRESSAO: houve carteira formada com menos de 5 acoes.")
        if (validation["diferenca_retorno"].abs() > 0.0001).any():
            log("REGRESSAO: retorno reportado nao bate com peso x retorno dos ativos.")

    summary = alpha_summary(shadow_rows)
    accum = summary[summary["mes"].eq("ACUMULADO_6_MESES")].iloc[0]
    log(f"ALFA ACUMULADO 6 MESES: {_pct(accum['alfa'])} | retorno sombra={_pct(accum['retorno_sombra'])} | IBOV={_pct(accum['retorno_ibov'])}")

    df_shadow = pd.DataFrame(shadow_rows).set_index("mes")
    high_months = df_shadow.loc[["2026-01", "2026-02"]]
    high_alpha = _compound(high_months["retorno_expost_sombra"]) - _compound(high_months["retorno_expost_ibov"])
    high_signals = set(high_months["sinal_quedas_aplicado"].astype(str).tolist())
    log(f"CHECAGEM ALTAS jan+fev: sinais={sorted(high_signals)} | alfa acumulado={_pct(high_alpha)}")
    for mes, reference in {"2026-03": -0.0064, "2026-04": -0.0294}.items():
        actual = float(df_shadow.loc[mes, "alfa_sombra"])
        log(f"CHECAGEM {mes}: alfa consolidado={_pct(actual)} vs V3 puro referencia={_pct(reference)}")
    log(f"CHECAGEM MAIO: alfa={_pct(df_shadow.loc['2026-05', 'alfa_sombra'])} | beta_real={df_shadow.loc['2026-05', 'beta_carteira_sombra']:.2f}")

    write_workbook(anchor_rows, shadow_rows, anchor_results, shadow_results, validation_rows)
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()












