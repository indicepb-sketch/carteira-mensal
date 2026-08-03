from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import optimizer as opt  # noqa: E402
from optimizer import optimize_weights, validate_portfolio  # noqa: E402
from scoring import score_assets  # noqa: E402
from utils import load_settings  # noqa: E402

MONTHS = {
    "2026-02": "carteira_recomendada_2026_02_v4.xlsx",
    "2026-03": "carteira_recomendada_2026_03_v4.xlsx",
    "2026-04": "carteira_recomendada_2026_04_v2.xlsx",
    "2026-05": "carteira_recomendada_2026_05_v3.xlsx",
    "2026-06": "carteira_recomendada_2026_06_v4.xlsx",
}
EXPECTED_JUNE = {
    "PETR3.SA": 0.20,
    "ENEV3.SA": 0.20,
    "CPLE3.SA": 0.10,
    "GGBR4.SA": 0.10,
    "GOAU4.SA": 0.10,
    "ABEV3.SA": 0.10,
    "BRAV3.SA": 0.10,
    "CSMG3.SA": 0.10,
}
EXCEL_DIR = ROOT / "output" / "excel"
EXPOST_FILE = EXCEL_DIR / "universo_expost_consolidado.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_simulacao.xlsx"
PARTIAL_OUTPUT_FILE = EXCEL_DIR / "shadow_simulacao_carteira_parcial.xlsx"
PARTIAL_LOG_FILE = EXCEL_DIR / "shadow_simulacao_carteira_parcial.log"
BETA_OUTPUT_FILE = EXCEL_DIR / "shadow_simulacao_beta.xlsx"
BETA_LOG_FILE = EXCEL_DIR / "shadow_simulacao_beta.log"
OBJ_OUTPUT_FILE = EXCEL_DIR / "shadow_simulacao_objetivo.xlsx"
OBJ_LOG_FILE = EXCEL_DIR / "shadow_simulacao_objetivo.log"
COMPOSITION_OUTPUT_FILE = EXCEL_DIR / "shadow_simulacao_composicao.xlsx"
COMPOSITION_LOG_FILE = EXCEL_DIR / "shadow_simulacao_composicao.log"
FREE_SIZE_OUTPUT_FILE = EXCEL_DIR / "shadow_simulacao_tamanho_livre.xlsx"
FREE_SIZE_LOG_FILE = EXCEL_DIR / "shadow_simulacao_tamanho_livre.log"
BETA_REGIME_OUTPUT_FILE = EXCEL_DIR / "shadow_simulacao_beta_regime.xlsx"
BETA_REGIME_LOG_FILE = EXCEL_DIR / "shadow_simulacao_beta_regime.log"
DOWNTURN_SIGNAL_OUTPUT_FILE = EXCEL_DIR / "shadow_simulacao_sinal_quedas.xlsx"
DOWNTURN_SIGNAL_LOG_FILE = EXCEL_DIR / "shadow_simulacao_sinal_quedas.log"
STRICT_REVERSAL_OUTPUT_FILE = EXCEL_DIR / "shadow_simulacao_reversao_estrito.xlsx"
STRICT_REVERSAL_LOG_FILE = EXCEL_DIR / "shadow_simulacao_reversao_estrito.log"
RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"


def norm_col(name: Any) -> str:
    text = str(name).strip().lower()
    for old, new in [("ã", "a"), ("á", "a"), ("à", "a"), ("â", "a"), ("é", "e"), ("ê", "e"), ("í", "i"), ("ó", "o"), ("ô", "o"), ("ú", "u"), ("ç", "c")]:
        text = text.replace(old, new)
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    seen: dict[str, int] = {}
    cols = []
    for col in out.columns:
        base = norm_col(col)
        count = seen.get(base, 0)
        seen[base] = count + 1
        cols.append(base if count == 0 else f"{base}_{count}")
    out.columns = cols
    return out


def read_sheet(path: Path, sheet: str, normalize: bool = True) -> pd.DataFrame:
    try:
        df = pd.read_excel(path, sheet_name=sheet)
        return normalize_columns(df) if normalize else df
    except Exception:
        return pd.DataFrame()


def fields_dict(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {}
    key_col = "campo" if "campo" in df.columns else "metrica" if "metrica" in df.columns else None
    if key_col and "valor" in df.columns:
        return {str(row[key_col]): row["valor"] for _, row in df.iterrows() if pd.notna(row.get(key_col))}
    return {}


def first_value(source: dict[str, Any], keys: list[str], default: Any = "") -> Any:
    for key in keys:
        value = source.get(key)
        if pd.notna(value) and str(value).strip() != "":
            return value
    return default


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "sim", "yes", "verdadeiro"}


def to_float(value: Any, default: float = np.nan) -> float:
    if pd.isna(value):
        return default
    if isinstance(value, str):
        text = value.strip().replace("%", "").replace(",", ".")
        try:
            number = float(text)
            return number / 100.0 if "%" in value else number
        except Exception:
            return default
    try:
        return float(value)
    except Exception:
        return default


def remove_tokens(text: Any, tokens: list[str]) -> str:
    parts = [p.strip() for p in str(text or "").split(";") if p.strip()]
    return "; ".join(dict.fromkeys(part for part in parts if not any(token in part.lower() for token in tokens)))


def append_token(text: Any, token: str) -> str:
    parts = [p.strip() for p in str(text or "").split(";") if p.strip()]
    parts.append(token)
    return "; ".join(dict.fromkeys(parts))


def workbook_path(mes: str) -> Path:
    path = EXCEL_DIR / MONTHS[mes]
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def read_covariance(path: Path) -> pd.DataFrame:
    cov = read_sheet(path, "Matriz de Covariancia", normalize=False)
    if cov.empty:
        return cov
    first = cov.columns[0]
    cov = cov.rename(columns={first: "ticker"}).set_index("ticker")
    cov.index = cov.index.astype(str)
    cov.columns = cov.columns.astype(str)
    return cov.apply(pd.to_numeric, errors="coerce")



