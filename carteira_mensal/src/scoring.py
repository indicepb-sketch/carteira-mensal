from __future__ import annotations

import numpy as np
import pandas as pd


def _timing_cfg(settings: dict | None) -> dict:
    cfg = (settings or {}).get("technical_timing", {})
    return {
        "ideal_rsi_min": float(cfg.get("ideal_rsi_min", 50)),
        "ideal_rsi_max": float(cfg.get("ideal_rsi_max", 65)),
        "attention_rsi_max": float(cfg.get("attention_rsi_max", 70)),
        "overbought_rsi": float(cfg.get("overbought_rsi", 70)),
        "extreme_overbought_rsi": float(cfg.get("extreme_overbought_rsi", 75)),
    }


def score_technical(row: pd.Series) -> int:
    score = 0
    score += 8 if row.get("mm9", np.nan) > row.get("mm21", np.nan) else 0
    score += 8 if row.get("mm50", np.nan) > row.get("mm100", np.nan) else 0
    score += 8 if row.get("preco_atual", np.nan) > row.get("mm50", np.nan) else 0
    score += 6 if row.get("retorno_ytd", np.nan) > 0 else 0
    return min(score, 30)


def score_timing(row: pd.Series, settings: dict | None = None) -> int:
    tipo = row.get("tipo_timing", "")
    if tipo == "timing_favoravel_tendencia":
        return 20
    if tipo in {"timing_favoravel_com_alerta", "timing_atencao_banda_superior"}:
        return 16
    if tipo == "timing_reversao_oportunidade":
        return 14
    if tipo == "timing_esticado_sobrecompra":
        return 0
    if tipo in {"timing_fraqueza_sem_confirmacao", "timing_reversao_nao_aprovada"}:
        return 2

    cfg = _timing_cfg(settings)
    rsi = row.get("rsi", np.nan)
    score = 0
    if pd.isna(rsi):
        score += 0
    elif cfg["ideal_rsi_min"] <= rsi <= cfg["ideal_rsi_max"]:
        score += 10
    elif cfg["ideal_rsi_max"] < rsi <= cfg["attention_rsi_max"]:
        score += 6
    elif rsi > cfg["overbought_rsi"]:
        score += 1
    elif 30 <= rsi < cfg["ideal_rsi_min"]:
        score += 3
    else:
        score += 1

    boll = row.get("bollinger_status", "")
    if boll == "favoravel":
        score += 10
    elif boll == "oportunidade":
        score += 8
    elif boll == "sobrecompra":
        score += 0
    elif boll == "alerta negativo":
        score += 0
    return min(score, 20)


def score_fundamentals(row: pd.Series) -> int:
    score = 0
    roe = row.get("roe", np.nan)
    roic = row.get("roic", np.nan)
    margin = row.get("margem_bruta", np.nan)
    pl = row.get("pl_atual", np.nan)
    if not pd.isna(roe):
        score += 7 if roe > 0.20 else 4 if roe >= 0.10 else 0
    if not pd.isna(roic):
        score += 7 if roic > 0.15 else 4 if roic >= 0.08 else 0
    if not pd.isna(margin) and margin > 0:
        score += 3
    if not pd.isna(pl) and pl > 0:
        score += 3
    return min(score, 20)


def score_sector(row: pd.Series) -> int:
    trend = row.get("tendencia_setorial", "neutro")
    if trend == "alta":
        return 10
    if trend == "neutro":
        return 5
    return 0


def cv_penalty(row: pd.Series, settings: dict) -> int:
    cv = row.get("cv", np.nan)
    if pd.isna(cv) or cv <= settings["risk"]["cv_limit"]:
        return 0
    levels = settings["risk"].get("cv_relaxation_levels", [settings["risk"]["cv_limit"], 25, 50])
    if cv <= levels[1]:
        return 5
    if cv <= levels[2]:
        return 10
    return 18


def timing_penalty(row: pd.Series, settings: dict) -> int:
    tipo = row.get("tipo_timing", "")
    rsi = row.get("rsi", np.nan)
    cfg = _timing_cfg(settings)
    if tipo == "timing_esticado_sobrecompra":
        return 15
    if not pd.isna(rsi) and rsi > cfg["attention_rsi_max"]:
        return 5
    return 0



def optimization_priority_penalty(row: pd.Series) -> int:
    penalties = str(row.get("penalizacoes_otimizacao", ""))
    score = 0
    weights = {
        "penalizacao_watchlist_flexivel": 4,
        "penalizacao_timing_com_alerta": 3,
        "penalizacao_sinal_tardio": 8,
        "penalizacao_timing_tardio": 10,
        "penalizacao_cv_individual_alto": 5,
        "penalizacao_beta_alto": 3,
        "penalizacao_correlacao_alta": 3,
        "penalizacao_beta_negativo_mercado_favoravel": 8,
        "penalizacao_beta_muito_baixo_mercado_favoravel": 6,
        "penalizacao_correlacao_negativa_mercado_favoravel": 8,
        "penalizacao_correlacao_muito_baixa_mercado_favoravel": 6,
        "penalizacao_beta_alto_mercado_fraco": 6,
        "penalizacao_correlacao_alta_mercado_fraco": 6,
    }
    for name, value in weights.items():
        if name in penalties:
            score += value
    return score
def score_risk(row: pd.Series, settings: dict) -> int:
    score = 0
    score += 5 if row.get("retorno_medio", np.nan) > 0 else 0
    score += 5 if row.get("desvio_padrao", np.nan) < settings["risk"]["std_limit_daily"] else 0
    cv = row.get("cv", np.nan)
    score += 5 if not pd.isna(cv) and 0 <= cv <= settings["risk"]["cv_limit"] else 0
    score += 3 if row.get("beta", np.nan) <= settings["risk"]["beta_alert"] else 0
    score += 2 if row.get("correlacao_ibov", np.nan) <= settings["risk"]["correlation_alert"] else 0
    return min(score, 20)


def score_assets(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    scored = frame.copy()
    scored["score_tendencia"] = scored.apply(score_technical, axis=1)
    scored["score_timing"] = scored.apply(lambda row: score_timing(row, settings), axis=1)
    scored["score_fundamentos"] = scored.apply(score_fundamentals, axis=1)
    scored["score_setor"] = scored.apply(score_sector, axis=1)
    scored["score_risco"] = scored.apply(lambda row: score_risk(row, settings), axis=1)
    scored["penalidade_cv"] = scored.apply(lambda row: cv_penalty(row, settings), axis=1)
    scored["penalidade_timing"] = scored.apply(lambda row: timing_penalty(row, settings), axis=1)
    scored["nota_final"] = scored[["score_tendencia", "score_timing", "score_fundamentos", "score_setor", "score_risco"]].sum(axis=1) - scored["penalidade_cv"] - scored["penalidade_timing"]
    scored["nota_final"] = scored["nota_final"].clip(lower=0, upper=100)
    scored["penalidade_prioridade_otimizacao"] = scored.apply(optimization_priority_penalty, axis=1)
    scored["score_prioridade_otimizacao"] = (scored["nota_final"] - scored["penalidade_prioridade_otimizacao"]).clip(lower=0, upper=100)
    return scored.sort_values(["score_prioridade_otimizacao", "nota_final"], ascending=[False, False])
