from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

from b3_calendar import first_b3_trading_day, resolve_b3_trading_days

from data_loader import daily_risk_free_rate, fetch_index_prices, fetch_yfinance_prices
from fundamentals import collect_fundamentals
from optimizer import apply_regime_fields, optimize_weights, validate_portfolio, validation_summary
from report_excel import write_excel
from report_pdf import write_pdf
from risk_analysis import annualize_return, annualize_risk, log_returns, risk_metrics
from scoring import score_assets, score_fundamentals, score_sector, score_technical, score_timing
from sector_analysis import analyze_sector_indexes, apply_sector_mapping
from technical_indicators import calculate_technical_snapshot, rsi_components, weekly_close
from universe_loader import load_universe
from utils import ROOT, alert_join, load_settings, setup_logging


LOGGER = logging.getLogger(__name__)


def technical_table(prices: pd.DataFrame, settings: dict) -> pd.DataFrame:
    rows = []
    for ticker in prices.columns:
        series = prices[ticker].dropna()
        if series.empty:
            continue
        row = calculate_technical_snapshot(series, settings)
        row["ticker"] = ticker
        rows.append(row)
    return pd.DataFrame(rows)



def technical_audit_table(tech: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "ticker",
        "timeframe_tecnico",
        "fonte_fechamento",
        "data_ultimo_fechamento",
        "fechamento_usado",
        "rsi",
        "rsi_periodos",
        "rsi_timeframe",
        "bollinger_upper",
        "bollinger_middle",
        "bollinger_lower",
        "bollinger_periodos",
        "bollinger_std",
        "bollinger_timeframe",
        "mm9",
        "mm21",
        "mm50",
        "mm100",
    ]
    audit = tech.reindex(columns=cols).copy()
    return audit.rename(columns={"rsi": "rsi_calculado"})


def build_prio3_rsi_log(prices: pd.DataFrame, settings: dict) -> pd.DataFrame:
    ticker = "PRIO3.SA"
    columns = [
        "ticker",
        "timeframe",
        "periodo_rsi",
        "formula",
        "data_fechamento",
        "fechamento",
        "variacao",
        "ganho",
        "perda",
        "media_ganho_wilder",
        "media_perda_wilder",
        "rs",
        "rsi",
    ]
    if ticker not in prices.columns:
        LOGGER.warning("AUDITORIA_RSI_PRIO3: ticker %s nao encontrado na matriz de precos", ticker)
        return pd.DataFrame(columns=columns)

    period = int(settings["technical"].get("rsi_period", 14))
    weekly = weekly_close(prices[ticker])
    components = rsi_components(weekly, period).tail(20).reset_index()
    if components.empty:
        LOGGER.warning("AUDITORIA_RSI_PRIO3: sem fechamentos semanais suficientes para %s", ticker)
        return pd.DataFrame(columns=columns)

    date_col = components.columns[0]
    components = components.rename(columns={date_col: "data_fechamento"})
    components.insert(0, "ticker", ticker)
    components.insert(1, "timeframe", "1W")
    components.insert(2, "periodo_rsi", period)
    components.insert(3, "formula", "RSI Wilder 14 sobre fechamento semanal W-FRI")
    components = components.reindex(columns=columns)

    latest = components.dropna(subset=["rsi"]).tail(1)
    if not latest.empty:
        row = latest.iloc[0]
        LOGGER.info(
            "AUDITORIA_RSI_PRIO3: timeframe=1W periodo=%s ultimo_fechamento=%s fechamento=%.4f rsi=%.4f",
            period,
            row.get("data_fechamento"),
            row.get("fechamento", np.nan),
            row.get("rsi", np.nan),
        )
    LOGGER.info(
        "AUDITORIA_RSI_PRIO3_ULTIMAS_20_SEMANAS:\n%s",
        components[["data_fechamento", "fechamento", "variacao", "ganho", "perda", "media_ganho_wilder", "media_perda_wilder", "rs", "rsi"]].to_string(index=False),
    )
    return components

def cumulative_return(series: pd.Series, months: int) -> float:
    clean = series.dropna().sort_index()
    if len(clean) < 2:
        return np.nan
    start = clean.index.max() - pd.DateOffset(months=months)
    window = clean[clean.index >= start]
    if len(window) < 2 or window.iloc[0] == 0:
        return np.nan
    return float(window.iloc[-1] / window.iloc[0] - 1)


def annual_returns_last_years(series: pd.Series, years: int = 5) -> str:
    clean = series.dropna().sort_index()
    if clean.empty:
        return ""
    last_year = int(clean.index.max().year)
    values = []
    for year in range(last_year - years + 1, last_year + 1):
        year_data = clean[clean.index.year == year]
        if len(year_data) >= 2 and year_data.iloc[0] != 0:
            values.append(f"{year}: {year_data.iloc[-1] / year_data.iloc[0] - 1:.2%}")
    return "; ".join(values)