def _raw_price_file(ticker: str) -> Path | None:
    stem = str(ticker).replace(".SA", "_SA").replace("^", "^").replace(".", "_")
    matches = sorted((ROOT / "data" / "raw").glob(f"prices_{stem}*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _read_cached_adjusted_close(ticker: str) -> pd.Series:
    path = _raw_price_file(ticker)
    if path is None:
        return pd.Series(dtype=float)
    try:
        frame = pd.read_csv(path, skiprows=[1, 2])
    except Exception:
        return pd.Series(dtype=float)
    date_col = "Price" if "Price" in frame.columns else "Date" if "Date" in frame.columns else frame.columns[0]
    price_col = "Adj Close" if "Adj Close" in frame.columns else "Close" if "Close" in frame.columns else None
    if price_col is None:
        return pd.Series(dtype=float)
    dates = pd.to_datetime(frame[date_col], errors="coerce")
    values = pd.to_numeric(frame[price_col], errors="coerce")
    series = pd.Series(values.to_numpy(float), index=dates).dropna()
    series = series[~series.index.isna()].sort_index()
    return series[~series.index.duplicated(keep="last")]


def _risk_returns_from_cache(ticker: str, start: Any, end: Any) -> pd.Series:
    prices = _read_cached_adjusted_close(ticker)
    if prices.empty:
        return pd.Series(dtype=float)
    start_ts = pd.to_datetime(start, errors="coerce")
    end_ts = pd.to_datetime(end, errors="coerce")
    if pd.isna(start_ts) or pd.isna(end_ts):
        return pd.Series(dtype=float)
    before_or_inside = prices[prices.index <= end_ts]
    if before_or_inside.empty:
        return pd.Series(dtype=float)
    window = before_or_inside[before_or_inside.index >= start_ts]
    prev = before_or_inside[before_or_inside.index < start_ts].tail(1)
    selected = pd.concat([prev, window]).sort_index()
    if len(selected) < 3:
        return pd.Series(dtype=float)
    returns = np.log(selected / selected.shift(1)).dropna()
    returns.name = ticker
    return returns


def expand_covariance_from_cache(candidates: pd.DataFrame, covariance: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    if candidates.empty or covariance.empty or "ticker" not in candidates.columns:
        return candidates, covariance, ""
    missing = [ticker for ticker in candidates["ticker"].astype(str).tolist() if ticker not in covariance.index]
    needs_metric = candidates[pd.to_numeric(candidates.get("retorno_medio", pd.Series(np.nan, index=candidates.index)), errors="coerce").isna()]["ticker"].astype(str).tolist() if "retorno_medio" in candidates else []
    needed = list(dict.fromkeys(list(covariance.index.astype(str)) + missing + needs_metric))
    if not missing and not needs_metric:
        return candidates, covariance, ""
    start = first_value(fields_dict(candidates[["janela_risco_inicio"]].rename(columns={"janela_risco_inicio": "campo"})) if False else {}, [], None)
    if "janela_risco_inicio" in candidates.columns:
        starts = candidates["janela_risco_inicio"].dropna()
        start = starts.iloc[0] if not starts.empty else None
    else:
        start = None
    if "janela_risco_fim" in candidates.columns:
        ends = candidates["janela_risco_fim"].dropna()
        end = ends.iloc[0] if not ends.empty else None
    else:
        end = None
    if start is None or end is None:
        return candidates, covariance, "sem janela_risco_inicio/fim para expandir covariancia"
    returns = []
    unavailable = []
    for ticker in needed:
        series = _risk_returns_from_cache(ticker, start, end)
        if series.empty:
            unavailable.append(ticker)
        else:
            returns.append(series)
    if not returns:
        return candidates, covariance, "sem cache de retornos para expansao"
    returns_df = pd.concat(returns, axis=1).dropna(how="all")
    valid_cols = [col for col in returns_df.columns if returns_df[col].dropna().shape[0] >= 20]
    returns_df = returns_df[valid_cols]
    if returns_df.empty:
        return candidates, covariance, "cache insuficiente para expansao"
    expanded_cov = returns_df.cov(ddof=0).fillna(0)
    common = [ticker for ticker in covariance.index.astype(str) if ticker in expanded_cov.index]
    if common:
        expanded_cov.loc[common, common] = covariance.reindex(index=common, columns=common).fillna(expanded_cov.loc[common, common])
    out = candidates.copy()
    for ticker in valid_cols:
        mask = out["ticker"].astype(str).eq(ticker)
        if not mask.any():
            continue
        ret = float(returns_df[ticker].dropna().mean())
        var = float(returns_df[ticker].dropna().var(ddof=0))
        std = float(np.sqrt(var))
        cv = float(abs(std / ret)) if ret != 0 else np.nan
        for col, value in [("retorno_medio", ret), ("desvio_padrao", std), ("variancia", var), ("cv", cv)]:
            if col not in out.columns:
                out[col] = np.nan
            current = pd.to_numeric(out.loc[mask, col], errors="coerce")
            out.loc[mask & current.isna(), col] = value
    available_missing = [ticker for ticker in missing if ticker in valid_cols]
    msg = f"covariancia_expandida_cache: adicionados={available_missing}; indisponiveis={unavailable}"
    return out, expanded_cov, msg
def market_regime(path: Path) -> str:
    fields = fields_dict(read_sheet(path, "Regime Mercado"))
    return str(first_value(fields, ["mercado_classificacao", "regime_mercado"], "")).strip().lower()


def real_status(path: Path) -> str:
    fields = fields_dict(read_sheet(path, "Validacao Final"))
    return str(first_value(fields, ["status da carteira", "status_carteira"], "")).strip()


def real_portfolio(path: Path) -> pd.DataFrame:
    return read_sheet(path, "Resumo da Carteira")


def weights_map(portfolio: pd.DataFrame) -> dict[str, float]:
    if portfolio.empty or "ticker" not in portfolio.columns:
        return {}
    weight_col = "peso_recomendado" if "peso_recomendado" in portfolio.columns else "peso_final" if "peso_final" in portfolio.columns else None
    if not weight_col:
        return {}
    out = {}
    for _, row in portfolio.iterrows():
        w = to_float(row.get(weight_col), 0.0)
        if w > 1e-9:
            out[str(row["ticker"])] = round(float(w), 10)
    return out


def format_weights(mapping: dict[str, float]) -> str:
    return "; ".join(f"{ticker}: {weight:.2%}" for ticker, weight in mapping.items())


def same_weights(a: dict[str, float], b: dict[str, float], tol: float = 1e-6) -> bool:
    return set(a) == set(b) and all(abs(a[t] - b[t]) <= tol for t in a)


def portfolio_expost_return(portfolio: pd.DataFrame, expost: pd.DataFrame, mes: str) -> float:
    mapping = weights_map(portfolio)
    if not mapping:
        return np.nan
    panel = expost[expost["mes"].astype(str).eq(mes)].set_index("ticker")
    total = 0.0
    has = False
    for ticker, weight in mapping.items():
        if ticker in panel.index:
            ret = panel.loc[ticker, "retorno_realizado_periodo"]
            if pd.notna(ret):
                total += weight * float(ret)
                has = True
    return total if has else np.nan



def normalize_portfolio_weights(portfolio: pd.DataFrame) -> pd.DataFrame:
    if portfolio.empty or "ticker" not in portfolio.columns:
        return portfolio
    out = portfolio.copy()
    if "peso_recomendado" in out.columns:
        weights = pd.to_numeric(out["peso_recomendado"], errors="coerce")
    elif "peso_final" in out.columns:
        weights = pd.to_numeric(out["peso_final"], errors="coerce")
        out["peso_recomendado"] = weights
    else:
        return out
    if "peso_final" in out.columns:
        original = pd.to_numeric(out["peso_final"], errors="coerce")
        if not original.fillna(0).round(12).equals(weights.fillna(0).round(12)):
            out["peso_final_original_shadow"] = original
    out["peso_recomendado"] = weights
    out["peso_final"] = weights
    return out


def ibov_return(expost: pd.DataFrame, mes: str) -> float:
    group = expost[expost["mes"].astype(str).eq(mes)]
    vals = group["retorno_ibov_periodo"].dropna() if not group.empty and "retorno_ibov_periodo" in group else pd.Series(dtype=float)
    return float(vals.iloc[0]) if not vals.empty else np.nan


def beta_target_settings(settings: dict) -> dict:
    shadow = settings.get("shadow", {})
    return {
        "enabled": bool(shadow.get("enable_beta_target", False)),
        "lambda": float(shadow.get("beta_target_lambda", 1.0)),
        "by_regime": shadow.get("beta_target_by_regime", {}),
    }


def objetivo_retorno_settings(settings: dict) -> dict:
    shadow = settings.get("shadow", {})
    return {
        "enabled": bool(shadow.get("enable_objetivo_retorno", False)),
        "variant": str(shadow.get("objetivo_retorno_variant", "V1")).upper(),
        "lambda_cv": float(shadow.get("objetivo_retorno_lambda_cv", 0.5)),
        "lambda_beta": float(shadow.get("objetivo_retorno_lambda_beta", 0.0)),
    }



def composition_settings(settings: dict) -> dict:
    shadow = settings.get("shadow", {})
    return {
        "enabled": bool(shadow.get("enable_composicao_ampliada", False)),
        "candidate_counts": [int(v) for v in shadow.get("composicao_candidate_counts", [5, 6, 8, 10, 12, 15])],
        "base_topn": int(shadow.get("composicao_base_topn", 25)),
        "expanded_topn": int(shadow.get("composicao_expanded_topn", 30)),
        "extra_min_nota_final": float(shadow.get("composicao_extra_min_nota_final", 50)),
        "quality_cap_strong": float(shadow.get("composicao_quality_cap_strong", 0.20)),
        "quality_cap_alert": float(shadow.get("composicao_quality_cap_alert", 0.15)),
        "quality_cap_watchlist": float(shadow.get("composicao_quality_cap_watchlist", 0.15)),
        "quality_cap_late_or_speculative": float(shadow.get("composicao_quality_cap_late_or_speculative", 0.05)),
    }


def has_timing_alert(row: pd.Series) -> bool:
    timing_quality = str(row.get("qualidade_do_timing", "")).lower()
    timing_type = str(row.get("tipo_timing", "")).lower()
    alerts = str(row.get("alertas_nao_bloqueantes", "")).lower() + ";" + str(row.get("penalizacoes_otimizacao", "")).lower()
    return any(token in timing_quality for token in ["alerta", "tardio"]) or "esticado" in timing_type or "sinal_tardio" in alerts


def has_good_fundamentals(row: pd.Series) -> bool:
    quality = str(row.get("qualidade_fundamentalista", "")).strip().lower()
    sector_quality = str(row.get("classificacao_fundamentalista_setorial", "")).strip().lower()
    if is_real_deterioration(row) or to_bool(row.get("fundamento_bloqueante", False)):
        return False
    return quality in {"otima", "boa", "aceitavel"} or sector_quality in {"forte_relativo_ao_setor", "bom_relativo_ao_setor", "neutro_relativo_ao_setor"}


def is_late_or_speculative(row: pd.Series) -> bool:
    timing_quality = str(row.get("qualidade_do_timing", "")).lower()
    timing_type = str(row.get("tipo_timing", "")).lower()
    profile = str(row.get("perfil_risco_empresa", "")).lower()
    watch_reason = str(row.get("motivo_tipo_watchlist", "")).lower() + ";" + str(row.get("motivo_watchlist", "")).lower()
    return (
        "timing_tardio" in timing_quality
        or "tardio" in timing_type
        or "tardio" in watch_reason
        or any(token in profile for token in ["especulativo", "turnaround"])
    )


def apply_expanded_composition_caps(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if frame.empty or not composition_settings(settings)["enabled"]:
        return frame
    cfg = composition_settings(settings)
    out = frame.copy()
    note = pd.to_numeric(out.get("nota_final", pd.Series(np.nan, index=out.index)), errors="coerce").fillna(pd.to_numeric(out.get("nota_preliminar_ajustada", pd.Series(0, index=out.index)), errors="coerce")).fillna(0)
    good_fund = out.apply(has_good_fundamentals, axis=1)
    late_or_spec = out.apply(is_late_or_speculative, axis=1)
    timing_alert = out.apply(has_timing_alert, axis=1)
    timing_quality = out.get("qualidade_do_timing", pd.Series("", index=out.index)).fillna("").astype(str).str.lower()
    watch_type = out.get("tipo_watchlist", pd.Series("", index=out.index)).fillna("").astype(str).str.lower()

    healthy = note.ge(70) & good_fund & ~timing_alert & ~late_or_spec
    alert_quality = note.ge(50) & good_fund & timing_quality.eq("timing_com_alerta") & ~late_or_spec
    watch_quality = note.ge(50) & good_fund & watch_type.eq("watchlist_flexivel") & ~late_or_spec

    for col in ["peso_maximo_timing_com_alerta", "peso_maximo_turnaround_especulativo"]:
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out["shadow_composicao_cap_qualidade"] = np.nan
    out["shadow_composicao_motivo_cap"] = ""

    out.loc[healthy, "peso_maximo_timing_com_alerta"] = np.maximum(out.loc[healthy, "peso_maximo_timing_com_alerta"].fillna(0), cfg["quality_cap_strong"])
    out.loc[healthy, "shadow_composicao_cap_qualidade"] = cfg["quality_cap_strong"]
    out.loc[healthy, "shadow_composicao_motivo_cap"] = "nota>=70_timing_saudavel_sem_deterioracao"

    out.loc[alert_quality, "peso_maximo_timing_com_alerta"] = np.maximum(out.loc[alert_quality, "peso_maximo_timing_com_alerta"].fillna(0), cfg["quality_cap_alert"])
    out.loc[alert_quality, "shadow_composicao_cap_qualidade"] = cfg["quality_cap_alert"]
    out.loc[alert_quality, "shadow_composicao_motivo_cap"] = "nota>=50_timing_com_alerta_fundamentos_bons"

    out.loc[watch_quality, "peso_maximo_timing_com_alerta"] = np.maximum(out.loc[watch_quality, "peso_maximo_timing_com_alerta"].fillna(0), cfg["quality_cap_watchlist"])
    out.loc[watch_quality, "shadow_composicao_cap_qualidade"] = cfg["quality_cap_watchlist"]
    out.loc[watch_quality, "shadow_composicao_motivo_cap"] = "watchlist_flexivel_nota>=50_fundamentos_bons"

    out.loc[late_or_spec, "peso_maximo_timing_com_alerta"] = cfg["quality_cap_late_or_speculative"]
    out.loc[late_or_spec, "peso_maximo_turnaround_especulativo"] = cfg["quality_cap_late_or_speculative"]
    out.loc[late_or_spec, "shadow_composicao_cap_qualidade"] = cfg["quality_cap_late_or_speculative"]
    out.loc[late_or_spec, "shadow_composicao_motivo_cap"] = "timing_tardio_ou_perfil_especulativo_mantido_5pct"

    out["shadow_composicao_timing_tardio_ou_especulativo"] = late_or_spec
    return out


def free_size_settings(settings: dict) -> dict:
    shadow = settings.get("shadow", {})
    return {
        "enabled": bool(shadow.get("enable_carteira_tamanho_livre", False)),
        "individual_cap": float(shadow.get("tamanho_livre_teto_individual", 0.25)),
        "min_assets": int(shadow.get("tamanho_livre_minimo_acoes", 5)),
        "signal_floor": float(shadow.get("tamanho_livre_signal_floor", 0.01)),
    }


def capped_proportional_weights(signal: pd.Series, cap: float, floor: float = 0.01) -> pd.Series:
    values = pd.to_numeric(signal, errors="coerce").fillna(0).clip(lower=0).astype(float)
    if values.empty:
        return values
    values = values + max(float(floor), 0.0)
    weights = values / values.sum()
    capped = pd.Series(0.0, index=weights.index, dtype=float)
    free = pd.Series(True, index=weights.index)
    remaining = 1.0
    remaining_signal = values.copy()
    for _ in range(len(values) + 2):
        if not free.any():
            break
        alloc = remaining * remaining_signal[free] / remaining_signal[free].sum()
        over = alloc > cap + 1e-12
        if not over.any():
            capped.loc[free] = alloc
            remaining = 0.0
            break
        over_idx = alloc[over].index
        capped.loc[over_idx] = cap
        free.loc[over_idx] = False
        remaining = 1.0 - capped.sum()
        if remaining <= 1e-12:
            break
    if capped.sum() > 0:
        capped = capped / capped.sum()
    return capped


def selected_free_size_pool(scored: pd.DataFrame, settings: dict) -> tuple[pd.DataFrame, list[str]]:
    reasons: list[str] = []
    if scored.empty:
        return scored, ["nenhum ativo liberado para tamanho livre"]
    out = scored.copy()
    out["shadow_tamanho_livre_motivo_exclusao"] = ""
    out["shadow_tamanho_livre_aprovada"] = True

    deterioration = out.apply(is_real_deterioration, axis=1)
    out.loc[deterioration, "shadow_tamanho_livre_aprovada"] = False
    out.loc[deterioration, "shadow_tamanho_livre_motivo_exclusao"] = "deterioracao_fundamental_real"

    signal_col = "_shadow_objetivo_sinal_norm" if "_shadow_objetivo_sinal_norm" in out.columns else "score_prioridade_otimizacao"
    out["shadow_tamanho_livre_sinal_v3"] = pd.to_numeric(out.get(signal_col, pd.Series(0, index=out.index)), errors="coerce").fillna(0)
    out["shadow_tamanho_livre_bloco_risco"] = out["ticker"].astype(str).map(opt._risk_block_for_ticker)
    out["shadow_tamanho_livre_ordem"] = np.arange(len(out))
    rank_cols = ["shadow_tamanho_livre_sinal_v3", "nota_final", "score_prioridade_otimizacao", "shadow_tamanho_livre_ordem"]
    ascending = [False, False, False, True]
    available_cols = [c for c in rank_cols if c in out.columns]
    available_ascending = [ascending[rank_cols.index(c)] for c in available_cols]

    approved = out[out["shadow_tamanho_livre_aprovada"].map(to_bool)].copy()
    if not approved.empty:
        approved = approved.sort_values(available_cols, ascending=available_ascending)
        duplicate_block = approved.duplicated("shadow_tamanho_livre_bloco_risco", keep="first")
        duplicate_tickers = approved.loc[duplicate_block, "ticker"].astype(str).tolist()
        if duplicate_tickers:
            out.loc[out["ticker"].astype(str).isin(duplicate_tickers), "shadow_tamanho_livre_aprovada"] = False
            out.loc[out["ticker"].astype(str).isin(duplicate_tickers), "shadow_tamanho_livre_motivo_exclusao"] = "bloco_risco_duplicado_precheck"
            reasons.append("blocos de risco duplicados removidos: " + ", ".join(duplicate_tickers))

    approved = out[out["shadow_tamanho_livre_aprovada"].map(to_bool)].copy()
    if not approved.empty and "setor" in approved.columns:
        approved_sorted = approved.sort_values(available_cols, ascending=available_ascending)
        sector_rank = approved_sorted.groupby("setor").cumcount() + 1
        drop_tickers = approved_sorted.loc[sector_rank > int(settings.get("portfolio", {}).get("max_assets_per_sector", 2)), "ticker"].astype(str).tolist()
        if drop_tickers:
            out.loc[out["ticker"].astype(str).isin(drop_tickers), "shadow_tamanho_livre_aprovada"] = False
            out.loc[out["ticker"].astype(str).isin(drop_tickers), "shadow_tamanho_livre_motivo_exclusao"] = "excedeu_maximo_2_por_setor_precheck"
            reasons.append("ativos removidos por limite 2/setor: " + ", ".join(drop_tickers))

    final_pool = out[out["shadow_tamanho_livre_aprovada"].map(to_bool)].copy()
    return final_pool, reasons


def build_free_size_portfolio(scored: pd.DataFrame, covariance: pd.DataFrame, settings: dict) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    cfg = free_size_settings(settings)
    audit = scored.copy()
    pool, reasons = selected_free_size_pool(scored, settings)
    if "shadow_tamanho_livre_aprovada" in pool.columns:
        audit_cols = ["ticker", "shadow_tamanho_livre_aprovada", "shadow_tamanho_livre_motivo_exclusao", "shadow_tamanho_livre_sinal_v3"]
        lookup = pool[[c for c in audit_cols if c in pool.columns]].drop_duplicates("ticker").set_index("ticker") if "ticker" in pool else pd.DataFrame()
        if not lookup.empty:
            for col in lookup.columns:
                audit[col] = audit["ticker"].map(lookup[col]).where(audit["ticker"].map(lookup[col]).notna(), audit.get(col, np.nan))
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
    signal = pd.to_numeric(pool.get("shadow_tamanho_livre_sinal_v3", pool.get("_shadow_objetivo_sinal_norm", pd.Series(0, index=pool.index))), errors="coerce").fillna(0)
    weights_before_cap = (signal.clip(lower=0) + cfg["signal_floor"])
    weights_before_cap = weights_before_cap / weights_before_cap.sum()
    weights_v3_capped = capped_proportional_weights(signal, cfg["individual_cap"], cfg["signal_floor"])
    pool["peso_antes_teto_tamanho_livre"] = weights_before_cap.to_numpy(float)
    pool["peso_v3_sem_beta_tamanho_livre"] = weights_v3_capped.to_numpy(float)
    pool["peso_maximo_permitido_ativo"] = cfg["individual_cap"]
    pool["teto_tamanho_livre_aplicado"] = pool["peso_antes_teto_tamanho_livre"] > cfg["individual_cap"] + 1e-12
    pool["grupo_economico_ou_bloco_risco"] = pool["ticker"].astype(str).map(opt._risk_block_for_ticker)

    tickers = pool["ticker"].astype(str).tolist()
    cov = covariance.reindex(index=tickers, columns=tickers).fillna(0).to_numpy(float)
    mean_returns = pd.to_numeric(pool.get("retorno_medio", pd.Series(0, index=pool.index)), errors="coerce").fillna(0).to_numpy(float)
    betas = pd.to_numeric(pool.get("beta", pd.Series(1.0, index=pool.index)), errors="coerce").fillna(1.0).to_numpy(float)
    signal_values = pd.to_numeric(pool.get("_shadow_objetivo_sinal_norm", pd.Series(0, index=pool.index)), errors="coerce").fillna(0).to_numpy(float)
    lambda_beta = float(objetivo_retorno_settings(settings).get("lambda_beta", 0.0))
    lambda_cv = float(objetivo_retorno_settings(settings).get("lambda_cv", 0.5))
    beta_target = float(settings.get("_runtime_beta_target", np.nan))
    w = weights_v3_capped.to_numpy(float)
    optimization_status = "v3_proporcional_sem_beta"
    if lambda_beta > 0 and pd.notna(beta_target) and len(pool) > 1:
        min_weight = min(0.001, 1.0 / (10.0 * len(pool)))
        bounds = [(min_weight, cfg["individual_cap"])] * len(pool)
        def objective_free(weights: np.ndarray) -> float:
            ret = opt.portfolio_return(weights, mean_returns)
            risk = opt.portfolio_risk(weights, cov)
            cv_val = risk / ret if ret > 0 else 1e3
            cv_norm = cv_val / (1.0 + abs(cv_val)) if np.isfinite(cv_val) else 1.0
            beta_val = opt.portfolio_beta(weights, betas)
            return -float(np.dot(weights, signal_values)) + lambda_cv * cv_norm + lambda_beta * abs(beta_val - beta_target)
        result = opt.minimize(
            objective_free,
            w,
            method="SLSQP",
            bounds=bounds,
            constraints=[{"type": "eq", "fun": lambda weights: np.sum(weights) - 1.0}],
            options={"maxiter": 1000, "ftol": 1e-12},
        )
        if result.success and np.isfinite(result.x).all() and abs(float(np.sum(result.x)) - 1.0) <= 1e-6 and float(np.max(result.x)) <= cfg["individual_cap"] + 1e-6:
            w = result.x
            optimization_status = "otimizado_com_beta_alvo"
        else:
            optimization_status = f"fallback_v3_proporcional: {getattr(result, 'message', 'falha_desconhecida')}"
    pool["peso_recomendado"] = w
    port_ret = opt.portfolio_return(w, mean_returns)
    port_risk = opt.portfolio_risk(w, cov)
    beta = opt.portfolio_beta(w, betas)
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
        "objetivo_retorno_lambda_cv": objetivo_retorno_settings(settings)["lambda_cv"],
        "objetivo_retorno_lambda_beta": lambda_beta,
        "objetivo_retorno_sinal_ponderado": float(np.dot(w, signal_values)),
        "tamanho_livre_status_otimizacao_pesos": optimization_status,
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
def _minmax(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() == 0:
        return pd.Series(0.0, index=series.index)
    values = values.fillna(values.median())
    lo = float(values.min())
    hi = float(values.max())
    if abs(hi - lo) < 1e-12:
        return pd.Series(0.5, index=series.index)
    return (values - lo) / (hi - lo)


def _zscore(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() == 0:
        return pd.Series(0.0, index=series.index)
    values = values.fillna(values.median())
    std = float(values.std(ddof=0))
    if std < 1e-12:
        return pd.Series(0.0, index=series.index)
    return (values - float(values.mean())) / std


def add_objetivo_retorno_signals(scored: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if scored.empty or not objetivo_retorno_settings(settings)["enabled"]:
        return scored
    out = scored.copy()
    nota = pd.to_numeric(out.get("nota_final", out.get("score_prioridade_otimizacao", pd.Series(0, index=out.index))), errors="coerce")
    fr = pd.to_numeric(out.get("forca_relativa_score", pd.Series(0, index=out.index)), errors="coerce")
    out["_shadow_signal_v1_norm"] = _minmax(nota)
    out["_shadow_signal_v2_norm"] = _minmax(fr)
    out["_shadow_signal_v3_norm"] = _minmax((_zscore(nota) + _zscore(fr)) / 2.0)
    variant = objetivo_retorno_settings(settings)["variant"]
    signal_col = {"V1": "_shadow_signal_v1_norm", "V2": "_shadow_signal_v2_norm", "V3": "_shadow_signal_v3_norm"}.get(variant, "_shadow_signal_v1_norm")
    out["_shadow_objetivo_sinal_norm"] = out[signal_col]
    out["objetivo_retorno_variant"] = variant
    return out

def _as_float_field(fields: dict[str, Any], keys: list[str], default: float = np.nan) -> float:
    return to_float(first_value(fields, keys, default), default)


def beta_target_profile(path: Path, settings: dict) -> dict[str, Any]:
    fields = fields_dict(read_sheet(path, "Regime Mercado"))
    regime = str(first_value(fields, ["mercado_classificacao", "regime_mercado"], market_regime(path))).strip().lower()
    rsi = _as_float_field(fields, ["rsi_ibov_data_base", "rsi_ibov"], np.nan)
    boll = str(first_value(fields, ["bollinger_ibov_data_base", "bollinger_ibov"], "")).strip().lower()
    amplitude = _as_float_field(fields, ["pct_ativos_positivos_1m", "percentual_ativos_positivos_1m"], np.nan)
    old_subtype = str(first_value(fields, ["subtipo_mercado_favoravel"], "")).strip().lower()
    if regime == "mercado fraco/desfavoravel":
        subtype = "fraco_desfavoravel"
        reason = "regime fraco/desfavoravel"
    elif regime == "mercado favoravel" and ((pd.notna(rsi) and rsi < 50) or "oportunidade" in boll):
        subtype = "favoravel_oportunidade"
        reason = f"RSI IBOV < 50 ou Bollinger oportunidade (RSI={rsi:.2f}, Bollinger={boll})"
    elif regime == "mercado favoravel" and "sobrecompra" in boll and pd.notna(amplitude) and amplitude >= 0.70:
        subtype = "favoravel_amplo"
        reason = f"sobrecompra com amplitude ampla ({amplitude:.1%})"
    elif regime == "mercado favoravel" and "sobrecompra" in boll and (pd.isna(amplitude) or amplitude < 0.70):
        subtype = "favoravel_estreitando"
        reason = f"sobrecompra com amplitude abaixo de 70% ({amplitude:.1%})"
    elif "cansado" in old_subtype or (pd.notna(amplitude) and amplitude < 0.40):
        subtype = "cansado"
        reason = f"mercado cansado/amplitude baixa ({amplitude:.1%})"
    else:
        subtype = "favoravel_amplo" if regime == "mercado favoravel" else "fraco_desfavoravel"
        reason = f"fallback por regime/subtipo atual ({regime}/{old_subtype})"
    cfg = beta_target_settings(settings)["by_regime"]
    profile = cfg.get(subtype, cfg.get("fraco_desfavoravel", {"target": 0.70, "min": 0.60, "max": 0.80}))
    return {
        "beta_target_subtipo": subtype,
        "beta_target": float(profile.get("target", 0.70)),
        "beta_target_min": float(profile.get("min", 0.60)),
        "beta_target_max": float(profile.get("max", 0.80)),
        "beta_target_reason": reason,
        "beta_target_rsi_ibov": rsi,
        "beta_target_bollinger_ibov": boll,
        "beta_target_amplitude_positiva_1m": amplitude,
    }


DOWNTURN_SIGNAL_SCENARIOS = {
    "V3_MOMENTUM": "V3 momentum atual",
    "SINAL_A_DEFENSIVO": "Defensivo puro",
    "SINAL_B_REVERSAO_DEFENSIVO": "Reversao + defensivo",
}
STRICT_REVERSAL_SCENARIOS = {
    "V3_MOMENTUM": "V3 momentum atual",
    "SINAL_A_DEFENSIVO": "Defensivo puro",
    "SINAL_B_REVERSAO_ESTRITO": "Reversao estrita",
}


def downturn_signal_settings(settings: dict) -> dict:
    shadow = settings.get("shadow", {})
    return {
        "enabled": bool(shadow.get("enable_sinal_defensivo_quedas", False)),
        "strict_enabled": bool(shadow.get("enable_sinal_reversao_estrito", False)),
        "strict_rsi_max": float(shadow.get("sinal_reversao_estrito_rsi_max", 40.0)),
        "strict_ret4_max": float(shadow.get("sinal_reversao_estrito_ret4_max", 0.0)),
        "strict_bollinger_dist_max": float(shadow.get("sinal_reversao_estrito_bollinger_dist_max", 0.10)),
        "reversal_threshold": float(shadow.get("sinal_quedas_reversal_threshold", 0.15)),
        "defensive_sectors": shadow.get("sinal_quedas_setores_defensivos", [
            "energia eletrica", "agua", "saneamento", "bebidas", "telecom", "saude",
        ]),
        "strong_amplitude_threshold": float(shadow.get("sinal_quedas_amplitude_forte", 0.34)),
        "strong_rsi_threshold": float(shadow.get("sinal_quedas_rsi_forte", 65.0)),
    }


def downturn_regime_profile(path: Path, settings: dict) -> dict[str, Any]:
    fields = fields_dict(read_sheet(path, "Regime Mercado"))
    beta_profile = beta_target_profile(path, settings)
    beta_subtype = str(beta_profile.get("beta_target_subtipo", "")).strip().lower()
    regime = str(first_value(fields, ["mercado_classificacao", "regime_mercado"], market_regime(path))).strip().lower()
    old_subtype = str(first_value(fields, ["subtipo_mercado_favoravel"], "")).strip().lower()
    rsi = _as_float_field(fields, ["rsi_ibov_data_base", "rsi_ibov"], np.nan)
    amplitude = _as_float_field(fields, ["pct_ativos_positivos_1m", "percentual_ativos_positivos_1m"], np.nan)
    boll = str(first_value(fields, ["bollinger_ibov_data_base", "bollinger_ibov"], "")).strip().lower()
    cfg = downturn_signal_settings(settings)
    if beta_subtype == "fraco_desfavoravel" or "fraco" in regime or "desfavoravel" in regime:
        subtype = "queda_forte"
        reason = f"regime fraco/desfavoravel na data-base ({regime})"
    elif beta_subtype == "cansado" and ((pd.notna(amplitude) and amplitude <= cfg["strong_amplitude_threshold"]) and (pd.notna(rsi) and rsi <= cfg["strong_rsi_threshold"])):
        subtype = "queda_forte"
        reason = f"mercado cansado com amplitude muito baixa ({amplitude:.1%}) e RSI <= {cfg['strong_rsi_threshold']:.0f} ({rsi:.2f})"
    elif beta_subtype in {"favoravel_estreitando", "cansado"} or "cansado" in old_subtype:
        subtype = "queda_leve_lateral"
        reason = f"mercado favoravel estreitando/cansado na data-base (subtipo={beta_subtype}, amplitude={amplitude:.1%}, RSI={rsi:.2f})"
    else:
        subtype = "alta"
        reason = f"sem regime de queda pela data-base (subtipo={beta_subtype}, bollinger={boll}, amplitude={amplitude:.1%}, RSI={rsi:.2f})"
    return {
        "subtipo_queda": subtype,
        "motivo_subtipo_queda": reason,
        "regime_mercado": regime,
        "subtipo_beta": beta_subtype,
        "rsi_ibov_data_base": rsi,
        "bollinger_ibov_data_base": boll,
        "amplitude_positiva_1m": amplitude,
    }


def _num(frame: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    return pd.to_numeric(frame.get(col, pd.Series(default, index=frame.index)), errors="coerce")


def _contains_any(series: pd.Series, tokens: list[str]) -> pd.Series:
    pattern = "|".join(re.escape(token) for token in tokens)
    return series.fillna("").astype(str).str.lower().str.contains(pattern, regex=True, na=False)


def defensive_sector_score(frame: pd.DataFrame, settings: dict) -> pd.Series:
    sectors = frame.get("setor", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    tokens = [str(v).lower() for v in downturn_signal_settings(settings)["defensive_sectors"]]
    score = pd.Series(0.0, index=frame.index, dtype=float)
    score.loc[_contains_any(sectors, tokens)] = 1.0
    return score


def add_downturn_signal_scores(scored: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if scored.empty:
        return scored
    out = scored.copy()
    vol_raw = _num(out, "desvio_padrao")
    if vol_raw.notna().sum() == 0:
        vol_raw = _num(out, "cv")
    beta_raw = _num(out, "beta", 1.0).clip(lower=-0.5, upper=2.5)
    vol_score = (1.0 - _minmax(vol_raw)).clip(lower=0, upper=1)
    beta_score = (1.0 - _minmax(beta_raw)).clip(lower=0, upper=1)
    sector_score = defensive_sector_score(out, settings)
    out["shadow_sinal_a_baixa_vol_score"] = vol_score
    out["shadow_sinal_a_beta_baixo_score"] = beta_score
    out["shadow_sinal_a_setor_defensivo_score"] = sector_score
    out["shadow_signal_downturn_A"] = (0.45 * vol_score + 0.35 * beta_score + 0.20 * sector_score).clip(lower=0, upper=1)

    rsi = _num(out, "rsi")
    ret1 = _num(out, "retorno_acumulado_1m")
    ret4 = _num(out, "retorno_acumulado_4m")
    dist_lower = _num(out, "distancia_banda_inferior_pct")
    boll_text = out.get("bollinger_status", pd.Series("", index=out.index)).fillna("").astype(str).str.lower()
    existing_reversal = _minmax(_num(out, "reversal_score", 0.0))
    rsi_score = ((45.0 - rsi) / 20.0).clip(lower=0, upper=1).fillna(0)
    ret1_drop_score = ((-ret1) / 0.20).clip(lower=0, upper=1).fillna(0)
    ret4_drop_score = ((-ret4) / 0.35).clip(lower=0, upper=1).fillna(0)
    boll_score = pd.Series(0.0, index=out.index, dtype=float)
    boll_score.loc[boll_text.str.contains("oportunidade|inferior|sobrevenda", regex=True, na=False)] = 1.0
    boll_score.loc[(pd.notna(dist_lower)) & (dist_lower <= 0.05)] = 1.0
    reversal_component = pd.concat([rsi_score, ret1_drop_score, ret4_drop_score, boll_score], axis=1).max(axis=1).clip(lower=0, upper=1)
    deterioration = out.apply(is_real_deterioration, axis=1)
    cfg = downturn_signal_settings(settings)
    price_reversal = (reversal_component >= cfg["reversal_threshold"]) & ~deterioration
    strict_rsi_ok = rsi <= cfg["strict_rsi_max"]
    strict_ret4_ok = ret4 < cfg["strict_ret4_max"]
    strict_boll_ok = ((pd.notna(dist_lower)) & (dist_lower <= cfg["strict_bollinger_dist_max"])) | boll_text.str.contains("oportunidade|inferior|sobrevenda", regex=True, na=False)
    strict_reversal = strict_rsi_ok & strict_ret4_ok & strict_boll_ok & ~deterioration
    strict_component = pd.concat([rsi_score, ret4_drop_score, boll_score], axis=1).mean(axis=1).where(strict_reversal, 0.0).clip(lower=0, upper=1)
    out["shadow_sinal_b_rsi_score"] = rsi_score
    out["shadow_sinal_b_ret1_queda_score"] = ret1_drop_score
    out["shadow_sinal_b_ret4_queda_score"] = ret4_drop_score
    out["shadow_sinal_b_bollinger_score"] = boll_score
    out["shadow_queda_reversal_score_legado_planilha"] = existing_reversal
    out["shadow_queda_reversal_score"] = reversal_component
    out["shadow_queda_reversao_preco_sem_deterioracao"] = price_reversal
    out["shadow_reversao_estrita_rsi_ok"] = strict_rsi_ok
    out["shadow_reversao_estrita_ret4_ok"] = strict_ret4_ok
    out["shadow_reversao_estrita_bollinger_ok"] = strict_boll_ok
    out["shadow_reversao_estrita_aprovada"] = strict_reversal
    out["shadow_reversao_estrita_score"] = strict_component
    out["shadow_queda_deterioracao_real"] = deterioration
    out["shadow_signal_downturn_B"] = (0.60 * reversal_component + 0.40 * out["shadow_signal_downturn_A"]).where(~deterioration, 0.0).clip(lower=0, upper=1)
    out["shadow_signal_downturn_B_estrito"] = strict_component

    motivos = []
    for idx, row in out.iterrows():
        parts = []
        if bool(row.get("shadow_reversao_estrita_aprovada", False)):
            parts.append("reversao_estrita: RSI<=40; ret4m<0; bollinger_inferior/ou_sobrevenda; sem_deterioracao_real")
        elif bool(row.get("shadow_queda_reversao_preco_sem_deterioracao", False)):
            parts.append("batida_de_preco_sem_deterioracao_real")
        if to_float(row.get("rsi"), np.nan) <= 45:
            parts.append(f"RSI_formacao={to_float(row.get('rsi'), np.nan):.2f}")
        if to_float(row.get("retorno_acumulado_1m"), np.nan) < 0:
            parts.append(f"ret1m_formacao={to_float(row.get('retorno_acumulado_1m'), np.nan):.2%}")
        if to_float(row.get("retorno_acumulado_4m"), np.nan) < 0:
            parts.append(f"ret4m_formacao={to_float(row.get('retorno_acumulado_4m'), np.nan):.2%}")
        if bool(row.get("shadow_queda_deterioracao_real", False)):
            parts.append("bloqueio_deterioracao_real: ROE<0 ou margem_liquida<0 ou P/L<0")
        motivos.append("; ".join(parts) if parts else "sem_sinal_reversao_relevante_na_data_base")
    out["shadow_queda_motivo_sinal"] = motivos
    return out


def apply_downturn_signal(scored: pd.DataFrame, settings: dict, signal_name: str) -> pd.DataFrame:
    if scored.empty:
        return scored
    out = add_downturn_signal_scores(scored, settings)
    profile = settings.get("_runtime_downturn_profile", {})
    subtype = profile.get("subtipo_queda", "alta")
    out["shadow_sinal_quedas_cenario"] = signal_name
    out["shadow_subtipo_queda"] = subtype
    out["shadow_motivo_subtipo_queda"] = profile.get("motivo_subtipo_queda", "")
    if signal_name == "V3_MOMENTUM" or subtype == "alta":
        out["shadow_sinal_quedas_aplicado"] = "V3_MOMENTUM"
        return out
    if signal_name == "SINAL_A_DEFENSIVO":
        out["_shadow_objetivo_sinal_norm"] = out["shadow_signal_downturn_A"]
        out["shadow_tamanho_livre_sinal_v3"] = out["shadow_signal_downturn_A"]
        out["shadow_sinal_quedas_aplicado"] = "SINAL_A_DEFENSIVO"
    elif signal_name == "SINAL_B_REVERSAO_DEFENSIVO":
        out["_shadow_objetivo_sinal_norm"] = out["shadow_signal_downturn_B"]
        out["shadow_tamanho_livre_sinal_v3"] = out["shadow_signal_downturn_B"]
        out["shadow_sinal_quedas_aplicado"] = "SINAL_B_REVERSAO_DEFENSIVO"
    elif signal_name == "SINAL_B_REVERSAO_ESTRITO":
        out = out[out["shadow_reversao_estrita_aprovada"].map(to_bool)].copy()
        out["_shadow_objetivo_sinal_norm"] = out["shadow_signal_downturn_B_estrito"]
        out["shadow_tamanho_livre_sinal_v3"] = out["shadow_signal_downturn_B_estrito"]
        out["shadow_sinal_quedas_aplicado"] = "SINAL_B_REVERSAO_ESTRITO"
    else:
        out["shadow_sinal_quedas_aplicado"] = "V3_MOMENTUM"
    return out


def technical_veto_to_penalty_in_opportunity(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if settings.get("_runtime_beta_target_subtipo") != "favoravel_oportunidade" or frame.empty:
        return frame
    out = frame.copy()
    index = out.index
    out["shadow_beta_target_motivos"] = out.get("shadow_beta_target_motivos", pd.Series("", index=index)).fillna("").astype(str)
    out["motivo_bloqueio_original_d3"] = out.get("motivo_bloqueio_otimizacao", pd.Series("", index=index)).fillna("").astype(str)
    out["shadow_liberado_antes_d3"] = out.get("liberado_para_otimizacao", pd.Series(False, index=index)).map(to_bool)
    out["liberado_por_d3"] = False

    technical_tokens = [
        "tendencia tecnica negativa", "tendencia_tecnica_negativa", "tendencia_mensal_desfavoravel",
        "entrada esticada", "entrada_esticada", "sobrecompra", "timing_esticado", "bloqueio_por_sobrecompra_extrema",
        "bloqueio_por_tendencia_mensal_desfavoravel", "bloqueio_por_forca_relativa_fraca", "retorno_1m_relativo_muito_fraco",
        "retorno_medio_negativo", "bloqueio_por_retorno_medio_negativo", "watchlist", "watchlist_flexivel",
        "timing", "sinal_tardio", "forca_relativa_fraca", "mercado_esticado", "retorno_1m_relativo",
    ]
    non_fundamental_tokens_to_remove = technical_tokens + [
        "beta_negativo_em_mercado_favoravel_watchlist_flexivel", "correlacao_negativa_em_mercado_favoravel_watchlist_flexivel",
        "beta_e_correlacao_negativos_em_mercado_favoravel", "beta_e_correlacao_muito_baixos_em_mercado_favoravel_watchlist_flexivel",
        "beta_e_correlacao_muito_baixos_em_mercado_favoravel", "beta_muito_baixo", "correlacao_muito_baixa",
    ]
    fundamental_tokens = ["fundamento", "deterioracao", "roe<0", "roe_negativo", "margem_liq<0", "margem_liquida_negativa", "p/l<0", "pl_negativo"]

    def text_col(name: str) -> pd.Series:
        return out.get(name, pd.Series("", index=index)).fillna("").astype(str).str.lower()

    reason = text_col("motivo_bloqueio_otimizacao")
    blob = pd.Series("", index=index, dtype=object)
    for col in [
        "motivo_bloqueio_otimizacao", "tipo_bloqueio_otimizacao", "penalizacoes_otimizacao", "alertas_nao_bloqueantes",
        "motivo_tipo_watchlist", "motivo_status_para_risco", "tipo_watchlist", "qualidade_do_timing", "tipo_timing",
        "decisao_preliminar_ajustada", "motivo_exclusao", "motivo_decisao_preliminar",
    ]:
        if col in out.columns:
            blob = blob + " ; " + text_col(col)

    status_ok = out.get("status_para_risco", pd.Series("", index=index)).isin(["aprovada_para_risco", "moderada_para_risco"])
    category_ok = out.get("categoria_elegibilidade", pd.Series("", index=index)).isin(["elegivel_forte", "elegivel_moderado"])
    release_blocked_by_status = (~status_ok) | (~category_ok)
    technical_like = blob.map(lambda txt: any(token in txt for token in technical_tokens))
    has_nonfundamental_reason = reason.str.strip().ne("") & ~reason.map(lambda txt: any(token in txt for token in fundamental_tokens))
    deterioration = out.apply(is_real_deterioration, axis=1)
    releasable = (~deterioration) & (technical_like | has_nonfundamental_reason | release_blocked_by_status)
    if not releasable.any():
        return out

    for idx in out.index[releasable]:
        original_reason = str(out.at[idx, "motivo_bloqueio_original_d3"] or "")
        if not original_reason.strip():
            original_reason = str(blob.at[idx]).strip(" ;")
        out.at[idx, "motivo_bloqueio_original_d3"] = original_reason
        out.at[idx, "motivo_bloqueio_otimizacao"] = remove_tokens(out.at[idx, "motivo_bloqueio_otimizacao"], non_fundamental_tokens_to_remove).strip()
        out.at[idx, "tipo_bloqueio_otimizacao"] = remove_tokens(out.get("tipo_bloqueio_otimizacao", pd.Series("", index=index)).at[idx], [
            "bloqueio_timing", "bloqueio_tecnico", "bloqueio_forca_relativa", "bloqueio_aderencia_regime", "bloqueio_risco",
        ]).strip()
        if str(out.at[idx, "motivo_bloqueio_otimizacao"]).strip():
            out.at[idx, "motivo_bloqueio_otimizacao"] = ""
        if str(out.at[idx, "tipo_bloqueio_otimizacao"]).strip():
            out.at[idx, "tipo_bloqueio_otimizacao"] = ""
        out.at[idx, "penalizacoes_otimizacao"] = append_token(out.get("penalizacoes_otimizacao", pd.Series("", index=index)).at[idx], "penalizacao_veto_tecnico_trailing_oportunidade_shadow")
        out.at[idx, "shadow_beta_target_motivos"] = append_token(out.at[idx, "shadow_beta_target_motivos"], "veto_tecnico_trailing_virou_score_em_favoravel_oportunidade")
        out.at[idx, "liberado_por_d3"] = True

    out.loc[releasable & ~status_ok, "status_para_risco"] = "moderada_para_risco"
    out.loc[releasable & ~category_ok, "categoria_elegibilidade"] = "elegivel_moderado"
    if "watchlist_bloqueia_otimizacao" in out.columns:
        out.loc[releasable, "watchlist_bloqueia_otimizacao"] = False
    if "tipo_watchlist" in out.columns:
        out.loc[releasable & out["tipo_watchlist"].fillna("").astype(str).str.contains("watchlist", case=False, na=False), "tipo_watchlist"] = "watchlist_monitoramento"

    reason2 = out.get("motivo_bloqueio_otimizacao", pd.Series("", index=index)).fillna("").astype(str).str.strip()
    status_ok2 = out.get("status_para_risco", pd.Series("", index=index)).isin(["aprovada_para_risco", "moderada_para_risco"])
    category_ok2 = out.get("categoria_elegibilidade", pd.Series("", index=index)).isin(["elegivel_forte", "elegivel_moderado"])
    has_risk = out.get("retorno_medio", pd.Series(np.nan, index=index)).notna()
    out["bloqueado_otimizacao"] = reason2.ne("")
    out["liberado_para_otimizacao"] = (~out["bloqueado_otimizacao"]) & status_ok2 & category_ok2 & has_risk
    out["liberado_por_d3"] = out["liberado_por_d3"].map(to_bool) & out["liberado_para_otimizacao"].map(to_bool) & ~out["shadow_liberado_antes_d3"].map(to_bool)
    return out

def _optimize_subset_beta_target(selected: pd.DataFrame, covariance: pd.DataFrame, settings: dict, max_sector_weight: float, max_block_weight: float, sector_relaxed: bool, precomputed: dict | None = None) -> tuple[pd.DataFrame | None, dict, list[str]]:
    sector_violation = opt._has_sector_count_violation(selected, settings)
    if sector_violation:
        return None, {}, [f"maximo de acoes por setor violado: {sector_violation}"]
    reversal_violation = opt._has_reversal_count_violation(selected, settings)
    if reversal_violation:
        return None, {}, [f"maximo de acoes de reversao violado: {reversal_violation}"]
    block_count_violation = opt._has_risk_block_count_violation(selected, settings)
    if block_count_violation:
        return None, {}, [f"bloco de risco duplicado violado: {block_count_violation}"]

    cfg = opt._portfolio_config(settings)
    indexed = selected.set_index("ticker")
    if precomputed:
        tickers = precomputed["tickers"]
        sectors = pd.Series(precomputed["sectors"], index=tickers)
        blocks = pd.Series(precomputed["blocks"], index=tickers)
        timing_types = pd.Series(precomputed["timing_types"], index=tickers)
        watchlist_types = pd.Series(precomputed["watchlist_types"], index=tickers)
        weight_caps = precomputed["weight_caps"]
        mean_returns = precomputed["returns"]
        cov = precomputed["covariance"]
        betas = precomputed["betas"]
    else:
        tickers = selected["ticker"].tolist()
        sectors = indexed["setor"]
        blocks = opt._risk_block_series(selected, tickers)
        timing_types = opt._timing_series(selected, tickers)
        watchlist_types = indexed.get("tipo_watchlist", pd.Series("", index=tickers)).reindex(tickers).fillna("")
        weight_caps = opt._asset_weight_caps(selected, settings).reindex(tickers).fillna(cfg["max_weight"]).to_numpy(float)
        mean_returns = indexed.loc[tickers, "retorno_medio"].to_numpy(float)
        cov = covariance.reindex(index=tickers, columns=tickers).fillna(0).to_numpy(float)
        betas = indexed.loc[tickers, "beta"].fillna(1.0).to_numpy(float)
    feasible_x0, feasible_message = opt._linear_feasible_weights(tickers, sectors, blocks, timing_types, watchlist_types, weight_caps, settings, max_sector_weight, max_block_weight)
    if feasible_x0 is None:
        return None, {}, [feasible_message]

    beta_target = float(settings.get("_runtime_beta_target", np.nan))
    lambda_beta = float(settings.get("_runtime_beta_target_lambda", 1.0))
    obj_cfg = objetivo_retorno_settings(settings)
    signal_values = indexed.loc[tickers, "_shadow_objetivo_sinal_norm"].fillna(0).to_numpy(float) if "_shadow_objetivo_sinal_norm" in indexed else np.zeros(len(tickers))
    nota_values = pd.to_numeric(indexed.get("nota_final", indexed.get("score_prioridade_otimizacao", pd.Series(0, index=tickers))).reindex(tickers), errors="coerce").fillna(0).to_numpy(float)
    forca_values = pd.to_numeric(indexed.get("forca_relativa_score", pd.Series(0, index=tickers)).reindex(tickers), errors="coerce").fillna(0).to_numpy(float)

    ret_ref = opt.portfolio_return(feasible_x0, mean_returns)
    risk_ref = opt.portfolio_risk(feasible_x0, cov)
    cv_ref = abs(risk_ref / ret_ref) if ret_ref > 0 else 1.0
    if not np.isfinite(cv_ref) or cv_ref < 1e-12:
        cv_ref = 1.0

    def objective(w: np.ndarray) -> float:
        ret = opt.portfolio_return(w, mean_returns)
        risk = opt.portfolio_risk(w, cov)
        if ret <= 0:
            return 1e6
        cv = risk / ret
        beta_value = opt.portfolio_beta(w, betas)
        if obj_cfg["enabled"]:
            expected_signal = float(np.dot(w, signal_values))
            beta_penalty = 0.0 if pd.isna(beta_target) else abs(beta_value - beta_target)
            cv_norm = cv / (1.0 + abs(cv))
            return -expected_signal + obj_cfg["lambda_cv"] * cv_norm
        if pd.isna(beta_target):
            return cv
        return cv * (1 + lambda_beta * abs(beta_value - beta_target))

    bounds = list(zip(np.repeat(cfg["min_weight"], len(tickers)), weight_caps))
    result = opt.minimize(
        objective,
        feasible_x0,
        method="SLSQP",
        bounds=bounds,
        constraints=opt._constraints_for_slsqp(tickers, sectors, blocks, timing_types, watchlist_types, max_sector_weight, max_block_weight, settings),
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    if result.success:
        weights = result.x
        status = "ok beta_target_shadow" if not sector_relaxed else "ok beta_target_shadow com limite setorial preferencial relaxado"
    else:
        weights = feasible_x0
        status = f"Otimizacao beta_target falhou; fallback factivel usado: {result.message}"
        if sector_relaxed:
            status += "; limite setorial preferencial relaxado"

    violations = opt._validate_weights(weights, tickers, sectors, blocks, timing_types, watchlist_types, weight_caps, settings, max_sector_weight, max_block_weight)
    if violations:
        return None, {}, violations
    metrics = opt._portfolio_metrics(selected, weights, covariance, settings, status, sector_relaxed, precomputed=precomputed)
    metrics["beta_target"] = beta_target
    metrics["beta_target_lambda"] = lambda_beta
    metrics["desvio_beta_target"] = metrics.get("beta_carteira", np.nan) - beta_target if pd.notna(beta_target) else np.nan
    metrics["objetivo_retorno_enabled"] = bool(obj_cfg["enabled"])
    metrics["objetivo_retorno_variant"] = obj_cfg["variant"] if obj_cfg["enabled"] else "CV_ATUAL"
    metrics["objetivo_retorno_lambda_cv"] = obj_cfg["lambda_cv"]
    metrics["objetivo_retorno_lambda_beta"] = obj_cfg["lambda_beta"]
    metrics["objetivo_retorno_sinal_ponderado"] = float(np.dot(weights, signal_values)) if obj_cfg["enabled"] else np.nan
    metrics["nota_final_ponderada"] = float(np.dot(weights, nota_values))
    metrics["forca_relativa_score_ponderada"] = float(np.dot(weights, forca_values))
    metrics["objetivo_retorno_cv_norm"] = float((metrics.get("cv_carteira", np.nan) or np.nan) / (1.0 + abs(metrics.get("cv_carteira", np.nan)))) if obj_cfg["enabled"] and pd.notna(metrics.get("cv_carteira", np.nan)) else np.nan
    metrics["objetivo_retorno_valor"] = objective(weights) if obj_cfg["enabled"] else np.nan
    if metrics["retorno_carteira"] <= 0:
        return None, {}, ["retorno esperado da carteira nao positivo"]
    if not metrics["carteira_valida"]:
        return None, {}, ["carteira invalida apos otimizacao"]
    portfolio = selected.copy()
    portfolio["peso_recomendado"] = weights
    portfolio["peso_maximo_permitido_ativo"] = portfolio["ticker"].map(pd.Series(weight_caps, index=tickers)).fillna(cfg["max_weight"])
    portfolio["grupo_economico_ou_bloco_risco"] = portfolio["ticker"].map(opt._risk_block_for_ticker)
    setor_pesos = portfolio.groupby("setor")["peso_recomendado"].sum().to_dict() if "setor" in portfolio else {}
    bloco_pesos = portfolio.groupby("grupo_economico_ou_bloco_risco")["peso_recomendado"].sum().to_dict()
    portfolio["peso_setor"] = portfolio.get("setor", pd.Series("", index=portfolio.index)).map(setor_pesos) if "setor" in portfolio else np.nan
    portfolio["peso_bloco_risco"] = portfolio["grupo_economico_ou_bloco_risco"].map(bloco_pesos)
    portfolio["score_aderencia_regime"] = metrics.get("score_aderencia_regime", np.nan)
    portfolio["motivo_aderencia_regime"] = metrics.get("motivo_aderencia_regime", "")
    portfolio["aderencia_carteira_ao_regime"] = metrics.get("aderencia_carteira_ao_regime", "")
    portfolio["beta_target"] = beta_target
    portfolio["desvio_beta_target"] = metrics.get("desvio_beta_target", np.nan)
    portfolio["objetivo_retorno_variant"] = metrics.get("objetivo_retorno_variant", "CV_ATUAL")
    portfolio["objetivo_retorno_sinal_norm"] = portfolio["ticker"].map(pd.Series(signal_values, index=tickers))
    portfolio["nota_final_usada_objetivo"] = portfolio["ticker"].map(pd.Series(nota_values, index=tickers))
    portfolio["forca_relativa_score_usada_objetivo"] = portfolio["ticker"].map(pd.Series(forca_values, index=tickers))
    return portfolio, metrics, []


def optimize_weights_shadow(scored: pd.DataFrame, covariance: pd.DataFrame, settings: dict) -> tuple[pd.DataFrame, dict]:
    if not beta_target_settings(settings)["enabled"]:
        return optimize_weights(scored, covariance, settings)
    original = opt._optimize_subset
    opt._optimize_subset = _optimize_subset_beta_target
    try:
        return opt.optimize_weights(scored, covariance, settings)
    finally:
        opt._optimize_subset = original


def partial_settings(settings: dict) -> dict:
    shadow = settings.get("shadow", {})
    return {
        "enabled": bool(shadow.get("enable_partial_portfolio", False)),
        "min_invested": float(shadow.get("partial_portfolio_min_invested", 0.40)),
        "max_invested": float(shadow.get("partial_portfolio_max_invested", 0.70)),
    }


def histogram_from_comparison(comparison: pd.DataFrame) -> dict[str, int]:
    histogram: dict[str, int] = {}
    if comparison.empty or "histograma_rejeicoes" not in comparison.columns:
        return histogram
    for raw in comparison["histograma_rejeicoes"].dropna().astype(str):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
        for key, value in data.items():
            histogram[str(key)] = histogram.get(str(key), 0) + int(value)
    return histogram


def partial_trigger_allowed(metrics: dict) -> tuple[bool, str, dict[str, int]]:
    comparison = metrics.get("comparativo_carteiras", pd.DataFrame())
    histogram = histogram_from_comparison(comparison)
    if not histogram:
        return False, "sem_histograma_rejeicoes", histogram
    blocking = {key: value for key, value in histogram.items() if key in {"setor", "bloco_risco", "watchlist_flexivel"} and int(value) > 0}
    if blocking:
        return False, "rejeicoes_nao_peso_individual: " + "; ".join(f"{k}={v}" for k, v in blocking.items()), histogram
    if int(histogram.get("peso_individual", 0)) <= 0:
        return False, "sem_rejeicao_exclusiva_por_peso_individual", histogram
    other = {key: value for key, value in histogram.items() if key not in {"peso_individual"} and int(value) > 0}
    if other:
        return False, "histograma_contem_outros_motivos: " + "; ".join(f"{k}={v}" for k, v in other.items()), histogram
    return True, "acionamento_por_peso_individual_exclusivo", histogram


def risk_block_for_ticker(ticker: str) -> str:
    t = str(ticker).upper().replace(".SA", "")
    if t in {"PETR3", "PETR4"}:
        return "PETROBRAS"
    if t in {"GGBR3", "GGBR4", "GOAU3", "GOAU4"}:
        return "GERDAU_GOAU"
    if t in {"ITUB3", "ITUB4", "ITSA3", "ITSA4"}:
        return "ITAU"
    return str(ticker)


def weight_caps_for_partial(scored: pd.DataFrame, settings: dict) -> pd.Series:
    portfolio = settings.get("portfolio", {})
    base_cap = float(portfolio.get("max_weight", 0.20))
    caps = pd.Series(base_cap, index=scored.index, dtype=float)
    for col in ["peso_maximo_permitido_ativo", "peso_maximo_timing_com_alerta", "peso_maximo_individual_watchlist_flexivel"]:
        if col in scored.columns:
            vals = pd.to_numeric(scored[col], errors="coerce")
            caps = np.minimum(caps, vals.fillna(base_cap))
    return pd.Series(caps, index=scored.index).clip(lower=0)


def build_partial_portfolio(scored: pd.DataFrame, metrics: dict, settings: dict) -> tuple[pd.DataFrame, dict]:
    cfg = partial_settings(settings)
    allowed, reason, histogram = partial_trigger_allowed(metrics)
    extra = {
        "partial_trigger_allowed": allowed,
        "partial_trigger_reason": reason,
        "partial_histograma_rejeicao": json.dumps(histogram, ensure_ascii=False, sort_keys=True),
        "partial_enabled": cfg["enabled"],
    }
    if not cfg["enabled"] or not allowed or scored.empty:
        return pd.DataFrame(), extra

    portfolio_cfg = settings.get("portfolio", {})
    max_total = cfg["max_invested"]
    min_total = cfg["min_invested"]
    sector_cap = float(portfolio_cfg.get("peso_maximo_setor_excepcional", portfolio_cfg.get("hard_max_sector_weight", 0.40)))
    block_cap = float(portfolio_cfg.get("peso_maximo_bloco_risco_tolerado", 0.25))
    watch_cap = float(portfolio_cfg.get("max_peso_total_watchlist_flexivel", 0.35))
    max_assets_per_sector = int(portfolio_cfg.get("max_assets_per_sector", 999))

    ordered = scored.copy().reset_index(drop=True)
    caps = weight_caps_for_partial(ordered, settings)
    rows = []
    sector_weights: dict[str, float] = {}
    sector_counts: dict[str, int] = {}
    block_weights: dict[str, float] = {}
    watch_weight = 0.0
    invested = 0.0
    for idx, row in ordered.iterrows():
        if invested >= max_total - 1e-12:
            break
        ticker = str(row.get("ticker", ""))
        if not ticker:
            continue
        sector = str(row.get("setor", "Outros") or "Outros")
        block = risk_block_for_ticker(ticker)
        is_watch = str(row.get("tipo_watchlist", "")).strip() == "watchlist_flexivel"
        if sector_counts.get(sector, 0) >= max_assets_per_sector:
            continue
        cap = float(caps.loc[idx])
        cap = min(cap, max_total - invested)
        cap = min(cap, sector_cap - sector_weights.get(sector, 0.0))
        cap = min(cap, block_cap - block_weights.get(block, 0.0))
        if is_watch:
            cap = min(cap, watch_cap - watch_weight)
        if cap <= 1e-9:
            continue
        out = row.copy()
        out["peso_recomendado"] = cap
        out["peso_maximo_permitido_ativo"] = float(caps.loc[idx])
        out["grupo_economico_ou_bloco_risco"] = block
        rows.append(out)
        invested += cap
        sector_weights[sector] = sector_weights.get(sector, 0.0) + cap
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        block_weights[block] = block_weights.get(block, 0.0) + cap
        if is_watch:
            watch_weight += cap

    if invested + 1e-9 < min_total:
        extra.update({
            "partial_trigger_allowed": False,
            "partial_trigger_reason": f"exposicao_investida_abaixo_do_piso: {invested:.2%} < {min_total:.2%}",
            "partial_invested": invested,
            "partial_cash": 1 - invested,
        })
        return pd.DataFrame(), extra

    portfolio = pd.DataFrame(rows)
    extra.update({
        "status_carteira": "carteira_parcial",
        "carteira_valida": True,
        "partial_acionada": True,
        "partial_invested": invested,
        "partial_cash": 1 - invested,
        "partial_min_invested": min_total,
        "partial_max_invested": max_total,
        "tickers_selecionados": ", ".join(portfolio["ticker"].astype(str).tolist()),
        "pesos": format_weights(weights_map(portfolio)),
        "restricoes_violadas": "carteira parcial: caixa mantido por inviabilidade de fechar 100% sem violar peso individual",
    })
    return portfolio, extra


def prepare_settings(base_settings: dict, path: Path, shadow_fixes: bool, enable_partial_portfolio: bool | None = None, enable_beta_target: bool | None = None, enable_objetivo_retorno: bool | None = None, objetivo_variant: str | None = None, enable_composicao_ampliada: bool | None = None) -> dict:
    settings = copy.deepcopy(base_settings)
    settings["shadow_fixes"] = bool(shadow_fixes)
    settings["_runtime_market_class"] = market_regime(path)
    settings.setdefault("shadow", {})
    if enable_partial_portfolio is not None:
        settings["shadow"]["enable_partial_portfolio"] = bool(enable_partial_portfolio)
    if enable_beta_target is not None:
        settings["shadow"]["enable_beta_target"] = bool(enable_beta_target)
    if enable_objetivo_retorno is not None:
        settings["shadow"]["enable_objetivo_retorno"] = bool(enable_objetivo_retorno)
    if objetivo_variant is not None:
        settings["shadow"]["objetivo_retorno_variant"] = str(objetivo_variant).upper()
    if enable_composicao_ampliada is not None:
        settings["shadow"]["enable_composicao_ampliada"] = bool(enable_composicao_ampliada)
    if composition_settings(settings)["enabled"]:
        comp = composition_settings(settings)
        settings.setdefault("portfolio", {})["candidate_counts"] = comp["candidate_counts"]
        settings["portfolio"]["peso_maximo_timing_com_alerta"] = comp["quality_cap_alert"]
        settings["portfolio"]["peso_maximo_individual_watchlist_flexivel"] = comp["quality_cap_watchlist"]
        settings.setdefault("strategy", {})["optimization_candidates"] = comp["expanded_topn"]
        settings["strategy"]["max_assets"] = max(comp["candidate_counts"])
    profile = beta_target_profile(path, settings)
    settings.update({f"_runtime_{key}": value for key, value in profile.items()})
    settings["_runtime_beta_target_lambda"] = beta_target_settings(settings)["lambda"]
    return settings


def textify(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    text_cols = [
        "motivos_alerta_realizacao_pos_rali", "penalizacoes_otimizacao", "alertas_nao_bloqueantes",
        "motivo_bloqueio_otimizacao", "tipo_bloqueio_otimizacao", "qualidade_do_timing",
        "perfil_risco_empresa", "qualidade_fundamentalista", "setor",
    ]
    for col in text_cols:
        if col in out.columns:
            out[col] = out[col].fillna("").astype(str)
    return out


def is_real_deterioration(row: pd.Series) -> bool:
    return to_float(row.get("roe"), np.nan) < 0 or to_float(row.get("margem_liquida"), np.nan) < 0 or to_float(row.get("pl_atual"), np.nan) < 0


def selective_realization_alert(row: pd.Series) -> tuple[bool, str, int]:
    ret_1m = to_float(row.get("retorno_acumulado_1m"), np.nan)
    rel_1m = to_float(row.get("retorno_1m_relativo_ibov"), np.nan)
    beta = to_float(row.get("beta"), np.nan)
    rsi = to_float(row.get("rsi"), np.nan)
    dist_upper = to_float(row.get("distancia_banda_superior_pct"), np.nan)
    boll_pos = to_float(row.get("bollinger_position"), np.nan)
    profile = str(row.get("perfil_risco_empresa", "")).lower()
    quality = str(row.get("qualidade_fundamentalista", "")).lower()
    sector = str(row.get("setor", "")).lower()
    signals = {
        "retorno_1m_forte": (pd.notna(ret_1m) and ret_1m >= 0.10) or (pd.notna(rel_1m) and rel_1m >= 0.05),
        "beta_alto": pd.notna(beta) and beta > 1.0,
        "rsi_alto": pd.notna(rsi) and rsi >= 70,
        "proximo_banda_superior": (pd.notna(dist_upper) and dist_upper <= 0.05) or (pd.notna(boll_pos) and boll_pos >= 0.90) or "sobrecompra" in str(row.get("bollinger_status", "")).lower(),
        "perfil_especulativo": any(token in profile for token in ["especulativo", "turnaround", "ciclica"]) or quality in {"fraca", "critica"} or any(token in sector for token in ["siderurgia", "construcao", "varejo"]),
    }
    active = [name for name, value in signals.items() if value]
    return len(active) >= 3, "; ".join(active), len(active)


def apply_shadow_fixes(frame: pd.DataFrame, regime: str) -> pd.DataFrame:
    shadow = textify(frame)
    shadow["shadow_fixes"] = True
    shadow["shadow_motivos_correcoes"] = ""
    for col in ["bloqueado_otimizacao", "liberado_para_otimizacao", "alerta_realizacao_pos_rali", "penalizacao_realizacao_pos_rali"]:
        if col in shadow.columns:
            shadow[col] = shadow[col].map(to_bool)
    if "retorno_medio" in shadow.columns:
        shadow["retorno_medio_original_shadow"] = pd.to_numeric(shadow["retorno_medio"], errors="coerce")

    selective = shadow.apply(selective_realization_alert, axis=1, result_type="expand")
    selective.columns = ["shadow_alerta_realizacao_pos_rali", "shadow_sinais_realizacao", "shadow_qtd_sinais_realizacao"]
    shadow = pd.concat([shadow, selective], axis=1)
    if "alerta_realizacao_pos_rali" in shadow.columns:
        removed = shadow["alerta_realizacao_pos_rali"].fillna(False) & ~shadow["shadow_alerta_realizacao_pos_rali"].fillna(False)
        kept = shadow["shadow_alerta_realizacao_pos_rali"].fillna(False)
        shadow["alerta_realizacao_pos_rali"] = kept
        shadow.loc[removed, "motivos_alerta_realizacao_pos_rali"] = ""
        shadow.loc[kept, "motivos_alerta_realizacao_pos_rali"] = shadow.loc[kept, "shadow_sinais_realizacao"]
        shadow.loc[removed, "penalizacao_realizacao_pos_rali"] = False
        shadow.loc[removed, "penalizacoes_otimizacao"] = shadow.loc[removed, "penalizacoes_otimizacao"].map(lambda x: remove_tokens(x, ["penalizacao_realizacao_pos_rali"]))
        shadow.loc[removed, "alertas_nao_bloqueantes"] = shadow.loc[removed, "alertas_nao_bloqueantes"].map(lambda x: remove_tokens(x, ["alerta_realizacao_pos_rali"]))
        shadow.loc[removed & shadow.get("qualidade_do_timing", pd.Series("", index=shadow.index)).eq("timing_com_alerta"), "qualidade_do_timing"] = "timing_saudavel"
        shadow.loc[removed, "shadow_motivos_correcoes"] = shadow.loc[removed, "shadow_motivos_correcoes"].map(lambda x: append_token(x, "realizacao_pos_rali_removida_por_regra_seletiva"))

    if "retorno_medio" in shadow.columns:
        ret = pd.to_numeric(shadow["retorno_medio"], errors="coerce")
        negative = ret <= 0
        deterioration = shadow.apply(is_real_deterioration, axis=1)
        releasable = negative & ~deterioration
        keep_block = negative & deterioration
        shadow.loc[releasable, "motivo_bloqueio_otimizacao"] = shadow.loc[releasable, "motivo_bloqueio_otimizacao"].map(lambda x: remove_tokens(x, ["retorno_medio_negativo", "bloqueio_por_retorno_medio_negativo"]))
        shadow.loc[releasable, "tipo_bloqueio_otimizacao"] = shadow.loc[releasable, "tipo_bloqueio_otimizacao"].map(lambda x: remove_tokens(x, ["bloqueio_risco"]))
        shadow.loc[releasable, "penalizacoes_otimizacao"] = shadow.loc[releasable, "penalizacoes_otimizacao"].map(lambda x: append_token(x, "penalizacao_retorno_medio_negativo_shadow"))
        shadow.loc[releasable, "alertas_nao_bloqueantes"] = shadow.loc[releasable, "alertas_nao_bloqueantes"].map(lambda x: append_token(x, "alerta_retorno_medio_negativo_sem_deterioracao"))
        shadow.loc[releasable, "retorno_medio"] = 1e-6
        if "peso_maximo_timing_com_alerta" in shadow.columns:
            current_cap = pd.to_numeric(shadow.loc[releasable, "peso_maximo_timing_com_alerta"], errors="coerce")
            shadow.loc[releasable, "peso_maximo_timing_com_alerta"] = np.minimum(current_cap.fillna(0.10), 0.10)
        shadow.loc[releasable, "shadow_motivos_correcoes"] = shadow.loc[releasable, "shadow_motivos_correcoes"].map(lambda x: append_token(x, "retorno_medio_negativo_virou_penalizacao"))
        shadow.loc[keep_block, "shadow_motivos_correcoes"] = shadow.loc[keep_block, "shadow_motivos_correcoes"].map(lambda x: append_token(x, "retorno_medio_negativo_mantido_por_deterioracao"))

    reason = shadow.get("motivo_bloqueio_otimizacao", pd.Series("", index=shadow.index)).fillna("").astype(str).str.strip()
    status_ok = shadow.get("status_para_risco", pd.Series("", index=shadow.index)).isin(["aprovada_para_risco", "moderada_para_risco"])
    category_ok = shadow.get("categoria_elegibilidade", pd.Series("", index=shadow.index)).isin(["elegivel_forte", "elegivel_moderado"])
    has_risk = shadow.get("retorno_medio", pd.Series(np.nan, index=shadow.index)).notna()
    shadow["bloqueado_otimizacao"] = reason.ne("")
    shadow["liberado_para_otimizacao"] = (~shadow["bloqueado_otimizacao"]) & status_ok & category_ok & has_risk

    if regime == "mercado favoravel":
        beta = pd.to_numeric(shadow.get("beta", pd.Series(np.nan, index=shadow.index)), errors="coerce")
        corr = pd.to_numeric(shadow.get("correlacao_ibov", pd.Series(np.nan, index=shadow.index)), errors="coerce")
        decoupled = (beta < 0.30) & (corr < 0.20)
        if "peso_maximo_timing_com_alerta" in shadow.columns:
            current_cap = pd.to_numeric(shadow.loc[decoupled, "peso_maximo_timing_com_alerta"], errors="coerce")
            shadow.loc[decoupled, "peso_maximo_timing_com_alerta"] = np.minimum(current_cap.fillna(0.10), 0.10)
        shadow.loc[decoupled, "penalizacoes_otimizacao"] = shadow.loc[decoupled, "penalizacoes_otimizacao"].map(lambda x: append_token(x, "cap_10pct_beta_correlacao_baixos_shadow"))
        shadow.loc[decoupled, "shadow_motivos_correcoes"] = shadow.loc[decoupled, "shadow_motivos_correcoes"].map(lambda x: append_token(x, "cap_10pct_descolado_ibov"))
    return shadow


def enrich_candidate_input(candidates: pd.DataFrame, path: Path, include_downturn_cols: bool = False) -> pd.DataFrame:
    if candidates.empty or "ticker" not in candidates.columns:
        return candidates
    out = candidates.copy()
    wanted = [
        "forca_relativa_score", "classificacao_forca_relativa", "forca_relativa_positiva_relevante",
        "nota_final", "nota_preliminar_ajustada", "nota_preliminar", "retorno_1m_relativo_ibov",
        "retorno_4m_relativo_ibov", "retorno_ytd_relativo_ibov",
    ]
    if include_downturn_cols:
        wanted += [
            "rsi", "rsi_status", "bollinger_status", "bollinger_position", "distancia_banda_superior_pct", "distancia_banda_inferior_pct",
            "reversal_score", "rsi_reversal_signal", "bollinger_reversal_signal", "motivo_reversao", "aprovado_reversao",
            "roe", "roic", "margem_liquida", "margem_ebit", "pl_atual", "qualidade_fundamentalista", "risco_fundamentalista_mensal",
            "fundamento_bloqueante", "motivo_fundamento_bloqueante", "desvio_padrao", "cv", "beta", "correlacao_ibov",
            "retorno_acumulado_1m", "retorno_acumulado_4m", "tipo_timing", "tipo_watchlist", "perfil_risco_empresa",
        ]
    for sheet in ["Otimizacao", "Analise Preliminar"]:
        src = read_sheet(path, sheet)
        if src.empty or "ticker" not in src.columns:
            continue
        cols = ["ticker"] + [col for col in wanted if col in src.columns]
        if len(cols) == 1:
            continue
        lookup = src[cols].drop_duplicates("ticker").set_index("ticker")
        for col in cols[1:]:
            values = out["ticker"].map(lookup[col])
            if col not in out.columns:
                out[col] = values
            else:
                current = out[col]
                out[col] = current.where(pd.notna(current), values)
    if "forca_relativa_score" not in out.columns:
        out["forca_relativa_score"] = np.nan
    out["forca_relativa_score"] = pd.to_numeric(out["forca_relativa_score"], errors="coerce")
    return out

def load_candidate_input(path: Path, settings: dict | None = None) -> pd.DataFrame:
    expanded = bool(settings and composition_settings(settings)["enabled"])
    if expanded:
        candidates = read_sheet(path, "Otimizacao")
    else:
        candidates = read_sheet(path, "Candidatas Risco")
        if candidates.empty:
            candidates = read_sheet(path, "Otimizacao")
    if candidates.empty or "ticker" not in candidates.columns:
        return candidates
    candidates = enrich_candidate_input(candidates.drop_duplicates("ticker").copy(), path, include_downturn_cols=False)
    if expanded:
        cfg = composition_settings(settings or {})
        candidates = candidates.reset_index(drop=True)
        candidates["shadow_pre_risk_rank"] = np.arange(1, len(candidates) + 1)
        note = pd.to_numeric(candidates.get("nota_final", pd.Series(np.nan, index=candidates.index)), errors="coerce").fillna(pd.to_numeric(candidates.get("nota_preliminar_ajustada", pd.Series(0, index=candidates.index)), errors="coerce")).fillna(0)
        base_mask = candidates["shadow_pre_risk_rank"].le(cfg["base_topn"])
        extra_mask = candidates["shadow_pre_risk_rank"].gt(cfg["base_topn"]) & candidates["shadow_pre_risk_rank"].le(cfg["expanded_topn"]) & note.ge(cfg["extra_min_nota_final"])
        candidates["shadow_topn_base"] = base_mask
        candidates["shadow_topn_extra"] = extra_mask
        candidates = candidates[base_mask | extra_mask].copy()
    return candidates


def run_optimizer_for_month(mes: str, path: Path, base_settings: dict, shadow_fixes: bool, enable_partial_portfolio: bool | None = None, enable_beta_target: bool | None = None, enable_objetivo_retorno: bool | None = None, objetivo_variant: str | None = None, enable_composicao_ampliada: bool | None = None) -> dict[str, Any]:
    start = time.perf_counter()
    settings = prepare_settings(base_settings, path, shadow_fixes, enable_partial_portfolio=enable_partial_portfolio, enable_beta_target=enable_beta_target, enable_objetivo_retorno=enable_objetivo_retorno, objetivo_variant=objetivo_variant, enable_composicao_ampliada=enable_composicao_ampliada)
    candidates = load_candidate_input(path, settings)
    covariance = read_covariance(path)
    risk_expansion_note = ""
    if composition_settings(settings)["enabled"] and not candidates.empty and not covariance.empty:
        candidates, covariance, risk_expansion_note = expand_covariance_from_cache(candidates, covariance)
    if candidates.empty or covariance.empty:
        return {"portfolio": pd.DataFrame(), "metrics": {"status_carteira": "sem_dados"}, "candidates": candidates, "elapsed": time.perf_counter() - start}
    candidates = candidates[candidates["ticker"].astype(str).isin(covariance.index)].copy()
    if shadow_fixes:
        candidates = apply_shadow_fixes(candidates, settings.get("_runtime_market_class", ""))
        candidates = technical_veto_to_penalty_in_opportunity(candidates, settings) if beta_target_settings(settings)["enabled"] else candidates
        candidates = apply_expanded_composition_caps(candidates, settings)
    else:
        candidates = textify(candidates)
    permitted = candidates[candidates.get("liberado_para_otimizacao", pd.Series(False, index=candidates.index)).map(to_bool)].copy()
    permitted = permitted[permitted["ticker"].astype(str).isin(covariance.index)].copy()
    scored = score_assets(permitted, settings) if not permitted.empty else permitted
    scored = add_objetivo_retorno_signals(scored, settings)
    if beta_target_settings(settings)["enabled"] and not scored.empty and "liberado_por_d3" in scored.columns:
        scored = scored.copy()
        scored["_shadow_ordem_original"] = np.arange(len(scored))
        base_score = pd.to_numeric(scored.get("score_prioridade_otimizacao", scored.get("nota_final", pd.Series(0, index=scored.index))), errors="coerce").fillna(0)
        d3_penalty = scored["liberado_por_d3"].map(to_bool).astype(float) * 30.0
        petrobras_score = pd.to_numeric(scored.get("retorno_medio", pd.Series(0, index=scored.index)), errors="coerce").fillna(0)
        petrobras_rank = petrobras_score.where(scored["ticker"].astype(str).map(opt._risk_block_for_ticker).eq("PETROBRAS")).rank(method="first", ascending=False)
        petrobras_duplicate = scored["ticker"].astype(str).map(opt._risk_block_for_ticker).eq("PETROBRAS") & (petrobras_rank > 1)
        block_penalty = petrobras_duplicate.astype(float) * 60.0
        scored["penalizacao_d3_oportunidade_shadow"] = d3_penalty
        scored["penalizacao_bloco_duplicado_shadow"] = block_penalty
        scored["score_prioridade_otimizacao"] = base_score - d3_penalty - block_penalty
        scored = scored.sort_values(["score_prioridade_otimizacao", "_shadow_ordem_original"], ascending=[False, True]).drop(columns=["_shadow_ordem_original"]).reset_index(drop=True)
    portfolio, metrics = optimize_weights_shadow(scored, covariance, settings)
    partial_portfolio, partial_metrics = build_partial_portfolio(scored, metrics, settings) if partial_settings(settings)["enabled"] else (pd.DataFrame(), {"partial_enabled": False, "partial_acionada": False})
    if not partial_portfolio.empty:
        portfolio = partial_portfolio
        metrics.update(partial_metrics)
    else:
        metrics.update({"partial_acionada": False, **partial_metrics})
    portfolio = normalize_portfolio_weights(portfolio)
    metrics.update({
        "beta_target_enabled": beta_target_settings(settings)["enabled"],
        "beta_target_subtipo": settings.get("_runtime_beta_target_subtipo", ""),
        "beta_target": settings.get("_runtime_beta_target", np.nan),
        "beta_target_min": settings.get("_runtime_beta_target_min", np.nan),
        "beta_target_max": settings.get("_runtime_beta_target_max", np.nan),
        "beta_target_lambda": settings.get("_runtime_beta_target_lambda", np.nan),
        "beta_target_reason": settings.get("_runtime_beta_target_reason", ""),
        "objetivo_retorno_enabled": objetivo_retorno_settings(settings)["enabled"],
        "objetivo_retorno_variant": objetivo_retorno_settings(settings)["variant"] if objetivo_retorno_settings(settings)["enabled"] else "CV_ATUAL",
        "objetivo_retorno_lambda_cv": objetivo_retorno_settings(settings)["lambda_cv"],
        "objetivo_retorno_lambda_beta": objetivo_retorno_settings(settings)["lambda_beta"],
        "composicao_ampliada_enabled": composition_settings(settings)["enabled"],
        "composicao_candidate_counts": ", ".join(str(v) for v in composition_settings(settings)["candidate_counts"]),
        "composicao_expanded_topn": composition_settings(settings)["expanded_topn"],
        "composicao_base_topn": composition_settings(settings)["base_topn"],
        "composicao_expansao_risco_cache": risk_expansion_note,
    })
    metrics["shadow_alertas_validacao"] = "; ".join(validate_portfolio(portfolio, settings)) if not portfolio.empty and metrics.get("status_carteira") != "carteira_parcial" else ""
    candidates["shadow_liberado_para_otimizacao"] = candidates["ticker"].isin(permitted.get("ticker", pd.Series(dtype=str))) if "ticker" in candidates else False
    return {"portfolio": portfolio, "metrics": metrics, "candidates": candidates, "elapsed": time.perf_counter() - start}


def anchor_passed_for_month(mes: str, path: Path, portfolio: pd.DataFrame, metrics: dict) -> tuple[bool, str]:
    real_map = weights_map(real_portfolio(path))
    anchor_map = weights_map(portfolio)
    real_stat = real_status(path)
    if mes == "2026-06":
        expected_ok = same_weights(anchor_map, EXPECTED_JUNE)
        real_ok = same_weights(anchor_map, real_map)
        if expected_ok and real_ok:
            return True, "junho reproduz carteira real exatamente"
        return False, f"junho diferente | sombra={format_weights(anchor_map)} | real={format_weights(real_map)} | esperado={format_weights(EXPECTED_JUNE)} | status={metrics.get('status_carteira')}"
    expected_no_portfolio = "sem_carteira" in real_stat
    if expected_no_portfolio and not anchor_map:
        return True, "sem carteira como no real"
    if expected_no_portfolio and anchor_map:
        return False, f"montou carteira mas real era sem carteira | sombra={format_weights(anchor_map)}"
    if same_weights(anchor_map, real_map):
        return True, "reproduz carteira real"
    return False, f"diferente | sombra={format_weights(anchor_map)} | real={format_weights(real_map)} | status={metrics.get('status_carteira')}"


def build_summary_row(mes: str, path: Path, result: dict[str, Any], expost: pd.DataFrame, shadow_fixes: bool) -> dict[str, Any]:
    portfolio = result["portfolio"]
    metrics = result["metrics"]
    ibov = ibov_return(expost, mes)
    ret = portfolio_expost_return(portfolio, expost, mes)
    real_ret = portfolio_expost_return(real_portfolio(path), expost, mes)
    return {
        "mes": mes,
        "arquivo_real": MONTHS[mes],
        "shadow_fixes": shadow_fixes,
        "regime_mercado": market_regime(path),
        "subtipo_queda": metrics.get("subtipo_queda", ""),
        "sinal_quedas_cenario": metrics.get("sinal_quedas_cenario", ""),
        "sinal_quedas_aplicado": metrics.get("sinal_quedas_aplicado", ""),
        "motivo_subtipo_queda": metrics.get("motivo_subtipo_queda", ""),
        "status_real": real_status(path),
        "status_sombra": metrics.get("status_carteira", ""),
        "beta_target_enabled": bool(metrics.get("beta_target_enabled", False)),
        "beta_target_subtipo": metrics.get("beta_target_subtipo", ""),
        "beta_target": metrics.get("beta_target", np.nan),
        "beta_target_min": metrics.get("beta_target_min", np.nan),
        "beta_target_max": metrics.get("beta_target_max", np.nan),
        "beta_target_lambda": metrics.get("beta_target_lambda", np.nan),
        "beta_target_reason": metrics.get("beta_target_reason", ""),
        "beta_carteira_sombra": metrics.get("beta_carteira", np.nan),
        "desvio_beta_target": metrics.get("desvio_beta_target", np.nan),
        "objetivo_retorno_enabled": bool(metrics.get("objetivo_retorno_enabled", False)),
        "objetivo_retorno_variant": metrics.get("objetivo_retorno_variant", "CV_ATUAL"),
        "objetivo_retorno_lambda_cv": metrics.get("objetivo_retorno_lambda_cv", np.nan),
        "objetivo_retorno_lambda_beta": metrics.get("objetivo_retorno_lambda_beta", np.nan),
        "composicao_ampliada_enabled": bool(metrics.get("composicao_ampliada_enabled", False)),
        "composicao_candidate_counts": metrics.get("composicao_candidate_counts", ""),
        "composicao_expanded_topn": metrics.get("composicao_expanded_topn", np.nan),
        "composicao_base_topn": metrics.get("composicao_base_topn", np.nan),
        "composicao_expansao_risco_cache": metrics.get("composicao_expansao_risco_cache", ""),
        "objetivo_retorno_sinal_ponderado": metrics.get("objetivo_retorno_sinal_ponderado", np.nan),
        "objetivo_retorno_cv_norm": metrics.get("objetivo_retorno_cv_norm", np.nan),
        "objetivo_retorno_valor": metrics.get("objetivo_retorno_valor", np.nan),
        "nota_final_ponderada": metrics.get("nota_final_ponderada", np.nan),
        "forca_relativa_score_ponderada": metrics.get("forca_relativa_score_ponderada", np.nan),
        "carteira_sombra_formada": bool(weights_map(portfolio)),
        "partial_enabled": bool(metrics.get("partial_enabled", False)),
        "partial_acionada": bool(metrics.get("partial_acionada", False)),
        "percentual_investido": metrics.get("partial_invested", np.nan),
        "percentual_caixa": metrics.get("partial_cash", np.nan),
        "partial_trigger_reason": metrics.get("partial_trigger_reason", ""),
        "partial_histograma_rejeicao": metrics.get("partial_histograma_rejeicao", ""),
        "tickers_pesos_sombra": format_weights(weights_map(portfolio)),
        "retorno_expost_sombra": ret,
        "retorno_expost_ibov": ibov,
        "alfa_sombra": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
        "retorno_expost_real": real_ret,
        "alfa_real": real_ret - ibov if pd.notna(real_ret) and pd.notna(ibov) else np.nan,
        "delta_alfa_sombra_menos_real": (ret - ibov) - (real_ret - ibov) if pd.notna(ret) and pd.notna(real_ret) and pd.notna(ibov) else np.nan,
        "n_candidatos_real": len(result["candidates"]),
        "n_liberados_sombra": int(result["candidates"].get("shadow_liberado_para_otimizacao", pd.Series(False, index=result["candidates"].index)).fillna(False).sum()) if not result["candidates"].empty else 0,
        "n_alerta_realizacao_sombra": int(result["candidates"].get("alerta_realizacao_pos_rali", pd.Series(False, index=result["candidates"].index)).map(to_bool).sum()) if not result["candidates"].empty and "alerta_realizacao_pos_rali" in result["candidates"] else 0,
        "motivo_sombra": metrics.get("restricoes_violadas", metrics.get("motivo_escolha_final", "")),
        "tempo_segundos": result["elapsed"],
    }


def write_output(anchor_rows: list[dict[str, Any]], shadow_rows: list[dict[str, Any]], anchor_results: dict[str, Any], shadow_results: dict[str, Any], output_file: Path = OUTPUT_FILE) -> None:
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="anchor_vs_real", index=False)
        shadow_frame = pd.DataFrame(shadow_rows)
        shadow_frame.to_excel(writer, sheet_name="shadow_vs_real", index=False)
        if not shadow_frame.empty and "mes" in shadow_frame.columns:
            for _, row in shadow_frame.iterrows():
                suffix = str(row.get("mes", "")).replace("-", "_")
                if suffix:
                    pd.DataFrame([row]).to_excel(writer, sheet_name=f"comparativo_{suffix}"[:31], index=False)
        for prefix, results in [("anchor", anchor_results), ("shadow", shadow_results)]:
            for mes, result in results.items():
                suffix = mes.replace("-", "_")
                result["portfolio"].to_excel(writer, sheet_name=f"{prefix}_cart_{suffix}"[:31], index=False)
                result["metrics"].get("comparativo_carteiras", pd.DataFrame()).to_excel(writer, sheet_name=f"{prefix}_comp_{suffix}"[:31], index=False)
                cand = result["candidates"]
                cols = [c for c in ["ticker", "nome", "setor", "retorno_medio_original_shadow", "retorno_medio", "roe", "margem_liquida", "pl_atual", "beta", "correlacao_ibov", "rsi", "retorno_acumulado_1m", "distancia_banda_superior_pct", "perfil_risco_empresa", "alerta_realizacao_pos_rali", "shadow_qtd_sinais_realizacao", "shadow_sinais_realizacao", "bloqueado_otimizacao", "motivo_bloqueio_otimizacao", "penalizacoes_otimizacao", "status_para_risco", "categoria_elegibilidade", "tipo_watchlist", "shadow_liberado_antes_d3", "shadow_liberado_para_otimizacao", "liberado_por_d3", "motivo_bloqueio_original_d3", "penalizacao_d3_oportunidade_shadow", "penalizacao_bloco_duplicado_shadow", "score_prioridade_otimizacao", "forca_relativa_score", "nota_final", "objetivo_retorno_variant", "_shadow_signal_v1_norm", "_shadow_signal_v2_norm", "_shadow_signal_v3_norm", "_shadow_objetivo_sinal_norm", "shadow_motivos_correcoes", "shadow_beta_target_motivos", "shadow_pre_risk_rank", "shadow_topn_base", "shadow_topn_extra", "shadow_composicao_cap_qualidade", "shadow_composicao_motivo_cap", "shadow_composicao_timing_tardio_ou_especulativo", "shadow_tamanho_livre_aprovada", "shadow_tamanho_livre_motivo_exclusao", "shadow_tamanho_livre_sinal_v3", "peso_antes_teto_tamanho_livre", "teto_tamanho_livre_aplicado", "beta_target", "desvio_beta_target"] if c in cand.columns]
                cand.reindex(columns=cols).to_excel(writer, sheet_name=f"{prefix}_cand_{suffix}"[:31], index=False)


def _result_sheets(writer: pd.ExcelWriter, prefix: str, results: dict[str, Any]) -> None:
    for mes, result in results.items():
        suffix = mes.replace("-", "_")
        result["portfolio"].to_excel(writer, sheet_name=f"{prefix}_cart_{suffix}"[:31], index=False)
        result["metrics"].get("comparativo_carteiras", pd.DataFrame()).to_excel(writer, sheet_name=f"{prefix}_comp_{suffix}"[:31], index=False)
        cand = result["candidates"]
        cols = [c for c in ["ticker", "nome", "setor", "retorno_medio_original_shadow", "retorno_medio", "roe", "margem_liquida", "pl_atual", "beta", "correlacao_ibov", "rsi", "retorno_acumulado_1m", "distancia_banda_superior_pct", "perfil_risco_empresa", "alerta_realizacao_pos_rali", "shadow_qtd_sinais_realizacao", "shadow_sinais_realizacao", "bloqueado_otimizacao", "motivo_bloqueio_otimizacao", "penalizacoes_otimizacao", "status_para_risco", "categoria_elegibilidade", "tipo_watchlist", "shadow_liberado_antes_d3", "shadow_liberado_para_otimizacao", "liberado_por_d3", "motivo_bloqueio_original_d3", "penalizacao_d3_oportunidade_shadow", "penalizacao_bloco_duplicado_shadow", "score_prioridade_otimizacao", "forca_relativa_score", "nota_final", "objetivo_retorno_variant", "_shadow_signal_v1_norm", "_shadow_signal_v2_norm", "_shadow_signal_v3_norm", "_shadow_objetivo_sinal_norm", "shadow_motivos_correcoes", "shadow_beta_target_motivos", "shadow_pre_risk_rank", "shadow_topn_base", "shadow_topn_extra", "shadow_composicao_cap_qualidade", "shadow_composicao_motivo_cap", "shadow_composicao_timing_tardio_ou_especulativo", "shadow_tamanho_livre_aprovada", "shadow_tamanho_livre_motivo_exclusao", "shadow_tamanho_livre_sinal_v3", "peso_antes_teto_tamanho_livre", "teto_tamanho_livre_aplicado", "beta_target", "desvio_beta_target"] if c in cand.columns]
        cand.reindex(columns=cols).to_excel(writer, sheet_name=f"{prefix}_cand_{suffix}"[:31], index=False)


def objective_comparison_rows(cv_rows: list[dict[str, Any]], variant_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cv_by_month = {row["mes"]: row for row in cv_rows}
    rows = []
    for row in variant_rows:
        cv = cv_by_month.get(row["mes"], {})
        rows.append({
            "mes": row.get("mes"),
            "variante": row.get("objetivo_retorno_variant"),
            "status_cv_atual": cv.get("status_sombra", ""),
            "status_objetivo_retorno": row.get("status_sombra", ""),
            "carteira_cv_atual": cv.get("tickers_pesos_sombra", ""),
            "carteira_objetivo_retorno": row.get("tickers_pesos_sombra", ""),
            "beta_cv_atual": cv.get("beta_carteira_sombra", np.nan),
            "beta_objetivo_retorno": row.get("beta_carteira_sombra", np.nan),
            "retorno_expost_cv_atual": cv.get("retorno_expost_sombra", np.nan),
            "retorno_expost_objetivo_retorno": row.get("retorno_expost_sombra", np.nan),
            "ibov_expost": row.get("retorno_expost_ibov", np.nan),
            "alfa_cv_atual": cv.get("alfa_sombra", np.nan),
            "alfa_objetivo_retorno": row.get("alfa_sombra", np.nan),
            "delta_alfa_objetivo_menos_cv": row.get("alfa_sombra", np.nan) - cv.get("alfa_sombra", np.nan) if pd.notna(row.get("alfa_sombra", np.nan)) and pd.notna(cv.get("alfa_sombra", np.nan)) else np.nan,
            "objetivo_retorno_sinal_ponderado": row.get("objetivo_retorno_sinal_ponderado", np.nan),
            "objetivo_retorno_cv_norm": row.get("objetivo_retorno_cv_norm", np.nan),
            "objetivo_retorno_valor": row.get("objetivo_retorno_valor", np.nan),
            "nota_ponderada_cv_atual": cv.get("nota_final_ponderada", np.nan),
            "nota_ponderada_objetivo": row.get("nota_final_ponderada", np.nan),
            "forca_ponderada_cv_atual": cv.get("forca_relativa_score_ponderada", np.nan),
            "forca_ponderada_objetivo": row.get("forca_relativa_score_ponderada", np.nan),
            "motivo_cv_atual": cv.get("motivo_sombra", ""),
            "motivo_objetivo_retorno": row.get("motivo_sombra", ""),
        })
    return rows


def write_objective_output(anchor_rows: list[dict[str, Any]], cv_rows: list[dict[str, Any]], variant_rows: list[dict[str, Any]], anchor_results: dict[str, Any], cv_results: dict[str, Any], variant_results: dict[str, dict[str, Any]], output_file: Path) -> None:
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="anchor_vs_real", index=False)
        all_rows = pd.DataFrame(cv_rows + variant_rows)
        all_rows.to_excel(writer, sheet_name="shadow_vs_real", index=False)
        pd.DataFrame(objective_comparison_rows(cv_rows, variant_rows)).to_excel(writer, sheet_name="comparativo_objetivos", index=False)
        _result_sheets(writer, "anchor", anchor_results)
        _result_sheets(writer, "cv", cv_results)
        for variant, results in variant_results.items():
            _result_sheets(writer, variant.lower(), results)


def has_fundamental_deterioration_in_portfolio(portfolio: pd.DataFrame) -> bool:
    if portfolio.empty:
        return False
    return bool(portfolio.apply(is_real_deterioration, axis=1).any())


def run_objective_mode(log, log_lines: list[str], base_settings: dict, expost: pd.DataFrame, anchor_rows: list[dict[str, Any]], anchor_results: dict[str, Any], output_file: Path, log_file: Path) -> None:
    log("ANCORA PASSOU. Executando objetivo-retorno em modo sombra: CV_ATUAL + V1/V2/V3.")
    cv_rows: list[dict[str, Any]] = []
    cv_results: dict[str, Any] = {}
    variant_rows: list[dict[str, Any]] = []
    variant_results: dict[str, dict[str, Any]] = {"V1": {}, "V2": {}, "V3": {}}
    sim_settings = copy.deepcopy(base_settings)
    sim_settings.setdefault("shadow", {})
    sim_settings["shadow"]["beta_target_lambda"] = 0.0
    sim_settings["shadow"]["objetivo_retorno_lambda_beta"] = 0.0

    def summarize(label: str, mes: str, row: dict[str, Any], result: dict[str, Any]) -> None:
        alpha = row["alfa_sombra"] * 100 if pd.notna(row["alfa_sombra"]) else np.nan
        ret = row["retorno_expost_sombra"] * 100 if pd.notna(row["retorno_expost_sombra"]) else np.nan
        ibov = row["retorno_expost_ibov"] * 100 if pd.notna(row["retorno_expost_ibov"]) else np.nan
        msg = f"{label} {mes}: status={row['status_sombra']} | beta={row.get('beta_carteira_sombra', np.nan)} | retorno={ret:.2f}% | IBOV={ibov:.2f}% | alfa={alpha:.2f}% | pesos={row['tickers_pesos_sombra']}"
        portfolio = result.get("portfolio", pd.DataFrame())
        if mes == "2026-05" and row["carteira_sombra_formada"]:
            counts = portfolio["setor"].fillna("Outros").value_counts() if "setor" in portfolio else pd.Series(dtype=int)
            weights_sector = portfolio.groupby("setor")["peso_recomendado"].sum() if "setor" in portfolio and "peso_recomendado" in portfolio else pd.Series(dtype=float)
            if bool((counts > 2).any() or (weights_sector > 0.40 + 1e-9).any()):
                msg = f"{RED}REGRESSAO: MAIO MONTOU CARTEIRA CONCENTRADA. {msg}{RESET}"
        if row["carteira_sombra_formada"] and has_fundamental_deterioration_in_portfolio(portfolio):
            msg = f"{RED}REGRESSAO: ativo com deterioracao fundamental real entrou. {msg}{RESET}"
        log(msg)

    for mes in MONTHS:
        path = workbook_path(mes)
        result = run_optimizer_for_month(mes, path, sim_settings, shadow_fixes=True, enable_partial_portfolio=False, enable_beta_target=True, enable_objetivo_retorno=False)
        row = build_summary_row(mes, path, result, expost, shadow_fixes=True)
        row["cenario_objetivo"] = "CV_ATUAL"
        cv_rows.append(row)
        cv_results[mes] = result
        summarize("CV_ATUAL", mes, row, result)

    for variant in ["V1", "V2", "V3"]:
        log(f"Rodando variante {variant} da objetivo-retorno.")
        for mes in MONTHS:
            path = workbook_path(mes)
            result = run_optimizer_for_month(mes, path, sim_settings, shadow_fixes=True, enable_partial_portfolio=False, enable_beta_target=True, enable_objetivo_retorno=True, objetivo_variant=variant)
            row = build_summary_row(mes, path, result, expost, shadow_fixes=True)
            row["cenario_objetivo"] = variant
            variant_rows.append(row)
            variant_results[variant][mes] = result
            summarize(variant, mes, row, result)

    cv_jun = next((row for row in cv_rows if row.get("mes") == "2026-06"), {})
    v1_jun = next((row for row in variant_rows if row.get("mes") == "2026-06" and row.get("cenario_objetivo") == "V1"), {})
    v2_jun = next((row for row in variant_rows if row.get("mes") == "2026-06" and row.get("cenario_objetivo") == "V2"), {})
    log(f"VALIDACAO JUNHO NOTA: CV_ATUAL={cv_jun.get('nota_final_ponderada', np.nan):.4f} | V1={v1_jun.get('nota_final_ponderada', np.nan):.4f}")
    log(f"VALIDACAO JUNHO FORCA: CV_ATUAL={cv_jun.get('forca_relativa_score_ponderada', np.nan):.4f} | V2={v2_jun.get('forca_relativa_score_ponderada', np.nan):.4f}")
    cand_v2_jun = variant_results.get("V2", {}).get("2026-06", {}).get("candidates", pd.DataFrame())
    if not cand_v2_jun.empty and "forca_relativa_score" in cand_v2_jun.columns:
        exemplos = cand_v2_jun[cand_v2_jun["ticker"].astype(str).isin(["PETR3.SA", "GGBR4.SA", "ABEV3.SA"])]
        if exemplos.empty:
            exemplos = cand_v2_jun.head(3)
        log("FORCA_RELATIVA_SCORE JUNHO: " + " | ".join(f"{r.get('ticker')}: {r.get('forca_relativa_score')}" for _, r in exemplos.iterrows()))
    validation_failed = False
    if pd.notna(cv_jun.get("nota_final_ponderada", np.nan)) and pd.notna(v1_jun.get("nota_final_ponderada", np.nan)) and not (v1_jun.get("nota_final_ponderada") > cv_jun.get("nota_final_ponderada")):
        validation_failed = True
        log(f"{RED}VALIDACAO FALHOU: V1 nao superou CV_ATUAL em nota ponderada. Parei a rodada como solicitado.{RESET}")
    if pd.notna(cv_jun.get("forca_relativa_score_ponderada", np.nan)) and pd.notna(v2_jun.get("forca_relativa_score_ponderada", np.nan)) and not (v2_jun.get("forca_relativa_score_ponderada") > cv_jun.get("forca_relativa_score_ponderada")):
        validation_failed = True
        log(f"{RED}VALIDACAO FALHOU: V2 nao superou CV_ATUAL em forca ponderada. Parei a rodada como solicitado.{RESET}")
    write_objective_output(anchor_rows, cv_rows, variant_rows, anchor_results, cv_results, variant_results, output_file)
    log(f"Arquivo gerado: {output_file}")
    log_file.write_text("\n".join(log_lines), encoding="utf-8")
    log(f"Log gerado: {log_file}")


def portfolio_detail_rows(results: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for mes, result in results.items():
        portfolio = result.get("portfolio", pd.DataFrame())
        if portfolio.empty:
            continue
        frame = portfolio.copy()
        frame.insert(0, "mes", mes)
        rows.append(frame)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def composition_validation_rows(results: dict[str, Any], expost: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for mes, result in results.items():
        portfolio = result.get("portfolio", pd.DataFrame())
        metrics = result.get("metrics", {})
        ret_reported = portfolio_expost_return(portfolio, expost, mes)
        ret_manual = np.nan
        if not portfolio.empty:
            mapping = weights_map(portfolio)
            month = expost[expost["mes"].astype(str).eq(mes)].set_index("ticker")
            ret_manual = float(sum(weight * month["retorno_realizado_periodo"].get(ticker, np.nan) for ticker, weight in mapping.items()))
        sector_count_max = 0
        sector_weight_max = np.nan
        if not portfolio.empty and "setor" in portfolio.columns:
            sector_count_max = int(portfolio.groupby("setor")["ticker"].count().max())
            sector_weight_max = float(portfolio.groupby("setor")["peso_recomendado"].sum().max())
        late_mask = portfolio.get("shadow_composicao_timing_tardio_ou_especulativo", pd.Series(False, index=portfolio.index)).map(to_bool) if not portfolio.empty else pd.Series(dtype=bool)
        late_cap_violation = False
        late_weight_violation = False
        if not portfolio.empty and not late_mask.empty:
            late_cap_violation = bool((pd.to_numeric(portfolio.loc[late_mask, "peso_maximo_permitido_ativo"], errors="coerce") > 0.050001).any()) if "peso_maximo_permitido_ativo" in portfolio else False
            late_weight_violation = bool((pd.to_numeric(portfolio.loc[late_mask, "peso_recomendado"], errors="coerce") > 0.050001).any()) if "peso_recomendado" in portfolio else False
        extra = portfolio[portfolio.get("shadow_topn_extra", pd.Series(False, index=portfolio.index)).map(to_bool)].copy() if not portfolio.empty and "shadow_topn_extra" in portfolio else pd.DataFrame()
        rows.append({
            "mes": mes,
            "status_sombra": metrics.get("status_carteira", ""),
            "retorno_reportado": ret_reported,
            "retorno_manual_peso_x_ativo": ret_manual,
            "diferenca_retorno": ret_manual - ret_reported if pd.notna(ret_manual) and pd.notna(ret_reported) else np.nan,
            "max_acoes_por_setor": sector_count_max,
            "maior_peso_setorial": sector_weight_max,
            "violou_max_2_por_setor": bool(sector_count_max > 2),
            "tem_deterioracao_fundamental_real": has_fundamental_deterioration_in_portfolio(portfolio),
            "timing_tardio_ou_especulativo_com_teto_acima_5pct": late_cap_violation,
            "timing_tardio_ou_especulativo_com_peso_acima_5pct": late_weight_violation,
            "qtd_extras_top30_entraram": len(extra),
            "extras_top30_entraram": "; ".join(f"{r.get('ticker')} nota={to_float(r.get('nota_final'), np.nan):.0f} peso={to_float(r.get('peso_recomendado'), np.nan):.2%}" for _, r in extra.iterrows()),
        })
    return rows


def write_composition_output(anchor_rows: list[dict[str, Any]], shadow_rows: list[dict[str, Any]], anchor_results: dict[str, Any], shadow_results: dict[str, Any], validation_rows: list[dict[str, Any]], output_file: Path) -> None:
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        pd.DataFrame(shadow_rows).to_excel(writer, sheet_name="shadow_vs_real", index=False)
        portfolio_detail_rows(shadow_results).to_excel(writer, sheet_name="carteiras_por_mes", index=False)
        pd.DataFrame(validation_rows).to_excel(writer, sheet_name="validacao_retorno", index=False)
        _result_sheets(writer, "anchor", anchor_results)
        _result_sheets(writer, "shadow", shadow_results)


def run_composition_mode(log, log_lines: list[str], base_settings: dict, expost: pd.DataFrame, anchor_rows: list[dict[str, Any]], anchor_results: dict[str, Any], output_file: Path, log_file: Path) -> None:
    log("ANCORA PASSOU. Executando composicao ampliada em modo sombra: V3 combinado, lambda_cv=0.5, lambda_beta=0.")
    sim_settings = copy.deepcopy(base_settings)
    sim_settings.setdefault("shadow", {})
    sim_settings["shadow"]["enable_beta_target"] = True
    sim_settings["shadow"]["beta_target_lambda"] = 0.0
    sim_settings["shadow"]["enable_objetivo_retorno"] = True
    sim_settings["shadow"]["objetivo_retorno_variant"] = "V3"
    sim_settings["shadow"]["objetivo_retorno_lambda_cv"] = 0.5
    sim_settings["shadow"]["objetivo_retorno_lambda_beta"] = 0.0
    sim_settings["shadow"]["enable_composicao_ampliada"] = True
    sim_settings["shadow"].setdefault("composicao_candidate_counts", [5, 6, 8, 10, 12, 15])
    sim_settings["shadow"].setdefault("composicao_base_topn", 25)
    sim_settings["shadow"].setdefault("composicao_expanded_topn", 30)
    sim_settings["shadow"].setdefault("composicao_extra_min_nota_final", 50)

    shadow_rows: list[dict[str, Any]] = []
    shadow_results: dict[str, Any] = {}
    for mes in MONTHS:
        path = workbook_path(mes)
        result = run_optimizer_for_month(
            mes,
            path,
            sim_settings,
            shadow_fixes=True,
            enable_partial_portfolio=False,
            enable_beta_target=True,
            enable_objetivo_retorno=True,
            objetivo_variant="V3",
            enable_composicao_ampliada=True,
        )
        row = build_summary_row(mes, path, result, expost, shadow_fixes=True)
        row["cenario_objetivo"] = "V3_COMPOSICAO_AMPLIADA"
        shadow_rows.append(row)
        shadow_results[mes] = result
        ret = row["retorno_expost_sombra"] * 100 if pd.notna(row["retorno_expost_sombra"]) else np.nan
        ibov = row["retorno_expost_ibov"] * 100 if pd.notna(row["retorno_expost_ibov"]) else np.nan
        alpha = row["alfa_sombra"] * 100 if pd.notna(row["alfa_sombra"]) else np.nan
        portfolio = result.get("portfolio", pd.DataFrame())
        size = len(portfolio) if not portfolio.empty else 0
        msg = f"{mes}: status={row['status_sombra']} | tamanho={size} | pesos={row['tickers_pesos_sombra']} | retorno={ret:.2f}% | IBOV={ibov:.2f}% | alfa={alpha:.2f}% | beta={row.get('beta_carteira_sombra', np.nan):.2f} | tempo={result['elapsed']:.1f}s"
        if mes == "2026-05" and not portfolio.empty and "setor" in portfolio and int(portfolio.groupby("setor")["ticker"].count().max()) > 2:
            log(f"{RED}REGRESSAO: maio violou maximo de 2 ativos por setor. {msg}{RESET}")
        else:
            log(msg)
        if mes == "2026-02":
            log(f"FEVEREIRO COMPOSICAO: tamanho={size}; pesos={row['tickers_pesos_sombra']}; alfa={alpha:.2f}% contra IBOV {ibov:.2f}%")

    validation_rows = composition_validation_rows(shadow_results, expost)
    validation = pd.DataFrame(validation_rows)
    if not validation.empty:
        bad_return = validation["diferenca_retorno"].abs().fillna(0).max()
        log(f"VALIDACAO RETORNO: maior diferenca manual vs reportado = {bad_return:.10f}")
        if validation["tem_deterioracao_fundamental_real"].fillna(False).any():
            log(f"{RED}REGRESSAO: entrou ativo com deterioracao fundamental real.{RESET}")
        if validation["timing_tardio_ou_especulativo_com_teto_acima_5pct"].fillna(False).any() or validation["timing_tardio_ou_especulativo_com_peso_acima_5pct"].fillna(False).any():
            log(f"{RED}REGRESSAO: timing tardio/especulativo recebeu teto ou peso acima de 5%.{RESET}")
        extra_total = int(validation["qtd_extras_top30_entraram"].fillna(0).sum())
        log(f"ALAVANCA 3: extras Top N 30 que entraram em carteiras finais = {extra_total}")
        for _, row in validation[validation["qtd_extras_top30_entraram"].fillna(0).gt(0)].iterrows():
            log(f"  {row['mes']}: {row['extras_top30_entraram']}")

    write_composition_output(anchor_rows, shadow_rows, anchor_results, shadow_results, validation_rows, output_file)
    log(f"Arquivo gerado: {output_file}")
    log_file.write_text("\n".join(log_lines), encoding="utf-8")
    log(f"Log gerado: {log_file}")

def run_free_size_for_month(mes: str, path: Path, base_settings: dict, lambda_beta: float = 0.0, downturn_signal: str = "V3_MOMENTUM") -> dict[str, Any]:
    start = time.perf_counter()
    settings = prepare_settings(
        base_settings,
        path,
        shadow_fixes=True,
        enable_partial_portfolio=False,
        enable_beta_target=True,
        enable_objetivo_retorno=True,
        objetivo_variant="V3",
    )
    settings.setdefault("shadow", {})
    settings["shadow"]["enable_carteira_tamanho_livre"] = True
    settings["shadow"]["objetivo_retorno_variant"] = "V3"
    settings["shadow"]["objetivo_retorno_lambda_cv"] = 0.5
    settings["shadow"]["objetivo_retorno_lambda_beta"] = float(lambda_beta)
    settings["shadow"]["beta_target_lambda"] = float(lambda_beta)
    settings["_runtime_beta_target_lambda"] = float(lambda_beta)
    settings["_runtime_downturn_profile"] = downturn_regime_profile(path, settings)
    settings["_runtime_downturn_signal"] = downturn_signal

    candidates = load_candidate_input(path, settings)
    covariance = read_covariance(path)
    if candidates.empty or covariance.empty:
        return {"portfolio": pd.DataFrame(), "metrics": {"status_carteira": "sem_dados"}, "candidates": candidates, "elapsed": time.perf_counter() - start}
    candidates = candidates[candidates["ticker"].astype(str).isin(covariance.index)].copy()
    candidates = apply_shadow_fixes(candidates, settings.get("_runtime_market_class", ""))
    candidates = technical_veto_to_penalty_in_opportunity(candidates, settings)
    permitted = candidates[candidates.get("liberado_para_otimizacao", pd.Series(False, index=candidates.index)).map(to_bool)].copy()
    permitted = permitted[permitted["ticker"].astype(str).isin(covariance.index)].copy()
    scored = score_assets(permitted, settings) if not permitted.empty else permitted
    scored = add_objetivo_retorno_signals(scored, settings)
    if downturn_signal != "V3_MOMENTUM":
        scored = enrich_candidate_input(scored, path, include_downturn_cols=True)
    scored = apply_downturn_signal(scored, settings, downturn_signal)
    portfolio, metrics, audit = build_free_size_portfolio(scored, covariance, settings)
    portfolio = normalize_portfolio_weights(portfolio)
    metrics.update({
        "beta_target_enabled": beta_target_settings(settings)["enabled"],
        "beta_target_subtipo": settings.get("_runtime_beta_target_subtipo", ""),
        "beta_target": settings.get("_runtime_beta_target", np.nan),
        "beta_target_min": settings.get("_runtime_beta_target_min", np.nan),
        "beta_target_max": settings.get("_runtime_beta_target_max", np.nan),
        "beta_target_lambda": float(lambda_beta),
        "desvio_beta_target": metrics.get("tamanho_livre_distancia_beta_target", np.nan),
        "beta_target_reason": settings.get("_runtime_beta_target_reason", ""),
        "objetivo_retorno_enabled": True,
        "objetivo_retorno_variant": "V3",
        "objetivo_retorno_lambda_cv": 0.5,
        "objetivo_retorno_lambda_beta": lambda_beta,
        "partial_enabled": False,
        "partial_acionada": False,
        "composicao_ampliada_enabled": False,
        "tamanho_livre_enabled": True,
        "sinal_quedas_cenario": downturn_signal,
        "sinal_quedas_aplicado": downturn_signal if settings.get("_runtime_downturn_profile", {}).get("subtipo_queda") != "alta" else "V3_MOMENTUM",
        "subtipo_queda": settings.get("_runtime_downturn_profile", {}).get("subtipo_queda", ""),
        "motivo_subtipo_queda": settings.get("_runtime_downturn_profile", {}).get("motivo_subtipo_queda", ""),
    })
    if not portfolio.empty:
        alerts = []
        if len(portfolio) < free_size_settings(settings)["min_assets"]:
            alerts.append("Carteira com menos que o minimo de acoes")
        if portfolio["peso_recomendado"].max() > free_size_settings(settings)["individual_cap"] + 1e-9:
            alerts.append("Peso individual acima do teto tamanho livre")
        if "setor" in portfolio and portfolio.groupby("setor")["ticker"].count().max() > int(settings.get("portfolio", {}).get("max_assets_per_sector", 2)):
            alerts.append("Maximo de acoes por setor excedido")
        if has_fundamental_deterioration_in_portfolio(portfolio):
            alerts.append("Ativo com deterioracao fundamental real")
        metrics["shadow_alertas_validacao"] = "; ".join(alerts)
    candidates["shadow_liberado_para_otimizacao"] = candidates["ticker"].isin(permitted.get("ticker", pd.Series(dtype=str))) if "ticker" in candidates else False
    return {"portfolio": portfolio, "metrics": metrics, "candidates": audit if not audit.empty else candidates, "elapsed": time.perf_counter() - start}


def free_size_validation_rows(results: dict[str, Any], expost: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for mes, result in results.items():
        portfolio = result.get("portfolio", pd.DataFrame())
        metrics = result.get("metrics", {})
        ret_reported = portfolio_expost_return(portfolio, expost, mes)
        ret_manual = np.nan
        if not portfolio.empty:
            mapping = weights_map(portfolio)
            month = expost[expost["mes"].astype(str).eq(mes)].set_index("ticker")
            ret_manual = float(sum(weight * month["retorno_realizado_periodo"].get(ticker, np.nan) for ticker, weight in mapping.items()))
        sector_count_max = int(portfolio.groupby("setor")["ticker"].count().max()) if not portfolio.empty and "setor" in portfolio else 0
        rows.append({
            "mes": mes,
            "status_sombra": metrics.get("status_carteira", ""),
            "numero_acoes_formadas": len(portfolio) if not portfolio.empty else 0,
            "numero_acoes_aprovadas": metrics.get("tamanho_livre_numero_aprovadas", 0),
            "retorno_reportado": ret_reported,
            "retorno_manual_peso_x_ativo": ret_manual,
            "diferenca_retorno": ret_manual - ret_reported if pd.notna(ret_manual) and pd.notna(ret_reported) else np.nan,
            "maior_peso_individual": float(portfolio["peso_recomendado"].max()) if not portfolio.empty and "peso_recomendado" in portfolio else np.nan,
            "alguma_acao_acima_25pct": bool((portfolio["peso_recomendado"] > 0.250001).any()) if not portfolio.empty and "peso_recomendado" in portfolio else False,
            "max_acoes_por_setor": sector_count_max,
            "violou_max_2_por_setor": bool(sector_count_max > 2),
            "tem_deterioracao_fundamental_real": has_fundamental_deterioration_in_portfolio(portfolio),
            "carteira_com_menos_de_5_acoes": bool((not portfolio.empty) and len(portfolio) < 5),
            "alertas_validacao": metrics.get("shadow_alertas_validacao", ""),
        })
    return rows


def write_free_size_output(anchor_rows: list[dict[str, Any]], shadow_rows: list[dict[str, Any]], anchor_results: dict[str, Any], shadow_results: dict[str, Any], validation_rows: list[dict[str, Any]], output_file: Path) -> None:
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        pd.DataFrame(shadow_rows).to_excel(writer, sheet_name="shadow_vs_real", index=False)
        portfolio_detail_rows(shadow_results).to_excel(writer, sheet_name="carteiras_por_mes", index=False)
        pd.DataFrame(validation_rows).to_excel(writer, sheet_name="validacao_retorno", index=False)
        _result_sheets(writer, "anchor", anchor_results)
        _result_sheets(writer, "shadow", shadow_results)


def run_free_size_mode(log, log_lines: list[str], base_settings: dict, expost: pd.DataFrame, anchor_rows: list[dict[str, Any]], anchor_results: dict[str, Any], output_file: Path, log_file: Path) -> None:
    log("ANCORA PASSOU. Executando carteira de tamanho livre em modo sombra: V3 combinado, lambda_cv=0.5, lambda_beta=0.")
    shadow_rows: list[dict[str, Any]] = []
    shadow_results: dict[str, Any] = {}
    for mes in MONTHS:
        path = workbook_path(mes)
        result = run_free_size_for_month(mes, path, base_settings)
        row = build_summary_row(mes, path, result, expost, shadow_fixes=True)
        row["cenario_objetivo"] = "V3_TAMANHO_LIVRE"
        shadow_rows.append(row)
        shadow_results[mes] = result
        ret = row["retorno_expost_sombra"] * 100 if pd.notna(row["retorno_expost_sombra"]) else np.nan
        ibov = row["retorno_expost_ibov"] * 100 if pd.notna(row["retorno_expost_ibov"]) else np.nan
        alpha = row["alfa_sombra"] * 100 if pd.notna(row["alfa_sombra"]) else np.nan
        portfolio = result.get("portfolio", pd.DataFrame())
        size = len(portfolio) if not portfolio.empty else 0
        msg = f"{mes}: status={row['status_sombra']} | acoes={size} | aprovadas={result['metrics'].get('tamanho_livre_numero_aprovadas', 0)} | pesos={row['tickers_pesos_sombra']} | retorno={ret:.2f}% | IBOV={ibov:.2f}% | alfa={alpha:.2f}% | beta={row.get('beta_carteira_sombra', np.nan):.2f} | tempo={result['elapsed']:.1f}s"
        if mes == "2026-05" and not portfolio.empty and "setor" in portfolio and int(portfolio.groupby("setor")["ticker"].count().max()) > 2:
            log(f"{RED}REGRESSAO: maio violou maximo de 2 ativos por setor. {msg}{RESET}")
        else:
            log(msg)
        if mes == "2026-02":
            log(f"FEVEREIRO TAMANHO LIVRE: acoes={size}; pesos={row['tickers_pesos_sombra']}; alfa={alpha:.2f}% contra IBOV {ibov:.2f}%")
        if mes == "2026-06":
            log(f"JUNHO TAMANHO LIVRE: acoes={size}; pesos={row['tickers_pesos_sombra']}; alfa={alpha:.2f}%")

    validation_rows = free_size_validation_rows(shadow_results, expost)
    validation = pd.DataFrame(validation_rows)
    if not validation.empty:
        bad_return = validation["diferenca_retorno"].abs().fillna(0).max()
        log(f"VALIDACAO RETORNO: maior diferenca manual vs reportado = {bad_return:.10f}")
        if bad_return > 0.0001:
            log(f"{RED}REGRESSAO: diferenca de retorno acima de 0.0001.{RESET}")
        if validation["alguma_acao_acima_25pct"].fillna(False).any():
            log(f"{RED}REGRESSAO: existe acao acima de 25%.{RESET}")
        if validation["tem_deterioracao_fundamental_real"].fillna(False).any():
            log(f"{RED}REGRESSAO: entrou ativo com deterioracao fundamental real.{RESET}")
        if validation["carteira_com_menos_de_5_acoes"].fillna(False).any():
            log(f"{RED}REGRESSAO: carteira formada com menos de 5 acoes.{RESET}")

    write_free_size_output(anchor_rows, shadow_rows, anchor_results, shadow_results, validation_rows, output_file)
    log(f"Arquivo gerado: {output_file}")
    log_file.write_text("\n".join(log_lines), encoding="utf-8")
    log(f"Log gerado: {log_file}")

BETA_REGIME_LEVELS = {
    "SUAVE": 0.5,
    "MEDIO": 1.5,
    "FORTE": 3.0,
}


def beta_regime_summary_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    output_rows = []
    if frame.empty:
        return pd.DataFrame()
    for nivel, group in frame.groupby("nivel_beta"):
        item = {"nivel_beta": nivel, "lambda_beta": group["lambda_beta"].iloc[0]}
        carteira_acc = 1.0
        ibov_acc = 1.0
        alpha_sum = 0.0
        for _, row in group.sort_values("mes").iterrows():
            mes = str(row["mes"])
            ret = row.get("retorno_expost_sombra", np.nan)
            ibov = row.get("retorno_expost_ibov", np.nan)
            alpha = row.get("alfa_sombra", np.nan)
            item[f"retorno_{mes}"] = ret
            item[f"ibov_{mes}"] = ibov
            item[f"alfa_{mes}"] = alpha
            item[f"beta_{mes}"] = row.get("beta_carteira_sombra", np.nan)
            item[f"beta_alvo_{mes}"] = row.get("beta_target", np.nan)
            item[f"dist_beta_{mes}"] = row.get("desvio_beta_target", np.nan)
            if pd.notna(ret):
                carteira_acc *= (1.0 + float(ret))
            if pd.notna(ibov):
                ibov_acc *= (1.0 + float(ibov))
            if pd.notna(alpha):
                alpha_sum += float(alpha)
        item["retorno_acumulado_carteira"] = carteira_acc - 1.0
        item["retorno_acumulado_ibov"] = ibov_acc - 1.0
        item["alfa_acumulado_composto"] = (carteira_acc - 1.0) - (ibov_acc - 1.0)
        item["alfa_soma_mensal"] = alpha_sum
        output_rows.append(item)
    return pd.DataFrame(output_rows)


def portfolio_detail_rows_by_level(results_by_level: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for nivel, results in results_by_level.items():
        for mes, result in results.items():
            portfolio = result.get("portfolio", pd.DataFrame())
            if portfolio.empty:
                continue
            frame = portfolio.copy()
            frame.insert(0, "lambda_beta", BETA_REGIME_LEVELS.get(nivel, np.nan))
            frame.insert(0, "nivel_beta", nivel)
            frame.insert(0, "mes", mes)
            rows.append(frame)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def validation_rows_by_level(results_by_level: dict[str, dict[str, Any]], expost: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for nivel, results in results_by_level.items():
        for row in free_size_validation_rows(results, expost):
            row = dict(row)
            row["nivel_beta"] = nivel
            row["lambda_beta"] = BETA_REGIME_LEVELS.get(nivel, np.nan)
            rows.append(row)
    return rows


def write_beta_regime_output(anchor_rows: list[dict[str, Any]], shadow_rows: list[dict[str, Any]], anchor_results: dict[str, Any], shadow_results_by_level: dict[str, dict[str, Any]], validation_rows: list[dict[str, Any]], output_file: Path) -> None:
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        beta_regime_summary_rows(shadow_rows).to_excel(writer, sheet_name="resumo_por_nivel", index=False)
        pd.DataFrame(shadow_rows).to_excel(writer, sheet_name="shadow_vs_real", index=False)
        portfolio_detail_rows_by_level(shadow_results_by_level).to_excel(writer, sheet_name="carteiras_por_mes_por_nivel", index=False)
        pd.DataFrame(validation_rows).to_excel(writer, sheet_name="validacao_retorno", index=False)
        _result_sheets(writer, "anchor", anchor_results)


def run_beta_regime_free_size_mode(log, log_lines: list[str], base_settings: dict, expost: pd.DataFrame, anchor_rows: list[dict[str, Any]], anchor_results: dict[str, Any], output_file: Path, log_file: Path) -> None:
    log("ANCORA PASSOU. Executando tamanho livre com beta-alvo por regime: SUAVE/MEDIO/FORTE.")
    shadow_rows: list[dict[str, Any]] = []
    shadow_results_by_level: dict[str, dict[str, Any]] = {}
    for nivel, lambda_beta in BETA_REGIME_LEVELS.items():
        log(f"Rodando nivel {nivel} com lambda_beta={lambda_beta}.")
        level_results: dict[str, Any] = {}
        for mes in MONTHS:
            path = workbook_path(mes)
            result = run_free_size_for_month(mes, path, base_settings, lambda_beta=lambda_beta)
            row = build_summary_row(mes, path, result, expost, shadow_fixes=True)
            row["cenario_objetivo"] = f"V3_TAMANHO_LIVRE_BETA_{nivel}"
            row["nivel_beta"] = nivel
            row["lambda_beta"] = lambda_beta
            shadow_rows.append(row)
            level_results[mes] = result
            ret = row["retorno_expost_sombra"] * 100 if pd.notna(row["retorno_expost_sombra"]) else np.nan
            ibov = row["retorno_expost_ibov"] * 100 if pd.notna(row["retorno_expost_ibov"]) else np.nan
            alpha = row["alfa_sombra"] * 100 if pd.notna(row["alfa_sombra"]) else np.nan
            beta_cart = row.get("beta_carteira_sombra", np.nan)
            beta_target = row.get("beta_target", np.nan)
            dist = row.get("desvio_beta_target", np.nan)
            subtype = row.get("beta_target_subtipo", "")
            portfolio = result.get("portfolio", pd.DataFrame())
            size = len(portfolio) if not portfolio.empty else 0
            msg = f"{nivel} {mes}: subtipo={subtype} | beta_alvo={beta_target:.2f} | beta_real={beta_cart:.2f} | dist={dist:.2f} | acoes={size} | retorno={ret:.2f}% | IBOV={ibov:.2f}% | alfa={alpha:.2f}% | pesos={row['tickers_pesos_sombra']}"
            if mes == "2026-05" and not portfolio.empty and "setor" in portfolio and int(portfolio.groupby("setor")["ticker"].count().max()) > 2:
                log(f"{RED}REGRESSAO: maio violou maximo de 2 ativos por setor. {msg}{RESET}")
            else:
                log(msg)
        shadow_results_by_level[nivel] = level_results

    validation_rows = validation_rows_by_level(shadow_results_by_level, expost)
    validation = pd.DataFrame(validation_rows)
    if not validation.empty:
        bad_return = validation["diferenca_retorno"].abs().fillna(0).max()
        log(f"VALIDACAO RETORNO: maior diferenca manual vs reportado = {bad_return:.10f}")
        if bad_return > 0.0001:
            log(f"{RED}REGRESSAO: diferenca de retorno acima de 0.0001.{RESET}")
        for flag, message in [
            ("alguma_acao_acima_25pct", "existe acao acima de 25%"),
            ("violou_max_2_por_setor", "alguma carteira violou maximo de 2 por setor"),
            ("tem_deterioracao_fundamental_real", "entrou ativo com deterioracao fundamental real"),
            ("carteira_com_menos_de_5_acoes", "carteira formada com menos de 5 acoes"),
        ]:
            if flag in validation and validation[flag].fillna(False).any():
                log(f"{RED}REGRESSAO: {message}.{RESET}")
    resumo = beta_regime_summary_rows(shadow_rows)
    if not resumo.empty:
        log("RESUMO ALFA ACUMULADO:")
        for _, row in resumo.iterrows():
            log(f"  {row['nivel_beta']} lambda={row['lambda_beta']}: alfa_composto={row['alfa_acumulado_composto']:.2%}; alfa_soma={row['alfa_soma_mensal']:.2%}")
    write_beta_regime_output(anchor_rows, shadow_rows, anchor_results, shadow_results_by_level, validation_rows, output_file)
    log(f"Arquivo gerado: {output_file}")
    log_file.write_text("\n".join(log_lines), encoding="utf-8")
    log(f"Log gerado: {log_file}")


def downturn_signal_summary_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    output_rows = []
    for signal, group in frame.groupby("sinal_quedas_cenario", dropna=False):
        item = {"sinal_quedas_cenario": signal}
        carteira_acc = 1.0
        ibov_acc = 1.0
        alpha_values: list[float] = []
        subtype_alpha: dict[str, list[float]] = {}
        sem_carteira = 0
        for _, row in group.sort_values("mes").iterrows():
            mes = row["mes"]
            ret_raw = row.get("retorno_expost_sombra", np.nan)
            ibov = row.get("retorno_expost_ibov", np.nan)
            used_cash = pd.isna(ret_raw)
            ret = 0.0 if used_cash else ret_raw
            subtype_key = str(row.get("subtipo_queda", "") or "indefinido")
            item[f"subtipo_{mes}"] = row.get("subtipo_queda", "")
            item[f"sem_carteira_{mes}"] = bool(used_cash)
            item[f"retorno_{mes}"] = ret_raw
            item[f"retorno_assumindo_caixa_{mes}"] = ret
            item[f"ibov_{mes}"] = ibov
            alpha = ret - ibov if pd.notna(ibov) else np.nan
            item[f"alfa_{mes}"] = alpha
            item[f"beta_{mes}"] = row.get("beta_carteira_sombra", np.nan)
            if used_cash:
                sem_carteira += 1
            if pd.notna(ret):
                carteira_acc *= 1.0 + float(ret)
            if pd.notna(ibov):
                ibov_acc *= 1.0 + float(ibov)
            if pd.notna(alpha):
                alpha_values.append(float(alpha))
                subtype_alpha.setdefault(subtype_key, []).append(float(alpha))
        item["meses_sem_carteira"] = sem_carteira
        item["retorno_acumulado_carteira"] = carteira_acc - 1.0
        item["retorno_acumulado_ibov"] = ibov_acc - 1.0
        item["alfa_acumulado_composto"] = (carteira_acc - 1.0) - (ibov_acc - 1.0)
        item["alfa_soma_mensal"] = sum(alpha_values)
        for key, values in subtype_alpha.items():
            item[f"alfa_medio_{key}"] = float(np.mean(values)) if values else np.nan
            item[f"alfa_soma_{key}"] = float(np.sum(values)) if values else np.nan
        output_rows.append(item)
    return pd.DataFrame(output_rows)


def portfolio_detail_rows_by_signal(results_by_signal: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for signal, results in results_by_signal.items():
        for mes, result in results.items():
            portfolio = result.get("portfolio", pd.DataFrame())
            if portfolio.empty:
                continue
            frame = portfolio.copy()
            frame.insert(0, "sinal_quedas_cenario", signal)
            frame.insert(0, "mes", mes)
            rows.append(frame)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def validation_rows_by_signal(results_by_signal: dict[str, dict[str, Any]], expost: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for signal, results in results_by_signal.items():
        for row in free_size_validation_rows(results, expost):
            row = dict(row)
            row["sinal_quedas_cenario"] = signal
            rows.append(row)
    return rows


def anti_lookahead_rows(results_by_signal: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    signal_key = "SINAL_B_REVERSAO_ESTRITO" if "SINAL_B_REVERSAO_ESTRITO" in results_by_signal else "SINAL_B_REVERSAO_DEFENSIVO"
    results = results_by_signal.get(signal_key, {})
    for mes, result in results.items():
        portfolio = result.get("portfolio", pd.DataFrame())
        if portfolio.empty:
            rows.append({"mes": mes, "sinal_quedas_cenario": signal_key, "sem_carteira_ou_sem_reversoes": True})
            continue
        subtype = str(result.get("metrics", {}).get("subtipo_queda", ""))
        if subtype == "alta":
            continue
        frame = portfolio.copy()
        if signal_key == "SINAL_B_REVERSAO_ESTRITO":
            if "shadow_reversao_estrita_aprovada" in frame.columns:
                frame = frame[frame["shadow_reversao_estrita_aprovada"].map(to_bool)].copy()
            else:
                frame = frame.iloc[0:0].copy()
        elif "shadow_queda_reversao_preco_sem_deterioracao" in frame.columns:
            frame = frame[frame["shadow_queda_reversao_preco_sem_deterioracao"].map(to_bool)].copy()
        else:
            frame = frame.iloc[0:0].copy()
        if frame.empty:
            rows.append({"mes": mes, "sinal_quedas_cenario": signal_key, "subtipo_queda": subtype, "sem_carteira_ou_sem_reversoes": True})
            continue
        if "shadow_reversao_estrita_score" in frame.columns:
            frame = frame.sort_values(["shadow_reversao_estrita_score", "peso_recomendado"], ascending=[False, False])
        elif "shadow_queda_reversal_score" in frame.columns:
            frame = frame.sort_values(["shadow_queda_reversal_score", "peso_recomendado"], ascending=[False, False])
        for _, row in frame.iterrows():
            rsi_val = row.get("rsi", np.nan)
            ret4_val = row.get("retorno_acumulado_4m", np.nan)
            rows.append({
                "mes": mes,
                "sinal_quedas_cenario": signal_key,
                "subtipo_queda": subtype,
                "ticker": row.get("ticker"),
                "setor": row.get("setor"),
                "peso_recomendado": row.get("peso_recomendado"),
                "rsi_formacao": rsi_val,
                "retorno_1m_formacao": row.get("retorno_acumulado_1m", np.nan),
                "retorno_4m_formacao": ret4_val,
                "bollinger_status_formacao": row.get("bollinger_status", ""),
                "distancia_banda_inferior_pct_formacao": row.get("distancia_banda_inferior_pct", np.nan),
                "roe_formacao": row.get("roe", np.nan),
                "margem_liquida_formacao": row.get("margem_liquida", np.nan),
                "pl_atual_formacao": row.get("pl_atual", np.nan),
                "rsi_criterio_ok": row.get("shadow_reversao_estrita_rsi_ok", np.nan),
                "retorno_4m_criterio_ok": row.get("shadow_reversao_estrita_ret4_ok", np.nan),
                "bollinger_criterio_ok": row.get("shadow_reversao_estrita_bollinger_ok", np.nan),
                "reversal_score_shadow": row.get("shadow_reversao_estrita_score", row.get("shadow_queda_reversal_score", np.nan)),
                "reversao_estrita_aprovada": row.get("shadow_reversao_estrita_aprovada", False),
                "deterioracao_real": row.get("shadow_queda_deterioracao_real", False),
                "falha_criterio_estrito": bool((pd.notna(rsi_val) and float(rsi_val) > 45) or (pd.notna(ret4_val) and float(ret4_val) >= 0)),
                "criterio_anti_lookahead": "Sinal B estrito usa somente dados de formacao: RSI<=40, retorno_4m<0, Bollinger/banda inferior e fundamentos; retorno realizado futuro nao entra no score",
                "motivo_sinal": row.get("shadow_queda_motivo_sinal", ""),
            })
    return pd.DataFrame(rows)


def write_downturn_signal_output(anchor_rows: list[dict[str, Any]], shadow_rows: list[dict[str, Any]], anchor_results: dict[str, Any], shadow_results_by_signal: dict[str, dict[str, Any]], validation_rows: list[dict[str, Any]], output_file: Path) -> None:
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        downturn_signal_summary_rows(shadow_rows).to_excel(writer, sheet_name="resumo_por_sinal", index=False)
        pd.DataFrame(shadow_rows).to_excel(writer, sheet_name="shadow_vs_real", index=False)
        portfolio_detail_rows_by_signal(shadow_results_by_signal).to_excel(writer, sheet_name="carteiras_por_mes_por_sinal", index=False)
        anti_lookahead_rows(shadow_results_by_signal).to_excel(writer, sheet_name="auditoria_antilookahead", index=False)
        pd.DataFrame(validation_rows).to_excel(writer, sheet_name="validacao_retorno", index=False)
        _result_sheets(writer, "anchor", anchor_results)


def run_downturn_signal_mode(log, log_lines: list[str], base_settings: dict, expost: pd.DataFrame, anchor_rows: list[dict[str, Any]], anchor_results: dict[str, Any], output_file: Path, log_file: Path) -> None:
    log("ANCORA PASSOU. Executando sinais alternativos de queda: V3, Sinal A defensivo, Sinal B reversao+defensivo.")
    shadow_rows: list[dict[str, Any]] = []
    shadow_results_by_signal: dict[str, dict[str, Any]] = {}
    for signal in DOWNTURN_SIGNAL_SCENARIOS:
        log(f"Rodando cenario {signal}: {DOWNTURN_SIGNAL_SCENARIOS[signal]}")
        signal_results: dict[str, Any] = {}
        for mes in MONTHS:
            path = workbook_path(mes)
            result = run_free_size_for_month(mes, path, base_settings, lambda_beta=0.0, downturn_signal=signal)
            row = build_summary_row(mes, path, result, expost, shadow_fixes=True)
            row["sinal_quedas_cenario"] = signal
            row["cenario_objetivo"] = signal
            shadow_rows.append(row)
            signal_results[mes] = result
            alpha = row["alfa_sombra"] * 100 if pd.notna(row["alfa_sombra"]) else np.nan
            ret = row["retorno_expost_sombra"] * 100 if pd.notna(row["retorno_expost_sombra"]) else np.nan
            ibov = row["retorno_expost_ibov"] * 100 if pd.notna(row["retorno_expost_ibov"]) else np.nan
            beta = row.get("beta_carteira_sombra", np.nan)
            subtype = row.get("subtipo_queda", "")
            msg = f"{signal} {mes}: subtipo={subtype} | beta={beta:.2f} | retorno={ret:.2f}% | IBOV={ibov:.2f}% | alfa={alpha:.2f}% | pesos={row['tickers_pesos_sombra']}"
            portfolio = result.get("portfolio", pd.DataFrame())
            if mes == "2026-05" and not portfolio.empty and "setor" in portfolio and int(portfolio.groupby("setor")["ticker"].count().max()) > 2:
                log(f"{RED}REGRESSAO: maio violou maximo de 2 ativos por setor. {msg}{RESET}")
            else:
                log(msg)
        shadow_results_by_signal[signal] = signal_results

    frame = pd.DataFrame(shadow_rows)
    feb = frame[frame["mes"].astype(str).eq("2026-02")]
    if len(feb["tickers_pesos_sombra"].dropna().unique()) == 1:
        log("FEVEREIRO: os 3 cenarios ficaram iguais, como esperado para regime de alta.")
    else:
        log(f"{RED}REGRESSAO: fevereiro mudou entre cenarios; sinal de queda vazou para alta.{RESET}")

    validation_rows = validation_rows_by_signal(shadow_results_by_signal, expost)
    validation = pd.DataFrame(validation_rows)
    if not validation.empty:
        max_diff = pd.to_numeric(validation["diferenca_retorno"], errors="coerce").abs().max()
        log(f"VALIDACAO RETORNO: maior diferenca manual vs reportado = {max_diff:.10f}")
        for col, label in [
            ("alguma_acao_acima_25pct", "peso individual acima de 25%"),
            ("violou_max_2_por_setor", "violacao max 2/setor"),
            ("tem_deterioracao_fundamental_real", "deterioracao fundamental real"),
            ("carteira_com_menos_de_5_acoes", "carteira com menos de 5 acoes"),
        ]:
            if col in validation.columns and validation[col].fillna(False).astype(bool).any():
                log(f"{RED}REGRESSAO: {label}.{RESET}")
    anti = anti_lookahead_rows(shadow_results_by_signal)
    if not anti.empty:
        sample = anti.head(3)
        log("AUDITORIA ANTI-RETROVISOR SINAL B: " + " | ".join(
            f"{r['mes']} {r['ticker']} RSI={to_float(r['rsi_formacao'], np.nan):.2f} ret1m={to_float(r['retorno_1m_formacao'], np.nan):.2%} ret4m={to_float(r['retorno_4m_formacao'], np.nan):.2%} boll={r['bollinger_status_formacao']}"
            for _, r in sample.iterrows()
        ))
    resumo = downturn_signal_summary_rows(shadow_rows)
    if not resumo.empty:
        log("RESUMO ALFA ACUMULADO:")
        for _, row in resumo.iterrows():
            log(f"  {row['sinal_quedas_cenario']}: alfa_composto={row['alfa_acumulado_composto']:.2%}; alfa_soma={row['alfa_soma_mensal']:.2%}")
    write_downturn_signal_output(anchor_rows, shadow_rows, anchor_results, shadow_results_by_signal, validation_rows, output_file)
    log(f"Arquivo gerado: {output_file}")
    log_file.write_text("\n".join(log_lines), encoding="utf-8")
    log(f"Log gerado: {log_file}")


def write_strict_reversal_output(anchor_rows: list[dict[str, Any]], shadow_rows: list[dict[str, Any]], anchor_results: dict[str, Any], shadow_results_by_signal: dict[str, dict[str, Any]], validation_rows: list[dict[str, Any]], output_file: Path) -> None:
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        downturn_signal_summary_rows(shadow_rows).to_excel(writer, sheet_name="resumo_por_sinal", index=False)
        pd.DataFrame(shadow_rows).to_excel(writer, sheet_name="shadow_vs_real", index=False)
        portfolio_detail_rows_by_signal(shadow_results_by_signal).to_excel(writer, sheet_name="carteiras_por_mes", index=False)
        anti_lookahead_rows(shadow_results_by_signal).to_excel(writer, sheet_name="auditoria_antilookahead", index=False)
        pd.DataFrame(validation_rows).to_excel(writer, sheet_name="validacao_retorno", index=False)
        _result_sheets(writer, "anchor", anchor_results)


def run_strict_reversal_mode(log, log_lines: list[str], base_settings: dict, expost: pd.DataFrame, anchor_rows: list[dict[str, Any]], anchor_results: dict[str, Any], output_file: Path, log_file: Path) -> None:
    log("ANCORA PASSOU. Executando V3, Sinal A defensivo e Sinal B reversao ESTRITO.")
    shadow_rows: list[dict[str, Any]] = []
    shadow_results_by_signal: dict[str, dict[str, Any]] = {}
    for signal in STRICT_REVERSAL_SCENARIOS:
        log(f"Rodando cenario {signal}: {STRICT_REVERSAL_SCENARIOS[signal]}")
        signal_results: dict[str, Any] = {}
        for mes in MONTHS:
            path = workbook_path(mes)
            result = run_free_size_for_month(mes, path, base_settings, lambda_beta=0.0, downturn_signal=signal)
            row = build_summary_row(mes, path, result, expost, shadow_fixes=True)
            row["sinal_quedas_cenario"] = signal
            row["cenario_objetivo"] = signal
            shadow_rows.append(row)
            signal_results[mes] = result
            alpha = row["alfa_sombra"] * 100 if pd.notna(row["alfa_sombra"]) else np.nan
            ret = row["retorno_expost_sombra"] * 100 if pd.notna(row["retorno_expost_sombra"]) else np.nan
            ibov = row["retorno_expost_ibov"] * 100 if pd.notna(row["retorno_expost_ibov"]) else np.nan
            beta = row.get("beta_carteira_sombra", np.nan)
            subtype = row.get("subtipo_queda", "")
            portfolio = result.get("portfolio", pd.DataFrame())
            strict_count = (int(result.get("metrics", {}).get("tamanho_livre_numero_aprovadas", 0)) if signal == "SINAL_B_REVERSAO_ESTRITO" and subtype != "alta" else (int(portfolio.get("shadow_reversao_estrita_aprovada", pd.Series(False, index=portfolio.index)).map(to_bool).sum()) if signal != "SINAL_B_REVERSAO_ESTRITO" and not portfolio.empty and "shadow_reversao_estrita_aprovada" in portfolio else 0))
            msg = f"{signal} {mes}: subtipo={subtype} | reversoes_estritas={strict_count} | beta={beta:.2f} | retorno={ret:.2f}% | IBOV={ibov:.2f}% | alfa={alpha:.2f}% | pesos={row['tickers_pesos_sombra']}"
            if mes == "2026-05" and not portfolio.empty and "setor" in portfolio and int(portfolio.groupby("setor")["ticker"].count().max()) > 2:
                log(f"{RED}REGRESSAO: maio violou maximo de 2 ativos por setor. {msg}{RESET}")
            else:
                log(msg)
        shadow_results_by_signal[signal] = signal_results

    frame = pd.DataFrame(shadow_rows)
    feb = frame[frame["mes"].astype(str).eq("2026-02")]
    if len(feb["tickers_pesos_sombra"].dropna().unique()) == 1:
        log("FEVEREIRO: os 3 cenarios ficaram iguais, como esperado para regime de alta.")
    else:
        log(f"{RED}REGRESSAO: fevereiro mudou entre cenarios; sinal de queda vazou para alta.{RESET}")

    validation_rows = validation_rows_by_signal(shadow_results_by_signal, expost)
    validation = pd.DataFrame(validation_rows)
    if not validation.empty:
        max_diff = pd.to_numeric(validation["diferenca_retorno"], errors="coerce").abs().max()
        log(f"VALIDACAO RETORNO: maior diferenca manual vs reportado = {max_diff:.10f}")
        for col, label in [
            ("alguma_acao_acima_25pct", "peso individual acima de 25%"),
            ("violou_max_2_por_setor", "violacao max 2/setor"),
            ("tem_deterioracao_fundamental_real", "deterioracao fundamental real"),
            ("carteira_com_menos_de_5_acoes", "carteira com menos de 5 acoes"),
        ]:
            if col in validation.columns and validation[col].fillna(False).astype(bool).any():
                log(f"{RED}REGRESSAO: {label}.{RESET}")

    anti = anti_lookahead_rows(shadow_results_by_signal)
    if not anti.empty:
        failures = int(anti.get("falha_criterio_estrito", pd.Series(False, index=anti.index)).fillna(False).astype(bool).sum()) if "falha_criterio_estrito" in anti else 0
        if failures:
            log(f"{RED}AUDITORIA ANTI-LOOKAHEAD FALHOU: {failures} acoes do Sinal B estrito tinham RSI>45 ou retorno_4m>=0. Nao tirar conclusao.{RESET}")
        else:
            log("AUDITORIA ANTI-LOOKAHEAD: criterios estritos respeitados nas acoes selecionadas pelo Sinal B.")
        sample = anti[anti.get("ticker", pd.Series('', index=anti.index)).fillna('').astype(str).ne("")].head(10)
        if not sample.empty:
            log("SINAL B ESTRITO - valores de formacao: " + " | ".join(
                f"{r['mes']} {r['ticker']} RSI={to_float(r['rsi_formacao'], np.nan):.2f} ret4m={to_float(r['retorno_4m_formacao'], np.nan):.2%} dist_inf={to_float(r['distancia_banda_inferior_pct_formacao'], np.nan):.2%} ROE={to_float(r['roe_formacao'], np.nan):.2%} margem={to_float(r['margem_liquida_formacao'], np.nan):.2%}"
                for _, r in sample.iterrows()
            ))
    else:
        log("AUDITORIA ANTI-LOOKAHEAD: nenhuma acao de reversao estrita selecionada nos meses de queda.")

    resumo = downturn_signal_summary_rows(shadow_rows)
    if not resumo.empty:
        log("RESUMO ALFA ACUMULADO:")
        for _, row in resumo.iterrows():
            log(f"  {row['sinal_quedas_cenario']}: alfa_composto={row['alfa_acumulado_composto']:.2%}; alfa_soma={row['alfa_soma_mensal']:.2%}")
    write_strict_reversal_output(anchor_rows, shadow_rows, anchor_results, shadow_results_by_signal, validation_rows, output_file)
    log(f"Arquivo gerado: {output_file}")
    log_file.write_text("\n".join(log_lines), encoding="utf-8")
    log(f"Log gerado: {log_file}")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulacao sombra com teste-ancora e carteira parcial opcional.")
    parser.add_argument("--enable-partial-portfolio", action="store_true", help="Liga a carteira parcial apenas no caminho sombra.")
    parser.add_argument("--enable-beta-target", action="store_true", help="Liga beta-alvo por regime apenas no caminho sombra.")
    parser.add_argument("--enable-objetivo-retorno", action="store_true", help="Liga a nova funcao-objetivo retorno/CV/beta e roda V1/V2/V3 no caminho sombra.")
    parser.add_argument("--enable-composicao-ampliada", action="store_true", help="Liga composicao ampliada com V3, Top N 30 e candidate_counts 5/6/8/10/12/15 no caminho sombra.")
    parser.add_argument("--enable-tamanho-livre", action="store_true", help="Liga carteira de tamanho livre ponderada por V3 no caminho sombra.")
    parser.add_argument("--enable-beta-regime-tamanho-livre", action="store_true", help="Liga tamanho livre com beta-alvo por regime em tres lambdas no caminho sombra.")
    parser.add_argument("--enable-sinal-defensivo-quedas", action="store_true", help="Liga teste sombra de sinais defensivo/reversao apenas em regimes de queda.")
    parser.add_argument("--enable-sinal-reversao-estrito", action="store_true", help="Liga teste sombra do Sinal B de reversao estrito em regimes de queda.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_file = STRICT_REVERSAL_OUTPUT_FILE if args.enable_sinal_reversao_estrito else (DOWNTURN_SIGNAL_OUTPUT_FILE if args.enable_sinal_defensivo_quedas else (BETA_REGIME_OUTPUT_FILE if args.enable_beta_regime_tamanho_livre else (FREE_SIZE_OUTPUT_FILE if args.enable_tamanho_livre else (COMPOSITION_OUTPUT_FILE if args.enable_composicao_ampliada else (OBJ_OUTPUT_FILE if args.enable_objetivo_retorno else (BETA_OUTPUT_FILE if args.enable_beta_target else (PARTIAL_OUTPUT_FILE if args.enable_partial_portfolio else OUTPUT_FILE)))))))
    log_file = STRICT_REVERSAL_LOG_FILE if args.enable_sinal_reversao_estrito else (DOWNTURN_SIGNAL_LOG_FILE if args.enable_sinal_defensivo_quedas else (BETA_REGIME_LOG_FILE if args.enable_beta_regime_tamanho_livre else (FREE_SIZE_LOG_FILE if args.enable_tamanho_livre else (COMPOSITION_LOG_FILE if args.enable_composicao_ampliada else (OBJ_LOG_FILE if args.enable_objetivo_retorno else (BETA_LOG_FILE if args.enable_beta_target else PARTIAL_LOG_FILE))))))
    log_lines: list[str] = []

    def log(message: str) -> None:
        print(message)
        clean = re.sub(r"\033\[[0-9;]*m", "", message)
        log_lines.append(clean)

    if not EXPOST_FILE.exists():
        raise FileNotFoundError(f"Ex-post consolidado ausente: {EXPOST_FILE}")
    base_settings = load_settings()
    expost = pd.read_excel(EXPOST_FILE, sheet_name="Universo Expost")

    log("TESTE-ANCORA: shadow_fixes=False")
    anchor_rows = []
    anchor_results = {}
    all_pass = True
    for mes in MONTHS:
        path = workbook_path(mes)
        result = run_optimizer_for_month(mes, path, base_settings, shadow_fixes=False, enable_partial_portfolio=False, enable_beta_target=False)
        passed, detail = anchor_passed_for_month(mes, path, result["portfolio"], result["metrics"])
        all_pass = all_pass and passed
        status_text = "PASSOU" if passed else "NAO PASSOU"
        color = GREEN if passed else RED
        log(f"{color}{mes}: {status_text} | {detail} | tempo={result['elapsed']:.1f}s{RESET}")
        row = build_summary_row(mes, path, result, expost, shadow_fixes=False)
        row["anchor_passou"] = passed
        row["anchor_detalhe"] = detail
        anchor_rows.append(row)
        anchor_results[mes] = result

    if not all_pass:
        log(f"{RED}ANCORA NAO PASSOU. Correcoes sombra NAO foram executadas.{RESET}")
        write_output(anchor_rows, [], anchor_results, {}, output_file=output_file)
        log(f"Arquivo gerado com diagnostico do ancora: {output_file}")
        if args.enable_partial_portfolio or args.enable_beta_target or args.enable_objetivo_retorno or args.enable_composicao_ampliada or args.enable_tamanho_livre or args.enable_beta_regime_tamanho_livre or args.enable_sinal_defensivo_quedas or args.enable_sinal_reversao_estrito:
            log_file.write_text("\n".join(log_lines), encoding="utf-8")
        return

    if args.enable_sinal_reversao_estrito:
        run_strict_reversal_mode(log, log_lines, base_settings, expost, anchor_rows, anchor_results, output_file, log_file)
        return

    if args.enable_sinal_defensivo_quedas:
        run_downturn_signal_mode(log, log_lines, base_settings, expost, anchor_rows, anchor_results, output_file, log_file)
        return

    if args.enable_beta_regime_tamanho_livre:
        run_beta_regime_free_size_mode(log, log_lines, base_settings, expost, anchor_rows, anchor_results, output_file, log_file)
        return

    if args.enable_tamanho_livre:
        run_free_size_mode(log, log_lines, base_settings, expost, anchor_rows, anchor_results, output_file, log_file)
        return

    if args.enable_composicao_ampliada:
        run_composition_mode(log, log_lines, base_settings, expost, anchor_rows, anchor_results, output_file, log_file)
        return

    if args.enable_objetivo_retorno:
        run_objective_mode(log, log_lines, base_settings, expost, anchor_rows, anchor_results, output_file, log_file)
        return

    log(f"ANCORA PASSOU. Executando sombra com enable_partial_portfolio={args.enable_partial_portfolio}; enable_beta_target={args.enable_beta_target}.")
    shadow_rows = []
    shadow_results = {}
    for mes in MONTHS:
        path = workbook_path(mes)
        use_shadow_fixes = not args.enable_partial_portfolio
        result = run_optimizer_for_month(mes, path, base_settings, shadow_fixes=use_shadow_fixes, enable_partial_portfolio=args.enable_partial_portfolio, enable_beta_target=args.enable_beta_target)
        row = build_summary_row(mes, path, result, expost, shadow_fixes=use_shadow_fixes)
        shadow_rows.append(row)
        shadow_results[mes] = result
        alpha = row["alfa_sombra"] * 100 if pd.notna(row["alfa_sombra"]) else np.nan
        ibov = row["retorno_expost_ibov"] * 100 if pd.notna(row["retorno_expost_ibov"]) else np.nan
        invested = row.get("percentual_investido", np.nan)
        cash = row.get("percentual_caixa", np.nan)
        partial = bool(row.get("partial_acionada", False))
        msg = (
            f"{mes}: status={row['status_sombra']} | subtipo_beta={row.get('beta_target_subtipo', '')} "
            f"| beta_alvo={(row.get('beta_target') if pd.notna(row.get('beta_target')) else np.nan):.2f} "
            f"| beta_cart={(row.get('beta_carteira_sombra') if pd.notna(row.get('beta_carteira_sombra')) else np.nan):.2f} "
            f"| parcial={partial} | carteira={row['carteira_sombra_formada']} "
            f"| investido={(invested * 100 if pd.notna(invested) else np.nan):.2f}% | caixa={(cash * 100 if pd.notna(cash) else np.nan):.2f}% "
            f"| IBOV={ibov:.2f}% | alfa_sombra={alpha:.2f}% | tempo={result['elapsed']:.1f}s"
        )
        maio_concentrado = False
        if mes == "2026-05" and row["carteira_sombra_formada"]:
            portfolio_month = result.get("portfolio", pd.DataFrame())
            if not portfolio_month.empty and "setor" in portfolio_month and "peso_recomendado" in portfolio_month:
                counts = portfolio_month["setor"].fillna("Outros").value_counts()
                weights_sector = portfolio_month.groupby("setor")["peso_recomendado"].sum()
                maio_concentrado = bool((counts > 2).any() or (weights_sector > 0.40 + 1e-9).any())
        if mes == "2026-05" and (partial or maio_concentrado):
            log(f"{RED}REGRESSAO DE PROTECAO: MAIO MONTOU CARTEIRA CONCENTRADA OU PARCIAL. {msg}{RESET}")
        else:
            log(msg)
        if mes == "2026-02":
            log(f"FEVEREIRO: subtipo_beta={row.get('beta_target_subtipo', '')}; beta_alvo={row.get('beta_target')}; pesos={row['tickers_pesos_sombra']}; alfa_expost_sombra={alpha:.2f}% contra IBOV {ibov:.2f}%")
        if mes == "2026-06":
            log(f"JUNHO: subtipo_beta={row.get('beta_target_subtipo', '')}; beta_alvo={row.get('beta_target')}; beta_carteira={row.get('beta_carteira_sombra')}; pesos={row['tickers_pesos_sombra']}; alfa={alpha:.2f}%")
            cand_jun = result.get("candidates", pd.DataFrame())
            if not cand_jun.empty and "liberado_por_d3" in cand_jun.columns:
                changed = cand_jun[cand_jun["liberado_por_d3"].map(to_bool)].copy()
                if not changed.empty:
                    detalhes = []
                    for _, item in changed.sort_values("ticker").iterrows():
                        motivo = str(item.get("motivo_bloqueio_original_d3", "") or "sem motivo original preenchido")
                        detalhes.append(f"{item.get('ticker')}: {motivo}")
                    log("JUNHO D3 liberou: " + " | ".join(detalhes))
                else:
                    log("JUNHO D3 liberou: nenhum ativo")
        if mes == "2026-05":
            log(f"MAIO: partial_trigger_reason={row.get('partial_trigger_reason', '')}")

    write_output(anchor_rows, shadow_rows, anchor_results, shadow_results, output_file=output_file)
    log(f"Arquivo gerado: {output_file}")
    if args.enable_partial_portfolio or args.enable_beta_target or args.enable_objetivo_retorno or args.enable_composicao_ampliada or args.enable_tamanho_livre or args.enable_beta_regime_tamanho_livre or args.enable_sinal_defensivo_quedas or args.enable_sinal_reversao_estrito:
        log_file.write_text("\n".join(log_lines), encoding="utf-8")
        log(f"Log gerado: {log_file}")


if __name__ == "__main__":
    main()







