def cumulative_returns_table(prices: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ticker in prices.columns:
        series = prices[ticker]
        clean = series.dropna().sort_index()
        rows.append(
            {
                "ticker": ticker,
                "cotacao_anterior": clean.iloc[-2] if len(clean) >= 2 else np.nan,
                "cotacao_atual": clean.iloc[-1] if len(clean) else np.nan,
                "retorno_acumulado_1m": cumulative_return(series, 1),
                "retorno_acumulado_4m": cumulative_return(series, 4),
                "valorizacao_anual_ultimos_5_anos": annual_returns_last_years(series, 5),
            }
        )
    return pd.DataFrame(rows)


def ibov_return_benchmarks(index_prices: pd.DataFrame, settings: dict) -> dict:
    ibov_ticker = settings["data"]["indexes"].get("IBOV", "^BVSP")
    if index_prices.empty or ibov_ticker not in index_prices:
        return {"retorno_1m_ibov": np.nan, "retorno_4m_ibov": np.nan, "retorno_ytd_ibov": np.nan}
    series = index_prices[ibov_ticker].dropna().sort_index()
    if series.empty:
        return {"retorno_1m_ibov": np.nan, "retorno_4m_ibov": np.nan, "retorno_ytd_ibov": np.nan}
    return {
        "retorno_1m_ibov": cumulative_return(series, 1),
        "retorno_4m_ibov": cumulative_return(series, 4),
        "retorno_ytd_ibov": calculate_technical_snapshot(series, settings).get("retorno_ytd", np.nan),
    }


def add_relative_strength(frame: pd.DataFrame, index_prices: pd.DataFrame, settings: dict) -> pd.DataFrame:
    result = frame.copy()
    cfg = settings.get("relative_strength", {})
    enabled = bool(cfg.get("enabled", True))
    benchmarks = ibov_return_benchmarks(index_prices, settings)
    result["retorno_1m_ibov"] = benchmarks["retorno_1m_ibov"]
    result["retorno_4m_ibov"] = benchmarks["retorno_4m_ibov"]
    result["retorno_ytd_ibov"] = benchmarks["retorno_ytd_ibov"]
    if not enabled:
        for col in ["retorno_1m_relativo_ibov", "retorno_4m_relativo_ibov", "retorno_ytd_relativo_ibov", "forca_relativa_score"]:
            result[col] = np.nan
        result["classificacao_forca_relativa"] = "desativada"
        result["forca_relativa_positiva_relevante"] = False
        return result

    result["retorno_1m_relativo_ibov"] = result.get("retorno_acumulado_1m", np.nan) - benchmarks["retorno_1m_ibov"]
    result["retorno_4m_relativo_ibov"] = result.get("retorno_acumulado_4m", np.nan) - benchmarks["retorno_4m_ibov"]
    result["retorno_ytd_relativo_ibov"] = result.get("retorno_ytd", np.nan) - benchmarks["retorno_ytd_ibov"]
    rel_1m = result["retorno_1m_relativo_ibov"] > 0
    rel_4m = result["retorno_4m_relativo_ibov"] > 0
    rel_ytd = result["retorno_ytd_relativo_ibov"] > 0
    result["forca_relativa_score"] = rel_1m.astype(int) * 2 + rel_4m.astype(int) * 2 + rel_ytd.astype(int)
    strong = (rel_1m & rel_4m) | (result["retorno_ytd_relativo_ibov"] >= 0.10)
    moderate = ~strong & (rel_1m | rel_4m | rel_ytd)
    result["classificacao_forca_relativa"] = np.select(
        [strong, moderate],
        ["forte_contra_ibov", "moderada_contra_ibov"],
        default="fraca_contra_ibov",
    )
    result["forca_relativa_positiva_relevante"] = rel_1m | rel_4m
    return result


def _ibov_snapshot_from_sector_indexes(sector_indexes: pd.DataFrame) -> dict:
    if sector_indexes is None or sector_indexes.empty:
        return {"rsi_ibov": np.nan, "bollinger_ibov": "", "retorno_1m_ibov": np.nan, "retorno_ytd_ibov": np.nan}
    frame = sector_indexes.copy()
    ibov = frame[frame["indice"].astype(str).str.upper().eq("IBOV")] if "indice" in frame else pd.DataFrame()
    if ibov.empty:
        ibov = frame.head(1)
    row = ibov.iloc[0]
    return {
        "rsi_ibov": row.get("rsi", np.nan),
        "bollinger_ibov": row.get("bollinger_status", ""),
        "retorno_ytd_ibov": row.get("retorno_ytd", np.nan),
        "tendencia_ibov": row.get("tendencia", row.get("tendencia_setorial", "")),
        "mm9_ibov": row.get("mm9", np.nan),
        "mm21_ibov": row.get("mm21", np.nan),
        "mm50_ibov": row.get("mm50", np.nan),
        "mm100_ibov": row.get("mm100", np.nan),
    }


def classify_favorable_market_subtype(preliminary: pd.DataFrame, sector_indexes: pd.DataFrame, market_class: str, settings: dict) -> dict:
    snapshot = _ibov_snapshot_from_sector_indexes(sector_indexes)
    total = len(preliminary) if preliminary is not None else 0
    positive_month = int((preliminary.get("retorno_acumulado_1m", pd.Series(dtype=float)) > 0).sum()) if total else 0
    positive_month_pct = positive_month / total if total else np.nan
    rsi_ibov = snapshot.get("rsi_ibov", np.nan)
    bollinger_ibov = str(snapshot.get("bollinger_ibov", "") or "").lower()
    cfg = settings.get("market_regime", {})
    rsi_stretched = float(cfg.get("rsi_ibov_esticado", 75))
    rsi_tired = float(cfg.get("rsi_ibov_cansado", 70))
    breadth_tired = float(cfg.get("amplitude_positiva_1m_cansado", 0.50))
    if market_class != "mercado favoravel":
        subtype = "nao_aplicavel"
        esticado = False
        cansado = False
        reason = "subtipo calculado apenas para mercado favoravel"
    elif pd.notna(rsi_ibov) and rsi_ibov >= rsi_stretched:
        subtype = "mercado_favoravel_esticado"
        esticado = True
        cansado = False
        reason = f"RSI do IBOV >= {rsi_stretched:.0f} ({rsi_ibov:.2f})"
    elif "sobrecompra" in bollinger_ibov:
        subtype = "mercado_favoravel_esticado"
        esticado = True
        cansado = False
        reason = "Bollinger do IBOV em sobrecompra"
    elif pd.notna(rsi_ibov) and rsi_ibov >= rsi_tired and pd.notna(positive_month_pct) and positive_month_pct < breadth_tired:
        subtype = "mercado_favoravel_cansado"
        esticado = False
        cansado = True
        reason = f"RSI do IBOV >= {rsi_tired:.0f} ({rsi_ibov:.2f}) com amplitude mensal positiva abaixo de {breadth_tired:.0%} ({positive_month_pct:.1%})"
    elif pd.notna(rsi_ibov) and rsi_ibov >= 50 and pd.notna(positive_month_pct) and positive_month_pct < 0.40 and "oportunidade" not in bollinger_ibov:
        subtype = "mercado_favoravel_cansado"
        esticado = False
        cansado = True
        reason = f"mercado favoravel com RSI neutro/positivo ({rsi_ibov:.2f}) mas amplitude mensal fraca ({positive_month_pct:.1%})"
    elif pd.notna(rsi_ibov) and rsi_ibov < rsi_tired and pd.notna(positive_month_pct) and positive_month_pct >= breadth_tired:
        subtype = "mercado_favoravel_limpo"
        esticado = False
        cansado = False
        reason = f"RSI do IBOV abaixo de {rsi_tired:.0f} ({rsi_ibov:.2f}) e amplitude mensal saudavel ({positive_month_pct:.1%})"
    else:
        subtype = "mercado_favoravel_indefinido"
        esticado = False
        cansado = False
        reason = "dados insuficientes ou sinais mistos para subtipo"
    return {
        **snapshot,
        "subtipo_mercado_favoravel": subtype,
        "mercado_favoravel_esticado": esticado,
        "mercado_favoravel_cansado": cansado,
        "motivo_subtipo_mercado_favoravel": reason,
        "ativos_positivos_1m": positive_month,
        "pct_ativos_positivos_1m": positive_month_pct,
    }


def apply_stretched_market_fields(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    result = frame.copy()
    subtype = settings.get("_runtime_subtipo_mercado_favoravel", "nao_aplicavel")
    stretched = bool(settings.get("_runtime_mercado_favoravel_esticado", False))
    tired = bool(settings.get("_runtime_mercado_favoravel_cansado", False))
    favorable_alert = stretched or tired
    weak_relative = result.get("classificacao_forca_relativa", pd.Series("", index=result.index)).eq("fraca_contra_ibov")
    rel_1m = result.get("retorno_1m_relativo_ibov", pd.Series(np.nan, index=result.index))
    beta = result.get("beta", pd.Series(np.nan, index=result.index))
    cv = result.get("cv", pd.Series(np.nan, index=result.index))
    ret_1m = result.get("retorno_acumulado_1m", pd.Series(np.nan, index=result.index))
    ret_4m = result.get("retorno_acumulado_4m", pd.Series(np.nan, index=result.index))
    rsi = result.get("rsi", pd.Series(np.nan, index=result.index))
    fundamentals = result.get("qualidade_fundamentalista", pd.Series("", index=result.index)).fillna("")
    sector_quality = result.get("classificacao_fundamentalista_setorial", pd.Series("", index=result.index)).fillna("")
    near_upper = result.apply(lambda row: _near_upper_band(row, settings), axis=1)
    high_rally_count = (
        (ret_1m > 0.08).astype(int)
        + (ret_4m > 0.25).astype(int)
        + (beta > 1.30).astype(int)
        + (rsi >= 60).astype(int)
        + near_upper.astype(int)
        + fundamentals.isin(["fraca", "critica"]).astype(int)
        + sector_quality.isin(["fraco_relativo_ao_setor", "critico_relativo_ao_setor"]).astype(int)
        + ((rel_1m < 0) & result.get("forca_relativa_positiva_relevante", pd.Series(False, index=result.index)).fillna(False)).astype(int)
    )
    profile = np.select(
        [
            result.get("fundamento_bloqueante", pd.Series(False, index=result.index)).fillna(False) | fundamentals.eq("critica"),
            fundamentals.isin(["fraca"]) | (beta > 1.50) | (cv > settings.get("risk", {}).get("cv_limit", np.inf) * 2),
            fundamentals.isin(["otima", "boa"]) & (beta <= 1.10),
            fundamentals.isin(["otima", "boa"]) & (beta > 1.10),
        ],
        ["risco_fundamentalista_elevado", "turnaround_especulativo", "qualidade_defensiva", "crescimento_com_qualidade"],
        default="qualidade_ciclica",
    )
    result["subtipo_mercado_favoravel"] = subtype
    result["mercado_favoravel_esticado"] = stretched
    result["mercado_favoravel_cansado"] = tired
    result["motivo_subtipo_mercado_favoravel"] = settings.get("_runtime_motivo_subtipo_mercado_favoravel", "")
    result["penalizacao_forca_relativa_fraca"] = weak_relative | (rel_1m < 0)
    result["bloqueio_forca_relativa_fraca"] = bool(favorable_alert) & weak_relative
    result["motivo_bloqueio_forca_relativa"] = np.where(
        result["bloqueio_forca_relativa_fraca"],
        f"forca_relativa_fraca_em_{subtype}",
        np.where(rel_1m < 0, "retorno_1m_relativo_ibov_negativo_penalizado", ""),
    )
    result["penalizacao_retorno_1m_relativo_negativo"] = rel_1m < 0
    result["penalizacao_retorno_1m_relativo_negativo_forte"] = rel_1m < -0.03
    result["bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado"] = bool(favorable_alert) & (rel_1m < -0.05)
    result["alerta_beta_alto_mercado_esticado"] = bool(favorable_alert) & (beta > 1.50)
    result["penalizacao_beta_alto_mercado_esticado"] = result["alerta_beta_alto_mercado_esticado"]
    result["peso_maximo_beta_alto_mercado_esticado"] = np.select(
        [bool(favorable_alert) & (beta > 1.80), bool(favorable_alert) & (beta > 1.50)],
        [0.05, 0.10],
        default=np.nan,
    )
    result["alerta_realizacao_pos_rali"] = bool(favorable_alert) & (high_rally_count >= 2)
    result["motivos_alerta_realizacao_pos_rali"] = np.where(
        result["alerta_realizacao_pos_rali"],
        "mercado favoravel esticado/cansado com ao menos dois sinais: rally recente, beta alto, RSI elevado, banda superior, fundamentos/forca relativa perdendo intensidade",
        "",
    )
    result["penalizacao_realizacao_pos_rali"] = result["alerta_realizacao_pos_rali"]
    result["perfil_risco_empresa"] = profile
    profile_series = pd.Series(profile, index=result.index)
    result["peso_maximo_turnaround_especulativo"] = np.where(bool(favorable_alert) & profile_series.isin(["turnaround_especulativo", "risco_fundamentalista_elevado"]), 0.05, np.nan)
    timing_quality = result.get("qualidade_do_timing", pd.Series("", index=result.index)).fillna("")
    result["peso_maximo_timing_com_alerta"] = np.select(
        [timing_quality.eq("timing_tardio"), timing_quality.eq("timing_com_alerta")],
        [0.05, 0.10],
        default=np.nan,
    )
    result["motivo_peso_maximo_reduzido"] = np.select(
        [
            result["alerta_beta_alto_mercado_esticado"] & (beta > 1.80),
            result["alerta_beta_alto_mercado_esticado"],
            (bool(favorable_alert) & profile_series.isin(["turnaround_especulativo", "risco_fundamentalista_elevado"])),
            result["alerta_realizacao_pos_rali"],
        ],
        [
            "beta_acima_1_80_em_mercado_esticado_cap_5pct",
            "beta_acima_1_50_em_mercado_esticado_cap_10pct",
            "perfil_especulativo_ou_risco_fundamentalista_cap_5pct",
            "alerta_realizacao_pos_rali_cap_reduzido",
        ],
        default="",
    )
    if not favorable_alert and "tipo_watchlist" in result:
        healthy_flex = result["tipo_watchlist"].eq("watchlist_flexivel") & result.get("qualidade_do_timing", pd.Series("", index=result.index)).eq("timing_saudavel")
        healthy_flex &= result.get("motivo_tipo_watchlist", pd.Series("", index=result.index)).astype(str).str.contains("watchlist_flexivel_por_cautela_de_timing", na=False)
        result.loc[healthy_flex, "tipo_watchlist"] = "watchlist_monitoramento"
        result.loc[healthy_flex, "watchlist_bloqueia_otimizacao"] = False
        result.loc[healthy_flex, "motivo_tipo_watchlist"] = result.loc[healthy_flex, "motivo_tipo_watchlist"].astype(str).str.replace("watchlist_flexivel_por_cautela_de_timing; ", "", regex=False).str.replace("watchlist_flexivel_por_cautela_de_timing", "timing_saudavel_monitorado", regex=False)
    current_quality = result.get("qualidade_do_timing", pd.Series("", index=result.index)).fillna("")
    result["qualidade_do_timing"] = np.where(result["alerta_realizacao_pos_rali"] & current_quality.eq("timing_saudavel"), "timing_com_alerta", current_quality)
    result["qualidade_do_timing"] = np.where(result["bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado"], "timing_tardio", result["qualidade_do_timing"])
    return result

def _late_signal_and_timing_quality(row: pd.Series, settings: dict) -> pd.Series:
    cfg = _timing_settings(settings)
    reasons = []
    rsi = row.get("rsi", np.nan)
    rel_1m = row.get("retorno_1m_relativo_ibov", np.nan)
    near_upper = _near_upper_band(row, settings)
    trend_month = row.get("tendencia_mensal", "")
    trend_favorable = trend_month in {"alta_forte_mensal", "alta_aceitavel_ou_virada"} or row.get("tendencia") in {"Forte alta", "Aceitavel"}
    extension_count = 0
    if not pd.isna(rsi) and rsi >= 65:
        extension_count += 1
        reasons.append("RSI semanal >= 65")
    if near_upper:
        extension_count += 1
        reasons.append("preco proximo/acima da banda superior de Bollinger")
    if not pd.isna(rel_1m) and rel_1m >= 0.10:
        extension_count += 1
        reasons.append("retorno 1m relativo ao IBOV >= 10%")
    late_signal = bool(trend_month == "alta_forte_mensal" and extension_count >= 2)
    if row.get("bloqueada_entrada_esticada", False) or row.get("fundamento_bloqueante", False) or row.get("retorno_medio", np.nan) <= 0 or trend_month == "descarte_tecnico" or row.get("tendencia") in {"Fraca", "Descarte"}:
        quality = "timing_bloqueante"
    elif late_signal:
        quality = "timing_tardio"
    elif trend_favorable and extension_count == 1:
        quality = "timing_com_alerta"
    elif trend_favorable and row.get("tipo_timing") in {"timing_favoravel_tendencia", "timing_favoravel_com_alerta", "timing_reversao_oportunidade"}:
        quality = "timing_saudavel"
    elif row.get("tipo_timing") in {"timing_neutro", "timing_favoravel_com_alerta", "timing_atencao_banda_superior"}:
        quality = "timing_com_alerta"
    else:
        quality = "timing_bloqueante" if row.get("tipo_timing") in {"timing_fraqueza_sem_confirmacao", "timing_reversao_nao_aprovada", "timing_esticado_sobrecompra"} else "timing_com_alerta"
    return pd.Series({"alerta_sinal_tardio": late_signal, "motivos_alerta_sinal_tardio": "; ".join(reasons) if late_signal else "", "qualidade_do_timing": quality})


def _watchlist_type_fields(row: pd.Series, settings: dict) -> pd.Series:
    block_reasons = []
    monitor_reasons = []
    flexible_reasons = []
    timing = row.get("tipo_timing", "")
    trend_month = row.get("tendencia_mensal", "")
    retorno_medio = row.get("retorno_medio", np.nan)
    if pd.notna(retorno_medio) and retorno_medio <= 0:
        block_reasons.append("retorno_medio_negativo")
    if row.get("fundamento_bloqueante", False):
        block_reasons.append("fundamento_bloqueante")
    if row.get("price_rows", 999) <= 0 or pd.isna(row.get("preco_atual", np.nan)):
        block_reasons.append("dados_insuficientes")
    if row.get("bloqueada_entrada_esticada", False):
        block_reasons.append("sobrecompra_extrema")
    if trend_month == "descarte_tecnico" or row.get("tendencia") in {"Fraca", "Descarte"}:
        block_reasons.append("tendencia_tecnica_negativa")
    if timing in {"timing_fraqueza_sem_confirmacao", "timing_reversao_nao_aprovada"}:
        block_reasons.append("fraqueza_ou_reversao_sem_confirmacao")
    if timing == "timing_neutro" and not bool(row.get("forca_relativa_positiva_relevante", False)):
        block_reasons.append("timing_neutro_sem_forca_relativa_relevante")

    flag_watchlist = bool(row.get("flag_watchlist", False)) or row.get("decisao_preliminar_ajustada") == "watchlist_qualificada"
    if not block_reasons and flag_watchlist:
        flexible_ok = (
            pd.notna(retorno_medio) and retorno_medio > 0
            and not row.get("fundamento_bloqueante", False)
            and row.get("status_para_risco") in {"aprovada_para_risco", "moderada_para_risco", ""}
            and row.get("categoria_elegibilidade", "elegivel_forte") != "inelegivel"
        )
        if flexible_ok or timing in {"timing_favoravel_tendencia", "timing_favoravel_com_alerta", "timing_atencao_banda_superior", "timing_neutro"}:
            flexible_reasons.append("watchlist_flexivel_por_cautela_de_timing")
    beta = row.get("beta", np.nan)
    corr = row.get("correlacao_ibov", np.nan)
    cv = row.get("cv", np.nan)
    if not pd.isna(beta) and beta > settings.get("risk", {}).get("beta_alert", 1.0):
        monitor_reasons.append("beta_acima_alerta")
    if not pd.isna(corr) and corr > settings.get("risk", {}).get("correlation_alert", 0.7):
        monitor_reasons.append("correlacao_ibov_alta")
    if not pd.isna(cv) and cv > settings.get("risk", {}).get("cv_limit", np.inf) and not settings.get("risk", {}).get("cv_as_hard_filter", False):
        monitor_reasons.append("cv_individual_alto_sem_hard_filter")
    if row.get("alerta_sinal_tardio", False):
        flexible_reasons.append("possivel_sinal_tardio")

    watchlist_de_virada = bool(pd.notna(retorno_medio) and retorno_medio <= 0 and not row.get("fundamento_bloqueante", False) and (row.get("qualidade_fundamentalista") in {"otima", "boa", "aceitavel"} or trend_month in {"alta_aceitavel_ou_virada", "correcao_em_tendencia"}))
    if block_reasons:
        tipo = "watchlist_bloqueante"
        motivo = "; ".join(dict.fromkeys(block_reasons))
        bloqueia = True
    elif flexible_reasons:
        tipo = "watchlist_flexivel"
        motivo = "; ".join(dict.fromkeys(flexible_reasons + monitor_reasons))
        bloqueia = False
    elif monitor_reasons:
        tipo = "watchlist_monitoramento"
        motivo = "; ".join(dict.fromkeys(monitor_reasons))
        bloqueia = False
    else:
        tipo = "nao_watchlist"
        motivo = ""
        bloqueia = False
    return pd.Series({"tipo_watchlist": tipo, "motivo_tipo_watchlist": motivo, "watchlist_bloqueia_otimizacao": bloqueia, "watchlist_de_virada": watchlist_de_virada})


def refine_timing_watchlist(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    output_cols = [
        "alerta_sinal_tardio", "motivos_alerta_sinal_tardio", "qualidade_do_timing",
        "tipo_watchlist", "motivo_tipo_watchlist", "watchlist_bloqueia_otimizacao", "watchlist_de_virada",
    ]
    refined = frame.copy().drop(columns=[col for col in output_cols if col in frame.columns], errors="ignore")
    late = refined.apply(lambda row: _late_signal_and_timing_quality(row, settings), axis=1)
    refined = pd.concat([refined, late], axis=1)
    watch_types = refined.apply(lambda row: _watchlist_type_fields(row, settings), axis=1)
    refined = pd.concat([refined, watch_types], axis=1)
    refined["motivo_exclusao_por_timing"] = np.where(
        refined["watchlist_bloqueia_otimizacao"].fillna(False),
        refined["motivo_tipo_watchlist"].fillna("watchlist bloqueante"),
        refined.get("motivo_exclusao_por_timing", pd.Series("", index=refined.index)).fillna(""),
    )
    return refined


def relative_strength_ranking(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.loc[:, ~frame.columns.duplicated()].copy()
    cols = [
        "ticker", "nome", "setor", "retorno_acumulado_1m", "retorno_1m_ibov", "retorno_1m_relativo_ibov",
        "retorno_acumulado_4m", "retorno_4m_ibov", "retorno_4m_relativo_ibov",
        "retorno_ytd", "retorno_ytd_ibov", "retorno_ytd_relativo_ibov",
        "forca_relativa_score", "classificacao_forca_relativa", "tipo_timing", "status_para_risco",
        "categoria_elegibilidade", "nota preliminar", "nota_final",
    ]
    ranking = frame.reindex(columns=cols).copy()
    sort_cols = [col for col in ["forca_relativa_score", "retorno_4m_relativo_ibov", "retorno_1m_relativo_ibov", "retorno_ytd_relativo_ibov"] if col in ranking]
    if sort_cols:
        ranking = ranking.sort_values(sort_cols, ascending=[False] * len(sort_cols))
    return ranking


def apply_watchlist_flags(preliminary: pd.DataFrame, watchlist: pd.DataFrame) -> pd.DataFrame:
    result = preliminary.copy()
    if watchlist.empty or "ticker" not in watchlist:
        result["flag_watchlist"] = False
        result["motivo_watchlist"] = ""
    else:
        reasons = watchlist.set_index("ticker")["motivo_watchlist"] if "motivo_watchlist" in watchlist else pd.Series(dtype=str)
        result["flag_watchlist"] = result["ticker"].isin(reasons.index)
        result["motivo_watchlist"] = result["ticker"].map(reasons).fillna("")
    result["flag_watchlist_na_carteira"] = False
    result["motivo_exclusao_por_timing"] = np.where(
        result["flag_watchlist"],
        "ativo em Watchlist por timing inadequado",
        np.where(result.get("bloqueada_entrada_esticada", pd.Series(False, index=result.index)).fillna(False), "entrada esticada por sobrecompra", ""),
    )
    return result


def selective_weak_market_mask(frame: pd.DataFrame, settings: dict) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    relative_ok = frame.get("forca_relativa_positiva_relevante", pd.Series(False, index=frame.index)).fillna(False)
    timing_ok = frame.get("tipo_timing", pd.Series("", index=frame.index)).isin([
        "timing_favoravel_tendencia",
        "timing_favoravel_com_alerta",
        "timing_atencao_banda_superior",
        "timing_reversao_oportunidade",
    ])
    fundamentals_ok = frame.get("classificacao ROE x ROIC", pd.Series("", index=frame.index)).isin(["misto", "bom", "otimo"])
    not_watchlist = ~frame.get("watchlist_bloqueia_otimizacao", pd.Series(False, index=frame.index)).fillna(False) | bool(settings.get("watchlist", {}).get("allow_watchlist_entries", False))
    not_blocked = ~frame.get("bloqueada_entrada_esticada", pd.Series(False, index=frame.index)).fillna(False) | bool(settings.get("technical_timing", {}).get("allow_overbought_entries", False))
    return relative_ok & timing_ok & fundamentals_ok & not_watchlist & not_blocked


def selective_portfolio_violations(portfolio: pd.DataFrame, metrics: dict, settings: dict) -> list[str]:
    if portfolio.empty:
        return ["carteira vazia"]
    violations = []
    min_assets = int(settings.get("market_regime", {}).get("min_assets_for_selective_portfolio", 5))
    if len(portfolio) < min_assets:
        violations.append(f"menos de {min_assets} ativos na carteira seletiva")
    if not portfolio.get("forca_relativa_positiva_relevante", pd.Series(False, index=portfolio.index)).fillna(False).all():
        violations.append("ativo sem forca relativa positiva relevante contra o IBOV")
    if portfolio.get("watchlist_bloqueia_otimizacao", pd.Series(False, index=portfolio.index)).fillna(False).any() and not settings.get("watchlist", {}).get("allow_watchlist_entries", False):
        violations.append("ativo em Watchlist bloqueante entrou na carteira")
    if portfolio.get("bloqueada_entrada_esticada", pd.Series(False, index=portfolio.index)).fillna(False).any() and not settings.get("technical_timing", {}).get("allow_overbought_entries", False):
        violations.append("ativo bloqueado por sobrecompra entrou na carteira")
    cv_limit = settings.get("risk", {}).get("cv_limit", np.inf)
    cv = metrics.get("cv_carteira", np.nan)
    if not pd.isna(cv) and cv > cv_limit:
        violations.append(f"CV da carteira acima do limite configurado: {cv:.2f} > {cv_limit}")
    return violations


def portfolio_status_fields(portfolio: pd.DataFrame, metrics: dict, market_class: str, has_moderate: bool, selective_violations: list[str], historical_simulation: bool = False) -> tuple[str, str, str]:
    if not metrics.get("carteira_valida", False) or portfolio.empty:
        if market_class == "mercado fraco/desfavoravel":
            return "sem_carteira_recomendada", "sem carteira recomendada", "Sem carteira recomendada - mercado fraco/desfavoravel; criterios seletivos nao foram atendidos."
        return "sem_carteira_recomendada", "sem carteira recomendada", "Sem carteira recomendada - restricoes ou ativos insuficientes."
    if historical_simulation:
        return "carteira_simulada_data_base", "carteira simulada na data-base", "Carteira formada na data-base para avaliacao de desempenho posterior; nao e recomendacao emitida em tempo real."
    if market_class == "mercado fraco/desfavoravel" and not selective_violations:
        return "carteira_seletiva_em_mercado_fraco", "carteira seletiva em mercado fraco", "Carteira seletiva em mercado fraco - formada por ativos com forca relativa positiva contra o IBOV."
    if has_moderate:
        return "carteira_valida_com_flexibilizacao", "criterios flexibilizados", "Carteira valida com flexibilizacao controlada dos criterios de risco."
    return "carteira_recomendada_atual", "criterios originais", "Carteira recomendada atual formada pelos criterios originais."
def oversold_confirmation(row: pd.Series) -> bool:
    return bool(
        row.get("recuperacao_forte", False)
        and row.get("tendencia") in {"Forte alta", "Aceitavel"}
        and row.get("preco_atual", np.nan) > row.get("mm50", np.nan)
        and row.get("mm9", np.nan) > row.get("mm21", np.nan)
        and row.get("bollinger_status") in {"favoravel", "oportunidade"}
    )


def classify_roe_roic(row: pd.Series) -> str:
    roe = row.get("roe", np.nan)
    roic = row.get("roic", np.nan)
    if pd.isna(roe) and pd.isna(roic):
        return "indisponivel"
    good_roe = not pd.isna(roe) and roe >= 0.10
    great_roe = not pd.isna(roe) and roe > 0.20
    good_roic = not pd.isna(roic) and roic >= 0.08
    great_roic = not pd.isna(roic) and roic > 0.15
    if great_roe and great_roic:
        return "otimo"
    if good_roe and good_roic:
        return "bom"
    if good_roe or good_roic:
        return "misto"
    return "fraco"


def rsi_signal(row: pd.Series) -> str:
    status = row.get("rsi_status", "")
    alert = row.get("alertas_tecnicos", "")
    return alert if isinstance(alert, str) and "RSI" in alert else status


def bollinger_signal(row: pd.Series) -> str:
    status = row.get("bollinger_status", "")
    alert = row.get("alertas_tecnicos", "")
    return alert if isinstance(alert, str) and "Bollinger" in alert else status


def _timing_settings(settings: dict) -> dict:
    cfg = settings.get("technical_timing", {})
    return {
        "ideal_rsi_min": float(cfg.get("ideal_rsi_min", 50)),
        "ideal_rsi_max": float(cfg.get("ideal_rsi_max", 65)),
        "attention_rsi_max": float(cfg.get("attention_rsi_max", 70)),
        "overbought_rsi": float(cfg.get("overbought_rsi", 70)),
        "extreme_overbought_rsi": float(cfg.get("extreme_overbought_rsi", 75)),
        "oversold_rsi": float(cfg.get("oversold_rsi", 30)),
        "reversal_rsi_limit": float(cfg.get("reversal_rsi_limit", 35)),
        "near_band_threshold": float(cfg.get("near_band_threshold", 0.05)),
        "block_overbought_near_upper_band": bool(cfg.get("block_overbought_near_upper_band", True)),
        "allow_overbought_entries": bool(cfg.get("allow_overbought_entries", False)),
    }


def _band_position(row: pd.Series) -> float:
    return row.get("bollinger_position", np.nan)


def _distance_percent(price: float, band: float, direction: str) -> float:
    if pd.isna(price) or pd.isna(band) or price == 0:
        return np.nan
    if direction == "upper":
        return float((band - price) / price)
    return float((price - band) / price)


def _near_upper_band(row: pd.Series, settings: dict) -> bool:
    threshold = _timing_settings(settings)["near_band_threshold"]
    position = _band_position(row)
    price = row.get("preco_atual", np.nan)
    upper = row.get("bollinger_upper", np.nan)
    if not pd.isna(position):
        return bool(position >= 1 - threshold)
    if pd.isna(price) or pd.isna(upper) or price == 0:
        return False
    return bool((upper - price) / price <= threshold)


def _near_lower_band(row: pd.Series, settings: dict) -> bool:
    threshold = _timing_settings(settings)["near_band_threshold"]
    position = _band_position(row)
    price = row.get("preco_atual", np.nan)
    lower = row.get("bollinger_lower", np.nan)
    if not pd.isna(position):
        return bool(position <= threshold)
    if pd.isna(price) or pd.isna(lower) or price == 0:
        return False
    return bool((price - lower) / price <= threshold)


def _fundamental_deterioration(row: pd.Series) -> bool:
    values = [row.get("roe", np.nan), row.get("roic", np.nan), row.get("margem_bruta", np.nan)]
    if any(not pd.isna(value) and value < 0 for value in values):
        return True
    pl = row.get("pl_atual", np.nan)
    return bool(not pd.isna(pl) and pl <= 0)


def _solid_reversal_fundamentals(row: pd.Series) -> bool:
    roe_roic = classify_roe_roic(row)
    margin = row.get("margem_bruta", np.nan)
    pl = row.get("pl_atual", np.nan)
    margin_ok = not pd.isna(margin) and margin > 0.10
    pl_ok = pd.isna(pl) or (pl > 0 and pl < 60)
    return bool(roe_roic in {"bom", "otimo"} and margin_ok and pl_ok and not _fundamental_deterioration(row))


def _acceptable_reversal_fundamentals(row: pd.Series) -> bool:
    if _fundamental_deterioration(row):
        return False
    roe_roic = classify_roe_roic(row)
    roe = row.get("roe", np.nan)
    roic = row.get("roic", np.nan)
    margin = row.get("margem_bruta", np.nan)
    pl = row.get("pl_atual", np.nan)
    quality_ok = roe_roic in {"misto", "bom", "otimo"}
    quality_ok = quality_ok or (not pd.isna(roe) and roe >= 0.08) or (not pd.isna(roic) and roic >= 0.06)
    margin_ok = pd.isna(margin) or margin > 0.10
    pl_ok = pd.isna(pl) or (pl > 0 and pl < 80)
    return bool(quality_ok and margin_ok and pl_ok)


def _reversal_signals(row: pd.Series, settings: dict) -> dict:
    cfg = _timing_settings(settings)
    rsi = row.get("rsi", np.nan)
    boll = row.get("bollinger_status", "")
    price = row.get("preco_atual", np.nan)
    lower = row.get("bollinger_lower", np.nan)
    near_lower = _near_lower_band(row, settings)
    below_lower = bool(not pd.isna(price) and not pd.isna(lower) and price <= lower)
    lower_distance = _distance_percent(price, lower, "lower")
    close_by_distance = bool(not pd.isna(lower_distance) and lower_distance <= cfg["near_band_threshold"])

    if pd.isna(rsi):
        rsi_signal = "rsi_indisponivel"
    elif rsi < cfg["oversold_rsi"]:
        rsi_signal = "sobrevenda_extrema"
    elif rsi <= cfg["reversal_rsi_limit"]:
        rsi_signal = "sobrevenda_moderada"
    else:
        rsi_signal = "sem_sinal_reversao"

    if boll == "oportunidade":
        boll_signal = "bollinger_oportunidade"
    elif below_lower:
        boll_signal = "abaixo_banda_inferior"
    elif near_lower or close_by_distance:
        boll_signal = "proximo_banda_inferior"
    else:
        boll_signal = "sem_sinal_reversao"

    rsi_ok = not pd.isna(rsi) and rsi <= cfg["reversal_rsi_limit"]
    boll_ok = boll_signal != "sem_sinal_reversao"
    fundamentals_good = _solid_reversal_fundamentals(row)
    fundamentals_ok = fundamentals_good or _acceptable_reversal_fundamentals(row)
    no_deterioration = not _fundamental_deterioration(row)
    structural_ok = bool(
        row.get("mm50", np.nan) > row.get("mm100", np.nan)
        or row.get("preco_atual", np.nan) > row.get("mm50", np.nan)
    )
    return_ok = bool(row.get("retorno_ytd", np.nan) > 0 or row.get("retorno_acumulado_4m", np.nan) > 0)

    score = 0
    score += 2 if rsi_ok else 0
    score += 2 if boll_ok else 0
    score += 2 if fundamentals_good else 1 if fundamentals_ok else 0
    score += 1 if no_deterioration else 0
    score += 1 if structural_ok else 0
    score += 1 if return_ok else 0
    approved = bool(rsi_ok and boll_ok and fundamentals_ok and no_deterioration and structural_ok and return_ok)
    strong = bool(approved and not pd.isna(rsi) and rsi < cfg["oversold_rsi"] and boll == "oportunidade" and fundamentals_good)
    moderate = bool(approved and not strong)

    reasons = []
    if rsi_ok:
        reasons.append(rsi_signal)
    if boll_ok:
        reasons.append(boll_signal)
    if fundamentals_good:
        reasons.append("fundamentos bons/otimos")
    elif fundamentals_ok:
        reasons.append("fundamentos aceitaveis")
    else:
        reasons.append("fundamentos insuficientes para reversao")
    if not no_deterioration:
        reasons.append("deterioracao fundamentalista grave")
    if structural_ok:
        reasons.append("estrutura tecnica preservada")
    else:
        reasons.append("estrutura tecnica deteriorada")
    if return_ok:
        reasons.append("YTD ou 4m positivo")
    else:
        reasons.append("retorno recente insuficiente")

    return {
        "rsi_reversal_signal": rsi_signal,
        "bollinger_reversal_signal": boll_signal,
        "reversal_score": score,
        "motivo_reversao": "; ".join(reasons),
        "aprovado_reversao": approved,
        "reversao_forte": strong,
        "reversao_moderada": moderate,
    }


def classify_timing(row: pd.Series, settings: dict) -> pd.Series:
    cfg = _timing_settings(settings)
    rsi = row.get("rsi", np.nan)
    price = row.get("preco_atual", np.nan)
    upper = row.get("bollinger_upper", np.nan)
    lower = row.get("bollinger_lower", np.nan)
    dist_upper = _distance_percent(price, upper, "upper")
    dist_lower = _distance_percent(price, lower, "lower")
    near_upper = _near_upper_band(row, settings)
    trend_positive = row.get("tendencia") in {"Forte alta", "Aceitavel"}
    above_main_mas = bool(
        row.get("preco_atual", np.nan) > row.get("mm50", np.nan)
        and row.get("mm9", np.nan) > row.get("mm21", np.nan)
    )
    reversal = _reversal_signals(row, settings)

    overbought = not pd.isna(rsi) and rsi > cfg["overbought_rsi"]
    extreme_overbought = not pd.isna(rsi) and rsi >= cfg["extreme_overbought_rsi"]
    blocked_overbought = bool(
        not cfg["allow_overbought_entries"]
        and (extreme_overbought or (cfg["block_overbought_near_upper_band"] and overbought and near_upper))
    )
    base = {
        "distancia_banda_superior_pct": dist_upper,
        "distancia_banda_inferior_pct": dist_lower,
        "rsi_reversal_signal": reversal["rsi_reversal_signal"],
        "bollinger_reversal_signal": reversal["bollinger_reversal_signal"],
        "reversal_score": reversal["reversal_score"],
        "motivo_reversao": reversal["motivo_reversao"],
        "aprovado_reversao": reversal["aprovado_reversao"],
    }
    if blocked_overbought:
        return pd.Series(
            base
            | {
                "tipo_timing": "timing_esticado_sobrecompra",
                "sinal_timing": "tendencia favoravel, mas entrada esticada" if trend_positive else "entrada esticada por sobrecompra",
                "justificativa_timing": "RSI acima do limite e preco proximo/acima da banda superior de Bollinger; enviar para Watchlist",
                "bloqueada_entrada_esticada": True,
                "candidata_reversao": False,
            }
        )

    if reversal["aprovado_reversao"]:
        signal = "reversao_forte" if reversal["reversao_forte"] else "reversao_moderada"
        return pd.Series(
            base
            | {
                "tipo_timing": "timing_reversao_oportunidade",
                "sinal_timing": signal,
                "justificativa_timing": reversal["motivo_reversao"],
                "bloqueada_entrada_esticada": False,
                "candidata_reversao": True,
            }
        )

    if not pd.isna(rsi) and rsi < cfg["oversold_rsi"]:
        return pd.Series(
            base
            | {
                "tipo_timing": "timing_fraqueza_sem_confirmacao",
                "sinal_timing": "fraqueza sem confirmacao",
                "justificativa_timing": reversal["motivo_reversao"],
                "bloqueada_entrada_esticada": False,
                "candidata_reversao": False,
            }
        )

    if not pd.isna(rsi) and rsi <= cfg["reversal_rsi_limit"] and reversal["bollinger_reversal_signal"] != "sem_sinal_reversao":
        return pd.Series(
            base
            | {
                "tipo_timing": "timing_reversao_nao_aprovada",
                "sinal_timing": "reversao nao aprovada",
                "justificativa_timing": reversal["motivo_reversao"],
                "bloqueada_entrada_esticada": False,
                "candidata_reversao": False,
            }
        )

    favorable_band_attention = bool(
        not pd.isna(rsi)
        and cfg["ideal_rsi_min"] <= rsi <= cfg["ideal_rsi_max"]
        and trend_positive
        and above_main_mas
        and near_upper
    )
    if favorable_band_attention:
        return pd.Series(
            base
            | {
                "tipo_timing": "timing_favoravel_com_alerta",
                "sinal_timing": "tendencia favoravel com atencao a banda superior",
                "justificativa_timing": "RSI em faixa ideal e tendencia positiva; preco proximo da banda superior gera alerta, sem bloqueio automatico",
                "bloqueada_entrada_esticada": False,
                "candidata_reversao": False,
            }
        )

    favorable = bool(
        not pd.isna(rsi)
        and cfg["ideal_rsi_min"] <= rsi <= cfg["ideal_rsi_max"]
        and trend_positive
        and above_main_mas
        and not near_upper
    )
    if favorable:
        return pd.Series(
            base
            | {
                "tipo_timing": "timing_favoravel_tendencia",
                "sinal_timing": "bom ponto de entrada em tendencia",
                "justificativa_timing": "RSI em faixa ideal, preco acima das medias e sem proximidade excessiva da banda superior",
                "bloqueada_entrada_esticada": False,
                "candidata_reversao": False,
            }
        )

    alerts = []
    if overbought:
        alerts.append("RSI acima de 70 reduz pontuacao de timing")
    if near_upper:
        alerts.append("preco proximo da banda superior")
    return pd.Series(
        base
        | {
            "tipo_timing": "timing_neutro",
            "sinal_timing": "; ".join(alerts) if alerts else "timing sem sinal extremo",
            "justificativa_timing": "Nao atende plenamente aos criterios de tendencia ideal, reversao ou bloqueio por sobrecompra",
            "bloqueada_entrada_esticada": False,
            "candidata_reversao": False,
        }
    )
def methodological_alerts(row: pd.Series, settings: dict) -> str:
    alerts = []
    cfg = _timing_settings(settings)
    if row.get("rsi", np.nan) < cfg["oversold_rsi"]:
        alerts.append("RSI abaixo de 30; alerta de sobrevenda, sem bloqueio automatico")
    if row.get("candidata_reversao", False):
        alerts.append("Candidata de reversao: exige limite de participacao e justificativa explicita")
    if row.get("bloqueada_entrada_esticada", False):
        alerts.append("Tendencia favoravel, mas entrada esticada por sobrecompra/Bollinger")
    if row.get("cv", np.nan) > settings["risk"]["cv_limit"]:
        alerts.append("CV acima do limite configurado; sem sinal verde de risco")
    if row.get("indice_setorial_fallback_ibov", False):
        alerts.append("IBOV usado como fallback setorial")
    timing_reason = row.get("justificativa_timing", "")
    if timing_reason and row.get("tipo_timing") in {"timing_reversao_oportunidade", "timing_esticado_sobrecompra"}:
        alerts.append(timing_reason)
    return "; ".join(dict.fromkeys(alerts))


def preliminary_block_reasons(row: pd.Series, settings: dict) -> list[str]:
    reasons = []
    timing = row.get("tipo_timing", "")
    if row.get("price_rows", 0) < settings["data"]["min_price_rows"]:
        reasons.append("dados de cotacao insuficientes")
    if row.get("bloqueada_entrada_esticada", False):
        reasons.append("entrada esticada por sobrecompra")
    if timing in {"timing_fraqueza_sem_confirmacao", "timing_reversao_nao_aprovada"}:
        reasons.append("reversao sem confirmacao suficiente")
    if row.get("tendencia") in {"Descarte", "Fraca"} and timing != "timing_reversao_oportunidade":
        reasons.append("tendencia tecnica negativa")
    if row.get("retorno_ytd", 0) < 0 and not row.get("recuperacao_forte", False) and timing != "timing_reversao_oportunidade":
        reasons.append("YTD negativo sem recuperacao forte")
    if row.get("tendencia_setorial") == "baixa" and row.get("tendencia") in {"Fraca", "Descarte"} and timing != "timing_reversao_oportunidade":
        reasons.append("acao fraca em setor fraco")
    if _fundamental_deterioration(row):
        reasons.append("deterioracao fundamentalista grave")
    return reasons


def preliminary_score(row: pd.Series, settings: dict) -> float:
    raw = score_technical(row) + score_timing(row, settings) + score_fundamentals(row) + score_sector(row)
    return round(raw / 80 * 100, 2)


def preliminary_classification(score: float) -> str:
    if score >= 75:
        return "alta prioridade"
    if score >= 55:
        return "prioridade media"
    if score >= 40:
        return "observacao"
    return "baixa prioridade"


def preliminary_risk_status(row: pd.Series) -> pd.Series:
    if row.get("decisao preliminar") == "descartar":
        reason = row.get("motivos_bloqueio_preliminar", "") or "bloqueio pela analise preliminar"
        return pd.Series({"status_para_risco": "bloqueada_para_risco", "motivo_status_para_risco": reason})
    if row.get("tipo_timing") == "timing_reversao_oportunidade":
        return pd.Series({"status_para_risco": "moderada_para_risco", "motivo_status_para_risco": "candidata de reversao com limite de participacao"})
    alerts = alert_join([row.get("alerta tecnico", ""), row.get("alerta fundamentalista", ""), row.get("sinal_timing", "") if row.get("tipo_timing") == "timing_neutro" else ""])
    score = row.get("nota preliminar", 0)
    if score >= 75 and not alerts:
        return pd.Series({"status_para_risco": "aprovada_para_risco", "motivo_status_para_risco": "aprovada pela analise preliminar"})
    reason = alerts or "candidata com ressalvas pela nota preliminar"
    return pd.Series({"status_para_risco": "moderada_para_risco", "motivo_status_para_risco": reason})


def _is_near(a: float, b: float, tolerance: float = 0.03) -> bool:
    if pd.isna(a) or pd.isna(b) or b == 0:
        return False
    return abs(a - b) / abs(b) <= tolerance


def _monthly_trend_fields(row: pd.Series) -> pd.Series:
    price = row.get("preco_atual", np.nan)
    mm9 = row.get("mm9", np.nan)
    mm21 = row.get("mm21", np.nan)
    mm50 = row.get("mm50", np.nan)
    mm100 = row.get("mm100", np.nan)
    rsi = row.get("rsi", np.nan)
    timing = row.get("tipo_timing", "")
    sector_trend = row.get("tendencia_setorial", "")
    fields = {
        "mm9_semanal": mm9,
        "mm21_semanal": mm21,
        "mm50_semanal": mm50,
        "mm100_semanal": mm100,
        "preco_acima_mm9": bool(not pd.isna(price) and not pd.isna(mm9) and price > mm9),
        "preco_acima_mm21": bool(not pd.isna(price) and not pd.isna(mm21) and price > mm21),
        "preco_acima_mm50": bool(not pd.isna(price) and not pd.isna(mm50) and price > mm50),
        "preco_acima_mm100": bool(not pd.isna(price) and not pd.isna(mm100) and price > mm100),
        "mm9_maior_mm21": bool(not pd.isna(mm9) and not pd.isna(mm21) and mm9 > mm21),
        "mm50_maior_mm100": bool(not pd.isna(mm50) and not pd.isna(mm100) and mm50 > mm100),
        "classificacao_antiga_medias": row.get("tendencia", ""),
    }
    if any(pd.isna(v) for v in [mm50, mm100]):
        contexto = "estrutural_indefinida"
    elif _is_near(mm50, mm100):
        contexto = "estrutural_indefinida"
    elif mm50 > mm100:
        contexto = "estrutural_alta"
    else:
        contexto = "estrutural_baixa"

    rsi_ok = pd.isna(rsi) or 45 <= rsi <= 70
    reversal_ok = timing == "timing_reversao_oportunidade"
    if fields["mm9_maior_mm21"] and fields["preco_acima_mm21"] and fields["preco_acima_mm50"] and rsi_ok:
        trend = "alta_forte_mensal"
    elif fields["mm9_maior_mm21"] and fields["preco_acima_mm21"] and rsi_ok:
        trend = "alta_aceitavel_ou_virada"
    elif fields["mm50_maior_mm100"] and not fields["mm9_maior_mm21"]:
        trend = "correcao_em_tendencia"
    elif (not fields["mm9_maior_mm21"] and not fields["preco_acima_mm50"] and contexto == "estrutural_baixa" and not reversal_ok) or (sector_trend == "baixa" and row.get("tendencia") in {"Fraca", "Descarte"}):
        trend = "descarte_tecnico"
    elif not fields["mm9_maior_mm21"] and (not fields["preco_acima_mm21"] or not fields["preco_acima_mm50"]) and not reversal_ok:
        trend = "fraca"
    else:
        trend = "alta_aceitavel_ou_virada" if fields["mm9_maior_mm21"] else "correcao_em_tendencia"
    fields["tendencia_mensal"] = trend
    fields["contexto_estrutural"] = contexto
    return pd.Series(fields)


def _relative_monthly_read(row: pd.Series) -> str:
    rel_1m = row.get("retorno_1m_relativo_ibov", np.nan)
    rel_4m = row.get("retorno_4m_relativo_ibov", np.nan)
    rel_ytd = row.get("retorno_ytd_relativo_ibov", np.nan)
    ret_1m = row.get("retorno_acumulado_1m", np.nan)
    boll = row.get("bollinger_status", "")
    rsi = row.get("rsi", np.nan)
    if not pd.isna(rel_1m) and rel_1m > 0 and not pd.isna(ret_1m) and ret_1m > 0:
        return "forte_no_mes" if rel_1m >= 0.05 else "positiva_no_mes"
    if not pd.isna(rel_1m) and rel_1m < 0 and (rel_4m > 0 or rel_ytd > 0):
        if boll == "oportunidade" or (not pd.isna(rsi) and rsi <= 45):
            return "reversao_em_observacao"
        return "forte_no_medio_prazo_mas_fraca_no_mes"
    if (pd.isna(rel_1m) or abs(rel_1m) <= 0.01) and (pd.isna(rel_4m) or rel_4m >= -0.01):
        return "neutra"
    if (not pd.isna(rel_1m) and rel_1m < 0) and (pd.isna(rel_4m) or rel_4m <= 0) and (pd.isna(rel_ytd) or rel_ytd <= 0):
        return "fraca"
    return "neutra"


def _valid_for_sector(series: pd.Series, positive_only: bool = False, allow_negative: bool = False) -> pd.Series:
    clean = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if positive_only:
        clean = clean[clean > 0]
    elif not allow_negative:
        clean = clean[clean >= 0]
    if clean.empty:
        return clean
    q01, q99 = clean.quantile(0.01), clean.quantile(0.99)
    return clean[(clean >= q01) & (clean <= q99)]


def _compare_indicator(value: float, median: float, higher_is_better: bool, positive_required: bool = False) -> str:
    if pd.isna(value) or pd.isna(median):
        return "dados_insuficientes"
    if positive_required and value <= 0:
        return "indicador_invalido_ou_negativo"
    band_low = median * 0.90
    band_high = median * 1.10
    if higher_is_better:
        if value > band_high:
            return "acima_do_setor"
        if value < band_low:
            return "abaixo_do_setor"
        return "em_linha_com_setor"
    if value < band_low:
        return "melhor_que_setor"
    if value > band_high:
        return "pior_que_setor"
    return "em_linha_com_setor"



def _gross_margin_reading(value: float, median: float) -> str:
    if pd.isna(value):
        return "margem_bruta_indisponivel"
    if value < 0:
        return "margem_bruta_negativa"
    if pd.isna(median) or median <= 0:
        return "margem_bruta_indisponivel"
    if value > median * 1.10:
        return "margem_bruta_acima_setor"
    if value < median * 0.90:
        return "margem_bruta_abaixo_setor"
    return "margem_bruta_em_linha_setor"


def _add_market_cap_fields(prelim: pd.DataFrame) -> pd.DataFrame:
    result = prelim.copy()
    if "valor_mercado" not in result:
        result["valor_mercado"] = np.nan
    market_cap = pd.to_numeric(result["valor_mercado"], errors="coerce")
    valid_market_cap = market_cap.where(market_cap > 0)
    total_universe = valid_market_cap.sum(min_count=1)
    sector_totals = valid_market_cap.groupby(result["setor"], dropna=False).transform("sum")
    result["valor_mercado_total_universo"] = total_universe
    result["valor_mercado_setor_total"] = sector_totals
    result["participacao_empresa_no_universo"] = np.where(total_universe > 0, valid_market_cap / total_universe, np.nan)
    result["participacao_empresa_no_setor"] = np.where(sector_totals > 0, valid_market_cap / sector_totals, np.nan)
    result["ranking_valor_mercado_universo"] = valid_market_cap.rank(ascending=False, method="min")
    result["ranking_valor_mercado_setor"] = valid_market_cap.groupby(result["setor"], dropna=False).rank(ascending=False, method="min")
    if "peso_oficial_ibov" not in result:
        result["peso_oficial_ibov"] = np.nan
    result["observacao_peso_ibov"] = np.where(
        result["peso_oficial_ibov"].notna(),
        "peso oficial do Ibovespa informado pela fonte",
        "peso oficial indisponivel; participacao por valor de mercado usada como proxy",
    )
    return result
def _sector_fundamental_table(prelim: pd.DataFrame) -> pd.DataFrame:
    data = prelim.copy()
    defaults = {
        "margem_liquida": "margem_bruta",
        "margem_ebit": "margem_bruta",
        "pvp": None,
        "dividend_yield": None,
        "divida_liquida_patrimonio": None,
        "crescimento_receita_5a": None,
        "valor_mercado": None,
    }
    for col, fallback in defaults.items():
        if col not in data:
            data[col] = data[fallback] if fallback and fallback in data else np.nan
    rows = []
    for sector, group in data.groupby("setor", dropna=False):
        rows.append(
            {
                "setor_base_comparacao": sector if pd.notna(sector) else "indisponivel",
                "qtd_empresas_setor": len(group),
                "qtd_empresas_validas_roe": len(_valid_for_sector(group.get("roe", pd.Series(dtype=float)), positive_only=True)),
                "qtd_empresas_validas_roic": len(_valid_for_sector(group.get("roic", pd.Series(dtype=float)), positive_only=True)),
                "qtd_empresas_validas_margem_bruta": len(_valid_for_sector(group.get("margem_bruta", pd.Series(dtype=float)), positive_only=True)),
                "qtd_empresas_validas_margem_liquida": len(_valid_for_sector(group.get("margem_liquida", pd.Series(dtype=float)), positive_only=True)),
                "qtd_empresas_validas_pl": len(_valid_for_sector(group.get("pl_atual", pd.Series(dtype=float)), positive_only=True)),
                "qtd_empresas_validas_divida_pl": len(_valid_for_sector(group.get("divida_liquida_patrimonio", pd.Series(dtype=float)), allow_negative=True)),
                "mediana_setorial_roe": _valid_for_sector(group.get("roe", pd.Series(dtype=float)), positive_only=True).median(),
                "mediana_setorial_roic": _valid_for_sector(group.get("roic", pd.Series(dtype=float)), positive_only=True).median(),
                "mediana_setorial_margem_bruta": _valid_for_sector(group.get("margem_bruta", pd.Series(dtype=float)), positive_only=True).median(),
                "mediana_setorial_margem_liquida": _valid_for_sector(group.get("margem_liquida", pd.Series(dtype=float)), positive_only=True).median(),
                "mediana_setorial_pl": _valid_for_sector(group.get("pl_atual", pd.Series(dtype=float)), positive_only=True).median(),
                "mediana_setorial_pvp": _valid_for_sector(group.get("pvp", pd.Series(dtype=float)), positive_only=True).median(),
                "mediana_setorial_dividend_yield": _valid_for_sector(group.get("dividend_yield", pd.Series(dtype=float))).median(),
                "mediana_setorial_divida_pl": _valid_for_sector(group.get("divida_liquida_patrimonio", pd.Series(dtype=float)), allow_negative=True).median(),
                "mediana_setorial_crescimento_receita_5a": _valid_for_sector(group.get("crescimento_receita_5a", pd.Series(dtype=float)), allow_negative=True).median(),
            }
        )
    return pd.DataFrame(rows)


def _sector_fundamental_fields(row: pd.Series) -> pd.Series:
    roe = row.get("roe", np.nan)
    roic = row.get("roic", np.nan)
    mb = row.get("margem_bruta", np.nan)
    ml = row.get("margem_liquida", np.nan)
    mebit = row.get("margem_ebit", np.nan)
    pl = row.get("pl_atual", np.nan)
    pvp = row.get("pvp", np.nan)
    dy = row.get("dividend_yield", np.nan)
    debt = row.get("divida_liquida_patrimonio", np.nan)
    growth = row.get("crescimento_receita_5a", np.nan)
    gaf = roe / roic if not pd.isna(roe) and not pd.isna(roic) and roic != 0 else np.nan
    if pd.isna(gaf):
        class_gaf = "dados_insuficientes"
    elif roic <= 0:
        class_gaf = "retorno_absoluto_fraco"
    elif gaf > 1.5:
        class_gaf = "alavancagem_elevando_roe"
    elif gaf > 1:
        class_gaf = "alavancagem_moderada"
    else:
        class_gaf = "baixo_efeito_alavancagem"
    comps = {
        "comparacao_roe_setor": _compare_indicator(roe, row.get("mediana_setorial_roe", np.nan), True, True),
        "comparacao_roic_setor": _compare_indicator(roic, row.get("mediana_setorial_roic", np.nan), True, True),
        "comparacao_margem_bruta_setor": _compare_indicator(mb, row.get("mediana_setorial_margem_bruta", np.nan), True, True),
        "comparacao_margem_liquida_setor": _compare_indicator(ml, row.get("mediana_setorial_margem_liquida", np.nan), True, True),
        "comparacao_pl_setor": _compare_indicator(pl, row.get("mediana_setorial_pl", np.nan), False, True),
        "comparacao_pvp_setor": _compare_indicator(pvp, row.get("mediana_setorial_pvp", np.nan), False, True),
        "comparacao_dividend_yield_setor": _compare_indicator(dy, row.get("mediana_setorial_dividend_yield", np.nan), True, False),
        "comparacao_divida_pl_setor": _compare_indicator(debt, row.get("mediana_setorial_divida_pl", np.nan), False, False),
        "comparacao_crescimento_receita_setor": _compare_indicator(growth, row.get("mediana_setorial_crescimento_receita_5a", np.nan), True, False),
    }
    score = 0
    score += 1 if comps["comparacao_roe_setor"] == "acima_do_setor" else 0
    score += 1 if comps["comparacao_roic_setor"] == "acima_do_setor" else 0
    score += 1 if comps["comparacao_margem_bruta_setor"] == "acima_do_setor" else 0
    score += 1 if comps["comparacao_margem_liquida_setor"] == "acima_do_setor" else 0
    score += 1 if comps["comparacao_pl_setor"] == "melhor_que_setor" else 0
    score += 1 if comps["comparacao_divida_pl_setor"] == "melhor_que_setor" else 0
    score += 1 if comps["comparacao_crescimento_receita_setor"] == "acima_do_setor" else 0
    score -= 1 if not pd.isna(roe) and roe < 0 else 0
    score -= 1 if not pd.isna(roic) and roic < 0 else 0
    score -= 1 if not pd.isna(mb) and mb < 0 else 0
    score -= 1 if not pd.isna(ml) and ml < 0 else 0
    score -= 1 if not pd.isna(pl) and pl < 0 else 0
    if not pd.isna(debt) and not pd.isna(row.get("mediana_setorial_divida_pl", np.nan)) and debt > row.get("mediana_setorial_divida_pl") * 2 and debt > 1:
        score -= 1
    score -= 1 if not pd.isna(growth) and growth < -0.10 else 0
    if score >= 4:
        cls = "forte_relativo_ao_setor"
    elif score >= 2:
        cls = "bom_relativo_ao_setor"
    elif score >= 0:
        cls = "neutro_relativo_ao_setor"
    elif score >= -2:
        cls = "fraco_relativo_ao_setor"
    else:
        cls = "critico_relativo_ao_setor"
    return pd.Series({"gaf_roe_roic": gaf, "classificacao_gaf": class_gaf, "leitura_margem_bruta": _gross_margin_reading(mb, row.get("mediana_setorial_margem_bruta", np.nan)), "score_fundamentalista_setorial": score, "classificacao_fundamentalista_setorial": cls, **comps})


def _fundamental_quality_fields(row: pd.Series) -> pd.Series:
    reasons = []
    warnings = []
    roe = row.get("roe", np.nan)
    roic = row.get("roic", np.nan)
    mb = row.get("margem_bruta", np.nan)
    ml = row.get("margem_liquida", np.nan)
    pl = row.get("pl_atual", np.nan)
    debt = row.get("divida_liquida_patrimonio", np.nan)
    growth = row.get("crescimento_receita_5a", np.nan)
    sector_cls = row.get("classificacao_fundamentalista_setorial", "")
    abs_score = 0
    abs_score += 1 if not pd.isna(roe) and roe >= 0.10 else 0
    abs_score += 1 if not pd.isna(roic) and roic >= 0.08 else 0
    abs_score += 1 if not pd.isna(ml) and ml > 0 else 0
    abs_score += 1 if not pd.isna(pl) and pl > 0 else 0
    abs_score += 1 if pd.isna(debt) or debt <= 1 else 0
    abs_score += 1 if pd.isna(growth) or growth >= 0 else 0
    critical_count = 0
    for label, value in [("ROE negativo", roe), ("ROIC negativo", roic), ("margem liquida negativa", ml)]:
        if not pd.isna(value) and value < 0:
            critical_count += 1
            reasons.append(label)
    if not pd.isna(pl) and pl < 0:
        critical_count += 1
        reasons.append("P/L negativo ou lucro distorcido")
    if not pd.isna(debt) and debt > 2 and (pd.isna(roic) or roic < 0.06):
        critical_count += 1
        reasons.append("alavancagem elevada com baixa rentabilidade")
    if not pd.isna(growth) and growth < -0.20:
        warnings.append("crescimento de receita negativo relevante")
    if row.get("classificacao_pl_setor", "") == "pior_que_setor" or (not pd.isna(pl) and not pd.isna(row.get("mediana_setorial_pl", np.nan)) and pl > row.get("mediana_setorial_pl") * 2):
        warnings.append("P/L alto em relacao ao setor")
    if pd.isna(pl):
        valuation = "P/L indisponivel; nao bloqueante sozinho"
    elif pl < 0:
        valuation = "P/L negativo; alerta de prejuizo ou lucro distorcido"
    elif not pd.isna(row.get("mediana_setorial_pl", np.nan)) and pl > row.get("mediana_setorial_pl") * 2:
        valuation = "P/L muito acima da mediana setorial"
    elif row.get("comparacao_pl_setor") == "melhor_que_setor":
        valuation = "P/L positivo abaixo da mediana setorial"
    else:
        valuation = "sem alerta relevante de P/L"
    alav = "alavancagem indisponivel"
    if not pd.isna(debt):
        alav = "alavancagem elevada" if debt > 1.5 else "alavancagem controlada"
    bloqueante = critical_count >= 2 or (not pd.isna(roe) and roe < -0.05) or (not pd.isna(ml) and ml < -0.05) or (not pd.isna(pl) and pl < 0 and critical_count >= 1)
    if bloqueante:
        qualidade = "critica"
    elif abs_score >= 5 and sector_cls in {"forte_relativo_ao_setor", "bom_relativo_ao_setor"}:
        qualidade = "otima"
    elif abs_score >= 4 or sector_cls in {"forte_relativo_ao_setor", "bom_relativo_ao_setor"}:
        qualidade = "boa"
    elif abs_score >= 2 and sector_cls != "critico_relativo_ao_setor":
        qualidade = "aceitavel"
    else:
        qualidade = "fraca"
    incomplete = any(pd.isna(row.get(col, np.nan)) for col in ["roe", "roic", "pl_atual"])
    risk = "fundamento_bloqueante" if bloqueante else "dados_fundamentalistas_incompletos" if incomplete else "risco_baixo" if qualidade in {"otima", "boa"} else "risco_moderado" if qualidade == "aceitavel" else "risco_elevado"
    return pd.Series(
        {
            "qualidade_fundamentalista": qualidade,
            "alerta_valuation": valuation,
            "alerta_alavancagem": alav,
            "risco_fundamentalista_mensal": risk,
            "motivo_risco_fundamentalista": "; ".join(dict.fromkeys(reasons + warnings)) or "sem sinais criticos objetivos",
            "fundamento_bloqueante": bool(bloqueante),
            "motivo_fundamento_bloqueante": "; ".join(dict.fromkeys(reasons)) if bloqueante else "",
        }
    )


def _preliminary_adjusted_decision(row: pd.Series) -> pd.Series:
    reasons = []
    if row.get("price_rows", 0) <= 0 or pd.isna(row.get("preco_atual", np.nan)):
        return pd.Series({"decisao_preliminar_ajustada": "descartar_dados_insuficientes", "motivo_decisao_preliminar": "dados essenciais de preco ausentes"})
    if row.get("fundamento_bloqueante", False):
        return pd.Series({"decisao_preliminar_ajustada": "descartar_fundamentalista", "motivo_decisao_preliminar": row.get("motivo_fundamento_bloqueante", "fundamento bloqueante")})
    timing = row.get("tipo_timing", "")
    trend = row.get("tendencia_mensal", "")
    rel = row.get("leitura_forca_relativa_mensal", "")
    qual = row.get("qualidade_fundamentalista", "")
    sector_cls = row.get("classificacao_fundamentalista_setorial", "")
    if trend == "descarte_tecnico" and rel == "fraca" and timing != "timing_reversao_oportunidade":
        return pd.Series({"decisao_preliminar_ajustada": "descartar_tecnico", "motivo_decisao_preliminar": "tendencia mensal fraca, preco abaixo das medias relevantes e forca relativa fraca"})
    clean_candidate = trend in {"alta_forte_mensal", "alta_aceitavel_ou_virada"} and timing in {"timing_favoravel_tendencia", "timing_favoravel_com_alerta"} and rel in {"forte_no_mes", "positiva_no_mes"} and qual in {"otima", "boa", "aceitavel"}
    if clean_candidate:
        return pd.Series({"decisao_preliminar_ajustada": "candidata_para_risco", "motivo_decisao_preliminar": "tendencia mensal favoravel, timing adequado, forca relativa positiva no mes e fundamentos sem bloqueio"})
    watch = False
    if qual in {"otima", "boa", "aceitavel"} and trend in {"correcao_em_tendencia", "fraca"}:
        watch = True; reasons.append("bons fundamentos, mas entrada mensal ainda sem confirmacao")
    if rel in {"forte_no_medio_prazo_mas_fraca_no_mes", "reversao_em_observacao"}:
        watch = True; reasons.append("forca relativa de medio prazo positiva, mas mes ainda fraco ou em observacao")
    if timing in {"timing_esticado_sobrecompra", "timing_reversao_nao_aprovada", "timing_fraqueza_sem_confirmacao"}:
        watch = True; reasons.append("timing exige espera ou confirmacao")
    if watch:
        return pd.Series({"decisao_preliminar_ajustada": "watchlist_qualificada", "motivo_decisao_preliminar": "; ".join(dict.fromkeys(reasons))})
    if trend in {"alta_forte_mensal", "alta_aceitavel_ou_virada"} or rel in {"forte_no_mes", "positiva_no_mes"} or timing == "timing_reversao_oportunidade":
        return pd.Series({"decisao_preliminar_ajustada": "candidata_com_restricao", "motivo_decisao_preliminar": "sinais tecnicos ou relativos favoraveis, mas com restricoes de timing, fundamento ou estrutura"})
    if qual == "fraca" or sector_cls in {"fraco_relativo_ao_setor", "critico_relativo_ao_setor"}:
        return pd.Series({"decisao_preliminar_ajustada": "descartar_fundamentalista", "motivo_decisao_preliminar": "fundamentos fracos em termos absolutos ou relativos ao setor"})
    return pd.Series({"decisao_preliminar_ajustada": "descartar_tecnico", "motivo_decisao_preliminar": "sem tendencia mensal favoravel, sem timing adequado e sem forca relativa mensal suficiente"})


def _preliminary_adjusted_score(row: pd.Series) -> float:
    score = 0
    trend_scores = {"alta_forte_mensal": 25, "alta_aceitavel_ou_virada": 20, "correcao_em_tendencia": 12, "fraca": 5, "descarte_tecnico": 0}
    rel_scores = {"forte_no_mes": 20, "positiva_no_mes": 15, "forte_no_medio_prazo_mas_fraca_no_mes": 8, "reversao_em_observacao": 8, "neutra": 5, "fraca": 0}
    qual_scores = {"otima": 15, "boa": 12, "aceitavel": 8, "fraca": 3, "critica": 0}
    sector_scores = {"forte_relativo_ao_setor": 10, "bom_relativo_ao_setor": 8, "neutro_relativo_ao_setor": 5, "fraco_relativo_ao_setor": 2, "critico_relativo_ao_setor": 0}
    timing_scores = {"timing_favoravel_tendencia": 20, "timing_favoravel_com_alerta": 16, "timing_reversao_oportunidade": 12, "timing_neutro": 6, "timing_reversao_nao_aprovada": 3, "timing_fraqueza_sem_confirmacao": 2, "timing_esticado_sobrecompra": 0}
    score += trend_scores.get(row.get("tendencia_mensal", ""), 0)
    score += rel_scores.get(row.get("leitura_forca_relativa_mensal", ""), 0)
    score += qual_scores.get(row.get("qualidade_fundamentalista", ""), 0)
    score += sector_scores.get(row.get("classificacao_fundamentalista_setorial", ""), 0)
    score += timing_scores.get(row.get("tipo_timing", ""), 0)
    score += 5 if row.get("tendencia_setorial") == "alta" else 2 if row.get("tendencia_setorial") == "neutro" else 0
    if row.get("fundamento_bloqueante", False):
        score -= 30
    if row.get("bloqueada_entrada_esticada", False):
        score -= 10
    return float(max(0, min(100, score)))



def _top_tickers_by(frame: pd.DataFrame, column: str, limit: int = 3) -> str:
    if frame.empty or column not in frame:
        return ""
    values = frame.dropna(subset=[column]).sort_values(column, ascending=False).head(limit)
    return ", ".join(values.get("ticker", pd.Series(dtype=str)).astype(str).tolist())


def _sector_sentiment(trend_pct: float, relative_pct: float, ret_1m: float, ret_ytd: float) -> str:
    trend_pct = 0.0 if pd.isna(trend_pct) else float(trend_pct)
    relative_pct = 0.0 if pd.isna(relative_pct) else float(relative_pct)
    ret_1m = 0.0 if pd.isna(ret_1m) else float(ret_1m)
    ret_ytd = 0.0 if pd.isna(ret_ytd) else float(ret_ytd)
    if trend_pct >= 0.45 and relative_pct >= 0.50 and ret_1m > 0 and ret_ytd > 0:
        return "setor_forte"
    if (trend_pct >= 0.30 or relative_pct >= 0.40) and ret_1m >= 0:
        return "setor_positivo"
    if trend_pct < 0.15 and relative_pct < 0.25 and ret_1m < 0 and ret_ytd < 0:
        return "setor_em_deterioracao"
    if trend_pct < 0.20 and relative_pct < 0.35 and ret_1m < 0:
        return "setor_fraco"
    return "setor_neutro"


def sector_market_diagnosis(preliminary: pd.DataFrame) -> pd.DataFrame:
    if preliminary.empty:
        return pd.DataFrame(
            columns=[
                "setor", "quantidade_empresas_analisadas", "quantidade_tendencia_mensal_favoravel",
                "percentual_tendencia_mensal_favoravel", "quantidade_forca_relativa_positiva_mes",
                "percentual_forca_relativa_positiva_mes", "retorno_medio_mes", "retorno_medio_ano",
                "sentimento_setorial", "principais_acoes_por_nota_preliminar",
                "principais_acoes_por_forca_relativa", "principais_acoes_por_valor_mercado",
            ]
        )
    rows = []
    for sector, group in preliminary.groupby("setor", dropna=False):
        total = len(group)
        favorable_trend = group.get("tendencia_mensal", pd.Series(dtype=str)).isin(["alta_forte_mensal", "alta_aceitavel_ou_virada"])
        positive_relative = pd.to_numeric(group.get("retorno_1m_relativo_ibov", pd.Series(dtype=float)), errors="coerce") > 0
        ret_1m = pd.to_numeric(group.get("retorno_acumulado_1m", pd.Series(dtype=float)), errors="coerce").mean()
        ret_ytd = pd.to_numeric(group.get("retorno_ytd", pd.Series(dtype=float)), errors="coerce").mean()
        trend_pct = float(favorable_trend.sum() / total) if total else np.nan
        relative_pct = float(positive_relative.sum() / total) if total else np.nan
        rows.append(
            {
                "setor": sector if pd.notna(sector) else "indisponivel",
                "quantidade_empresas_analisadas": total,
                "quantidade_tendencia_mensal_favoravel": int(favorable_trend.sum()),
                "percentual_tendencia_mensal_favoravel": trend_pct,
                "quantidade_forca_relativa_positiva_mes": int(positive_relative.sum()),
                "percentual_forca_relativa_positiva_mes": relative_pct,
                "retorno_medio_mes": ret_1m,
                "retorno_medio_ano": ret_ytd,
                "sentimento_setorial": _sector_sentiment(trend_pct, relative_pct, ret_1m, ret_ytd),
                "principais_acoes_por_nota_preliminar": _top_tickers_by(group, "nota_preliminar_ajustada"),
                "principais_acoes_por_forca_relativa": _top_tickers_by(group, "retorno_1m_relativo_ibov"),
                "principais_acoes_por_valor_mercado": _top_tickers_by(group, "valor_mercado"),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["sentimento_setorial", "percentual_tendencia_mensal_favoravel", "percentual_forca_relativa_positiva_mes"],
        ascending=[True, False, False],
    )
def preliminary_summary_table(preliminary: pd.DataFrame) -> pd.DataFrame:
    if preliminary.empty:
        return pd.DataFrame(columns=["metrica", "valor"])
    return pd.DataFrame(
        [
            ("quantidade total de ativos analisados", len(preliminary)),
            ("quantidade de candidata_para_risco", int(preliminary["decisao_preliminar_ajustada"].eq("candidata_para_risco").sum())),
            ("quantidade de candidata_com_restricao", int(preliminary["decisao_preliminar_ajustada"].eq("candidata_com_restricao").sum())),
            ("quantidade de watchlist_qualificada", int(preliminary["decisao_preliminar_ajustada"].eq("watchlist_qualificada").sum())),
            ("quantidade de descartar_tecnico", int(preliminary["decisao_preliminar_ajustada"].eq("descartar_tecnico").sum())),
            ("quantidade de descartar_fundamentalista", int(preliminary["decisao_preliminar_ajustada"].eq("descartar_fundamentalista").sum())),
            ("quantidade de descartar_dados_insuficientes", int(preliminary["decisao_preliminar_ajustada"].eq("descartar_dados_insuficientes").sum())),
            ("quantidade de fundamentos bloqueantes", int(preliminary["fundamento_bloqueante"].fillna(False).sum())),
            ("quantidade de acoes com forca relativa forte no mes", int(preliminary["leitura_forca_relativa_mensal"].eq("forte_no_mes").sum())),
            ("quantidade de acoes com tendencia mensal favoravel", int(preliminary["tendencia_mensal"].isin(["alta_forte_mensal", "alta_aceitavel_ou_virada"]).sum())),
            ("quantidade de acoes em sobrecompra", int(preliminary["tipo_timing"].eq("timing_esticado_sobrecompra").sum())),
            ("quantidade de acoes em reversao observada", int(preliminary["leitura_forca_relativa_mensal"].eq("reversao_em_observacao").sum() + preliminary["tipo_timing"].eq("timing_reversao_oportunidade").sum())),
        ],
        columns=["metrica", "valor"],
    )


def _apply_preliminary_enrichment(prelim: pd.DataFrame) -> pd.DataFrame:
    for col in ["margem_liquida", "margem_ebit", "pvp", "dividend_yield", "divida_liquida_patrimonio", "crescimento_receita_5a", "valor_mercado"]:
        if col not in prelim:
            prelim[col] = np.nan
    if "margem_liquida" in prelim and prelim["margem_liquida"].isna().all() and "margem_bruta" in prelim:
        prelim["margem_liquida"] = prelim["margem_bruta"]
    if "margem_ebit" in prelim and prelim["margem_ebit"].isna().all() and "margem_bruta" in prelim:
        prelim["margem_ebit"] = prelim["margem_bruta"]
    prelim = _add_market_cap_fields(prelim)
    prelim = pd.concat([prelim, prelim.apply(_monthly_trend_fields, axis=1)], axis=1)
    prelim["leitura_forca_relativa_mensal"] = prelim.apply(_relative_monthly_read, axis=1)
    sector_table = _sector_fundamental_table(prelim)
    prelim = prelim.merge(sector_table, left_on="setor", right_on="setor_base_comparacao", how="left")
    sector_fields = prelim.apply(_sector_fundamental_fields, axis=1)
    prelim = pd.concat([prelim, sector_fields], axis=1)
    quality = prelim.apply(_fundamental_quality_fields, axis=1)
    prelim = pd.concat([prelim, quality], axis=1)
    decisions = prelim.apply(_preliminary_adjusted_decision, axis=1)
    prelim = pd.concat([prelim, decisions], axis=1)
    prelim["watchlist_qualificada"] = prelim["decisao_preliminar_ajustada"].eq("watchlist_qualificada")
    prelim["motivo_watchlist_qualificada"] = np.where(prelim["watchlist_qualificada"], prelim["motivo_decisao_preliminar"], "")
    prelim["nota_preliminar_ajustada"] = prelim.apply(_preliminary_adjusted_score, axis=1)
    return prelim
def build_preliminary(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    prelim = frame.copy()
    prelim["MM semanal"] = prelim.apply(lambda r: f"MM9 {r.get('mm9', np.nan):.2f}; MM21 {r.get('mm21', np.nan):.2f}; MM50 {r.get('mm50', np.nan):.2f}; MM100 {r.get('mm100', np.nan):.2f}", axis=1)
    prelim["tendencia das medias moveis"] = prelim["tendencia"]
    prelim["RSI/IFR"] = prelim["rsi"]
    prelim["sinal do RSI"] = prelim.apply(rsi_signal, axis=1)
    prelim["Bollinger"] = prelim["bollinger_status"]
    prelim["sinal da Bollinger"] = prelim.apply(bollinger_signal, axis=1)
    prelim["classificacao ROE x ROIC"] = prelim.apply(classify_roe_roic, axis=1)
    timing = prelim.apply(lambda row: classify_timing(row, settings), axis=1)
    prelim = pd.concat([prelim, timing], axis=1)
    prelim["nota preliminar"] = prelim.apply(lambda row: preliminary_score(row, settings), axis=1)
    prelim["classificacao preliminar"] = prelim["nota preliminar"].map(preliminary_classification)
    prelim["alerta tecnico"] = prelim["alertas_tecnicos"].fillna("")
    prelim["alerta fundamentalista"] = prelim["alertas_fundamentos"].fillna("")
    prelim["motivos_bloqueio_preliminar"] = prelim.apply(lambda r: "; ".join(preliminary_block_reasons(r, settings)), axis=1)
    prelim["decisao preliminar"] = np.where(prelim["motivos_bloqueio_preliminar"].eq(""), "candidata", "descartar")
    prelim = pd.concat([prelim, prelim.apply(preliminary_risk_status, axis=1)], axis=1)
    prelim = _apply_preliminary_enrichment(prelim)
    prelim = prelim.sort_values(["retorno_ytd", "nota preliminar"], ascending=[False, False])
    return prelim


def has_positive_recent_returns(row: pd.Series) -> bool:
    return bool(
        row.get("retorno_acumulado_1m", np.nan) > 0
        and row.get("retorno_acumulado_4m", np.nan) > 0
        and row.get("retorno_ytd", np.nan) > 0
    )


def has_good_technical_trend(row: pd.Series) -> bool:
    return bool(
        row.get("tendencia") in {"Forte alta", "Aceitavel"}
        and row.get("mm9", np.nan) > row.get("mm21", np.nan)
        and row.get("preco_atual", np.nan) > row.get("mm50", np.nan)
        and row.get("rsi", np.nan) >= 30
    )


def risk_reasons(row: pd.Series, settings: dict, include_cv: bool) -> list[str]:
    reasons = []
    if row.get("retorno_medio", 0) <= 0:
        reasons.append("retorno medio nao positivo")
    cv = row.get("cv", np.nan)
    if include_cv and not pd.isna(cv) and cv > settings["risk"]["cv_limit"]:
        reasons.append("CV acima do limite configurado")
    return reasons


def optimization_alerts_and_penalties(row: pd.Series, settings: dict) -> pd.Series:
    alerts = []
    penalties = []
    beta_alert = settings.get("risk", {}).get("beta_alert", 1.0)
    corr_alert = settings.get("risk", {}).get("correlation_alert", 0.7)
    cv_limit = settings.get("risk", {}).get("cv_limit", np.inf)
    beta = row.get("beta", np.nan)
    corr = row.get("correlacao_ibov", np.nan)
    cv = row.get("cv", np.nan)
    if not pd.isna(beta) and beta > beta_alert:
        alerts.append(f"alerta_beta_acima_de_{beta_alert}")
        penalties.append("penalizacao_beta_alto")
    if not pd.isna(corr) and corr > corr_alert:
        alerts.append(f"alerta_correlacao_ibov_acima_de_{corr_alert}")
        penalties.append("penalizacao_correlacao_alta")
    if not pd.isna(cv) and cv > cv_limit:
        if settings.get("risk", {}).get("cv_as_hard_filter", False):
            penalties.append("cv_hard_filter_ativo")
        else:
            alerts.append(f"alerta_cv_individual_acima_de_{cv_limit}")
            penalties.append("penalizacao_cv_individual_alto")
    if row.get("tipo_watchlist") == "watchlist_flexivel":
        alerts.append("watchlist_flexivel_sem_bloqueio")
        penalties.append("penalizacao_watchlist_flexivel")
    if row.get("alerta_sinal_tardio", False):
        alerts.append("alerta_possivel_sinal_tardio")
        penalties.append("penalizacao_sinal_tardio")
    if row.get("qualidade_do_timing") == "timing_com_alerta":
        penalties.append("penalizacao_timing_com_alerta")
    if row.get("qualidade_do_timing") == "timing_tardio":
        penalties.append("penalizacao_timing_tardio")
    regime_penalties = [
        "penalizacao_beta_negativo_mercado_favoravel",
        "penalizacao_beta_muito_baixo_mercado_favoravel",
        "penalizacao_correlacao_negativa_mercado_favoravel",
        "penalizacao_correlacao_muito_baixa_mercado_favoravel",
        "penalizacao_beta_alto_mercado_fraco",
        "penalizacao_correlacao_alta_mercado_fraco",
        "penalizacao_forca_relativa_fraca",
        "penalizacao_retorno_1m_relativo_negativo",
        "penalizacao_retorno_1m_relativo_negativo_forte",
        "penalizacao_beta_alto_mercado_esticado",
        "penalizacao_realizacao_pos_rali",
    ]
    for name in regime_penalties:
        if bool(row.get(name, False)):
            alerts.append(name)
            penalties.append(name)
    return pd.Series({"alertas_nao_bloqueantes": "; ".join(dict.fromkeys(alerts)), "penalizacoes_otimizacao": "; ".join(dict.fromkeys(penalties))})


def optimization_block_fields(row: pd.Series, settings: dict, weak_market: bool) -> pd.Series:
    reasons = []
    types = []
    allow_overbought = _timing_settings(settings)["allow_overbought_entries"]
    allow_watchlist_entries = bool(settings.get("watchlist", {}).get("allow_watchlist_entries", False))
    cv_hard = bool(settings.get("risk", {}).get("cv_as_hard_filter", False))
    if row.get("status_para_risco") not in {"aprovada_para_risco", "moderada_para_risco"}:
        reasons.append("bloqueio_por_status_para_risco")
        types.append("bloqueio_preliminar")
    if row.get("categoria_elegibilidade") not in {"elegivel_forte", "elegivel_moderado"}:
        motivo = row.get("motivo_exclusao", "")
        if isinstance(motivo, str) and "retorno medio nao positivo" in motivo:
            reasons.append("bloqueio_por_retorno_medio_negativo")
            types.append("bloqueio_risco")
        elif isinstance(motivo, str) and "CV acima do limite configurado" in motivo and cv_hard:
            reasons.append("bloqueio_por_cv_hard_filter")
            types.append("bloqueio_risco")
        elif row.get("fundamento_bloqueante", False):
            reasons.append("bloqueio_por_fundamento_bloqueante")
            types.append("bloqueio_fundamentalista")
        elif row.get("price_rows", 0) <= 0 or pd.isna(row.get("retorno_medio", np.nan)):
            reasons.append("bloqueio_por_dados_insuficientes")
            types.append("bloqueio_dados")
        else:
            reasons.append(f"bloqueio_por_elegibilidade_{row.get('categoria_elegibilidade', 'indefinida')}")
            types.append("bloqueio_elegibilidade")
    if row.get("retorno_medio", np.nan) <= 0:
        reasons.append("bloqueio_por_retorno_medio_negativo")
        types.append("bloqueio_risco")
    if bool(row.get("watchlist_bloqueia_otimizacao", False)) and not allow_watchlist_entries:
        reasons.append(row.get("motivo_tipo_watchlist", "bloqueio_por_watchlist_timing") or "bloqueio_por_watchlist_timing")
        types.append("bloqueio_timing")
    if bool(row.get("bloqueada_entrada_esticada", False)) and not allow_overbought:
        reasons.append("bloqueio_por_sobrecompra_extrema")
        types.append("bloqueio_timing")
    if bool(row.get("bloqueio_forca_relativa_fraca", False)):
        reasons.append(row.get("motivo_bloqueio_forca_relativa", "bloqueio_por_forca_relativa_fraca_em_mercado_favoravel_esticado") or "bloqueio_por_forca_relativa_fraca_em_mercado_favoravel_esticado")
        types.append("bloqueio_forca_relativa")
    if bool(row.get("bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado", False)):
        reasons.append("bloqueio_por_retorno_1m_relativo_muito_fraco_em_mercado_esticado")
        types.append("bloqueio_forca_relativa")
    if bool(row.get("mercado_favoravel_esticado", False) or row.get("mercado_favoravel_cansado", False)) and pd.notna(row.get("beta", np.nan)) and row.get("beta", np.nan) > 1.80:
        weak_rel = row.get("classificacao_forca_relativa", "") == "fraca_contra_ibov"
        weak_fund = row.get("qualidade_fundamentalista", "") in {"fraca", "critica"}
        if weak_rel or weak_fund or bool(row.get("alerta_realizacao_pos_rali", False)):
            reasons.append("bloqueio_por_beta_acima_1_80_em_mercado_esticado_com_alertas")
            types.append("bloqueio_aderencia_regime")
    regime = str(settings.get("_runtime_market_class", "")).strip().lower()
    portfolio_cfg = settings.get("portfolio", {})
    allow_negative_regime = bool(portfolio_cfg.get("permitir_beta_negativo_em_mercado_favoravel", False))
    block_low_flex = bool(portfolio_cfg.get("bloquear_watchlist_flexivel_baixa_aderencia_mercado_favoravel", True))
    beta_min_flex = float(portfolio_cfg.get("beta_minimo_watchlist_flexivel_mercado_favoravel", portfolio_cfg.get("beta_muito_baixo_mercado_favoravel", 0.30)))
    corr_min_flex = float(portfolio_cfg.get("correlacao_minima_watchlist_flexivel_mercado_favoravel", portfolio_cfg.get("correlacao_muito_baixa_mercado_favoravel", 0.20)))
    beta = row.get("beta", np.nan)
    corr = row.get("correlacao_ibov", np.nan)
    watch_flex = row.get("tipo_watchlist", "") == "watchlist_flexivel"
    regime_block_reasons = []
    if regime == "mercado favoravel":
        beta_negative = pd.notna(beta) and beta < 0
        corr_negative = pd.notna(corr) and corr < 0
        beta_low = pd.notna(beta) and beta < beta_min_flex
        corr_low = pd.notna(corr) and corr < corr_min_flex
        if not allow_negative_regime:
            if watch_flex and beta_negative:
                regime_block_reasons.append("beta_negativo_em_mercado_favoravel_watchlist_flexivel")
            if watch_flex and corr_negative:
                regime_block_reasons.append("correlacao_negativa_em_mercado_favoravel_watchlist_flexivel")
            if beta_negative and corr_negative:
                regime_block_reasons.append("beta_e_correlacao_negativos_em_mercado_favoravel")
        if block_low_flex and watch_flex and beta_low and corr_low:
            regime_block_reasons.append("beta_e_correlacao_muito_baixos_em_mercado_favoravel_watchlist_flexivel")
    if regime_block_reasons:
        reasons.extend(regime_block_reasons)
        types.append("bloqueio_aderencia_regime")
    if weak_market and settings.get("relative_strength", {}).get("require_positive_relative_strength_in_weak_market", True):
        if not bool(row.get("forca_relativa_positiva_relevante", False)):
            reasons.append("bloqueio_por_forca_relativa_insuficiente_em_mercado_fraco")
            types.append("bloqueio_regime_mercado")
        quality = row.get("qualidade_fundamentalista", "")
        old_quality = row.get("classificacao ROE x ROIC", "")
        if quality not in {"otima", "boa", "aceitavel"} and old_quality not in {"misto", "bom", "otimo"}:
            reasons.append("bloqueio_por_qualidade_minima_em_mercado_fraco")
            types.append("bloqueio_regime_mercado")
    reasons = list(dict.fromkeys([reason for reason in reasons if reason]))
    types = list(dict.fromkeys([kind for kind in types if kind]))
    regime_reasons = [reason for reason in reasons if reason in {"beta_negativo_em_mercado_favoravel_watchlist_flexivel", "correlacao_negativa_em_mercado_favoravel_watchlist_flexivel", "beta_e_correlacao_negativos_em_mercado_favoravel", "beta_e_correlacao_muito_baixos_em_mercado_favoravel_watchlist_flexivel"}]
    return pd.Series({
        "bloqueado_otimizacao": bool(reasons),
        "motivo_bloqueio_otimizacao": "; ".join(reasons),
        "tipo_bloqueio_otimizacao": "; ".join(types),
        "bloqueio_aderencia_regime": bool(regime_reasons),
        "motivo_bloqueio_aderencia_regime": "; ".join(regime_reasons),
        "beta_minimo_exigido_regime": beta_min_flex if regime == "mercado favoravel" and watch_flex else np.nan,
        "correlacao_minima_exigida_regime": corr_min_flex if regime == "mercado favoravel" and watch_flex else np.nan,
        "liberado_para_otimizacao": not bool(reasons),
    })


def classify_eligibility(row: pd.Series, settings: dict) -> pd.Series:
    cv_limit = settings["risk"]["cv_limit"]
    cv = row.get("cv", np.nan)
    cv_hard = bool(settings["risk"].get("cv_as_hard_filter", True))
    strict_reasons = risk_reasons(row, settings, include_cv=True)
    hard_reasons = risk_reasons(row, settings, include_cv=cv_hard)
    prelim_ok = row.get("decisao preliminar") == "candidata"
    timing_type = row.get("tipo_timing", "")
    timing_cfg = _timing_settings(settings)

    if timing_type == "timing_esticado_sobrecompra" and not timing_cfg["allow_overbought_entries"]:
        return pd.Series({"elegibilidade_original": False, "elegibilidade_flexibilizada": False, "categoria_elegibilidade": "inelegivel", "motivo_flexibilizacao": "", "motivo_exclusao": "entrada esticada por sobrecompra; enviada para Watchlist"})

    reversal_double_negative = row.get("retorno_medio", 0) < 0 and row.get("retorno_acumulado_4m", 0) < 0
    if prelim_ok and timing_type == "timing_reversao_oportunidade" and not reversal_double_negative and not _fundamental_deterioration(row):
        return pd.Series({"elegibilidade_original": False, "elegibilidade_flexibilizada": True, "categoria_elegibilidade": "elegivel_moderado", "motivo_flexibilizacao": "candidata de reversao com fundamentos/estrutura/retorno suficientes; limite de peso aplicado", "motivo_exclusao": ""})

    if prelim_ok and not strict_reasons:
        return pd.Series({"elegibilidade_original": True, "elegibilidade_flexibilizada": False, "categoria_elegibilidade": "elegivel_forte", "motivo_flexibilizacao": "", "motivo_exclusao": ""})

    cv_only_blocks = [reason for reason in strict_reasons if reason != "CV acima do limite configurado"]
    if (
        prelim_ok
        and not cv_hard
        and not cv_only_blocks
        and not pd.isna(cv)
        and cv > cv_limit
        and settings["risk"].get("allow_relaxed_portfolio", False)
        and row.get("retorno_medio", 0) > 0
        and not _fundamental_deterioration(row)
    ):
        return pd.Series({"elegibilidade_original": False, "elegibilidade_flexibilizada": True, "categoria_elegibilidade": "elegivel_moderado", "motivo_flexibilizacao": f"CV {cv:.2f} acima de {cv_limit}; tratado como alerta/penalizacao porque cv_as_hard_filter=false", "motivo_exclusao": ""})

    reasons = []
    if not prelim_ok:
        reasons.append("nao aprovado na analise preliminar")
    reasons.extend(hard_reasons if hard_reasons else strict_reasons)
    return pd.Series({"elegibilidade_original": False, "elegibilidade_flexibilizada": False, "categoria_elegibilidade": "inelegivel", "motivo_flexibilizacao": "", "motivo_exclusao": "; ".join(dict.fromkeys(reasons))})


def build_alerts(frame: pd.DataFrame, portfolio_alerts: list[str]) -> pd.DataFrame:
    rows = [{"ticker": "carteira", "alerta": alert} for alert in portfolio_alerts if alert]
    for _, row in frame.iterrows():
        alerts = alert_join([
            row.get("alertas_tecnicos", ""), row.get("alertas_fundamentos", ""), row.get("alertas_metodologicos", ""), row.get("sinal_timing", ""), row.get("justificativa_timing", ""), row.get("motivo_status_para_risco", ""), row.get("motivo_flexibilizacao", ""), row.get("motivo_tipo_watchlist", ""), row.get("motivos_alerta_sinal_tardio", ""), row.get("motivos_alerta_realizacao_pos_rali", ""), row.get("motivo_bloqueio_forca_relativa", ""), row.get("penalizacoes_otimizacao", ""),
            "Setor em baixa" if row.get("tendencia_setorial") == "baixa" else "",
            "Beta acima de 1" if row.get("beta", 0) > 1 else "",
            "Correlacao alta com IBOV" if row.get("correlacao_ibov", 0) > 0.7 else "",
        ])
        if alerts:
            rows.append({"ticker": row["ticker"], "alerta": alerts})
    return pd.DataFrame(rows)




def add_zero_weight_candidates(risk_candidates_all: pd.DataFrame, portfolio: pd.DataFrame) -> pd.DataFrame:
    result = risk_candidates_all.copy()
    weight_map = dict(zip(portfolio.get("ticker", []), portfolio.get("peso_recomendado", []))) if portfolio is not None else {}
    result["peso_final"] = result.get("ticker", pd.Series(dtype=str)).map(weight_map).fillna(0.0)
    result["decisao de entrada na carteira"] = np.where(result["peso_final"] > 0, "selecionada", "peso zero")
    sort_cols = [col for col in ["peso_final", "nota_final"] if col in result]
    return result.sort_values(sort_cols, ascending=[False] * len(sort_cols)) if sort_cols else result

def _market_breadth_rows(preliminary: pd.DataFrame) -> tuple[list[dict], int, int, float]:
    if preliminary.empty:
        return [], 0, 0, 0.0
    total = len(preliminary)
    favorable = preliminary.get("tendencia_mensal", pd.Series("", index=preliminary.index)).isin(["alta_forte_mensal", "alta_aceitavel_ou_virada"]) | preliminary.get("tendencia", pd.Series("", index=preliminary.index)).isin(["Forte alta", "Aceitavel"])
    positive_month = preliminary.get("retorno_acumulado_1m", pd.Series(np.nan, index=preliminary.index)) > 0
    positive_ytd = preliminary.get("retorno_ytd", pd.Series(np.nan, index=preliminary.index)) > 0
    mm9_gt_mm21 = preliminary.get("mm9", pd.Series(np.nan, index=preliminary.index)) > preliminary.get("mm21", pd.Series(np.nan, index=preliminary.index))
    price_above_mm50 = preliminary.get("preco_atual", pd.Series(np.nan, index=preliminary.index)) > preliminary.get("mm50", pd.Series(np.nan, index=preliminary.index))
    rsi_50_70 = preliminary.get("rsi", pd.Series(np.nan, index=preliminary.index)).between(50, 70)
    blocked_negative = preliminary.get("motivos_bloqueio_preliminar", pd.Series("", index=preliminary.index)).fillna("").str.contains("tendencia tecnica negativa|YTD negativo", regex=True)
    rows = []
    for label, mask in [
        ("ativos com retorno positivo no mes", positive_month),
        ("ativos com retorno positivo no ano", positive_ytd),
        ("ativos com MM9 > MM21", mm9_gt_mm21),
        ("ativos com preco acima da MM50", price_above_mm50),
        ("ativos com RSI entre 50 e 70", rsi_50_70),
        ("ativos com tendencia favoravel", favorable),
        ("ativos bloqueados por tendencia tecnica negativa", blocked_negative),
    ]:
        qty = int(mask.fillna(False).sum())
        rows.append({"categoria": "Amplitude", "indicador": label, "quantidade": qty, "percentual": qty / total if total else 0.0, "detalhe": ""})
    return rows, total, int(favorable.fillna(False).sum()), float(favorable.fillna(False).sum() / total if total else 0.0)


def _market_classification(favorable_count: int, total: int) -> tuple[str, float]:
    pct = favorable_count / total if total else 0.0
    if pct > 0.40:
        return "mercado favoravel", pct
    if pct >= 0.20:
        return "mercado seletivo", pct
    return "mercado fraco/desfavoravel", pct


def build_watchlist(preliminary: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if preliminary.empty:
        return pd.DataFrame()
    rsi = preliminary.get("rsi", preliminary.get("RSI/IFR", pd.Series(np.nan, index=preliminary.index)))
    near_upper = preliminary.get("distancia_banda_superior_pct", pd.Series(np.nan, index=preliminary.index)) <= _timing_settings(settings)["near_band_threshold"]
    blocked = preliminary.get("bloqueada_entrada_esticada", pd.Series(False, index=preliminary.index)).fillna(False)
    watch_decision = preliminary.get("decisao_preliminar_ajustada", pd.Series("", index=preliminary.index)).eq("watchlist_qualificada")
    overbought = (rsi > 70) & near_upper
    inadequate_timing = preliminary.get("tipo_timing", pd.Series("", index=preliminary.index)).isin(["timing_neutro", "timing_reversao_nao_aprovada", "timing_esticado_sobrecompra"])
    mask = watch_decision | blocked | overbought | inadequate_timing
    watch = preliminary[mask].copy()
    if watch.empty:
        return watch
    def reason(row: pd.Series) -> str:
        values = [row.get("motivo_watchlist_qualificada", ""), row.get("justificativa_timing", "")]
        if bool(row.get("bloqueada_entrada_esticada", False)):
            values.append("entrada esticada por sobrecompra/Bollinger")
        if row.get("tipo_timing") == "timing_neutro":
            values.append("timing neutro; exige confirmacao")
        return alert_join(values) or "watchlist por timing ou confirmacao insuficiente"
    watch["motivo_watchlist"] = watch.apply(reason, axis=1)
    return watch



def _ticker_list(frame: pd.DataFrame, mask: pd.Series, limit: int = 30) -> str:
    if frame is None or frame.empty or "ticker" not in frame:
        return ""
    subset = frame[mask.fillna(False)] if len(mask) else frame.head(0)
    return ", ".join(subset.get("ticker", pd.Series(dtype=str)).head(limit).astype(str).tolist())


def build_timing_watchlist_diagnosis(risk_candidates: pd.DataFrame) -> pd.DataFrame:
    if risk_candidates is None or risk_candidates.empty:
        return pd.DataFrame(columns=["metrica", "valor", "tickers"])
    frame = risk_candidates.copy()
    tipo = frame.get("tipo_watchlist", pd.Series("nao_watchlist", index=frame.index)).fillna("nao_watchlist")
    liberado = frame.get("liberado_para_otimizacao", pd.Series(False, index=frame.index)).fillna(False)
    flag = frame.get("flag_watchlist", pd.Series(False, index=frame.index)).fillna(False)
    flex = tipo.eq("watchlist_flexivel")
    bloqueante = tipo.eq("watchlist_bloqueante")
    monitor = tipo.eq("watchlist_monitoramento")
    late = frame.get("alerta_sinal_tardio", pd.Series(False, index=frame.index)).fillna(False)
    tardio = frame.get("qualidade_do_timing", pd.Series("", index=frame.index)).eq("timing_tardio")
    converted = flag & flex & liberado
    timing_blocked = bloqueante & frame.get("tipo_bloqueio_otimizacao", pd.Series("", index=frame.index)).astype(str).str.contains("timing", case=False, na=False)
    rows = [
        {"metrica": "watchlist_bloqueante", "valor": int(bloqueante.sum()), "tickers": _ticker_list(frame, bloqueante)},
        {"metrica": "watchlist_flexivel", "valor": int(flex.sum()), "tickers": _ticker_list(frame, flex)},
        {"metrica": "watchlist_monitoramento", "valor": int(monitor.sum()), "tickers": _ticker_list(frame, monitor)},
        {"metrica": "liberados_para_otimizacao_depois", "valor": int(liberado.sum()), "tickers": _ticker_list(frame, liberado)},
        {"metrica": "convertidos_watchlist_timing_para_flexivel", "valor": int(converted.sum()), "tickers": _ticker_list(frame, converted)},
        {"metrica": "mantidos_bloqueados_por_timing", "valor": int(timing_blocked.sum()), "tickers": _ticker_list(frame, timing_blocked)},
        {"metrica": "alerta_sinal_tardio", "valor": int(late.sum()), "tickers": _ticker_list(frame, late)},
        {"metrica": "qualidade_timing_tardio", "valor": int(tardio.sum()), "tickers": _ticker_list(frame, tardio)},
    ]
    return pd.DataFrame(rows)
def build_timing_summary(preliminary: pd.DataFrame, watchlist: pd.DataFrame, portfolio: pd.DataFrame) -> pd.DataFrame:
    rows = []
    groups = [
        ("tendencia_bom_ponto", preliminary.get("tipo_timing", pd.Series(dtype=str)).eq("timing_favoravel_tendencia"), "Acoes em tendencia com ponto de entrada favoravel"),
        ("reversao_oportunidade", preliminary.get("tipo_timing", pd.Series(dtype=str)).eq("timing_reversao_oportunidade"), "Acoes com oportunidade de reversao"),
        ("esticadas_sobrecompra", preliminary.get("tipo_timing", pd.Series(dtype=str)).eq("timing_esticado_sobrecompra"), "Acoes boas, mas com entrada esticada"),
        ("watchlist", preliminary.get("ticker", pd.Series(dtype=str)).isin(watchlist.get("ticker", pd.Series(dtype=str))) if not watchlist.empty else pd.Series(False, index=preliminary.index), "Acoes em watchlist"),
    ]
    for group, mask, obs in groups:
        subset = preliminary[mask.fillna(False)] if len(mask) else preliminary.head(0)
        rows.append({"grupo": group, "quantidade": len(subset), "tickers": ", ".join(subset.get("ticker", pd.Series(dtype=str)).head(12).astype(str)), "observacao": obs})
    if not portfolio.empty:
        rows.append({"grupo": "carteira_final", "quantidade": len(portfolio), "tickers": ", ".join(portfolio.get("ticker", pd.Series(dtype=str)).astype(str)), "observacao": "Acoes selecionadas pela otimizacao"})
    return pd.DataFrame(rows)


def build_market_diagnosis(preliminary: pd.DataFrame, sector_indexes: pd.DataFrame, index_prices: pd.DataFrame, metrics: dict, comparison: pd.DataFrame, settings: dict) -> pd.DataFrame:
    rows, total, favorable, pct = _market_breadth_rows(preliminary)
    market_class, _ = _market_classification(favorable, total)
    output = []
    ibov_ticker = settings["data"]["indexes"].get("IBOV", "^BVSP")
    if ibov_ticker in index_prices:
        ibov_snapshot = calculate_technical_snapshot(index_prices[ibov_ticker].dropna(), settings)
        for indicator, key in [
            ("retorno no mes", "retorno_1m"), ("retorno YTD", "retorno_ytd"), ("MM9 semanal", "mm9"),
            ("MM21 semanal", "mm21"), ("MM50 semanal", "mm50"), ("MM100 semanal", "mm100"),
            ("tendencia do IBOV", "tendencia"), ("RSI do IBOV", "rsi"), ("Bollinger do IBOV", "bollinger_status"),
        ]:
            output.append({"categoria": "IBOV", "indicador": indicator, "valor": ibov_snapshot.get(key, np.nan), "detalhe": ""})
    output.extend(rows)
    if not sector_indexes.empty:
        for _, row in sector_indexes.iterrows():
            output.append({"categoria": "Setorial", "indicador": row.get("setor", row.get("indice", "setor")), "valor": row.get("tendencia_setorial", row.get("tendencia", "indisponivel")), "quantidade": "", "percentual": "", "detalhe": row.get("fonte_setorial", "")})
    output.append({"categoria": "Classificacao", "indicador": "classificacao geral do mercado", "valor": market_class, "quantidade": favorable, "percentual": pct, "detalhe": "favoravel >40%, seletivo 20%-40%, fraco <20%"})
    cause = "nao aplicavel"
    if not metrics.get("carteira_valida", False):
        if market_class == "mercado fraco/desfavoravel":
            cause = "mercado desfavoravel ou seletivo com poucos ativos liberados"
        elif metrics.get("ativos_liberados_otimizacao_depois_correcao", 0) < min(settings.get("portfolio", {}).get("candidate_counts", [5])):
            cause = "ativos liberados insuficientes para carteira valida"
        else:
            cause = metrics.get("restricoes_violadas", "restricoes da otimizacao") or "restricoes da otimizacao"
    output.append({"categoria": "Explicacao carteira invalida", "indicador": "causa principal", "valor": cause, "detalhe": metrics.get("restricoes_violadas", "")})
    return pd.DataFrame(output)

def select_pre_risk_candidates(prelim: pd.DataFrame, settings: dict) -> pd.DataFrame:
    limit = int(settings["strategy"].get("pre_risk_candidates", 20))
    allowed_status = ["aprovada_para_risco", "moderada_para_risco"]
    ranked = prelim.sort_values(["retorno_ytd", "nota preliminar"], ascending=[False, False]).copy()
    selected = ranked[ranked["status_para_risco"].isin(allowed_status)].head(limit).copy()
    selected["motivo_flexibilizacao_preliminar"] = np.where(
        selected["status_para_risco"].eq("moderada_para_risco"),
        selected["motivo_status_para_risco"].fillna("candidata moderada para risco"),
        "",
    )
    selected["levada_para_risco"] = True
    return selected


def _existing_columns(frame: pd.DataFrame, columns: list[str]) -> list[str]:
    seen = set()
    result = []
    for col in columns:
        if col in frame.columns and col not in seen:
            result.append(col)
            seen.add(col)
    return result


def _date_from_setting(value: object) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return pd.Timestamp(value).normalize()


def _reference_year_month(settings: dict) -> tuple[int, int]:
    strategy = settings.get("strategy", {})
    today = pd.Timestamp(datetime.today()).normalize()
    year = int(strategy.get("ano_referencia") or today.year)
    month = int(strategy.get("mes_referencia") or today.month)
    return year, month


def _first_business_day(year: int, month: int) -> pd.Timestamp:
    return pd.bdate_range(f"{year:04d}-{month:02d}-01", periods=1)[0].normalize()


def _last_observation_date(frame: pd.DataFrame | pd.Series, limit: pd.Timestamp | None = None) -> pd.Timestamp | None:
    if frame is None or frame.empty:
        return None
    index = pd.DatetimeIndex(frame.dropna(how="all").index if isinstance(frame, pd.DataFrame) else frame.dropna().index)
    if limit is not None:
        index = index[index <= limit]
    if len(index) == 0:
        return None
    return pd.Timestamp(index.max()).normalize()


def _previous_observation_date(frame: pd.DataFrame | pd.Series, before: pd.Timestamp | None = None) -> pd.Timestamp | None:
    if frame is None or frame.empty or before is None:
        return None
    index = pd.DatetimeIndex(frame.dropna(how="all").index if isinstance(frame, pd.DataFrame) else frame.dropna().index)
    index = index[index < before]
    if len(index) == 0:
        return None
    return pd.Timestamp(index.max()).normalize()

def _slice_until(frame: pd.DataFrame, end_date: pd.Timestamp | None) -> pd.DataFrame:
    if frame is None or frame.empty or end_date is None:
        return pd.DataFrame(index=frame.index if isinstance(frame, pd.DataFrame) else None)
    return frame.loc[frame.index <= end_date].copy()


def _resolve_temporal_context(settings: dict, prices: pd.DataFrame, index_prices: pd.DataFrame) -> dict:
    strategy = settings.get("strategy", {})
    year, month = _reference_year_month(settings)
    configured_formation = _date_from_setting(strategy.get("data_formacao_carteira"))
    trading_days_month, calendar_source, calendar_status = resolve_b3_trading_days(settings, index_prices, year, month)
    if configured_formation is None and bool(strategy.get("usar_primeiro_dia_util_mes", True)):
        configured_formation, calendar_source, calendar_status = first_b3_trading_day(settings, index_prices, year, month)
    if configured_formation is None:
        configured_formation = pd.Timestamp(f"{year:04d}-{month:02d}-01").normalize()

    requested_evaluation = _date_from_setting(strategy.get("data_avaliacao_carteira")) or pd.Timestamp(datetime.today()).normalize()
    selection_end = _last_observation_date(prices, configured_formation) or configured_formation
    evaluation_candidates = [_last_observation_date(prices, requested_evaluation), _last_observation_date(index_prices, requested_evaluation)]
    valid_evaluation = [item for item in evaluation_candidates if item is not None]
    evaluation_end = max(valid_evaluation) if valid_evaluation else requested_evaluation
    if evaluation_end < selection_end:
        evaluation_end = selection_end
    benchmark = settings.get("data", {}).get("indexes", {}).get("IBOV", "^BVSP")
    benchmark_frame = index_prices[[benchmark]] if index_prices is not None and benchmark in index_prices else index_prices
    performance_start = (
        _previous_observation_date(benchmark_frame, configured_formation)
        or _previous_observation_date(prices, configured_formation)
        or selection_end
    )

    return {
        "mes_referencia": month,
        "ano_referencia": year,
        "year_month": f"{year:04d}_{month:02d}",
        "data_formacao_carteira": configured_formation,
        "data_avaliacao_solicitada": requested_evaluation,
        "data_avaliacao_carteira": evaluation_end,
        "data_limite_dados_selecao": selection_end,
        "data_inicio_performance": performance_start,
        "periodo_dados_selecao": f"ate {selection_end.date()}",
        "periodo_avaliacao_performance": f"{performance_start.date()} a {evaluation_end.date()}",
        "calendario_mercado": "B3",
        "calendario_fonte": calendar_source,
        "calendario_status": calendar_status,
        "pregoes_mes_referencia": int(len(trading_days_month)),
        "primeiro_pregao_mes": pd.Timestamp(trading_days_month[0]).date().isoformat() if len(trading_days_month) else "",
        "ultimo_pregao_mes": pd.Timestamp(trading_days_month[-1]).date().isoformat() if len(trading_days_month) else "",
    }


def _price_at_or_before(prices: pd.DataFrame, ticker: str, date: pd.Timestamp) -> float:
    if prices.empty or ticker not in prices:
        return np.nan
    series = prices[ticker].dropna().sort_index()
    series = series[series.index <= date]
    return float(series.iloc[-1]) if len(series) else np.nan


def _period_return(prices: pd.DataFrame, ticker: str, start_date: pd.Timestamp, end_date: pd.Timestamp) -> float:
    start_price = _price_at_or_before(prices, ticker, start_date)
    end_price = _price_at_or_before(prices, ticker, end_date)
    if pd.isna(start_price) or pd.isna(end_price) or start_price == 0:
        return np.nan
    return float(end_price / start_price - 1)


def build_realized_performance(portfolio: pd.DataFrame, prices: pd.DataFrame, index_prices: pd.DataFrame, settings: dict, temporal_context: dict) -> pd.DataFrame:
    start_date = temporal_context.get("data_inicio_performance", temporal_context["data_limite_dados_selecao"])
    end_date = temporal_context["data_avaliacao_carteira"]
    ibov_ticker = settings.get("data", {}).get("indexes", {}).get("IBOV", "^BVSP")
    ibov_return = _period_return(index_prices, ibov_ticker, start_date, end_date) if ibov_ticker in index_prices else np.nan
    rows = []
    if portfolio is not None and not portfolio.empty:
        for _, row in portfolio.iterrows():
            ticker = row.get("ticker")
            weight = float(row.get("peso_recomendado", row.get("peso_final", 0)) or 0)
            start_price = _price_at_or_before(prices, ticker, start_date)
            formation_price = _price_at_or_before(prices, ticker, temporal_context["data_formacao_carteira"])
            end_price = _price_at_or_before(prices, ticker, end_date)
            realized = np.nan if pd.isna(start_price) or pd.isna(end_price) or start_price == 0 else float(end_price / start_price - 1)
            rows.append({
                "tipo_linha": "ativo",
                "ticker": ticker,
                "peso_recomendado": weight,
                "preco_inicio_performance": start_price,
                "preco_formacao": formation_price,
                "preco_avaliacao": end_price,
                "retorno_realizado_periodo": realized,
                "contribuicao_para_retorno_carteira": weight * realized if pd.notna(realized) else np.nan,
                "retorno_ibov_periodo": ibov_return,
                "alfa_vs_ibov": realized - ibov_return if pd.notna(realized) and pd.notna(ibov_return) else np.nan,
                "data_formacao_carteira": temporal_context["data_formacao_carteira"].date().isoformat(),
                "data_inicio_performance": start_date.date().isoformat(),
                "data_avaliacao_carteira": end_date.date().isoformat(),
            })
    detail = pd.DataFrame(rows)
    portfolio_return = float(detail["contribuicao_para_retorno_carteira"].sum()) if not detail.empty else np.nan
    positive = int((detail.get("retorno_realizado_periodo", pd.Series(dtype=float)) > 0).sum()) if not detail.empty else 0
    negative = int((detail.get("retorno_realizado_periodo", pd.Series(dtype=float)) < 0).sum()) if not detail.empty else 0
    best = detail.sort_values("retorno_realizado_periodo", ascending=False).head(1)["ticker"].iloc[0] if not detail.empty and detail["retorno_realizado_periodo"].notna().any() else ""
    worst = detail.sort_values("retorno_realizado_periodo", ascending=True).head(1)["ticker"].iloc[0] if not detail.empty and detail["retorno_realizado_periodo"].notna().any() else ""
    summary = pd.DataFrame([
        {"tipo_linha": "resumo", "ticker": "retorno_realizado_carteira", "retorno_realizado_periodo": portfolio_return, "retorno_ibov_periodo": ibov_return, "alfa_vs_ibov": portfolio_return - ibov_return if pd.notna(portfolio_return) and pd.notna(ibov_return) else np.nan, "data_formacao_carteira": temporal_context["data_formacao_carteira"].date().isoformat(), "data_inicio_performance": start_date.date().isoformat(), "data_avaliacao_carteira": end_date.date().isoformat()},
        {"tipo_linha": "resumo", "ticker": "melhor_ativo_da_carteira", "preco_formacao": best, "data_formacao_carteira": temporal_context["data_formacao_carteira"].date().isoformat(), "data_inicio_performance": start_date.date().isoformat(), "data_avaliacao_carteira": end_date.date().isoformat()},
        {"tipo_linha": "resumo", "ticker": "pior_ativo_da_carteira", "preco_formacao": worst, "data_formacao_carteira": temporal_context["data_formacao_carteira"].date().isoformat(), "data_inicio_performance": start_date.date().isoformat(), "data_avaliacao_carteira": end_date.date().isoformat()},
        {"tipo_linha": "resumo", "ticker": "quantidade_de_ativos_positivos", "preco_formacao": positive, "data_formacao_carteira": temporal_context["data_formacao_carteira"].date().isoformat(), "data_inicio_performance": start_date.date().isoformat(), "data_avaliacao_carteira": end_date.date().isoformat()},
        {"tipo_linha": "resumo", "ticker": "quantidade_de_ativos_negativos", "preco_formacao": negative, "data_formacao_carteira": temporal_context["data_formacao_carteira"].date().isoformat(), "data_inicio_performance": start_date.date().isoformat(), "data_avaliacao_carteira": end_date.date().isoformat()},
    ])
    return pd.concat([detail, summary], ignore_index=True, sort=False)
def _risk_price_type(settings: dict) -> str:
    return "adjusted_close" if bool(settings.get("data", {}).get("use_adjusted_prices", True)) else "close"


def build_risk_series_history(candidate_prices: pd.DataFrame, risk_returns: pd.DataFrame, risk_candidates: pd.DataFrame, settings: dict, risk_window_info: dict, temporal_context: dict) -> pd.DataFrame:
    columns = [
        "data", "ticker", "preco_usado", "log_retorno", "fonte", "tipo_preco",
        "janela_risco_inicio", "janela_risco_fim", "data_formacao_carteira", "observacao",
    ]
    if risk_candidates is None or risk_candidates.empty:
        return pd.DataFrame(columns=columns)
    tickers = risk_candidates.get("ticker", pd.Series(dtype=str)).dropna().astype(str).tolist()
    source = settings.get("data", {}).get("price_source_primary", "yfinance")
    price_type = _risk_price_type(settings)
    start = pd.Timestamp(risk_window_info["janela_risco_inicio"])
    end = pd.Timestamp(risk_window_info["janela_risco_fim"])
    rows = []
    for ticker in tickers:
        if candidate_prices is None or candidate_prices.empty or ticker not in candidate_prices:
            rows.append({
                "data": "", "ticker": ticker, "preco_usado": np.nan, "log_retorno": np.nan,
                "fonte": source, "tipo_preco": price_type,
                "janela_risco_inicio": risk_window_info["janela_risco_inicio"],
                "janela_risco_fim": risk_window_info["janela_risco_fim"],
                "data_formacao_carteira": temporal_context["data_formacao_carteira"].date().isoformat(),
                "observacao": "preco_ausente",
            })
            continue
        prices = candidate_prices[ticker].dropna().sort_index()
        if prices.empty:
            rows.append({
                "data": "", "ticker": ticker, "preco_usado": np.nan, "log_retorno": np.nan,
                "fonte": source, "tipo_preco": price_type,
                "janela_risco_inicio": risk_window_info["janela_risco_inicio"],
                "janela_risco_fim": risk_window_info["janela_risco_fim"],
                "data_formacao_carteira": temporal_context["data_formacao_carteira"].date().isoformat(),
                "observacao": "serie_insuficiente",
            })
            continue
        log_ret = np.log(prices / prices.shift(1))
        if risk_returns is not None and not risk_returns.empty and ticker in risk_returns:
            log_ret.update(risk_returns[ticker])
        for date, price in prices.items():
            obs = []
            date_ts = pd.Timestamp(date).normalize()
            if date_ts < start:
                obs.append("preco_base_para_primeiro_log_retorno")
            if date_ts > end:
                obs.append("fora_janela_risco")
            if len(prices) < 2:
                obs.append("serie_insuficiente")
            rows.append({
                "data": date_ts.date().isoformat(),
                "ticker": ticker,
                "preco_usado": float(price),
                "log_retorno": float(log_ret.loc[date]) if pd.notna(log_ret.loc[date]) else np.nan,
                "fonte": source,
                "tipo_preco": price_type,
                "janela_risco_inicio": risk_window_info["janela_risco_inicio"],
                "janela_risco_fim": risk_window_info["janela_risco_fim"],
                "data_formacao_carteira": temporal_context["data_formacao_carteira"].date().isoformat(),
                "observacao": "; ".join(obs),
            })
    return pd.DataFrame(rows, columns=columns)


def append_benchmark_to_risk_series(risk_series_history: pd.DataFrame, benchmark_prices: pd.Series, benchmark_returns: pd.Series, benchmark_ticker: str, settings: dict, risk_window_info: dict, temporal_context: dict) -> pd.DataFrame:
    columns = [
        "data", "ticker", "preco_usado", "log_retorno", "fonte", "tipo_preco",
        "janela_risco_inicio", "janela_risco_fim", "data_formacao_carteira", "observacao",
    ]
    source = settings.get("data", {}).get("price_source_primary", "yfinance")
    price_type = _risk_price_type(settings)
    start = pd.Timestamp(risk_window_info["janela_risco_inicio"])
    end = pd.Timestamp(risk_window_info["janela_risco_fim"])
    rows = []
    prices = benchmark_prices.dropna().sort_index() if benchmark_prices is not None and not benchmark_prices.empty else pd.Series(dtype=float)
    if prices.empty:
        rows.append({
            "data": "", "ticker": benchmark_ticker, "preco_usado": np.nan, "log_retorno": np.nan,
            "fonte": source, "tipo_preco": price_type,
            "janela_risco_inicio": risk_window_info["janela_risco_inicio"],
            "janela_risco_fim": risk_window_info["janela_risco_fim"],
            "data_formacao_carteira": temporal_context["data_formacao_carteira"].date().isoformat(),
            "observacao": "benchmark_preco_ausente",
        })
    else:
        log_ret = np.log(prices / prices.shift(1))
        if benchmark_returns is not None and not benchmark_returns.empty:
            log_ret.update(benchmark_returns)
        for date, price in prices.items():
            obs = ["benchmark_ibov"]
            date_ts = pd.Timestamp(date).normalize()
            if date_ts < start:
                obs.append("preco_base_para_primeiro_log_retorno")
            if date_ts > end:
                obs.append("fora_janela_risco")
            rows.append({
                "data": date_ts.date().isoformat(),
                "ticker": benchmark_ticker,
                "preco_usado": float(price),
                "log_retorno": float(log_ret.loc[date]) if pd.notna(log_ret.loc[date]) else np.nan,
                "fonte": source,
                "tipo_preco": price_type,
                "janela_risco_inicio": risk_window_info["janela_risco_inicio"],
                "janela_risco_fim": risk_window_info["janela_risco_fim"],
                "data_formacao_carteira": temporal_context["data_formacao_carteira"].date().isoformat(),
                "observacao": "; ".join(obs),
            })
    benchmark_frame = pd.DataFrame(rows, columns=columns)
    if risk_series_history is None or risk_series_history.empty:
        return benchmark_frame
    return pd.concat([risk_series_history, benchmark_frame], ignore_index=True)


def build_beta_correlation_audit(risk_series_history: pd.DataFrame, risk_candidates: pd.DataFrame, benchmark_ticker: str, risk_window_info: dict, tolerance_beta: float = 0.000001, tolerance_correlation: float = 0.000001) -> pd.DataFrame:
    columns = [
        "ticker", "benchmark", "janela_risco_inicio", "janela_risco_fim", "quantidade_observacoes_alinhadas",
        "retorno_medio_ativo", "retorno_medio_benchmark", "variancia_benchmark", "covariancia_ativo_benchmark",
        "beta_calculado_auditoria", "beta_usado_no_robo", "diferenca_beta", "correlacao_calculada_auditoria",
        "correlacao_ibov_usada_no_robo", "diferenca_correlacao", "beta_bate_com_robo", "correlacao_bate_com_robo", "observacao",
    ]
    if risk_series_history is None or risk_series_history.empty:
        return pd.DataFrame(columns=columns)
    history = risk_series_history.copy()
    history["data"] = pd.to_datetime(history["data"], errors="coerce")
    history["log_retorno"] = pd.to_numeric(history["log_retorno"], errors="coerce")
    benchmark_returns = history[history["ticker"].astype(str).eq(benchmark_ticker)].set_index("data")["log_retorno"].dropna().sort_index()
    robot = risk_candidates.set_index("ticker") if risk_candidates is not None and not risk_candidates.empty and "ticker" in risk_candidates else pd.DataFrame()
    tickers = risk_candidates.get("ticker", pd.Series(dtype=str)).dropna().astype(str).tolist() if risk_candidates is not None and not risk_candidates.empty else []
    rows = []
    audit_tickers = tickers + ([benchmark_ticker] if benchmark_ticker not in tickers else [])
    for ticker in audit_tickers:
        asset_returns = history[history["ticker"].astype(str).eq(ticker)].set_index("data")["log_retorno"].dropna().sort_index()
        data = pd.concat([asset_returns.rename("ativo"), benchmark_returns.rename("benchmark")], axis=1).dropna()
        observations = int(len(data))
        mean_asset = float(data["ativo"].mean()) if observations else np.nan
        mean_benchmark = float(data["benchmark"].mean()) if observations else np.nan
        var_benchmark = float(np.var(data["benchmark"], ddof=0)) if observations else np.nan
        cov_ab = float(np.cov(data["ativo"], data["benchmark"], ddof=0)[0, 1]) if observations >= 2 else np.nan
        beta_calc = np.nan if pd.isna(var_benchmark) or var_benchmark == 0 or pd.isna(cov_ab) else cov_ab / var_benchmark
        corr_calc = float(data.corr().iloc[0, 1]) if observations >= 2 else np.nan
        beta_robot = robot.at[ticker, "beta"] if ticker in robot.index and "beta" in robot else np.nan
        corr_robot = robot.at[ticker, "correlacao_ibov"] if ticker in robot.index and "correlacao_ibov" in robot else np.nan
        if ticker == benchmark_ticker:
            beta_robot = 1.0
            corr_robot = 1.0
        diff_beta = beta_calc - beta_robot if pd.notna(beta_calc) and pd.notna(beta_robot) else np.nan
        diff_corr = corr_calc - corr_robot if pd.notna(corr_calc) and pd.notna(corr_robot) else np.nan
        beta_match = "sim" if pd.notna(diff_beta) and abs(float(diff_beta)) < tolerance_beta else "nao"
        corr_match = "sim" if pd.notna(diff_corr) and abs(float(diff_corr)) < tolerance_correlation else "nao"
        notes = ["covariancia_variancia_populacional_ddof_0", "datas_alinhadas_por_log_retorno"]
        if observations < 2:
            notes.append("observacoes_alinhadas_insuficientes")
        if ticker == benchmark_ticker:
            notes.append("linha_benchmark_ibov")
            if beta_match != "sim":
                notes.append("alerta_beta_ibov_diferente_de_1")
            if corr_match != "sim":
                notes.append("alerta_correlacao_ibov_diferente_de_1")
        rows.append({
            "ticker": ticker,
            "benchmark": benchmark_ticker,
            "janela_risco_inicio": risk_window_info["janela_risco_inicio"],
            "janela_risco_fim": risk_window_info["janela_risco_fim"],
            "quantidade_observacoes_alinhadas": observations,
            "retorno_medio_ativo": mean_asset,
            "retorno_medio_benchmark": mean_benchmark,
            "variancia_benchmark": var_benchmark,
            "covariancia_ativo_benchmark": cov_ab,
            "beta_calculado_auditoria": beta_calc,
            "beta_usado_no_robo": beta_robot,
            "diferenca_beta": diff_beta,
            "correlacao_calculada_auditoria": corr_calc,
            "correlacao_ibov_usada_no_robo": corr_robot,
            "diferenca_correlacao": diff_corr,
            "beta_bate_com_robo": beta_match,
            "correlacao_bate_com_robo": corr_match,
            "observacao": "; ".join(notes),
        })
    return pd.DataFrame(rows, columns=columns)
def build_risk_calculation_audit(risk_series_history: pd.DataFrame, risk_candidates: pd.DataFrame, tolerance: float = 0.000001) -> pd.DataFrame:
    columns = [
        "ticker", "quantidade_precos", "quantidade_log_retornos", "primeiro_preco", "ultimo_preco",
        "retorno_acumulado_periodo", "retorno_medio_log", "desvio_padrao_log_populacional",
        "variancia_log_populacional", "cv_calculado", "retorno_medio_usado_no_robo",
        "desvio_padrao_usado_no_robo", "cv_usado_no_robo", "diferenca_retorno_medio",
        "diferenca_desvio_padrao", "diferenca_cv", "calculo_bate_com_robo", "observacao",
    ]
    if risk_candidates is None or risk_candidates.empty:
        return pd.DataFrame(columns=columns)
    robot = risk_candidates.set_index("ticker") if "ticker" in risk_candidates else pd.DataFrame()
    rows = []
    for ticker in risk_candidates.get("ticker", pd.Series(dtype=str)).dropna().astype(str).tolist():
        subset = risk_series_history[risk_series_history.get("ticker", pd.Series(dtype=str)).eq(ticker)].copy() if risk_series_history is not None and not risk_series_history.empty else pd.DataFrame()
        prices = pd.to_numeric(subset.get("preco_usado", pd.Series(dtype=float)), errors="coerce").dropna()
        returns = pd.to_numeric(subset.get("log_retorno", pd.Series(dtype=float)), errors="coerce").dropna()
        mean = float(returns.mean()) if len(returns) else np.nan
        std = float(returns.std(ddof=0)) if len(returns) else np.nan
        var = float(returns.var(ddof=0)) if len(returns) else np.nan
        cv = std / mean if pd.notna(mean) and mean > 0 else np.nan
        first_price = float(prices.iloc[0]) if len(prices) else np.nan
        last_price = float(prices.iloc[-1]) if len(prices) else np.nan
        accumulated = last_price / first_price - 1 if pd.notna(first_price) and first_price != 0 and pd.notna(last_price) else np.nan
        robot_mean = robot.at[ticker, "retorno_medio"] if ticker in robot.index and "retorno_medio" in robot else np.nan
        robot_std = robot.at[ticker, "desvio_padrao"] if ticker in robot.index and "desvio_padrao" in robot else np.nan
        robot_cv = robot.at[ticker, "cv"] if ticker in robot.index and "cv" in robot else np.nan
        diff_mean = mean - robot_mean if pd.notna(mean) and pd.notna(robot_mean) else np.nan
        diff_std = std - robot_std if pd.notna(std) and pd.notna(robot_std) else np.nan
        diff_cv = cv - robot_cv if pd.notna(cv) and pd.notna(robot_cv) else np.nan
        checks = []
        for calc, rob in [(mean, robot_mean), (std, robot_std), (cv, robot_cv)]:
            if pd.isna(calc) and pd.isna(rob):
                checks.append(True)
            elif pd.notna(calc) and pd.notna(rob):
                checks.append(abs(float(calc) - float(rob)) < tolerance)
            else:
                checks.append(False)
        observations = ["desvio_padrao_populacional_ddof_0"]
        if pd.notna(mean) and mean <= 0:
            observations.append("retorno_medio_negativo_cv_nao_interpretavel")
        if subset.empty:
            observations.append("serie_insuficiente")
        if subset.get("observacao", pd.Series(dtype=str)).astype(str).str.contains("preco_base_para_primeiro_log_retorno", na=False).any():
            observations.append("inclui_preco_base_anterior_ao_primeiro_log_retorno")
        rows.append({
            "ticker": ticker,
            "quantidade_precos": int(len(prices)),
            "quantidade_log_retornos": int(len(returns)),
            "primeiro_preco": first_price,
            "ultimo_preco": last_price,
            "retorno_acumulado_periodo": accumulated,
            "retorno_medio_log": mean,
            "desvio_padrao_log_populacional": std,
            "variancia_log_populacional": var,
            "cv_calculado": cv,
            "retorno_medio_usado_no_robo": robot_mean,
            "desvio_padrao_usado_no_robo": robot_std,
            "cv_usado_no_robo": robot_cv,
            "diferenca_retorno_medio": diff_mean,
            "diferenca_desvio_padrao": diff_std,
            "diferenca_cv": diff_cv,
            "calculo_bate_com_robo": "sim" if all(checks) else "nao",
            "observacao": "; ".join(observations),
        })
    return pd.DataFrame(rows, columns=columns)
def _diagnostic_result(row: pd.Series) -> str:
    realized = row.get("retorno_realizado_periodo", np.nan)
    ibov = row.get("retorno_ibov_periodo", np.nan)
    if pd.isna(realized) or pd.isna(ibov):
        return "neutro"
    if realized > ibov + 1e-9:
        return "superou_ibov"
    if realized < ibov - 1e-9:
        return "ficou_abaixo_ibov"
    return "neutro"


def _diagnostic_contribution(row: pd.Series) -> str:
    contribution = row.get("contribuicao_para_retorno_carteira", np.nan)
    if pd.isna(contribution) or abs(float(contribution)) <= 1e-9:
        return "impacto_neutro"
    return "contribuiu_positivamente" if contribution > 0 else "contribuiu_negativamente"


def _diagnostic_reading(row: pd.Series, sector_underperformance: set[str]) -> str:
    result = row.get("resultado_individual", "")
    realized = row.get("retorno_realizado_periodo", np.nan)
    ibov = row.get("retorno_ibov_periodo", np.nan)
    beta = row.get("beta", np.nan)
    sector = row.get("setor", "")
    strong_signal = row.get("status_para_risco") == "aprovada_para_risco" or row.get("categoria_elegibilidade") == "elegivel_forte"
    rel_positive = bool(row.get("forca_relativa_positiva_relevante", False)) or row.get("classificacao_forca_relativa") in {"forte_contra_ibov", "moderada_contra_ibov"}
    if result == "superou_ibov":
        return "acerto_metodologico"
    if pd.notna(beta) and beta > 1 and pd.notna(realized) and realized < -0.02:
        return "risco_realizado_acima_do_esperado"
    if rel_positive and pd.notna(realized) and realized < 0:
        return "sinal_tardio"
    if sector in sector_underperformance and result == "ficou_abaixo_ibov":
        return "queda_setorial_pos_formacao"
    if strong_signal and pd.notna(realized) and pd.notna(ibov) and realized < ibov - 0.03:
        return "falso_positivo"
    if pd.notna(beta) and beta < 0.8 and result == "ficou_abaixo_ibov":
        return "ativo_defensivo_mas_sem_forca"
    return "necessita_backtest_para_confirmar"


def _diagnostic_general(performance: pd.DataFrame, temporal_context: dict) -> str:
    summary = performance[performance.get("ticker", pd.Series(dtype=str)).eq("retorno_realizado_carteira")]
    if summary.empty:
        return "resultado_parcial_nao_conclusivo"
    portfolio_return = summary["retorno_realizado_periodo"].iloc[0]
    ibov_return = summary["retorno_ibov_periodo"].iloc[0]
    evaluation = temporal_context["data_avaliacao_carteira"]
    month_end = pd.Timestamp(evaluation.year, evaluation.month, 1) + pd.offsets.MonthEnd(0)
    if evaluation.normalize() < month_end.normalize():
        return "resultado_parcial_nao_conclusivo"
    if pd.isna(portfolio_return) or pd.isna(ibov_return):
        return "resultado_parcial_nao_conclusivo"
    if portfolio_return > ibov_return + 1e-9:
        return "carteira_superou_ibov"
    if portfolio_return < ibov_return - 1e-9:
        return "carteira_abaixo_ibov"
    return "carteira_neutra"


def build_post_selection_diagnosis(portfolio: pd.DataFrame, performance: pd.DataFrame, alerts: pd.DataFrame, metrics: dict, temporal_context: dict) -> pd.DataFrame:
    detail = performance[performance.get("tipo_linha", pd.Series(dtype=str)).eq("ativo")].copy() if performance is not None and not performance.empty else pd.DataFrame()
    if portfolio is None or portfolio.empty or detail.empty:
        return pd.DataFrame([
            {"tipo_linha": "resumo", "metrica": "diagnostico_geral_da_carteira", "valor": "sem carteira selecionada para diagnostico"},
        ])
    keep_cols = [
        "ticker", "setor", "nota_final", "decisao_preliminar_ajustada", "status_para_risco", "categoria_elegibilidade",
        "tipo_timing", "sinal_timing", "tendencia_mensal", "leitura_forca_relativa_mensal", "classificacao_forca_relativa",
        "retorno_1m_relativo_ibov", "retorno_4m_relativo_ibov", "retorno_ytd_relativo_ibov", "rsi", "RSI/IFR",
        "MM semanal", "tendencia das medias moveis", "Bollinger", "sinal da Bollinger", "bollinger_status",
        "qualidade_fundamentalista", "classificacao_fundamentalista_setorial", "fundamento_bloqueante",
        "retorno_medio", "desvio_padrao", "cv", "beta", "correlacao_ibov", "correlacao_media_ativos",
        "janela_risco_inicio", "janela_risco_fim", "quantidade_observacoes_risco", "forca_relativa_positiva_relevante",
        "alertas_nao_bloqueantes", "penalizacoes_otimizacao",
        "tipo_watchlist", "motivo_tipo_watchlist", "watchlist_bloqueia_otimizacao", "watchlist_de_virada",
        "alerta_sinal_tardio", "motivos_alerta_sinal_tardio", "qualidade_do_timing",
        "score_prioridade_otimizacao", "penalidade_prioridade_otimizacao", "liberado_para_otimizacao",
        "bloqueado_otimizacao", "motivo_bloqueio_otimizacao", "tipo_bloqueio_otimizacao",
        "regime_mercado_data_base", "peso_maximo_permitido_ativo",
        "penalizacao_beta_negativo_mercado_favoravel", "penalizacao_beta_muito_baixo_mercado_favoravel",
        "penalizacao_correlacao_negativa_mercado_favoravel", "penalizacao_correlacao_muito_baixa_mercado_favoravel",
        "penalizacao_watchlist_flexivel", "score_aderencia_regime", "motivo_aderencia_regime",
        "limite_peso_watchlist_flexivel_aplicado", "limite_quantidade_watchlist_flexivel_aplicado",
    ]
    selected = portfolio.reindex(columns=[col for col in keep_cols if col in portfolio.columns]).copy()
    diag = selected.merge(detail, on="ticker", how="left", suffixes=("", "_performance"))
    if "setor_performance" in diag:
        diag["setor"] = diag["setor"].fillna(diag["setor_performance"])
    alert_map = alerts.groupby("ticker")["alerta"].apply("; ".join) if alerts is not None and not alerts.empty and {"ticker", "alerta"}.issubset(alerts.columns) else pd.Series(dtype=str)
    diag["principais_alertas"] = diag["ticker"].map(alert_map).fillna(diag.get("alertas_nao_bloqueantes", ""))
    diag["alfa_individual_vs_ibov"] = diag.get("alfa_vs_ibov", np.nan)
    diag["resultado_individual"] = diag.apply(_diagnostic_result, axis=1)
    diag["contribuicao_resultado"] = diag.apply(_diagnostic_contribution, axis=1)
    diag["ranking_contribuicao"] = diag["contribuicao_para_retorno_carteira"].rank(ascending=False, method="min")
    best_contribution = diag["contribuicao_para_retorno_carteira"].max()
    worst_contribution = diag["contribuicao_para_retorno_carteira"].min()
    diag["melhor_ou_pior_contribuicao"] = np.select(
        [diag["contribuicao_para_retorno_carteira"].eq(best_contribution), diag["contribuicao_para_retorno_carteira"].eq(worst_contribution)],
        ["melhor_contribuicao", "pior_contribuicao"],
        default="intermediaria",
    )
    sector_perf = diag.groupby("setor").agg(retorno_medio_setor_pos_formacao=("retorno_realizado_periodo", "mean"), qtd_ativos_setor=("ticker", "count"))
    ibov_return = diag["retorno_ibov_periodo"].dropna().iloc[0] if diag["retorno_ibov_periodo"].notna().any() else np.nan
    underperforming_sectors = set(sector_perf[(sector_perf["qtd_ativos_setor"] >= 2) & (sector_perf["retorno_medio_setor_pos_formacao"] < ibov_return)].index) if pd.notna(ibov_return) else set()
    diag["leitura_diagnostica"] = diag.apply(lambda row: _diagnostic_reading(row, underperforming_sectors), axis=1)
    diag["tipo_linha"] = "ativo"
    positives = int((diag["retorno_realizado_periodo"] > 0).sum())
    negatives = int((diag["retorno_realizado_periodo"] < 0).sum())
    beat_ibov = int(diag["resultado_individual"].eq("superou_ibov").sum())
    below_ibov = int(diag["resultado_individual"].eq("ficou_abaixo_ibov").sum())
    portfolio_return = metrics.get("retorno_realizado_carteira_periodo", np.nan)
    alpha = metrics.get("alfa_realizado_vs_ibov", np.nan)
    best_asset = diag.sort_values("retorno_realizado_periodo", ascending=False).head(1)["ticker"].iloc[0]
    worst_asset = diag.sort_values("retorno_realizado_periodo", ascending=True).head(1)["ticker"].iloc[0]
    top_positive = diag.sort_values("contribuicao_para_retorno_carteira", ascending=False).head(1)["ticker"].iloc[0]
    top_negative = diag.sort_values("contribuicao_para_retorno_carteira", ascending=True).head(1)["ticker"].iloc[0]
    false_positive_mask = diag["leitura_diagnostica"].isin(["falso_positivo", "sinal_tardio", "risco_realizado_acima_do_esperado", "queda_setorial_pos_formacao"])
    false_positive_tickers = ", ".join(diag.loc[false_positive_mask, "ticker"].astype(str).tolist())
    general = _diagnostic_general(performance, temporal_context)
    summary_items = [
        ("retorno_realizado_carteira", portfolio_return),
        ("retorno_realizado_ibov", ibov_return),
        ("alfa_realizado_vs_ibov", alpha),
        ("quantidade_ativos_superaram_ibov", beat_ibov),
        ("quantidade_ativos_abaixo_ibov", below_ibov),
        ("quantidade_ativos_positivos", positives),
        ("quantidade_ativos_negativos", negatives),
        ("melhor_ativo", best_asset),
        ("pior_ativo", worst_asset),
        ("maior_contribuicao_positiva", top_positive),
        ("maior_contribuicao_negativa", top_negative),
        ("percentual_da_carteira_que_superou_ibov", beat_ibov / len(diag) if len(diag) else np.nan),
        ("diagnostico_geral_da_carteira", general),
        ("observacao_resultado_parcial", "ate a data de avaliacao, a carteira ficou abaixo do IBOV" if general == "resultado_parcial_nao_conclusivo" and pd.notna(alpha) and alpha < 0 else ""),
        ("principais_falsos_positivos", false_positive_tickers),
    ]
    summary = pd.DataFrame([{"tipo_linha": "resumo", "metrica": key, "valor": value} for key, value in summary_items])
    ordered = [
        "tipo_linha", "ticker", "setor", "peso_recomendado", "data_formacao_carteira", "data_inicio_performance", "data_avaliacao_carteira",
        "preco_inicio_performance", "preco_formacao", "preco_avaliacao", "retorno_realizado_periodo", "retorno_ibov_periodo", "alfa_individual_vs_ibov",
        "contribuicao_para_retorno_carteira", "ranking_contribuicao", "melhor_ou_pior_contribuicao",
        "nota_final", "decisao_preliminar_ajustada", "status_para_risco", "categoria_elegibilidade", "tipo_timing", "sinal_timing",
        "tendencia_mensal", "leitura_forca_relativa_mensal", "classificacao_forca_relativa", "retorno_1m_relativo_ibov",
        "retorno_4m_relativo_ibov", "retorno_ytd_relativo_ibov", "rsi", "RSI/IFR", "MM semanal", "tendencia das medias moveis",
        "Bollinger", "sinal da Bollinger", "bollinger_status", "qualidade_fundamentalista", "classificacao_fundamentalista_setorial",
        "fundamento_bloqueante", "principais_alertas", "retorno_medio", "desvio_padrao", "cv", "beta", "correlacao_ibov",
        "correlacao_media_ativos", "janela_risco_inicio", "janela_risco_fim", "quantidade_observacoes_risco",
        "tipo_watchlist", "motivo_tipo_watchlist", "watchlist_bloqueia_otimizacao", "watchlist_de_virada",
        "alerta_sinal_tardio", "motivos_alerta_sinal_tardio", "qualidade_do_timing",
        "score_prioridade_otimizacao", "penalidade_prioridade_otimizacao", "liberado_para_otimizacao",
        "bloqueado_otimizacao", "motivo_bloqueio_otimizacao", "tipo_bloqueio_otimizacao",
        "regime_mercado_data_base", "peso_maximo_permitido_ativo",
        "penalizacao_beta_negativo_mercado_favoravel", "penalizacao_beta_muito_baixo_mercado_favoravel",
        "penalizacao_correlacao_negativa_mercado_favoravel", "penalizacao_correlacao_muito_baixa_mercado_favoravel",
        "penalizacao_watchlist_flexivel", "score_aderencia_regime", "motivo_aderencia_regime",
        "limite_peso_watchlist_flexivel_aplicado", "limite_quantidade_watchlist_flexivel_aplicado",
        "resultado_individual", "contribuicao_resultado", "leitura_diagnostica", "metrica", "valor",
    ]
    return pd.concat([diag, summary], ignore_index=True, sort=False).reindex(columns=ordered)
def main() -> None:
    log_path = setup_logging()
    settings = load_settings()
    ref_year, ref_month = _reference_year_month(settings)
    year_month = f"{ref_year:04d}_{ref_month:02d}"
    assets, universe_frame, universe_summary, universe_alerts = load_universe(settings)
    tickers = assets["ticker"].tolist()
    LOGGER.info("Iniciando carteira mensal para %s ativos", len(tickers))

    data_settings = settings["data"]
    prices, price_log = fetch_yfinance_prices(
        tickers,
        data_settings["history_months"],
        data_settings["use_adjusted_prices"],
        fallback_map=data_settings.get("ticker_fallbacks", {}),
        retries=int(data_settings.get("download_retries", 3)),
        min_rows=int(data_settings.get("min_price_rows", 120)),
    )
    index_prices, index_log = fetch_index_prices(data_settings["indexes"], data_settings["history_months"], data_settings["use_adjusted_prices"], settings=settings)
    fundamentals, fundamentals_log = collect_fundamentals(tickers)
    if prices.empty:
        raise RuntimeError("Nenhuma cotacao foi coletada. Verifique internet, yfinance ou tickers.")

    temporal_context = _resolve_temporal_context(settings, prices, index_prices)
    selection_prices = _slice_until(prices, temporal_context["data_limite_dados_selecao"])
    selection_index_prices = _slice_until(index_prices, temporal_context["data_limite_dados_selecao"])
    historical_simulation = temporal_context["data_formacao_carteira"] < temporal_context["data_avaliacao_carteira"]
    settings["_runtime_historical_simulation"] = bool(historical_simulation)
    settings["_runtime_sem_look_ahead_bias"] = True
    settings["_runtime_trading_days_month"] = int(temporal_context.get("pregoes_mes_referencia", settings.get("calendar", {}).get("fallback_trading_days_month", 21)))
    settings["_runtime_calendar_source"] = temporal_context.get("calendario_fonte", "")
    settings["_runtime_calendar_status"] = temporal_context.get("calendario_status", "")
    LOGGER.info("Data-base: formacao=%s limite_selecao=%s avaliacao=%s", temporal_context["data_formacao_carteira"].date(), temporal_context["data_limite_dados_selecao"].date(), temporal_context["data_avaliacao_carteira"].date())

    tech = technical_table(selection_prices, settings)
    technical_audit = technical_audit_table(tech)
    prio3_rsi_audit = build_prio3_rsi_log(selection_prices, settings)
    recent_returns = cumulative_returns_table(selection_prices)
    sector_indexes = analyze_sector_indexes(selection_index_prices, settings) if not selection_index_prices.empty else pd.DataFrame()
    base = assets.merge(tech, on="ticker", how="left").merge(recent_returns, on="ticker", how="left").merge(fundamentals, on="ticker", how="left")
    missing_sector = base["setor"].isna() | base["setor"].eq("Outros")
    base.loc[missing_sector, "setor"] = base.loc[missing_sector, "setor_fundamentus"].fillna("Outros")
    base = apply_sector_mapping(base, sector_indexes, settings)
    base["price_rows"] = base["ticker"].map(selection_prices.count()).fillna(0).astype(int)
    base["retorno_YTD"] = base["retorno_ytd"]
    base = add_relative_strength(base, selection_index_prices, settings)

    preliminary = build_preliminary(base, settings)
    sector_market = sector_market_diagnosis(preliminary)
    if not sector_market.empty:
        preliminary["sentimento_setorial"] = preliminary["setor"].map(sector_market.set_index("setor")["sentimento_setorial"]).fillna("setor_neutro")
    else:
        preliminary["sentimento_setorial"] = "setor_neutro"
    preliminary_summary = preliminary_summary_table(preliminary)
    _, market_total_pre, market_favorable_pre, market_favorable_pct_pre = _market_breadth_rows(preliminary)
    market_class_pre, market_class_pct_pre = _market_classification(market_favorable_pre, market_total_pre)
    settings["_runtime_market_class"] = market_class_pre
    settings["_runtime_market_pct"] = market_class_pct_pre
    market_subtype = classify_favorable_market_subtype(preliminary, sector_indexes, market_class_pre, settings)
    settings["_runtime_subtipo_mercado_favoravel"] = market_subtype.get("subtipo_mercado_favoravel", "nao_aplicavel")
    settings["_runtime_mercado_favoravel_esticado"] = bool(market_subtype.get("mercado_favoravel_esticado", False))
    settings["_runtime_mercado_favoravel_cansado"] = bool(market_subtype.get("mercado_favoravel_cansado", False))
    settings["_runtime_motivo_subtipo_mercado_favoravel"] = market_subtype.get("motivo_subtipo_mercado_favoravel", "")

    watchlist = build_watchlist(preliminary, settings)
    preliminary = apply_watchlist_flags(preliminary, watchlist)
    preliminary = refine_timing_watchlist(preliminary, settings)
    preliminary = apply_stretched_market_fields(preliminary, settings)
    if not watchlist.empty and "ticker" in watchlist:
        watchlist = preliminary[preliminary["ticker"].isin(watchlist["ticker"])].copy()
    pre_risk_candidates = select_pre_risk_candidates(preliminary, settings)
    candidate_tickers = pre_risk_candidates["ticker"].tolist()
    risk_start = temporal_context["data_limite_dados_selecao"] - pd.DateOffset(months=settings["data"]["risk_window_months"])
    candidate_prices = selection_prices[candidate_tickers][selection_prices.index >= risk_start] if candidate_tickers else pd.DataFrame(index=selection_prices.index)
    returns = log_returns(candidate_prices) if not candidate_prices.empty else pd.DataFrame()
    ibov_ticker = settings["data"]["indexes"]["IBOV"]
    ibov_returns = log_returns(selection_index_prices[selection_index_prices.index >= risk_start][ibov_ticker]) if ibov_ticker in selection_index_prices else pd.Series(dtype=float)
    risk, corr, cov = risk_metrics(returns, ibov_returns, settings) if not returns.empty else (pd.DataFrame(columns=["ticker"]), pd.DataFrame(), pd.DataFrame())
    ibov_price_window = selection_index_prices[selection_index_prices.index >= risk_start][ibov_ticker] if ibov_ticker in selection_index_prices else pd.Series(dtype=float)
    risk_price_window = candidate_prices.dropna(how="all") if not candidate_prices.empty else pd.DataFrame()
    risk_return_window = returns.dropna(how="all") if not returns.empty else pd.DataFrame()
    risk_window_start_actual = pd.Timestamp(risk_return_window.index.min()).normalize() if not risk_return_window.empty else (pd.Timestamp(risk_price_window.index.min()).normalize() if not risk_price_window.empty else risk_start.normalize())
    risk_window_end_actual = pd.Timestamp(risk_return_window.index.max()).normalize() if not risk_return_window.empty else temporal_context["data_limite_dados_selecao"]
    risk_observations = int(len(risk_return_window))
    risk_window_info = {
        "janela_risco_inicio": risk_window_start_actual.date().isoformat(),
        "janela_risco_fim": risk_window_end_actual.date().isoformat(),
        "janela_risco_meses": settings["data"].get("risk_window_months"),
        "periodicidade_risco": "diaria",
        "tipo_retorno_risco": "log-retornos",
        "quantidade_observacoes_risco": risk_observations,
    }

    risk_candidates_all = pre_risk_candidates.merge(risk, on="ticker", how="left")
    risk_candidates_all["variancia"] = risk_candidates_all.get("desvio_padrao", pd.Series(np.nan, index=risk_candidates_all.index)) ** 2
    risk_candidates_all["alertas_metodologicos"] = risk_candidates_all.apply(lambda row: methodological_alerts(row, settings), axis=1)
    eligibility_all = risk_candidates_all.apply(lambda row: classify_eligibility(row, settings), axis=1)
    risk_candidates_all = pd.concat([risk_candidates_all, eligibility_all], axis=1)
    risk_candidates_all = refine_timing_watchlist(risk_candidates_all, settings)
    risk_candidates_all = apply_stretched_market_fields(risk_candidates_all, settings)
    risk_candidates_all = apply_regime_fields(risk_candidates_all, settings)
    serie_historica_risco = build_risk_series_history(candidate_prices, returns, risk_candidates_all, settings, risk_window_info, temporal_context)
    serie_historica_risco = append_benchmark_to_risk_series(
        serie_historica_risco,
        ibov_price_window,
        ibov_returns,
        ibov_ticker,
        settings,
        risk_window_info,
        temporal_context,
    )
    auditoria_calculo_risco = build_risk_calculation_audit(serie_historica_risco, risk_candidates_all)
    auditoria_beta_correlacao = build_beta_correlation_audit(serie_historica_risco, risk_candidates_all, ibov_ticker, risk_window_info)

    weak_market = market_class_pre == "mercado fraco/desfavoravel"
    selective_mode_enabled = bool(settings.get("market_regime", {}).get("allow_selective_portfolio_in_weak_market", True))
    liberados_otimizacao_antes_refino = int((~risk_candidates_all.get("flag_watchlist", pd.Series(False, index=risk_candidates_all.index)).fillna(False) & risk_candidates_all.get("categoria_elegibilidade", pd.Series("", index=risk_candidates_all.index)).isin({"elegivel_forte", "elegivel_moderado"}) & risk_candidates_all.get("status_para_risco", pd.Series("", index=risk_candidates_all.index)).isin({"aprovada_para_risco", "moderada_para_risco"}) & (risk_candidates_all.get("retorno_medio", pd.Series(np.nan, index=risk_candidates_all.index)) > 0)).sum())
    risk_alerts = risk_candidates_all.apply(lambda row: optimization_alerts_and_penalties(row, settings), axis=1)
    risk_candidates_all = pd.concat([risk_candidates_all, risk_alerts], axis=1)
    block_fields = risk_candidates_all.apply(lambda row: optimization_block_fields(row, settings, weak_market), axis=1)
    risk_candidates_all = pd.concat([risk_candidates_all, block_fields], axis=1)
    permitted_for_optimization = risk_candidates_all[risk_candidates_all["liberado_para_otimizacao"].fillna(False)].copy()
    risk_candidates_scored = score_assets(permitted_for_optimization, settings)
    timing_watchlist_diagnosis = build_timing_watchlist_diagnosis(risk_candidates_all)

    portfolio, portfolio_metrics = optimize_weights(risk_candidates_scored, cov, settings)
    portfolio_metrics.update(risk_window_info)
    selective_violations = []
    if weak_market and selective_mode_enabled and not portfolio.empty:
        selective_violations = selective_portfolio_violations(portfolio, portfolio_metrics, settings)
        if selective_violations:
            portfolio = portfolio.head(0).copy()
            portfolio_metrics["carteira_valida"] = False
            portfolio_metrics["status_carteira"] = "sem_carteira_recomendada"
            portfolio_metrics["restricoes_violadas"] = "; ".join(dict.fromkeys([portfolio_metrics.get("restricoes_violadas", "")] + selective_violations)).strip("; ")
    comparison = portfolio_metrics.get("comparativo_carteiras", pd.DataFrame())
    if not isinstance(comparison, pd.DataFrame):
        comparison = pd.DataFrame()

    if not comparison.empty:
        for key, value in risk_window_info.items():
            comparison[key] = value

    score_cols = ["ticker", "nota_final", "score_prioridade_otimizacao", "penalidade_prioridade_otimizacao", "score_tendencia", "score_timing", "score_fundamentos", "score_setor", "score_risco", "penalidade_cv", "penalidade_timing"]
    optimization_full = add_zero_weight_candidates(risk_candidates_all.merge(risk_candidates_scored.reindex(columns=score_cols), on="ticker", how="left"), portfolio)
    optimization_full["decisao de entrada na carteira"] = np.where(
        optimization_full["bloqueado_otimizacao"].fillna(False),
        "bloqueada_otimizacao",
        np.where(optimization_full["peso_final"].fillna(0) > 0, "selecionada", "peso zero"),
    )

    blocked_audit = preliminary[preliminary["status_para_risco"].eq("bloqueada_para_risco")].copy()
    if not blocked_audit.empty:
        blocked_audit["peso_final"] = 0.0
        blocked_audit["peso_testado_composicao_escolhida"] = 0.0
        blocked_audit["decisao de entrada na carteira"] = "bloqueada_para_risco"
        blocked_audit["bloqueado_otimizacao"] = True
        blocked_audit["motivo_bloqueio_otimizacao"] = blocked_audit["motivo_status_para_risco"].fillna("bloqueio_por_status_para_risco")
        blocked_audit["tipo_bloqueio_otimizacao"] = "bloqueio_preliminar"
        blocked_audit["alertas_nao_bloqueantes"] = ""
        blocked_audit["penalizacoes_otimizacao"] = ""
        blocked_audit["liberado_para_otimizacao"] = False
        blocked_audit["categoria_elegibilidade"] = "bloqueada_para_risco"
        optimization_audit = pd.concat([optimization_full, blocked_audit], ignore_index=True, sort=False)
    else:
        optimization_audit = optimization_full.copy()

    optimization_full["peso_testado_composicao_escolhida"] = optimization_full["peso_final"].fillna(0.0)
    optimization_audit["peso_testado_composicao_escolhida"] = optimization_audit["peso_final"].fillna(0.0)
    optimization_audit["quantidade_acoes_carteira_escolhida"] = portfolio_metrics.get("quantidade_acoes", 0)
    optimization_audit["retorno_carteira_escolhida"] = portfolio_metrics.get("retorno_carteira", np.nan)
    optimization_audit["retorno_carteira_diario_escolhida"] = portfolio_metrics.get("retorno_carteira_diario", portfolio_metrics.get("retorno_carteira", np.nan))
    optimization_audit["retorno_carteira_mensal_escolhida"] = portfolio_metrics.get("retorno_carteira_mensal", np.nan)
    optimization_audit["retorno_carteira_anual_escolhida"] = portfolio_metrics.get("retorno_carteira_anual", np.nan)
    optimization_audit["risco_carteira_escolhida"] = portfolio_metrics.get("risco_carteira", np.nan)
    optimization_audit["cv_carteira_escolhida"] = portfolio_metrics.get("cv_carteira", np.nan)
    optimization_audit["beta_carteira_escolhida"] = portfolio_metrics.get("beta_carteira", np.nan)
    optimization_audit["sharpe_carteira_escolhida"] = portfolio_metrics.get("sharpe_diario", np.nan)
    optimization_audit["correlacao_carteira_ibov_escolhida"] = portfolio_metrics.get("correlacao_carteira_ibov", np.nan)
    optimization_audit["score_aderencia_regime_carteira_escolhida"] = portfolio_metrics.get("score_aderencia_regime", np.nan)
    optimization_audit["aderencia_carteira_ao_regime_escolhida"] = portfolio_metrics.get("aderencia_carteira_ao_regime", "")
    optimization_audit["peso_total_watchlist_flexivel_carteira_escolhida"] = portfolio_metrics.get("peso_total_watchlist_flexivel", np.nan)
    optimization_audit["concentracao_setorial_carteira_escolhida"] = portfolio_metrics.get("concentracao_por_setor", "")

    focus_audit_tickers = ["ENEV3.SA", "GOAU4.SA", "GGBR4.SA", "PETR3.SA", "PETR4.SA", "PRIO3.SA", "BRAV3.SA", "PSSA3.SA", "CPLE3.SA", "EGIE3.SA", "ABEV3.SA"]
    audit_cols = ["ticker", "setor", "decisao_preliminar_ajustada", "status_para_risco", "categoria_elegibilidade", "tipo_timing", "leitura_forca_relativa_mensal", "qualidade_fundamentalista", "fundamento_bloqueante", "beta", "cv", "correlacao_ibov", "retorno_medio", "bloqueado_otimizacao", "motivo_bloqueio_otimizacao", "tipo_bloqueio_otimizacao", "alertas_nao_bloqueantes", "penalizacoes_otimizacao", "score_prioridade_otimizacao", "liberado_para_otimizacao", "tipo_watchlist", "motivo_tipo_watchlist", "watchlist_bloqueia_otimizacao", "watchlist_de_virada", "alerta_sinal_tardio", "motivos_alerta_sinal_tardio", "qualidade_do_timing", "subtipo_mercado_favoravel", "mercado_favoravel_esticado", "mercado_favoravel_cansado", "motivo_subtipo_mercado_favoravel", "penalizacao_forca_relativa_fraca", "bloqueio_forca_relativa_fraca", "motivo_bloqueio_forca_relativa", "penalizacao_retorno_1m_relativo_negativo", "penalizacao_retorno_1m_relativo_negativo_forte", "bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado", "alerta_beta_alto_mercado_esticado", "penalizacao_beta_alto_mercado_esticado", "peso_maximo_beta_alto_mercado_esticado", "alerta_realizacao_pos_rali", "motivos_alerta_realizacao_pos_rali", "penalizacao_realizacao_pos_rali", "perfil_risco_empresa", "peso_maximo_turnaround_especulativo", "peso_maximo_timing_com_alerta", "motivo_peso_maximo_reduzido", "regime_mercado_data_base", "peso_maximo_permitido_ativo", "subtipo_mercado_favoravel", "mercado_favoravel_esticado", "mercado_favoravel_cansado", "motivo_subtipo_mercado_favoravel", "penalizacao_forca_relativa_fraca", "bloqueio_forca_relativa_fraca", "motivo_bloqueio_forca_relativa", "penalizacao_retorno_1m_relativo_negativo", "penalizacao_retorno_1m_relativo_negativo_forte", "bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado", "alerta_beta_alto_mercado_esticado", "penalizacao_beta_alto_mercado_esticado", "peso_maximo_beta_alto_mercado_esticado", "alerta_realizacao_pos_rali", "motivos_alerta_realizacao_pos_rali", "penalizacao_realizacao_pos_rali", "perfil_risco_empresa", "peso_maximo_turnaround_especulativo", "peso_maximo_timing_com_alerta", "motivo_peso_maximo_reduzido", "grupo_economico_ou_bloco_risco", "peso_setor", "peso_bloco_risco", "alerta_concentracao_setorial", "motivo_concentracao_setorial", "alerta_bloco_risco", "motivo_alerta_bloco_risco", "penalizacao_beta_negativo_mercado_favoravel", "penalizacao_beta_muito_baixo_mercado_favoravel", "penalizacao_correlacao_negativa_mercado_favoravel", "penalizacao_correlacao_muito_baixa_mercado_favoravel", "bloqueio_aderencia_regime", "motivo_bloqueio_aderencia_regime", "beta_minimo_exigido_regime", "correlacao_minima_exigida_regime", "grupo_economico_ou_bloco_risco", "peso_setor", "peso_bloco_risco", "alerta_concentracao_setorial", "motivo_concentracao_setorial", "alerta_bloco_risco", "motivo_alerta_bloco_risco", "penalizacao_watchlist_flexivel", "score_aderencia_regime", "motivo_aderencia_regime", "limite_peso_watchlist_flexivel_aplicado", "limite_quantidade_watchlist_flexivel_aplicado"]
    optimization_block_audit = optimization_audit[optimization_audit["ticker"].isin(focus_audit_tickers)].reindex(columns=audit_cols).copy()
    hard_filter_settings = pd.DataFrame([
        {"parametro": "cv_as_hard_filter", "valor_atual": settings.get("risk", {}).get("cv_as_hard_filter", False), "ativo": bool(settings.get("risk", {}).get("cv_as_hard_filter", False)), "impacto_otimizacao": "se ativo, CV individual acima do limite bloqueia; se inativo, vira alerta/penalizacao"},
        {"parametro": "beta_as_hard_filter", "valor_atual": False, "ativo": False, "impacto_otimizacao": "nao existe como filtro duro; beta acima do alerta nao bloqueia sozinho"},
        {"parametro": "correlation_as_hard_filter", "valor_atual": False, "ativo": False, "impacto_otimizacao": "nao existe como filtro duro; correlacao alta nao bloqueia sozinha"},
        {"parametro": "allow_watchlist_entries", "valor_atual": settings.get("watchlist", {}).get("allow_watchlist_entries", False), "ativo": bool(settings.get("watchlist", {}).get("allow_watchlist_entries", False)), "impacto_otimizacao": "se falso, ativos em Watchlist por timing sao bloqueados"},
        {"parametro": "allow_overbought_entries", "valor_atual": _timing_settings(settings)["allow_overbought_entries"], "ativo": bool(_timing_settings(settings)["allow_overbought_entries"]), "impacto_otimizacao": "se falso, sobrecompra extrema bloqueia entrada"},
        {"parametro": "max_beta", "valor_atual": "nao_configurado", "ativo": False, "impacto_otimizacao": "nao bloqueia; beta_alert apenas gera alerta"},
        {"parametro": "max_cv_individual", "valor_atual": settings.get("risk", {}).get("cv_limit", np.nan), "ativo": bool(settings.get("risk", {}).get("cv_as_hard_filter", False)), "impacto_otimizacao": "limite de alerta/penalizacao quando cv_as_hard_filter=false"},
    ])
    participation_cols = ["ticker", "setor", "decisao_preliminar_ajustada", "valor_mercado", "participacao_empresa_no_setor", "participacao_empresa_no_universo", "ranking_valor_mercado_setor", "ranking_valor_mercado_universo", "observacao_peso_ibov"]
    market_participation = preliminary[preliminary["decisao_preliminar_ajustada"].isin(["candidata_para_risco", "candidata_com_restricao", "watchlist_qualificada"])].sort_values(["decisao_preliminar_ajustada", "nota_preliminar_ajustada"], ascending=[True, False]).head(25).reindex(columns=participation_cols).copy()

    if not portfolio.empty:
        merge_cols = _existing_columns(optimization_full, ["ticker", "decisao de entrada na carteira", "peso_final", "flag_watchlist", "flag_watchlist_na_carteira", "motivo_watchlist", "tipo_watchlist", "motivo_tipo_watchlist", "watchlist_bloqueia_otimizacao", "watchlist_de_virada", "alerta_sinal_tardio", "motivos_alerta_sinal_tardio", "qualidade_do_timing", "subtipo_mercado_favoravel", "mercado_favoravel_esticado", "mercado_favoravel_cansado", "motivo_subtipo_mercado_favoravel", "penalizacao_forca_relativa_fraca", "bloqueio_forca_relativa_fraca", "motivo_bloqueio_forca_relativa", "penalizacao_retorno_1m_relativo_negativo", "penalizacao_retorno_1m_relativo_negativo_forte", "bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado", "alerta_beta_alto_mercado_esticado", "penalizacao_beta_alto_mercado_esticado", "peso_maximo_beta_alto_mercado_esticado", "alerta_realizacao_pos_rali", "motivos_alerta_realizacao_pos_rali", "penalizacao_realizacao_pos_rali", "perfil_risco_empresa", "peso_maximo_turnaround_especulativo", "peso_maximo_timing_com_alerta", "motivo_peso_maximo_reduzido", "regime_mercado_data_base", "peso_maximo_permitido_ativo", "subtipo_mercado_favoravel", "mercado_favoravel_esticado", "mercado_favoravel_cansado", "motivo_subtipo_mercado_favoravel", "penalizacao_forca_relativa_fraca", "bloqueio_forca_relativa_fraca", "motivo_bloqueio_forca_relativa", "penalizacao_retorno_1m_relativo_negativo", "penalizacao_retorno_1m_relativo_negativo_forte", "bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado", "alerta_beta_alto_mercado_esticado", "penalizacao_beta_alto_mercado_esticado", "peso_maximo_beta_alto_mercado_esticado", "alerta_realizacao_pos_rali", "motivos_alerta_realizacao_pos_rali", "penalizacao_realizacao_pos_rali", "perfil_risco_empresa", "peso_maximo_turnaround_especulativo", "peso_maximo_timing_com_alerta", "motivo_peso_maximo_reduzido", "grupo_economico_ou_bloco_risco", "peso_setor", "peso_bloco_risco", "alerta_concentracao_setorial", "motivo_concentracao_setorial", "alerta_bloco_risco", "motivo_alerta_bloco_risco", "penalizacao_beta_negativo_mercado_favoravel", "penalizacao_beta_muito_baixo_mercado_favoravel", "penalizacao_correlacao_negativa_mercado_favoravel", "penalizacao_correlacao_muito_baixa_mercado_favoravel", "bloqueio_aderencia_regime", "motivo_bloqueio_aderencia_regime", "beta_minimo_exigido_regime", "correlacao_minima_exigida_regime", "grupo_economico_ou_bloco_risco", "peso_setor", "peso_bloco_risco", "alerta_concentracao_setorial", "motivo_concentracao_setorial", "alerta_bloco_risco", "motivo_alerta_bloco_risco", "penalizacao_watchlist_flexivel", "score_aderencia_regime", "motivo_aderencia_regime", "limite_peso_watchlist_flexivel_aplicado", "limite_quantidade_watchlist_flexivel_aplicado", "motivo_exclusao_por_timing"])
        merge_cols = [col for col in merge_cols if col == "ticker" or col not in portfolio.columns]
        if len(merge_cols) > 1:
            portfolio = portfolio.merge(optimization_full[merge_cols], on="ticker", how="left")
        portfolio["flag_watchlist"] = portfolio.get("flag_watchlist", pd.Series(False, index=portfolio.index)).fillna(False)
        portfolio["flag_watchlist_na_carteira"] = portfolio["flag_watchlist"]
    timing_summary = build_timing_summary(preliminary, watchlist, portfolio)

    has_moderate = (not portfolio.empty) and (portfolio["categoria_elegibilidade"].eq("elegivel_moderado").any() or portfolio["status_para_risco"].eq("moderada_para_risco").any())
    selective_violations = selective_violations if 'selective_violations' in locals() else []
    status_carteira, criterio_formacao, justificativa_carteira = portfolio_status_fields(portfolio, portfolio_metrics, market_class_pre, has_moderate, selective_violations, historical_simulation)
    portfolio_metrics.update({
        "status_carteira": status_carteira,
        "criterio_formacao": criterio_formacao,
        "justificativa_carteira": justificativa_carteira,
        "mercado_classificacao": market_class_pre,
        "mercado_tendencia_favoravel_pct": market_favorable_pct_pre,
        "subtipo_mercado_favoravel": settings.get("_runtime_subtipo_mercado_favoravel", "nao_aplicavel"),
        "mercado_favoravel_esticado": settings.get("_runtime_mercado_favoravel_esticado", False),
        "mercado_favoravel_cansado": settings.get("_runtime_mercado_favoravel_cansado", False),
        "motivo_subtipo_mercado_favoravel": settings.get("_runtime_motivo_subtipo_mercado_favoravel", ""),
        "rsi_ibov_data_base": market_subtype.get("rsi_ibov", np.nan),
        "bollinger_ibov_data_base": market_subtype.get("bollinger_ibov", ""),
        "pct_ativos_positivos_1m": market_subtype.get("pct_ativos_positivos_1m", np.nan),
    })
    if not portfolio.empty:
        portfolio["status_carteira"] = status_carteira
        portfolio["justificativa_carteira"] = justificativa_carteira

    portfolio_metrics["elegiveis_fortes"] = int((risk_candidates_all["categoria_elegibilidade"] == "elegivel_forte").sum()) if "categoria_elegibilidade" in risk_candidates_all else 0
    portfolio_metrics["elegiveis_moderados"] = int((risk_candidates_all["categoria_elegibilidade"] == "elegivel_moderado").sum()) if "categoria_elegibilidade" in risk_candidates_all else 0
    portfolio_metrics["aprovadas_para_risco"] = int((preliminary["status_para_risco"] == "aprovada_para_risco").sum())
    portfolio_metrics["moderadas_para_risco"] = int((preliminary["status_para_risco"] == "moderada_para_risco").sum())
    portfolio_metrics["bloqueadas_para_risco"] = int((preliminary["status_para_risco"] == "bloqueada_para_risco").sum())
    portfolio_metrics["ativos_permitidos_otimizacao"] = len(risk_candidates_scored)
    portfolio_metrics["ativos_liberados_otimizacao_depois_correcao"] = int(risk_candidates_all["liberado_para_otimizacao"].fillna(False).sum()) if "liberado_para_otimizacao" in risk_candidates_all else 0
    portfolio_metrics["ativos_bloqueados_otimizacao"] = int(risk_candidates_all["bloqueado_otimizacao"].fillna(False).sum()) if "bloqueado_otimizacao" in risk_candidates_all else 0
    portfolio_metrics["candidatas_preliminares"] = len(pre_risk_candidates)
    portfolio_metrics["candidatas_risco"] = len(risk_candidates_all)
    portfolio_metrics["acoes_peso_zero"] = int((optimization_full["peso_final"] == 0).sum()) if not optimization_full.empty else 0
    portfolio_metrics["ativos_bloqueados_com_peso"] = int((optimization_audit["bloqueado_otimizacao"].fillna(False) & (optimization_audit["peso_final"].fillna(0) > 1e-9)).sum()) if not optimization_audit.empty else 0
    portfolio_metrics["watchlist_na_carteira"] = int(portfolio.get("flag_watchlist_na_carteira", pd.Series(dtype=bool)).fillna(False).sum()) if not portfolio.empty else 0
    portfolio_metrics["ativos_liberados_otimizacao_antes_refino"] = liberados_otimizacao_antes_refino
    portfolio_metrics["watchlist_bloqueante"] = int(risk_candidates_all.get("tipo_watchlist", pd.Series("", index=risk_candidates_all.index)).eq("watchlist_bloqueante").sum())
    portfolio_metrics["watchlist_flexivel"] = int(risk_candidates_all.get("tipo_watchlist", pd.Series("", index=risk_candidates_all.index)).eq("watchlist_flexivel").sum())
    portfolio_metrics["watchlist_monitoramento"] = int(risk_candidates_all.get("tipo_watchlist", pd.Series("", index=risk_candidates_all.index)).eq("watchlist_monitoramento").sum())
    portfolio_metrics["ativos_alerta_sinal_tardio"] = int(risk_candidates_all.get("alerta_sinal_tardio", pd.Series(False, index=risk_candidates_all.index)).fillna(False).sum())
    portfolio_metrics["ativos_timing_tardio"] = int(risk_candidates_all.get("qualidade_do_timing", pd.Series("", index=risk_candidates_all.index)).eq("timing_tardio").sum())
    portfolio_metrics["ativos_bloqueados_forca_relativa_fraca"] = int(risk_candidates_all.get("bloqueio_forca_relativa_fraca", pd.Series(False, index=risk_candidates_all.index)).fillna(False).sum())
    portfolio_metrics["ativos_alerta_realizacao_pos_rali"] = int(risk_candidates_all.get("alerta_realizacao_pos_rali", pd.Series(False, index=risk_candidates_all.index)).fillna(False).sum())
    portfolio_metrics["ativos_alerta_beta_alto_mercado_esticado"] = int(risk_candidates_all.get("alerta_beta_alto_mercado_esticado", pd.Series(False, index=risk_candidates_all.index)).fillna(False).sum())
    portfolio_metrics["ativos_turnaround_especulativo"] = int(risk_candidates_all.get("perfil_risco_empresa", pd.Series("", index=risk_candidates_all.index)).eq("turnaround_especulativo").sum())
    portfolio_metrics["ativos_convertidos_watchlist_flexivel"] = ", ".join(risk_candidates_all.loc[risk_candidates_all.get("flag_watchlist", pd.Series(False, index=risk_candidates_all.index)).fillna(False) & risk_candidates_all.get("tipo_watchlist", pd.Series("", index=risk_candidates_all.index)).eq("watchlist_flexivel") & risk_candidates_all.get("liberado_para_otimizacao", pd.Series(False, index=risk_candidates_all.index)).fillna(False), "ticker"].astype(str).tolist())
    portfolio_metrics["ativos_mantidos_bloqueados_timing"] = ", ".join(risk_candidates_all.loc[risk_candidates_all.get("watchlist_bloqueia_otimizacao", pd.Series(False, index=risk_candidates_all.index)).fillna(False), "ticker"].astype(str).tolist())
    portfolio_metrics["ativos_com_alerta_sinal_tardio"] = ", ".join(risk_candidates_all.loc[risk_candidates_all.get("alerta_sinal_tardio", pd.Series(False, index=risk_candidates_all.index)).fillna(False), "ticker"].astype(str).tolist())
    portfolio_metrics["ativos_com_timing_tardio"] = ", ".join(risk_candidates_all.loc[risk_candidates_all.get("qualidade_do_timing", pd.Series("", index=risk_candidates_all.index)).eq("timing_tardio"), "ticker"].astype(str).tolist())
    portfolio_metrics["risk_window_months"] = settings["data"].get("risk_window_months")
    portfolio_metrics["risk_return_periodicity"] = "diaria"
    portfolio_metrics["risk_return_type"] = "log-retornos"
    portfolio_metrics["risk_price_source"] = settings["data"].get("price_source_primary", "yfinance")
    portfolio_metrics["risk_benchmark"] = settings["data"].get("indexes", {}).get("IBOV", "^BVSP")
    portfolio_metrics["mes_referencia"] = f"{temporal_context['ano_referencia']:04d}-{temporal_context['mes_referencia']:02d}"
    portfolio_metrics["data_formacao_carteira"] = temporal_context["data_formacao_carteira"].date().isoformat()
    portfolio_metrics["data_avaliacao_carteira"] = temporal_context["data_avaliacao_carteira"].date().isoformat()
    portfolio_metrics["data_avaliacao_solicitada"] = temporal_context["data_avaliacao_solicitada"].date().isoformat()
    portfolio_metrics["data_limite_dados_selecao"] = temporal_context["data_limite_dados_selecao"].date().isoformat()
    portfolio_metrics["data_inicio_performance"] = temporal_context["data_inicio_performance"].date().isoformat()
    portfolio_metrics["periodo_dados_selecao"] = temporal_context["periodo_dados_selecao"]
    portfolio_metrics["periodo_avaliacao_performance"] = temporal_context["periodo_avaliacao_performance"]
    portfolio_metrics["sem_look_ahead_bias"] = True
    portfolio_metrics["fundamentos_point_in_time"] = "limitado: fonte online atual, sem base historica point-in-time"
    portfolio_metrics["observacao_execucao"] = "carteira formada em cenario historico para avaliacao, nao recomendacao em tempo real" if historical_simulation and portfolio_metrics.get("carteira_valida", False) else ""

    portfolio_alerts = validate_portfolio(portfolio, settings)
    min_configured_count = min(settings["portfolio"].get("candidate_counts", [settings["strategy"].get("min_assets", 5)]))
    if len(risk_candidates_scored) < min_configured_count:
        portfolio_alerts.append(f"Ativos permitidos insuficientes sem usar bloqueados: {len(risk_candidates_scored)}; minimo testado: {min_configured_count}")
    if portfolio_metrics.get("ativos_bloqueados_com_peso", 0) > 0:
        portfolio_alerts.append("Erro metodologico: ativo bloqueado recebeu peso maior que zero")
    if weak_market and not portfolio_metrics.get("carteira_valida", False):
        portfolio_alerts.append("Sem carteira recomendada - mercado fraco/desfavoravel ou criterios seletivos nao atendidos")
    if has_moderate:
        portfolio_alerts.append("Carteira formada com flexibilizacao controlada dos criterios de risco")
    if portfolio_metrics.get("restricoes_violadas"):
        portfolio_alerts.extend([portfolio_metrics["restricoes_violadas"]])
    portfolio_alerts.extend(universe_alerts)
    portfolio_alerts = list(dict.fromkeys([alert for alert in portfolio_alerts if alert]))

    stretch_cols = ["subtipo_mercado_favoravel", "mercado_favoravel_esticado", "mercado_favoravel_cansado", "motivo_subtipo_mercado_favoravel", "penalizacao_forca_relativa_fraca", "bloqueio_forca_relativa_fraca", "motivo_bloqueio_forca_relativa", "penalizacao_retorno_1m_relativo_negativo", "penalizacao_retorno_1m_relativo_negativo_forte", "bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado", "alerta_beta_alto_mercado_esticado", "penalizacao_beta_alto_mercado_esticado", "peso_maximo_beta_alto_mercado_esticado", "alerta_realizacao_pos_rali", "motivos_alerta_realizacao_pos_rali", "penalizacao_realizacao_pos_rali", "perfil_risco_empresa", "peso_maximo_turnaround_especulativo", "peso_maximo_timing_com_alerta", "motivo_peso_maximo_reduzido", "peso_maximo_permitido_ativo"]
    if not risk_candidates_all.empty and "ticker" in risk_candidates_all:
        risk_by_ticker = risk_candidates_all.set_index("ticker")
        for col in stretch_cols:
            if col in risk_by_ticker:
                mapped = preliminary["ticker"].map(risk_by_ticker[col])
                current = preliminary[col] if col in preliminary else pd.Series(np.nan, index=preliminary.index)
                preliminary[col] = mapped.combine_first(current)
    merge_cols = _existing_columns(risk_candidates_all, ["ticker", "retorno_medio", "desvio_padrao", "variancia", "cv", "beta", "correlacao_ibov", "correlacao_media_ativos", "elegibilidade_original", "elegibilidade_flexibilizada", "categoria_elegibilidade", "motivo_flexibilizacao", "motivo_exclusao", "bloqueado_otimizacao", "motivo_bloqueio_otimizacao", "tipo_bloqueio_otimizacao", "alertas_nao_bloqueantes", "penalizacoes_otimizacao", "score_prioridade_otimizacao", "liberado_para_otimizacao", "tipo_watchlist", "motivo_tipo_watchlist", "watchlist_bloqueia_otimizacao", "watchlist_de_virada", "alerta_sinal_tardio", "motivos_alerta_sinal_tardio", "qualidade_do_timing", "subtipo_mercado_favoravel", "mercado_favoravel_esticado", "mercado_favoravel_cansado", "motivo_subtipo_mercado_favoravel", "penalizacao_forca_relativa_fraca", "bloqueio_forca_relativa_fraca", "motivo_bloqueio_forca_relativa", "penalizacao_retorno_1m_relativo_negativo", "penalizacao_retorno_1m_relativo_negativo_forte", "bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado", "alerta_beta_alto_mercado_esticado", "penalizacao_beta_alto_mercado_esticado", "peso_maximo_beta_alto_mercado_esticado", "alerta_realizacao_pos_rali", "motivos_alerta_realizacao_pos_rali", "penalizacao_realizacao_pos_rali", "perfil_risco_empresa", "peso_maximo_turnaround_especulativo", "peso_maximo_timing_com_alerta", "motivo_peso_maximo_reduzido", "regime_mercado_data_base", "peso_maximo_permitido_ativo", "subtipo_mercado_favoravel", "mercado_favoravel_esticado", "mercado_favoravel_cansado", "motivo_subtipo_mercado_favoravel", "penalizacao_forca_relativa_fraca", "bloqueio_forca_relativa_fraca", "motivo_bloqueio_forca_relativa", "penalizacao_retorno_1m_relativo_negativo", "penalizacao_retorno_1m_relativo_negativo_forte", "bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado", "alerta_beta_alto_mercado_esticado", "penalizacao_beta_alto_mercado_esticado", "peso_maximo_beta_alto_mercado_esticado", "alerta_realizacao_pos_rali", "motivos_alerta_realizacao_pos_rali", "penalizacao_realizacao_pos_rali", "perfil_risco_empresa", "peso_maximo_turnaround_especulativo", "peso_maximo_timing_com_alerta", "motivo_peso_maximo_reduzido", "grupo_economico_ou_bloco_risco", "peso_setor", "peso_bloco_risco", "alerta_concentracao_setorial", "motivo_concentracao_setorial", "alerta_bloco_risco", "motivo_alerta_bloco_risco", "penalizacao_beta_negativo_mercado_favoravel", "penalizacao_beta_muito_baixo_mercado_favoravel", "penalizacao_correlacao_negativa_mercado_favoravel", "penalizacao_correlacao_muito_baixa_mercado_favoravel", "bloqueio_aderencia_regime", "motivo_bloqueio_aderencia_regime", "beta_minimo_exigido_regime", "correlacao_minima_exigida_regime", "grupo_economico_ou_bloco_risco", "peso_setor", "peso_bloco_risco", "alerta_concentracao_setorial", "motivo_concentracao_setorial", "alerta_bloco_risco", "motivo_alerta_bloco_risco", "penalizacao_watchlist_flexivel", "score_aderencia_regime", "motivo_aderencia_regime", "limite_peso_watchlist_flexivel_aplicado", "limite_quantidade_watchlist_flexivel_aplicado"])
    risk_merge_cols = [col for col in merge_cols if col == "ticker" or col not in preliminary.columns]
    full_analysis = preliminary.merge(risk_candidates_all[risk_merge_cols], on="ticker", how="left") if risk_merge_cols else preliminary.copy()
    score_export_cols = _existing_columns(risk_candidates_scored, ["ticker", "nota_final", "score_prioridade_otimizacao", "penalidade_prioridade_otimizacao"])
    score_export_cols = [col for col in score_export_cols if col == "ticker" or col not in full_analysis.columns]
    if len(score_export_cols) > 1:
        full_analysis = full_analysis.merge(risk_candidates_scored[score_export_cols], on="ticker", how="left")
    for frame in [preliminary, risk_candidates_all, optimization_full, optimization_audit, full_analysis, watchlist, portfolio]:
        if frame is not None:
            frame["status_carteira"] = portfolio_metrics.get("status_carteira", "")
            frame["justificativa_carteira"] = portfolio_metrics.get("justificativa_carteira", "")
            frame["data_formacao_carteira"] = temporal_context["data_formacao_carteira"].date().isoformat()
            frame["data_avaliacao_carteira"] = temporal_context["data_avaliacao_carteira"].date().isoformat()
            frame["data_limite_dados_selecao"] = temporal_context["data_limite_dados_selecao"].date().isoformat()
            frame["periodo_dados_selecao"] = temporal_context["periodo_dados_selecao"]
            for key, value in risk_window_info.items():
                frame[key] = value
            if "flag_watchlist" not in frame:
                frame["flag_watchlist"] = False
            if "flag_watchlist_na_carteira" not in frame:
                frame["flag_watchlist_na_carteira"] = False
            if "motivo_exclusao_por_timing" not in frame:
                frame["motivo_exclusao_por_timing"] = ""

    relative_strength_source = full_analysis.merge(risk_candidates_scored.reindex(columns=["ticker", "nota_final"]), on="ticker", how="left")
    relative_strength_table = relative_strength_ranking(relative_strength_source)
    alerts = build_alerts(full_analysis, portfolio_alerts)
    trading_days = settings["risk"]["trading_days_year"]
    monthly_days = int(temporal_context.get("pregoes_mes_referencia") or settings["risk"].get("trading_days_month", round(trading_days / 12)))
    daily_return = portfolio_metrics.get("retorno_carteira", np.nan)
    daily_risk = portfolio_metrics.get("risco_carteira", np.nan)
    portfolio_metrics["retorno_carteira_diario"] = daily_return
    portfolio_metrics["retorno_carteira_mensal"] = float((1 + daily_return) ** monthly_days - 1) if pd.notna(daily_return) else np.nan
    portfolio_metrics["retorno_carteira_anual"] = annualize_return(daily_return, trading_days)
    portfolio_metrics["retorno_anual"] = portfolio_metrics["retorno_carteira_anual"]
    portfolio_metrics["risco_carteira_diario"] = daily_risk
    portfolio_metrics["risco_carteira_mensal"] = float(daily_risk * np.sqrt(monthly_days)) if pd.notna(daily_risk) else np.nan
    portfolio_metrics["risco_carteira_anual"] = annualize_risk(daily_risk, trading_days)
    portfolio_metrics["risco_anual"] = portfolio_metrics["risco_carteira_anual"]
    portfolio_metrics["dias_uteis_mes_retorno"] = monthly_days
    portfolio_metrics["dias_uteis_ano_retorno"] = trading_days
    portfolio_metrics["calendario_mercado"] = temporal_context.get("calendario_mercado", "B3")
    portfolio_metrics["calendario_fonte"] = temporal_context.get("calendario_fonte", "")
    portfolio_metrics["calendario_status"] = temporal_context.get("calendario_status", "")
    portfolio_metrics["primeiro_pregao_mes"] = temporal_context.get("primeiro_pregao_mes", "")
    portfolio_metrics["ultimo_pregao_mes"] = temporal_context.get("ultimo_pregao_mes", "")
    portfolio_metrics["taxa_livre_risco_diaria"] = daily_risk_free_rate(settings)
    portfolio_metrics["log_path"] = str(log_path)
    portfolio_metrics["ativos_analisados"] = len(preliminary)
    portfolio_metrics["ativos_excluidos"] = int((full_analysis.get("categoria_elegibilidade", pd.Series("inelegivel", index=full_analysis.index)).fillna("inelegivel") == "inelegivel").sum())
    portfolio_metrics["ativos_elegiveis_total"] = portfolio_metrics.get("elegiveis_fortes", 0) + portfolio_metrics.get("elegiveis_moderados", 0)
    portfolio_metrics["universo_modo"] = settings.get("universe", {}).get("mode", "custom_csv")
    if not universe_summary.empty and {"metrica", "valor"}.issubset(universe_summary.columns):
        if not universe_summary.loc[universe_summary["metrica"] == "quantidade_ativos_coletados", "valor"].empty:
            portfolio_metrics["universo_ativos_coletados"] = int(universe_summary.loc[universe_summary["metrica"] == "quantidade_ativos_coletados", "valor"].iloc[0])
        if not universe_summary.loc[universe_summary["metrica"] == "quantidade_ativos_validados", "valor"].empty:
            portfolio_metrics["universo_ativos_validados"] = int(universe_summary.loc[universe_summary["metrica"] == "quantidade_ativos_validados", "valor"].iloc[0])

    performance_realizada = build_realized_performance(portfolio, prices, index_prices, settings, temporal_context)
    perf_summary = performance_realizada[performance_realizada["ticker"].eq("retorno_realizado_carteira")]
    if not perf_summary.empty:
        portfolio_metrics["retorno_realizado_carteira_periodo"] = perf_summary["retorno_realizado_periodo"].iloc[0]
        portfolio_metrics["retorno_realizado_ibov_periodo"] = perf_summary["retorno_ibov_periodo"].iloc[0]
        portfolio_metrics["alfa_realizado_vs_ibov"] = perf_summary["alfa_vs_ibov"].iloc[0]
    portfolio_metrics["performance_realizada_calculada"] = bool(not portfolio.empty and performance_realizada["tipo_linha"].eq("ativo").any())
    diagnostico_pos_selecao = build_post_selection_diagnosis(portfolio, performance_realizada, alerts, portfolio_metrics, temporal_context)

    regime_mercado_table = pd.DataFrame([
        {"campo": "mercado_classificacao", "valor": portfolio_metrics.get("mercado_classificacao", "")},
        {"campo": "subtipo_mercado_favoravel", "valor": portfolio_metrics.get("subtipo_mercado_favoravel", "")},
        {"campo": "mercado_favoravel_esticado", "valor": portfolio_metrics.get("mercado_favoravel_esticado", False)},
        {"campo": "mercado_favoravel_cansado", "valor": portfolio_metrics.get("mercado_favoravel_cansado", False)},
        {"campo": "motivo_subtipo_mercado_favoravel", "valor": portfolio_metrics.get("motivo_subtipo_mercado_favoravel", "")},
        {"campo": "rsi_ibov_data_base", "valor": portfolio_metrics.get("rsi_ibov_data_base", np.nan)},
        {"campo": "bollinger_ibov_data_base", "valor": portfolio_metrics.get("bollinger_ibov_data_base", "")},
        {"campo": "pct_ativos_positivos_1m", "valor": portfolio_metrics.get("pct_ativos_positivos_1m", np.nan)},
        {"campo": "ativos_bloqueados_forca_relativa_fraca", "valor": portfolio_metrics.get("ativos_bloqueados_forca_relativa_fraca", 0)},
        {"campo": "ativos_alerta_realizacao_pos_rali", "valor": portfolio_metrics.get("ativos_alerta_realizacao_pos_rali", 0)},
        {"campo": "ativos_alerta_beta_alto_mercado_esticado", "valor": portfolio_metrics.get("ativos_alerta_beta_alto_mercado_esticado", 0)},
        {"campo": "ativos_turnaround_especulativo", "valor": portfolio_metrics.get("ativos_turnaround_especulativo", 0)},
    ])


    data_base_table = pd.DataFrame([
        {"campo": "mes_referencia", "valor": portfolio_metrics["mes_referencia"]},
        {"campo": "data_formacao_carteira", "valor": portfolio_metrics["data_formacao_carteira"]},
        {"campo": "data_limite_dados_selecao", "valor": portfolio_metrics["data_limite_dados_selecao"]},
        {"campo": "data_inicio_performance", "valor": portfolio_metrics["data_inicio_performance"]},
        {"campo": "data_avaliacao_carteira", "valor": portfolio_metrics["data_avaliacao_carteira"]},
        {"campo": "periodo_dados_selecao", "valor": portfolio_metrics["periodo_dados_selecao"]},
        {"campo": "periodo_avaliacao_performance", "valor": portfolio_metrics["periodo_avaliacao_performance"]},
        {"campo": "sem_look_ahead_bias", "valor": portfolio_metrics["sem_look_ahead_bias"]},
        {"campo": "calendario_mercado", "valor": portfolio_metrics["calendario_mercado"]},
        {"campo": "calendario_fonte", "valor": portfolio_metrics["calendario_fonte"]},
        {"campo": "calendario_status", "valor": portfolio_metrics["calendario_status"]},
        {"campo": "primeiro_pregao_mes", "valor": portfolio_metrics["primeiro_pregao_mes"]},
        {"campo": "ultimo_pregao_mes", "valor": portfolio_metrics["ultimo_pregao_mes"]},
        {"campo": "fundamentos_point_in_time", "valor": portfolio_metrics["fundamentos_point_in_time"]},
        {"campo": "observacao_execucao", "valor": portfolio_metrics["observacao_execucao"]},
        {"campo": "janela_risco_inicio", "valor": portfolio_metrics["janela_risco_inicio"]},
        {"campo": "janela_risco_fim", "valor": portfolio_metrics["janela_risco_fim"]},
        {"campo": "janela_risco_meses", "valor": portfolio_metrics["janela_risco_meses"]},
        {"campo": "periodicidade_risco", "valor": portfolio_metrics["periodicidade_risco"]},
        {"campo": "tipo_retorno_risco", "valor": portfolio_metrics["tipo_retorno_risco"]},
        {"campo": "dias_uteis_mes_retorno", "valor": portfolio_metrics["dias_uteis_mes_retorno"]},
        {"campo": "dias_uteis_ano_retorno", "valor": portfolio_metrics["dias_uteis_ano_retorno"]},
        {"campo": "quantidade_observacoes_risco", "valor": portfolio_metrics["quantidade_observacoes_risco"]},
    ])
    market_diagnosis = build_market_diagnosis(preliminary, sector_indexes, selection_index_prices, portfolio_metrics, comparison, settings)
    validation = validation_summary(portfolio, portfolio_metrics, settings, portfolio_alerts)
    sources = pd.concat([price_log, index_log, fundamentals_log], ignore_index=True)
    collection_log = sources.copy()
    optimization = optimization_audit.copy()

    prelim_cols = list(full_analysis.columns)
    risk_cols = _existing_columns(optimization_full, ["ticker", "nome", "setor", "nota preliminar", "tipo_timing", "retorno_medio", "retorno_acumulado_1m", "retorno_acumulado_4m", "desvio_padrao", "variancia", "cv", "beta", "correlacao_ibov", "correlacao_media_ativos", "status_para_risco", "categoria_elegibilidade", "motivo_status_para_risco", "motivo_exclusao", "bloqueado_otimizacao", "motivo_bloqueio_otimizacao", "tipo_bloqueio_otimizacao", "alertas_nao_bloqueantes", "penalizacoes_otimizacao", "score_prioridade_otimizacao", "liberado_para_otimizacao", "tipo_watchlist", "motivo_tipo_watchlist", "watchlist_bloqueia_otimizacao", "watchlist_de_virada", "alerta_sinal_tardio", "motivos_alerta_sinal_tardio", "qualidade_do_timing", "decisao de entrada na carteira", "peso_final", "janela_risco_inicio", "janela_risco_fim", "janela_risco_meses", "periodicidade_risco", "tipo_retorno_risco", "quantidade_observacoes_risco", "status_carteira", "justificativa_carteira", "flag_watchlist", "flag_watchlist_na_carteira", "motivo_watchlist", "tipo_watchlist", "motivo_tipo_watchlist", "watchlist_bloqueia_otimizacao", "watchlist_de_virada", "alerta_sinal_tardio", "motivos_alerta_sinal_tardio", "qualidade_do_timing", "subtipo_mercado_favoravel", "mercado_favoravel_esticado", "mercado_favoravel_cansado", "motivo_subtipo_mercado_favoravel", "penalizacao_forca_relativa_fraca", "bloqueio_forca_relativa_fraca", "motivo_bloqueio_forca_relativa", "penalizacao_retorno_1m_relativo_negativo", "penalizacao_retorno_1m_relativo_negativo_forte", "bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado", "alerta_beta_alto_mercado_esticado", "penalizacao_beta_alto_mercado_esticado", "peso_maximo_beta_alto_mercado_esticado", "alerta_realizacao_pos_rali", "motivos_alerta_realizacao_pos_rali", "penalizacao_realizacao_pos_rali", "perfil_risco_empresa", "peso_maximo_turnaround_especulativo", "peso_maximo_timing_com_alerta", "motivo_peso_maximo_reduzido", "regime_mercado_data_base", "peso_maximo_permitido_ativo", "subtipo_mercado_favoravel", "mercado_favoravel_esticado", "mercado_favoravel_cansado", "motivo_subtipo_mercado_favoravel", "penalizacao_forca_relativa_fraca", "bloqueio_forca_relativa_fraca", "motivo_bloqueio_forca_relativa", "penalizacao_retorno_1m_relativo_negativo", "penalizacao_retorno_1m_relativo_negativo_forte", "bloqueio_retorno_1m_relativo_muito_fraco_mercado_esticado", "alerta_beta_alto_mercado_esticado", "penalizacao_beta_alto_mercado_esticado", "peso_maximo_beta_alto_mercado_esticado", "alerta_realizacao_pos_rali", "motivos_alerta_realizacao_pos_rali", "penalizacao_realizacao_pos_rali", "perfil_risco_empresa", "peso_maximo_turnaround_especulativo", "peso_maximo_timing_com_alerta", "motivo_peso_maximo_reduzido", "grupo_economico_ou_bloco_risco", "peso_setor", "peso_bloco_risco", "alerta_concentracao_setorial", "motivo_concentracao_setorial", "alerta_bloco_risco", "motivo_alerta_bloco_risco", "penalizacao_beta_negativo_mercado_favoravel", "penalizacao_beta_muito_baixo_mercado_favoravel", "penalizacao_correlacao_negativa_mercado_favoravel", "penalizacao_correlacao_muito_baixa_mercado_favoravel", "bloqueio_aderencia_regime", "motivo_bloqueio_aderencia_regime", "beta_minimo_exigido_regime", "correlacao_minima_exigida_regime", "grupo_economico_ou_bloco_risco", "peso_setor", "peso_bloco_risco", "alerta_concentracao_setorial", "motivo_concentracao_setorial", "alerta_bloco_risco", "motivo_alerta_bloco_risco", "penalizacao_watchlist_flexivel", "score_aderencia_regime", "motivo_aderencia_regime", "limite_peso_watchlist_flexivel_aplicado", "limite_quantidade_watchlist_flexivel_aplicado", "motivo_exclusao_por_timing"])
    watchlist_cols = _existing_columns(watchlist, ["ticker", "nome", "setor", "nota preliminar", "tipo_timing", "sinal_timing", "justificativa_timing", "tipo_watchlist", "motivo_tipo_watchlist", "watchlist_bloqueia_otimizacao", "watchlist_de_virada", "alerta_sinal_tardio", "motivos_alerta_sinal_tardio", "qualidade_do_timing", "motivo_watchlist", "retorno_1m_relativo_ibov", "retorno_4m_relativo_ibov", "retorno_ytd_relativo_ibov", "rsi", "bollinger_status", "tendencia", "roe", "roic", "margem_bruta", "pl_atual"])
    tables = {
        "Resumo da Carteira": portfolio,
        "Validacao Final": validation,
        "Data Base Carteira": data_base_table,
        "Regime Mercado": regime_mercado_table,
        "Performance Realizada": performance_realizada,
        "Diagnostico Pos Selecao": diagnostico_pos_selecao,
        "Serie Historica Risco": serie_historica_risco,
        "Auditoria Calculo Risco": auditoria_calculo_risco,
        "Auditoria Beta Correlacao": auditoria_beta_correlacao,
        "Universo de Ativos": pd.concat([universe_summary, universe_frame], ignore_index=True),
        "Timing de Entrada": timing_summary,
        "Watchlist": watchlist.reindex(columns=watchlist_cols) if watchlist_cols else watchlist,
        "Diagnostico de Mercado": market_diagnosis,
        "Diagnostico Timing Watchlist": timing_watchlist_diagnosis,
        "Analise Preliminar": full_analysis.reindex(columns=prelim_cols),
        "Resumo Analise Preliminar": preliminary_summary,
        "Diagnostico Setorial Mercado": sector_market,
        "Candidatas Risco": optimization_full.reindex(columns=risk_cols) if risk_cols else optimization_full,
        "Auditoria Bloqueios Otimizacao": optimization_block_audit,
        "Parametros Hard Filter": hard_filter_settings,
        "Valor Mercado Participacao": market_participation,
        "Ranking das Acoes": risk_candidates_scored,
        "Forca Relativa": relative_strength_table,
        "Auditoria Tecnica": technical_audit,
        "Log RSI PRIO3": prio3_rsi_audit,
        "Indicadores Tecnicos": tech,
        "Indicadores Fundamentalistas": fundamentals,
        "Analise Setorial": sector_indexes,
        "Matriz de Correlacao": corr.reset_index().rename(columns={"index": "ticker"}) if not corr.empty else corr,
        "Matriz de Covariancia": cov.reset_index().rename(columns={"index": "ticker"}) if not cov.empty else cov,
        "Comparativo Carteiras": comparison,
        "Otimizacao": optimization,
        "Ativos Excluidos": full_analysis[full_analysis.get("categoria_elegibilidade", pd.Series("inelegivel", index=full_analysis.index)).fillna("inelegivel") == "inelegivel"],
        "Alertas": alerts,
        "Fontes de Dados": sources,
        "Log de Coleta": collection_log,
    }
    excel_path = write_excel(tables, year_month)
    pdf_path = write_pdf(portfolio, portfolio_metrics, alerts, year_month, universe_summary, optimization_full, comparison, market_diagnosis, timing_summary, watchlist, relative_strength_table, sector_market, optimization_block_audit, market_participation, hard_filter_settings, performance_realizada, diagnostico_pos_selecao)
    beta_audit_assets = auditoria_beta_correlacao[~auditoria_beta_correlacao["ticker"].astype(str).eq(ibov_ticker)].copy() if not auditoria_beta_correlacao.empty else pd.DataFrame()
    beta_divergent = beta_audit_assets[~beta_audit_assets.get("beta_bate_com_robo", pd.Series(dtype=str)).eq("sim")]["ticker"].astype(str).tolist() if not beta_audit_assets.empty else []
    corr_divergent = beta_audit_assets[~beta_audit_assets.get("correlacao_bate_com_robo", pd.Series(dtype=str)).eq("sim")]["ticker"].astype(str).tolist() if not beta_audit_assets.empty else []
    ibov_audit = auditoria_beta_correlacao[auditoria_beta_correlacao["ticker"].astype(str).eq(ibov_ticker)].copy() if not auditoria_beta_correlacao.empty else pd.DataFrame()
    LOGGER.info(
        "Auditoria beta/correlacao: janela_inicio=%s janela_fim=%s benchmark=%s fonte=%s tipo_preco=%s observacoes_ibov=%s ativos_auditados=%s beta_ibov=%s correlacao_ibov=%s beta_batendo=%s beta_divergencias=%s beta_divergentes=%s correlacao_batendo=%s correlacao_divergencias=%s correlacao_divergentes=%s",
        risk_window_info.get("janela_risco_inicio"),
        risk_window_info.get("janela_risco_fim"),
        ibov_ticker,
        settings.get("data", {}).get("price_source_primary", "yfinance"),
        _risk_price_type(settings),
        int(ibov_audit["quantidade_observacoes_alinhadas"].iloc[0]) if not ibov_audit.empty else 0,
        len(beta_audit_assets),
        float(ibov_audit["beta_calculado_auditoria"].iloc[0]) if not ibov_audit.empty and pd.notna(ibov_audit["beta_calculado_auditoria"].iloc[0]) else np.nan,
        float(ibov_audit["correlacao_calculada_auditoria"].iloc[0]) if not ibov_audit.empty and pd.notna(ibov_audit["correlacao_calculada_auditoria"].iloc[0]) else np.nan,
        int(beta_audit_assets["beta_bate_com_robo"].eq("sim").sum()) if not beta_audit_assets.empty else 0,
        len(beta_divergent),
        ", ".join(beta_divergent),
        int(beta_audit_assets["correlacao_bate_com_robo"].eq("sim").sum()) if not beta_audit_assets.empty else 0,
        len(corr_divergent),
        ", ".join(corr_divergent),
    )
    LOGGER.info("Arquivos gerados: %s, %s", excel_path, pdf_path)
    LOGGER.info("Resumo: analisados=%s preliminares=%s risco=%s liberados=%s selecionados=%s peso_zero=%s valida=%s", len(preliminary), len(pre_risk_candidates), len(risk_candidates_all), len(risk_candidates_scored), len(portfolio), portfolio_metrics.get("acoes_peso_zero", 0), portfolio_metrics.get("carteira_valida", False))
    comparativo_cols = [col for col in ["quantidade de acoes", "CV", "beta", "correlacao_carteira_ibov", "score_aderencia_regime", "maior_peso_setorial", "setor_mais_concentrado", "quantidade_blocos_risco_duplicados", "carteira_elegivel_para_escolha_final", "motivo de escolha ou rejeicao"] if col in comparison]
    LOGGER.info("Comparativo regime: %s", comparison[comparativo_cols].to_dict("records") if comparativo_cols and not comparison.empty else [])
    bloqueados_regime = risk_candidates_all[risk_candidates_all.get("bloqueio_aderencia_regime", pd.Series(False, index=risk_candidates_all.index)).fillna(False)].copy()
    bloqueio_cols = [col for col in ["ticker", "beta", "correlacao_ibov", "tipo_watchlist", "grupo_economico_ou_bloco_risco", "motivo_bloqueio_aderencia_regime"] if col in bloqueados_regime]
    bloqueados_negativos = bloqueados_regime[bloqueados_regime.get("motivo_bloqueio_aderencia_regime", pd.Series("", index=bloqueados_regime.index)).astype(str).str.contains("negativo", case=False, na=False)] if not bloqueados_regime.empty else pd.DataFrame()
    bloqueados_muito_baixos = bloqueados_regime[bloqueados_regime.get("motivo_bloqueio_aderencia_regime", pd.Series("", index=bloqueados_regime.index)).astype(str).str.contains("muito_baixos", case=False, na=False)] if not bloqueados_regime.empty else pd.DataFrame()
    LOGGER.info("Ativos bloqueados por aderencia ao regime: %s", bloqueados_regime[bloqueio_cols].to_dict("records") if bloqueio_cols and not bloqueados_regime.empty else [])
    LOGGER.info("Ativos bloqueados por beta/correlacao negativa em mercado favoravel: %s", bloqueados_negativos[bloqueio_cols].to_dict("records") if bloqueio_cols and not bloqueados_negativos.empty else [])
    LOGGER.info("Ativos bloqueados por beta/correlacao muito baixos em mercado favoravel: %s", bloqueados_muito_baixos[bloqueio_cols].to_dict("records") if bloqueio_cols and not bloqueados_muito_baixos.empty else [])
    LOGGER.info(
        "Carteira escolhida: motivo=%s ativos=%s pesos=%s retorno=%s risco=%s CV=%s beta=%s Sharpe=%s maior_peso_setorial=%s setor_concentrado=%s blocos_duplicados=%s perf_carteira=%s perf_ibov=%s alfa=%s",
        portfolio_metrics.get("motivo_escolha_final", portfolio_metrics.get("motivo_escolha_carteira", "")),
        portfolio_metrics.get("tickers_selecionados", ""),
        portfolio_metrics.get("pesos", ""),
        portfolio_metrics.get("retorno_carteira", np.nan),
        portfolio_metrics.get("risco_carteira", np.nan),
        portfolio_metrics.get("cv_carteira", np.nan),
        portfolio_metrics.get("beta_carteira", np.nan),
        portfolio_metrics.get("sharpe_diario", np.nan),
        portfolio_metrics.get("maior_peso_setorial", portfolio_metrics.get("maior_concentracao_setorial", np.nan)),
        portfolio_metrics.get("setor_mais_concentrado", portfolio_metrics.get("setor_concentrado", "")),
        portfolio_metrics.get("blocos_risco_duplicados", ""),
        portfolio_metrics.get("retorno_realizado_carteira_periodo", np.nan),
        portfolio_metrics.get("retorno_realizado_ibov_periodo", np.nan),
        portfolio_metrics.get("alfa_realizado_vs_ibov", np.nan),
    )


if __name__ == "__main__":
    main()











































