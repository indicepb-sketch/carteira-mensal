from __future__ import annotations

import json
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.optimize import linprog, minimize

from risk_analysis import portfolio_beta, portfolio_return, portfolio_risk, sharpe_ratio

def _periods(settings: dict) -> tuple[int, int]:
    trading_days = int(settings.get("risk", {}).get("trading_days_year", 252))
    monthly_days = int(settings.get("_runtime_trading_days_month") or settings.get("risk", {}).get("trading_days_month", round(trading_days / 12)))
    return monthly_days, trading_days


def _compound_return(daily_return: float, periods: int) -> float:
    if pd.isna(daily_return):
        return np.nan
    return float((1 + daily_return) ** periods - 1)


def _scale_risk(daily_risk: float, periods: int) -> float:
    if pd.isna(daily_risk):
        return np.nan
    return float(daily_risk * np.sqrt(periods))


def _portfolio_config(settings: dict) -> dict:
    portfolio = settings.get("portfolio", {})
    return {
        "min_weight": float(portfolio.get("min_weight", 0.05)),
        "max_weight": float(portfolio.get("max_weight", 0.20)),
        "candidate_counts": [int(v) for v in portfolio.get("candidate_counts", [])],
        "diversification_preferred_counts": [int(v) for v in portfolio.get("diversification_preferred_counts", [6, 8])],
        "tolerancia_cv_para_maior_diversificacao": float(portfolio.get("tolerancia_cv_para_maior_diversificacao", 0.15)),
        "max_ativos_watchlist_flexivel": int(portfolio.get("max_ativos_watchlist_flexivel", 2)),
        "max_peso_total_watchlist_flexivel": float(portfolio.get("max_peso_total_watchlist_flexivel", 0.35)),
        "peso_maximo_individual_watchlist_flexivel": float(portfolio.get("peso_maximo_individual_watchlist_flexivel", 0.15)),
        "peso_maximo_ativo_com_beta_negativo_em_mercado_favoravel": float(portfolio.get("peso_maximo_ativo_com_beta_negativo_em_mercado_favoravel", 0.10)),
        "peso_maximo_ativo_com_correlacao_baixa_em_mercado_favoravel": float(portfolio.get("peso_maximo_ativo_com_correlacao_baixa_em_mercado_favoravel", 0.10)),
        "score_aderencia_regime_minimo": float(portfolio.get("score_aderencia_regime_minimo", 70)),
        "beta_carteira_minimo_mercado_favoravel": float(portfolio.get("beta_carteira_minimo_mercado_favoravel", portfolio.get("beta_carteira_minimo_preferencial_mercado_favoravel", 0.75))),
        "correlacao_carteira_ibov_minima_mercado_favoravel": float(portfolio.get("correlacao_carteira_ibov_minima_mercado_favoravel", portfolio.get("correlacao_carteira_ibov_minima_preferencial_mercado_favoravel", 0.45))),
        "bloquear_baixa_aderencia_em_mercado_favoravel": bool(portfolio.get("bloquear_baixa_aderencia_em_mercado_favoravel", True)),
        "permitir_beta_negativo_em_mercado_favoravel": bool(portfolio.get("permitir_beta_negativo_em_mercado_favoravel", False)),
        "bloquear_watchlist_flexivel_baixa_aderencia_mercado_favoravel": bool(portfolio.get("bloquear_watchlist_flexivel_baixa_aderencia_mercado_favoravel", True)),
        "beta_minimo_watchlist_flexivel_mercado_favoravel": float(portfolio.get("beta_minimo_watchlist_flexivel_mercado_favoravel", portfolio.get("beta_muito_baixo_mercado_favoravel", 0.30))),
        "correlacao_minima_watchlist_flexivel_mercado_favoravel": float(portfolio.get("correlacao_minima_watchlist_flexivel_mercado_favoravel", portfolio.get("correlacao_muito_baixa_mercado_favoravel", 0.20))),
        "peso_maximo_setor_preferencial": float(portfolio.get("peso_maximo_setor_preferencial", portfolio.get("preferred_max_sector_weight", portfolio.get("max_sector_weight", 0.30)))),
        "peso_maximo_setor_tolerado": float(portfolio.get("peso_maximo_setor_tolerado", portfolio.get("max_sector_weight", portfolio.get("preferred_max_sector_weight", 0.35)))),
        "peso_maximo_setor_excepcional": float(portfolio.get("peso_maximo_setor_excepcional", portfolio.get("hard_max_sector_weight", portfolio.get("max_sector_weight", 0.40)))),
        "permitir_peso_setor_excepcional": bool(portfolio.get("permitir_peso_setor_excepcional", True)),
        "peso_maximo_bloco_risco_preferencial": float(portfolio.get("peso_maximo_bloco_risco_preferencial", 0.20)),
        "peso_maximo_bloco_risco_tolerado": float(portfolio.get("peso_maximo_bloco_risco_tolerado", 0.25)),
        "beta_carteira_minimo_preferencial_mercado_favoravel": float(portfolio.get("beta_carteira_minimo_preferencial_mercado_favoravel", portfolio.get("beta_carteira_minimo_mercado_favoravel", 0.75))),
        "correlacao_carteira_ibov_minima_preferencial_mercado_favoravel": float(portfolio.get("correlacao_carteira_ibov_minima_preferencial_mercado_favoravel", portfolio.get("correlacao_carteira_ibov_minima_mercado_favoravel", 0.45))),
        "beta_muito_baixo_mercado_favoravel": float(portfolio.get("beta_muito_baixo_mercado_favoravel", 0.30)),
        "correlacao_muito_baixa_mercado_favoravel": float(portfolio.get("correlacao_muito_baixa_mercado_favoravel", 0.20)),
        "max_assets_per_sector": int(portfolio.get("max_assets_per_sector", 999)),
        "preferred_max_sector_weight": float(portfolio.get("peso_maximo_setor_preferencial", portfolio.get("preferred_max_sector_weight", portfolio.get("max_sector_weight", 0.30)))),
        "hard_max_sector_weight": float(portfolio.get("peso_maximo_setor_excepcional", portfolio.get("hard_max_sector_weight", portfolio.get("max_sector_weight", 0.40)))),
        "max_reversal_assets": int(portfolio.get("max_reversal_assets", 999)),
        "max_reversal_weight": float(portfolio.get("max_reversal_weight", 1.0)),
        "peso_maximo_timing_com_alerta": float(portfolio.get("peso_maximo_timing_com_alerta", 0.10)),
        "peso_maximo_timing_tardio": float(portfolio.get("peso_maximo_timing_tardio", 0.05)),
        "peso_maximo_turnaround_especulativo": float(portfolio.get("peso_maximo_turnaround_especulativo", 0.05)),
    }


def _minimum_assets_required(candidates_count: int, settings: dict) -> int:
    cfg = _portfolio_config(settings)
    by_weight = int(np.ceil(1 / cfg["max_weight"] - 1e-12))
    configured = int(settings.get("strategy", {}).get("min_assets", by_weight))
    return max(by_weight, configured if candidates_count >= configured else by_weight)


def _empty_portfolio(candidates: pd.DataFrame, metrics: dict) -> tuple[pd.DataFrame, dict]:
    portfolio = candidates.head(0).copy()
    portfolio["peso_recomendado"] = pd.Series(dtype=float)
    return portfolio, metrics


def _base_metrics(candidates_count: int, status: str, valid: bool, violations: list[str]) -> dict:
    return {
        "status_carteira": status,
        "carteira_valida": valid,
        "ativos_elegiveis": candidates_count,
        "restricoes_violadas": "; ".join(violations),
        "retorno_carteira": np.nan,
        "risco_carteira": np.nan,
        "cv_carteira": np.nan,
        "beta_carteira": np.nan,
        "sharpe_diario": np.nan,
        "status_otimizacao": status,
        "comparativo_carteiras": pd.DataFrame(),
    }


def _failure_bucket(reason: str) -> str:
    normalized = str(reason or "").lower()
    if "bloco" in normalized or "petr3/petr4" in normalized or "gerdau_goau" in normalized or "petrobras" in normalized:
        return "bloco_risco"
    if "setor" in normalized:
        return "setor"
    if "peso" in normalized or "limites individuais" in normalized or "soma dos pesos" in normalized:
        return "peso_individual"
    if "regime" in normalized or "aderencia" in normalized or "beta" in normalized or "correlacao" in normalized:
        return "regime"
    if "watchlist" in normalized:
        return "watchlist_flexivel"
    return "outros"


def _add_failures_to_histogram(histogram: dict[str, int], errors: list[str]) -> None:
    if not errors:
        histogram["outros"] = histogram.get("outros", 0) + 1
        return
    for error in errors:
        bucket = _failure_bucket(error)
        histogram[bucket] = histogram.get(bucket, 0) + 1


def _pool_context(pool: pd.DataFrame, covariance: pd.DataFrame, settings: dict) -> dict:
    cfg = _portfolio_config(settings)
    tickers = pool["ticker"].astype(str).tolist()
    indexed = pool.set_index("ticker")
    return {
        "tickers": tickers,
        "covariance": covariance.reindex(index=tickers, columns=tickers).fillna(0).to_numpy(float),
        "returns": indexed.loc[tickers, "retorno_medio"].to_numpy(float),
        "betas": indexed.loc[tickers, "beta"].fillna(1.0).to_numpy(float) if "beta" in indexed else np.ones(len(tickers)),
        "correlations": indexed.loc[tickers, "correlacao_ibov"].fillna(0.0).to_numpy(float) if "correlacao_ibov" in indexed else np.zeros(len(tickers)),
        "sectors": indexed.loc[tickers, "setor"].fillna("Outros").astype(str).to_numpy(object) if "setor" in indexed else np.array(["Outros"] * len(tickers), dtype=object),
        "blocks": _risk_block_series(pool, tickers).reindex(tickers).fillna("").astype(str).to_numpy(object),
        "timing_types": _timing_series(pool, tickers).reindex(tickers).fillna("").astype(str).to_numpy(object),
        "watchlist_types": indexed.get("tipo_watchlist", pd.Series("", index=tickers)).reindex(tickers).fillna("").astype(str).to_numpy(object),
        "weight_caps": _asset_weight_caps(pool, settings).reindex(tickers).fillna(cfg["max_weight"]).to_numpy(float),
    }


def _slice_pool_context(context: dict, indexes: tuple[int, ...]) -> dict:
    idx = np.fromiter(indexes, dtype=int)
    return {
        "tickers": [context["tickers"][i] for i in indexes],
        "covariance": context["covariance"][np.ix_(idx, idx)],
        "returns": context["returns"][idx],
        "betas": context["betas"][idx],
        "correlations": context["correlations"][idx],
        "sectors": context["sectors"][idx],
        "blocks": context["blocks"][idx],
        "timing_types": context["timing_types"][idx],
        "watchlist_types": context["watchlist_types"][idx],
        "weight_caps": context["weight_caps"][idx],
    }


def _combo_precheck_errors(context: dict, indexes: tuple[int, ...], settings: dict, count: int) -> list[str]:
    cfg = _portfolio_config(settings)
    idx = np.fromiter(indexes, dtype=int)
    min_weight = cfg["min_weight"]
    tickers = [context["tickers"][i] for i in indexes]
    caps = context["weight_caps"][idx]
    if float(np.sum(caps)) < 1 - 1e-12:
        return ["limites individuais de peso tornam a carteira inviavel"]
    if count * min_weight > 1 + 1e-12:
        return ["numero de ativos excede limite imposto pelo peso minimo"]

    sectors = context["sectors"][idx]
    sector_values, sector_counts = np.unique(sectors, return_counts=True)
    exceeded = [(str(sector), int(qty)) for sector, qty in zip(sector_values, sector_counts) if qty > cfg["max_assets_per_sector"]]
    if exceeded:
        return ["maximo de acoes por setor violado: " + "; ".join(f"{sector} com {qty} acoes" for sector, qty in exceeded)]
    if len(sector_values) * cfg["peso_maximo_setor_excepcional"] < 1 - 1e-12:
        return ["setores insuficientes para limite setorial"]
    if any(int(qty) * min_weight > cfg["peso_maximo_setor_excepcional"] + 1e-12 for qty in sector_counts):
        return ["peso minimo dos ativos excede limite setorial"]

    timing = context["timing_types"][idx]
    reversal_count = int(np.sum(timing == "timing_reversao_oportunidade"))
    if reversal_count > cfg["max_reversal_assets"]:
        return [f"maximo de acoes de reversao violado: {reversal_count} reversoes; maximo permitido: {cfg['max_reversal_assets']}"]
    if reversal_count and reversal_count * min_weight > cfg["max_reversal_weight"] + 1e-12:
        return ["peso minimo das reversoes excede limite de reversao"]

    watchlist = context["watchlist_types"][idx]
    flex_count = int(np.sum(watchlist == "watchlist_flexivel"))
    if flex_count > cfg["max_ativos_watchlist_flexivel"]:
        return ["maximo de ativos em watchlist flexivel excedido"]
    if flex_count and flex_count * min_weight > cfg["max_peso_total_watchlist_flexivel"] + 1e-12:
        return ["peso minimo da watchlist flexivel excede limite total"]

    blocks = context["blocks"][idx]
    violations = []
    for block in dict.fromkeys(blocks.tolist()):
        block_idx = [i for i, value in enumerate(blocks) if value == block]
        if len(block_idx) <= 1:
            continue
        block_tickers = [tickers[i] for i in block_idx]
        if block == "PETROBRAS":
            violations.append(f"{block} com {len(block_tickers)} ativos ({', '.join(block_tickers)}); PETR3/PETR4 nao podem entrar juntos")
        elif count <= 6 and block == "GERDAU_GOAU":
            violations.append(f"{block} com {len(block_tickers)} ativos ({', '.join(block_tickers)}) em carteira pequena")
        if len(block_idx) * min_weight > cfg["peso_maximo_bloco_risco_tolerado"] + 1e-12:
            return ["peso minimo dos ativos excede limite de bloco de risco"]
    if violations:
        return ["bloco de risco duplicado violado: " + "; ".join(violations)]
    return []


def _timing_series(selected: pd.DataFrame, tickers: list[str]) -> pd.Series:
    if "tipo_timing" not in selected:
        return pd.Series("", index=tickers)
    return selected.set_index("ticker")["tipo_timing"].reindex(tickers).fillna("")


def _reversal_indexes(tickers: list[str], timing_types: pd.Series) -> list[int]:
    timing_map = timing_types.reindex(tickers).fillna("")
    return [i for i, ticker in enumerate(tickers) if timing_map.loc[ticker] == "timing_reversao_oportunidade"]


def _market_regime(settings: dict) -> str:
    return str(settings.get("_runtime_market_class", "")).strip().lower()


def _is_favorable_market(settings: dict) -> bool:
    return _market_regime(settings) == "mercado favoravel"


def _is_weak_market(settings: dict) -> bool:
    return _market_regime(settings) == "mercado fraco/desfavoravel"


def _regime_minimum_status(metrics: dict, settings: dict) -> dict:
    cfg = _portfolio_config(settings)
    regime = str(metrics.get("regime_mercado_data_base", _market_regime(settings)) or "indefinido").strip().lower()
    score = metrics.get("score_aderencia_regime", np.nan)
    beta = metrics.get("beta_carteira", np.nan)
    corr = metrics.get("correlacao_carteira_ibov", np.nan)
    score_min = cfg["score_aderencia_regime_minimo"]
    beta_min = cfg["beta_carteira_minimo_mercado_favoravel"]
    corr_min = cfg["correlacao_carteira_ibov_minima_mercado_favoravel"]
    if regime != "mercado favoravel" or not cfg["bloquear_baixa_aderencia_em_mercado_favoravel"]:
        return {
            "carteira_aderente_ao_regime": True,
            "carteira_valida_mas_incompativel_com_regime": False,
            "score_aderencia_regime_minimo": score_min,
            "beta_carteira_minimo_exigido": beta_min if regime == "mercado favoravel" else np.nan,
            "correlacao_carteira_minima_exigida": corr_min if regime == "mercado favoravel" else np.nan,
            "motivo_rejeicao_por_regime": "",
            "carteira_elegivel_para_escolha_final": True,
        }
    score_ok = pd.notna(score) and float(score) >= score_min
    beta_corr_ok = pd.notna(beta) and pd.notna(corr) and float(beta) >= beta_min and float(corr) >= corr_min
    adherent = bool(score_ok or beta_corr_ok)
    reason = "" if adherent else f"baixa aderencia ao mercado favoravel: score<{score_min} e beta/correlacao abaixo dos minimos ({beta_min}/{corr_min})"
    return {
        "carteira_aderente_ao_regime": adherent,
        "carteira_valida_mas_incompativel_com_regime": not adherent,
        "score_aderencia_regime_minimo": score_min,
        "beta_carteira_minimo_exigido": beta_min,
        "correlacao_carteira_minima_exigida": corr_min,
        "motivo_rejeicao_por_regime": reason,
        "carteira_elegivel_para_escolha_final": adherent,
    }


def _watchlist_flex_indexes(tickers: list[str], watchlist_types: pd.Series) -> list[int]:
    watch_map = watchlist_types.reindex(tickers).fillna("")
    return [i for i, ticker in enumerate(tickers) if watch_map.loc[ticker] == "watchlist_flexivel"]


def _risk_block_for_ticker(ticker: str) -> str:
    base = str(ticker).upper().replace(".SA", "")
    special = {
        "PETR3": "PETROBRAS",
        "PETR4": "PETROBRAS",
        "GGBR3": "GERDAU_GOAU",
        "GGBR4": "GERDAU_GOAU",
        "GOAU3": "GERDAU_GOAU",
        "GOAU4": "GERDAU_GOAU",
        "CPLE3": "COPEL",
        "CPLE6": "COPEL",
        "ITUB3": "ITAU",
        "ITUB4": "ITAU",
        "BBDC3": "BRADESCO",
        "BBDC4": "BRADESCO",
        "ELET3": "ELETROBRAS",
        "ELET6": "ELETROBRAS",
        "VALE3": "VALE_BRAP",
        "BRAP4": "VALE_BRAP",
        "SANB3": "SANTANDER_BR",
        "SANB4": "SANTANDER_BR",
        "SANB11": "SANTANDER_BR",
    }
    if base in special:
        return special[base]
    root = base.rstrip("0123456789")
    return root or base


def _risk_block_series(selected: pd.DataFrame, tickers: list[str]) -> pd.Series:
    if "grupo_economico_ou_bloco_risco" in selected:
        values = selected.set_index("ticker")["grupo_economico_ou_bloco_risco"].reindex(tickers)
        return values.fillna(pd.Series(tickers, index=tickers).map(_risk_block_for_ticker))
    return pd.Series(tickers, index=tickers).map(_risk_block_for_ticker)


def _risk_block_indexes(tickers: list[str], blocks: pd.Series) -> dict[str, list[int]]:
    block_map = blocks.reindex(tickers).fillna(pd.Series(tickers, index=tickers).map(_risk_block_for_ticker))
    return {block: [i for i, ticker in enumerate(tickers) if block_map.loc[ticker] == block] for block in block_map.unique()}


def _block_text(weights: np.ndarray, tickers: list[str], blocks: pd.Series) -> tuple[str, str, float, int, int]:
    block_map = blocks.reindex(tickers).fillna(pd.Series(tickers, index=tickers).map(_risk_block_for_ticker))
    weight_parts = []
    duplicated_parts = []
    max_weight = 0.0
    duplicated_count = 0
    for block in sorted(block_map.unique()):
        indexes = [i for i, ticker in enumerate(tickers) if block_map.loc[ticker] == block]
        block_weight = float(weights[indexes].sum())
        max_weight = max(max_weight, block_weight)
        weight_parts.append(f"{block}: {block_weight:.2%}")
        if len(indexes) > 1:
            duplicated_count += 1
            duplicated_parts.append(f"{block}: {len(indexes)} ativos/{block_weight:.2%}")
    return "; ".join(weight_parts), "; ".join(duplicated_parts), max_weight, int(block_map.nunique()), duplicated_count

def _asset_weight_caps(selected: pd.DataFrame, settings: dict) -> pd.Series:
    cfg = _portfolio_config(settings)
    indexed = selected.set_index("ticker")
    caps = pd.Series(cfg["max_weight"], index=indexed.index, dtype=float)
    watch = indexed.get("tipo_watchlist", pd.Series("", index=indexed.index)).fillna("")
    beta = indexed.get("beta", pd.Series(np.nan, index=indexed.index))
    corr = indexed.get("correlacao_ibov", pd.Series(np.nan, index=indexed.index))
    flex_mask = watch.eq("watchlist_flexivel")
    caps.loc[flex_mask] = np.minimum(caps.loc[flex_mask], cfg["peso_maximo_individual_watchlist_flexivel"])
    if _is_favorable_market(settings):
        beta_negative = beta < 0
        beta_low_flex = (beta < cfg["beta_muito_baixo_mercado_favoravel"]) & flex_mask
        corr_negative = corr < 0
        corr_low_flex = (corr < cfg["correlacao_muito_baixa_mercado_favoravel"]) & flex_mask
        beta_cap_mask = (beta_negative | beta_low_flex).fillna(False)
        corr_cap_mask = (corr_negative | corr_low_flex).fillna(False)
        caps.loc[beta_cap_mask] = np.minimum(caps.loc[beta_cap_mask], cfg["peso_maximo_ativo_com_beta_negativo_em_mercado_favoravel"])
        caps.loc[corr_cap_mask] = np.minimum(caps.loc[corr_cap_mask], cfg["peso_maximo_ativo_com_correlacao_baixa_em_mercado_favoravel"])
    for col in ["peso_maximo_beta_alto_mercado_esticado", "peso_maximo_turnaround_especulativo", "peso_maximo_timing_com_alerta"]:
        if col in indexed:
            values = pd.to_numeric(indexed[col], errors="coerce")
            mask = values.notna()
            caps.loc[mask] = np.minimum(caps.loc[mask], values.loc[mask])
    timing_quality = indexed.get("qualidade_do_timing", pd.Series("", index=indexed.index)).fillna("")
    caps.loc[timing_quality.eq("timing_com_alerta")] = np.minimum(caps.loc[timing_quality.eq("timing_com_alerta")], cfg["peso_maximo_timing_com_alerta"])
    caps.loc[timing_quality.eq("timing_tardio")] = np.minimum(caps.loc[timing_quality.eq("timing_tardio")], cfg["peso_maximo_timing_tardio"])

    return caps


def _regime_penalty_flags(row: pd.Series, settings: dict) -> pd.Series:
    cfg = _portfolio_config(settings)
    beta = row.get("beta", np.nan)
    corr = row.get("correlacao_ibov", np.nan)
    watch_flex = row.get("tipo_watchlist", "") == "watchlist_flexivel"
    favorable = _is_favorable_market(settings)
    weak = _is_weak_market(settings)
    return pd.Series({
        "regime_mercado_data_base": _market_regime(settings) or "indefinido",
        "penalizacao_beta_negativo_mercado_favoravel": bool(favorable and pd.notna(beta) and beta < 0),
        "penalizacao_beta_muito_baixo_mercado_favoravel": bool(favorable and watch_flex and pd.notna(beta) and beta < cfg["beta_muito_baixo_mercado_favoravel"]),
        "penalizacao_correlacao_negativa_mercado_favoravel": bool(favorable and pd.notna(corr) and corr < 0),
        "penalizacao_correlacao_muito_baixa_mercado_favoravel": bool(favorable and watch_flex and pd.notna(corr) and corr < cfg["correlacao_muito_baixa_mercado_favoravel"]),
        "penalizacao_beta_alto_mercado_fraco": bool(weak and pd.notna(beta) and beta > 1.0),
        "penalizacao_correlacao_alta_mercado_fraco": bool(weak and pd.notna(corr) and corr > 0.70),
    })


def apply_regime_fields(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    result = frame.copy()
    if "ticker" in result:
        result["grupo_economico_ou_bloco_risco"] = result["ticker"].map(_risk_block_for_ticker)
    flags = result.apply(lambda row: _regime_penalty_flags(row, settings), axis=1)
    result = pd.concat([result.drop(columns=[col for col in flags.columns if col in result.columns], errors="ignore"), flags], axis=1)
    caps = _asset_weight_caps(result, settings) if "ticker" in result else pd.Series(dtype=float)
    result["peso_maximo_permitido_ativo"] = result["ticker"].map(caps).fillna(_portfolio_config(settings)["max_weight"]) if "ticker" in result else np.nan
    result["limite_peso_watchlist_flexivel_aplicado"] = result.get("tipo_watchlist", pd.Series("", index=result.index)).eq("watchlist_flexivel")
    result["limite_quantidade_watchlist_flexivel_aplicado"] = result["limite_peso_watchlist_flexivel_aplicado"]
    result["penalizacao_watchlist_flexivel"] = result["limite_peso_watchlist_flexivel_aplicado"]
    if "score_aderencia_regime" not in result:
        result["score_aderencia_regime"] = np.nan
    if "motivo_aderencia_regime" not in result:
        result["motivo_aderencia_regime"] = ""
    return result


def _sector_indexes(tickers: list[str], sectors: pd.Series) -> dict[str, list[int]]:
    sector_map = sectors.reindex(tickers).fillna("Outros")
    return {sector: [i for i, ticker in enumerate(tickers) if sector_map.loc[ticker] == sector] for sector in sector_map.unique()}


def _sector_text(weights: np.ndarray, tickers: list[str], sectors: pd.Series) -> tuple[str, str, float, int]:
    sector_map = sectors.reindex(tickers).fillna("Outros")
    weight_parts = []
    count_parts = []
    max_weight = 0.0
    for sector in sorted(sector_map.unique()):
        indexes = [i for i, ticker in enumerate(tickers) if sector_map.loc[ticker] == sector]
        sector_weight = float(weights[indexes].sum())
        max_weight = max(max_weight, sector_weight)
        weight_parts.append(f"{sector}: {sector_weight:.2%}")
        count_parts.append(f"{sector}: {len(indexes)}")
    return "; ".join(weight_parts), "; ".join(count_parts), max_weight, int(sector_map.nunique())


def _has_sector_count_violation(selected: pd.DataFrame, settings: dict) -> str:
    cfg = _portfolio_config(settings)
    counts = selected["setor"].fillna("Outros").value_counts()
    exceeded = counts[counts > cfg["max_assets_per_sector"]]
    if exceeded.empty:
        return ""
    return "; ".join(f"{sector} com {int(count)} acoes" for sector, count in exceeded.items())


def _has_reversal_count_violation(selected: pd.DataFrame, settings: dict) -> str:
    cfg = _portfolio_config(settings)
    if "tipo_timing" not in selected:
        return ""
    reversal = selected[selected["tipo_timing"].eq("timing_reversao_oportunidade")]
    if len(reversal) <= cfg["max_reversal_assets"]:
        return ""
    return f"{len(reversal)} reversoes; maximo permitido: {cfg['max_reversal_assets']}"


def _has_risk_block_count_violation(selected: pd.DataFrame, settings: dict) -> str:
    if selected.empty or "ticker" not in selected:
        return ""
    count = len(selected)
    blocks = selected["ticker"].map(_risk_block_for_ticker)
    duplicated = blocks[blocks.duplicated(keep=False)]
    if duplicated.empty:
        return ""
    selected_blocks = selected.assign(grupo_economico_ou_bloco_risco=blocks)
    violations = []
    for block, frame in selected_blocks.groupby("grupo_economico_ou_bloco_risco"):
        tickers = frame["ticker"].astype(str).tolist()
        if len(tickers) <= 1:
            continue
        if block == "PETROBRAS":
            violations.append(f"{block} com {len(tickers)} ativos ({', '.join(tickers)}); PETR3/PETR4 nao podem entrar juntos")
        elif count <= 6 and block == "GERDAU_GOAU":
            violations.append(f"{block} com {len(tickers)} ativos ({', '.join(tickers)}) em carteira pequena")
    return "; ".join(violations)

def _constraints_for_slsqp(tickers: list[str], sectors: pd.Series, blocks: pd.Series, timing_types: pd.Series, watchlist_types: pd.Series, max_sector_weight: float, max_block_weight: float, settings: dict) -> list[dict]:
    cfg = _portfolio_config(settings)
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    for indexes in _sector_indexes(tickers, sectors).values():
        constraints.append({"type": "ineq", "fun": lambda w, idx=indexes: max_sector_weight - np.sum(w[idx])})
    for indexes in _risk_block_indexes(tickers, blocks).values():
        if len(indexes) > 1:
            constraints.append({"type": "ineq", "fun": lambda w, idx=indexes: max_block_weight - np.sum(w[idx])})
    reversal_idx = _reversal_indexes(tickers, timing_types)
    if reversal_idx:
        constraints.append({"type": "ineq", "fun": lambda w, idx=reversal_idx: cfg["max_reversal_weight"] - np.sum(w[idx])})
    flex_idx = _watchlist_flex_indexes(tickers, watchlist_types)
    if flex_idx:
        constraints.append({"type": "ineq", "fun": lambda w, idx=flex_idx: cfg["max_peso_total_watchlist_flexivel"] - np.sum(w[idx])})
    return constraints


def _linear_feasible_weights(tickers: list[str], sectors: pd.Series, blocks: pd.Series, timing_types: pd.Series, watchlist_types: pd.Series, weight_caps: np.ndarray, settings: dict, max_sector_weight: float, max_block_weight: float) -> tuple[np.ndarray | None, str]:
    cfg = _portfolio_config(settings)
    n = len(tickers)
    min_weight = cfg["min_weight"]
    if float(np.sum(weight_caps)) < 1 - 1e-12:
        return None, "limites individuais de peso tornam a carteira inviavel"
    if n * min_weight > 1 + 1e-12:
        return None, "numero de ativos excede limite imposto pelo peso minimo"

    a_ub = []
    b_ub = []
    sector_groups = _sector_indexes(tickers, sectors)
    if len(sector_groups) * max_sector_weight < 1 - 1e-12:
        return None, "setores insuficientes para limite setorial"
    for indexes in sector_groups.values():
        row = np.zeros(n)
        row[indexes] = 1
        a_ub.append(row)
        b_ub.append(max_sector_weight)
        if len(indexes) * min_weight > max_sector_weight + 1e-12:
            return None, "peso minimo dos ativos excede limite setorial"

    block_groups = _risk_block_indexes(tickers, blocks)
    for indexes in block_groups.values():
        if len(indexes) > 1:
            if len(indexes) * min_weight > max_block_weight + 1e-12:
                return None, "peso minimo dos ativos excede limite de bloco de risco"
            row = np.zeros(n)
            row[indexes] = 1
            a_ub.append(row)
            b_ub.append(max_block_weight)
    reversal_idx = _reversal_indexes(tickers, timing_types)
    if len(reversal_idx) > cfg["max_reversal_assets"]:
        return None, "maximo de acoes de reversao excedido"
    if reversal_idx:
        if len(reversal_idx) * min_weight > cfg["max_reversal_weight"] + 1e-12:
            return None, "peso minimo das reversoes excede limite de reversao"
        row = np.zeros(n)
        row[reversal_idx] = 1
        a_ub.append(row)
        b_ub.append(cfg["max_reversal_weight"])

    flex_idx = _watchlist_flex_indexes(tickers, watchlist_types)
    if len(flex_idx) > cfg["max_ativos_watchlist_flexivel"]:
        return None, "maximo de ativos em watchlist flexivel excedido"
    if flex_idx:
        if len(flex_idx) * min_weight > cfg["max_peso_total_watchlist_flexivel"] + 1e-12:
            return None, "peso minimo da watchlist flexivel excede limite total"
        row = np.zeros(n)
        row[flex_idx] = 1
        a_ub.append(row)
        b_ub.append(cfg["max_peso_total_watchlist_flexivel"])

    result = linprog(
        c=np.zeros(n),
        A_ub=np.array(a_ub),
        b_ub=np.array(b_ub),
        A_eq=np.ones((1, n)),
        b_eq=np.array([1.0]),
        bounds=list(zip(np.repeat(min_weight, n), weight_caps)),
        method="highs",
    )
    if not result.success:
        return None, str(result.message)
    return result.x, "ok"


def _validate_weights(weights: np.ndarray, tickers: list[str], sectors: pd.Series, blocks: pd.Series, timing_types: pd.Series, watchlist_types: pd.Series, weight_caps: np.ndarray, settings: dict, max_sector_weight: float, max_block_weight: float) -> list[str]:
    cfg = _portfolio_config(settings)
    violations = []
    if not np.isclose(weights.sum(), 1.0, atol=1e-5):
        violations.append("soma dos pesos diferente de 100%")
    if (weights < cfg["min_weight"] - 1e-6).any():
        violations.append("peso abaixo do minimo")
    if (weights > cfg["max_weight"] + 1e-6).any():
        violations.append("peso acima do maximo")
    if (weights > weight_caps + 1e-6).any():
        violations.append("peso acima do maximo permitido por regime/watchlist")
    for sector, indexes in _sector_indexes(tickers, sectors).items():
        if weights[indexes].sum() > max_sector_weight + 1e-6:
            violations.append(f"limite setorial excedido: {sector}")
    block_groups = _risk_block_indexes(tickers, blocks)
    for block, indexes in block_groups.items():
        if len(indexes) > 1 and weights[indexes].sum() > max_block_weight + 1e-6:
            violations.append(f"limite de bloco de risco excedido: {block}")
    reversal_idx = _reversal_indexes(tickers, timing_types)
    if len(reversal_idx) > cfg["max_reversal_assets"]:
        violations.append("maximo de acoes de reversao excedido")
    if reversal_idx and weights[reversal_idx].sum() > cfg["max_reversal_weight"] + 1e-6:
        violations.append("peso maximo de reversao excedido")
    flex_idx = _watchlist_flex_indexes(tickers, watchlist_types)
    if len(flex_idx) > cfg["max_ativos_watchlist_flexivel"]:
        violations.append("maximo de ativos em watchlist flexivel excedido")
    if flex_idx and weights[flex_idx].sum() > cfg["max_peso_total_watchlist_flexivel"] + 1e-6:
        violations.append("peso maximo em watchlist flexivel excedido")
    return violations


def _regime_adherence(selected: pd.DataFrame, weights: np.ndarray, beta_portfolio: float, corr_portfolio: float, flex_count: int, flex_weight: float, settings: dict) -> dict:
    cfg = _portfolio_config(settings)
    regime = _market_regime(settings) or "indefinido"
    indexed = selected.set_index("ticker")
    watch = indexed.get("tipo_watchlist", pd.Series("", index=indexed.index)).fillna("")
    beta = indexed.get("beta", pd.Series(np.nan, index=indexed.index))
    corr = indexed.get("correlacao_ibov", pd.Series(np.nan, index=indexed.index))
    score = 100.0
    reasons: list[str] = []

    if regime == "mercado favoravel":
        if pd.notna(beta_portfolio) and beta_portfolio < cfg["beta_carteira_minimo_mercado_favoravel"]:
            score -= 12
            reasons.append("beta da carteira abaixo do preferencial para mercado favoravel")
        if pd.notna(corr_portfolio) and corr_portfolio < cfg["correlacao_carteira_ibov_minima_mercado_favoravel"]:
            score -= 12
            reasons.append("correlacao da carteira com IBOV abaixo do preferencial para mercado favoravel")
        for ticker in indexed.index:
            flex = watch.loc[ticker] == "watchlist_flexivel"
            b = beta.loc[ticker]
            c = corr.loc[ticker]
            if pd.notna(b) and b < 0:
                score -= 10 + (8 if flex else 0)
                reasons.append(f"{ticker} com beta negativo em mercado favoravel")
            elif pd.notna(b) and b < cfg["beta_muito_baixo_mercado_favoravel"] and flex:
                score -= 8
                reasons.append(f"{ticker} com beta muito baixo e watchlist flexivel")
            if pd.notna(c) and c < 0:
                score -= 10 + (8 if flex else 0)
                reasons.append(f"{ticker} com correlacao negativa em mercado favoravel")
            elif pd.notna(c) and c < cfg["correlacao_muito_baixa_mercado_favoravel"] and flex:
                score -= 8
                reasons.append(f"{ticker} com correlacao baixa e watchlist flexivel")
        if len(selected) == 5:
            score -= 8
            reasons.append("carteira minima de 5 acoes reduz flexibilidade de pesos")
    elif regime == "mercado fraco/desfavoravel":
        if pd.notna(beta_portfolio) and beta_portfolio > 1.0:
            score -= 12
            reasons.append("beta da carteira alto para mercado fraco")
        if pd.notna(corr_portfolio) and corr_portfolio > 0.70:
            score -= 10
            reasons.append("correlacao da carteira com IBOV alta para mercado fraco")

    if flex_count > cfg["max_ativos_watchlist_flexivel"]:
        score -= 20
        reasons.append("quantidade de watchlist flexivel acima do limite")
    if flex_weight > cfg["max_peso_total_watchlist_flexivel"] + 1e-9:
        score -= 20
        reasons.append("peso total de watchlist flexivel acima do limite")

    score = max(0.0, min(100.0, score))
    if regime == "mercado favoravel" and (pd.notna(beta_portfolio) and beta_portfolio < cfg["beta_carteira_minimo_mercado_favoravel"] or pd.notna(corr_portfolio) and corr_portfolio < cfg["correlacao_carteira_ibov_minima_mercado_favoravel"]):
        label = "baixa_aderencia_ao_ibov_em_mercado_favoravel" if score < 70 else "parcialmente_aderente"
    elif regime == "mercado favoravel" and score < 60:
        label = "defensiva_demais_para_mercado_favoravel"
    elif regime == "mercado fraco/desfavoravel" and score < 60:
        label = "agressiva_demais_para_mercado_fraco"
    elif regime == "mercado fraco/desfavoravel" and score >= 75:
        label = "adequada_para_mercado_fraco"
    elif score >= 80:
        label = "aderente_ao_regime"
    elif score >= 60:
        label = "parcialmente_aderente"
    else:
        label = "necessita_avaliacao"
    result = {
        "regime_mercado_data_base": regime,
        "score_aderencia_regime": score,
        "aderencia_carteira_ao_regime": label,
        "alerta_incompatibilidade_regime": bool(score < cfg["score_aderencia_regime_minimo"]),
        "motivo_incompatibilidade_regime": "; ".join(dict.fromkeys(reasons)),
        "motivo_aderencia_regime": "; ".join(dict.fromkeys(reasons)) or "carteira compativel com o regime de mercado",
    }
    result.update(_regime_minimum_status(result | {"beta_carteira": beta_portfolio, "correlacao_carteira_ibov": corr_portfolio}, settings))
    if result["carteira_valida_mas_incompativel_com_regime"]:
        result["aderencia_carteira_ao_regime"] = "carteira_valida_mas_incompativel_com_regime"
        result["alerta_incompatibilidade_regime"] = True
        result["motivo_incompatibilidade_regime"] = result["motivo_rejeicao_por_regime"] or result["motivo_incompatibilidade_regime"]
    return result


def _portfolio_metrics(selected: pd.DataFrame, weights: np.ndarray, covariance: pd.DataFrame, settings: dict, status: str, sector_relaxed: bool, precomputed: dict | None = None) -> dict:
    cfg = _portfolio_config(settings)
    indexed = selected.set_index("ticker")
    if precomputed:
        tickers = precomputed["tickers"]
        mean_returns = precomputed["returns"]
        betas = precomputed["betas"]
        correlations = precomputed["correlations"]
        cov = precomputed["covariance"]
        sectors = pd.Series(precomputed["sectors"], index=tickers)
        blocks = pd.Series(precomputed["blocks"], index=tickers)
        timing_types = pd.Series(precomputed["timing_types"], index=tickers)
        watchlist_types = pd.Series(precomputed["watchlist_types"], index=tickers)
    else:
        tickers = selected["ticker"].tolist()
        mean_returns = indexed.loc[tickers, "retorno_medio"].to_numpy(float)
        betas = indexed.loc[tickers, "beta"].fillna(1.0).to_numpy(float)
        correlations = indexed.loc[tickers, "correlacao_ibov"].fillna(0.0).to_numpy(float) if "correlacao_ibov" in indexed else np.zeros(len(tickers))
        cov = covariance.reindex(index=tickers, columns=tickers).fillna(0).to_numpy(float)
        sectors = indexed["setor"]
        blocks = _risk_block_series(selected, tickers)
        timing_types = _timing_series(selected, tickers)
        watchlist_types = indexed.get("tipo_watchlist", pd.Series("", index=tickers)).reindex(tickers).fillna("")
    port_ret = portfolio_return(weights, mean_returns)
    port_std = portfolio_risk(weights, cov)
    daily_rf = (1 + settings["risk_free_rate"]["annual_rate"]) ** (1 / settings["risk"]["trading_days_year"]) - 1
    monthly_days, trading_days = _periods(settings)
    port_ret_monthly = _compound_return(port_ret, monthly_days)
    port_ret_annual = _compound_return(port_ret, trading_days)
    port_risk_monthly = _scale_risk(port_std, monthly_days)
    port_risk_annual = _scale_risk(port_std, trading_days)
    sector_weights, sector_counts, max_sector, diversification = _sector_text(weights, tickers, sectors)
    sector_map = sectors.reindex(tickers).fillna("Outros")
    sector_weight_map = {sector: float(weights[[i for i, ticker in enumerate(tickers) if sector_map.loc[ticker] == sector]].sum()) for sector in sector_map.unique()}
    setor_concentrado = max(sector_weight_map, key=sector_weight_map.get) if sector_weight_map else ""
    peso_setor_concentrado = sector_weight_map.get(setor_concentrado, np.nan) if setor_concentrado else np.nan
    block_weights, duplicated_blocks, max_block_raw, block_diversification, duplicated_block_count = _block_text(weights, tickers, blocks)
    max_block = max_block_raw if duplicated_block_count > 0 else 0.0
    reversal_idx = _reversal_indexes(tickers, timing_types)
    flex_idx = _watchlist_flex_indexes(tickers, watchlist_types)
    reversal_weight = float(weights[reversal_idx].sum()) if reversal_idx else 0.0
    flex_weight = float(weights[flex_idx].sum()) if flex_idx else 0.0
    beta_value = portfolio_beta(weights, betas)
    corr_value = float(np.dot(weights, correlations)) if len(correlations) else np.nan
    adherence = _regime_adherence(selected, weights, beta_value, corr_value, len(flex_idx), flex_weight, settings)
    alerta_concentracao_setorial = bool(max_sector > cfg["peso_maximo_setor_tolerado"] + 1e-9)
    respeita_peso_maximo_setor = bool(max_sector <= cfg["peso_maximo_setor_tolerado"] + 1e-9)
    if max_sector > cfg["peso_maximo_setor_excepcional"] + 1e-9:
        motivo_concentracao = "peso setorial acima do limite excepcional"
    elif max_sector > cfg["peso_maximo_setor_tolerado"] + 1e-9:
        motivo_concentracao = "peso setorial acima do limite tolerado; uso excepcional"
    elif max_sector > cfg["peso_maximo_setor_preferencial"] + 1e-9:
        motivo_concentracao = "peso setorial acima do limite preferencial; dentro do tolerado"
    else:
        motivo_concentracao = ""
    alerta_bloco_risco = bool(duplicated_block_count > 0 or max_block > cfg["peso_maximo_bloco_risco_preferencial"] + 1e-9)
    respeita_bloco_risco = bool(max_block <= cfg["peso_maximo_bloco_risco_tolerado"] + 1e-9)
    motivo_bloco = duplicated_blocks if duplicated_blocks else ("peso de bloco de risco acima do preferencial" if alerta_bloco_risco else "")
    restrictions = "limite setorial/bloco preferencial relaxado" if sector_relaxed else ""
    metrics = {
        "status_carteira": "valida com relaxamento setorial" if sector_relaxed else "valida",
        "carteira_valida": bool(port_ret > 0 and max_sector <= cfg["peso_maximo_setor_excepcional"] + 1e-6 and max_block <= cfg["peso_maximo_bloco_risco_tolerado"] + 1e-6 and reversal_weight <= cfg["max_reversal_weight"] + 1e-6 and len(reversal_idx) <= cfg["max_reversal_assets"]),
        "ativos_elegiveis": len(selected),
        "quantidade_acoes": len(selected),
        "restricoes_violadas": restrictions,
        "retorno_carteira": port_ret,
        "retorno_carteira_diario": port_ret,
        "retorno_carteira_mensal": port_ret_monthly,
        "retorno_carteira_anual": port_ret_annual,
        "risco_carteira": port_std,
        "risco_carteira_diario": port_std,
        "risco_carteira_mensal": port_risk_monthly,
        "risco_carteira_anual": port_risk_annual,
        "dias_uteis_mes_retorno": monthly_days,
        "dias_uteis_ano_retorno": trading_days,
        "cv_carteira": np.nan if port_ret <= 0 else port_std / port_ret,
        "beta_carteira": beta_value,
        "beta_medio_ponderado": beta_value,
        "correlacao_carteira_ibov": corr_value,
        "correlacao_media_ponderada_ibov": corr_value,
        "sharpe_diario": sharpe_ratio(port_ret, port_std, daily_rf),
        "status_otimizacao": status,
        "limite_setorial_relaxado": sector_relaxed,
        "maior_concentracao_setorial": max_sector,
        "maior_peso_setorial": max_sector,
        "setor_mais_concentrado": setor_concentrado,
        "setor_concentrado": setor_concentrado,
        "peso_setor_concentrado": peso_setor_concentrado,
        "peso_setor": peso_setor_concentrado,
        "peso_maximo_setor_preferencial": cfg["peso_maximo_setor_preferencial"],
        "peso_maximo_setor_tolerado": cfg["peso_maximo_setor_tolerado"],
        "peso_maximo_setor_excepcional": cfg["peso_maximo_setor_excepcional"],
        "alerta_concentracao_setorial": alerta_concentracao_setorial,
        "motivo_concentracao_setorial": motivo_concentracao,
        "carteira_respeita_limite_setorial": respeita_peso_maximo_setor,
        "concentracao_por_setor": sector_weights,
        "acoes_por_setor": sector_counts,
        "diversificacao_setorial": diversification,
        "peso_por_bloco_risco": block_weights,
        "blocos_risco_duplicados": duplicated_blocks,
        "quantidade_blocos_risco_duplicados": duplicated_block_count,
        "maior_peso_bloco_risco": max_block,
        "diversificacao_bloco_risco": block_diversification,
        "peso_bloco_risco": max_block,
        "alerta_bloco_risco": alerta_bloco_risco,
        "motivo_alerta_bloco_risco": motivo_bloco,
        "carteira_respeita_bloco_risco": respeita_bloco_risco,
        "tickers_selecionados": ", ".join(tickers),
        "pesos": "; ".join(f"{ticker}: {weight:.2%}" for ticker, weight in zip(tickers, weights)),
        "limite_setorial_usado": cfg["hard_max_sector_weight"] if sector_relaxed else cfg["preferred_max_sector_weight"],
        "acoes_reversao": len(reversal_idx),
        "peso_reversao": reversal_weight,
        "tickers_reversao": ", ".join(tickers[i] for i in reversal_idx),
        "quantidade_watchlist_flexivel": len(flex_idx),
        "peso_total_watchlist_flexivel": flex_weight,
        "peso_medio_por_ativo": float(np.mean(weights)) if len(weights) else np.nan,
        "maior_peso_individual": float(np.max(weights)) if len(weights) else np.nan,
        "limite_peso_watchlist_flexivel_aplicado": bool(flex_idx),
        "limite_quantidade_watchlist_flexivel_aplicado": bool(flex_idx),
    }
    metrics.update(adherence)
    metrics["carteira_elegivel_para_escolha_final"] = bool(metrics.get("carteira_elegivel_para_escolha_final", False) and metrics["carteira_valida"] and respeita_peso_maximo_setor and respeita_bloco_risco)
    rejection_parts = []
    if not metrics.get("carteira_aderente_ao_regime", False):
        rejection_parts.append(metrics.get("motivo_rejeicao_por_regime", "baixa aderencia ao regime"))
    if not respeita_peso_maximo_setor:
        rejection_parts.append(motivo_concentracao or "concentracao setorial acima do tolerado")
    if not respeita_bloco_risco:
        rejection_parts.append(motivo_bloco or "bloco de risco acima do tolerado")
    metrics["motivo_rejeicao_carteira"] = "; ".join(dict.fromkeys([part for part in rejection_parts if part]))
    return metrics


def _optimize_subset(selected: pd.DataFrame, covariance: pd.DataFrame, settings: dict, max_sector_weight: float, max_block_weight: float, sector_relaxed: bool, precomputed: dict | None = None) -> tuple[pd.DataFrame | None, dict, list[str]]:
    sector_violation = _has_sector_count_violation(selected, settings)
    if sector_violation:
        return None, {}, [f"maximo de acoes por setor violado: {sector_violation}"]
    reversal_violation = _has_reversal_count_violation(selected, settings)
    if reversal_violation:
        return None, {}, [f"maximo de acoes de reversao violado: {reversal_violation}"]
    block_count_violation = _has_risk_block_count_violation(selected, settings)
    if block_count_violation:
        return None, {}, [f"bloco de risco duplicado violado: {block_count_violation}"]

    cfg = _portfolio_config(settings)
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
    else:
        tickers = selected["ticker"].tolist()
        sectors = indexed["setor"]
        blocks = _risk_block_series(selected, tickers)
        timing_types = _timing_series(selected, tickers)
        watchlist_types = indexed.get("tipo_watchlist", pd.Series("", index=tickers)).reindex(tickers).fillna("")
        weight_caps = _asset_weight_caps(selected, settings).reindex(tickers).fillna(cfg["max_weight"]).to_numpy(float)
        mean_returns = indexed.loc[tickers, "retorno_medio"].to_numpy(float)
        cov = covariance.reindex(index=tickers, columns=tickers).fillna(0).to_numpy(float)
    feasible_x0, feasible_message = _linear_feasible_weights(tickers, sectors, blocks, timing_types, watchlist_types, weight_caps, settings, max_sector_weight, max_block_weight)
    if feasible_x0 is None:
        return None, {}, [feasible_message]

    def objective(w: np.ndarray) -> float:
        ret = portfolio_return(w, mean_returns)
        risk = portfolio_risk(w, cov)
        if ret <= 0:
            return 1e6
        return risk / ret

    bounds = list(zip(np.repeat(cfg["min_weight"], len(tickers)), weight_caps))
    result = minimize(
        objective,
        feasible_x0,
        method="SLSQP",
        bounds=bounds,
        constraints=_constraints_for_slsqp(tickers, sectors, blocks, timing_types, watchlist_types, max_sector_weight, max_block_weight, settings),
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    if result.success:
        weights = result.x
        status = "ok" if not sector_relaxed else "ok com limite setorial preferencial relaxado"
    else:
        weights = feasible_x0
        status = f"Otimizacao falhou; fallback factivel usado: {result.message}"
        if sector_relaxed:
            status += "; limite setorial preferencial relaxado"

    violations = _validate_weights(weights, tickers, sectors, blocks, timing_types, watchlist_types, weight_caps, settings, max_sector_weight, max_block_weight)
    if violations:
        return None, {}, violations

    metrics = _portfolio_metrics(selected, weights, covariance, settings, status, sector_relaxed, precomputed=precomputed)
    if metrics["retorno_carteira"] <= 0:
        return None, {}, ["retorno esperado da carteira nao positivo"]
    if not metrics["carteira_valida"]:
        return None, {}, ["carteira invalida apos otimizacao"]

    portfolio = selected.copy()
    portfolio["peso_recomendado"] = weights
    portfolio["peso_maximo_permitido_ativo"] = portfolio["ticker"].map(pd.Series(weight_caps, index=tickers)).fillna(cfg["max_weight"])
    portfolio["grupo_economico_ou_bloco_risco"] = portfolio["ticker"].map(_risk_block_for_ticker)
    setor_pesos = portfolio.groupby("setor")["peso_recomendado"].sum().to_dict() if "setor" in portfolio else {}
    bloco_pesos = portfolio.groupby("grupo_economico_ou_bloco_risco")["peso_recomendado"].sum().to_dict()
    portfolio["peso_setor"] = portfolio.get("setor", pd.Series("", index=portfolio.index)).map(setor_pesos) if "setor" in portfolio else np.nan
    portfolio["peso_bloco_risco"] = portfolio["grupo_economico_ou_bloco_risco"].map(bloco_pesos)
    portfolio["alerta_concentracao_setorial"] = metrics.get("alerta_concentracao_setorial", False)
    portfolio["motivo_concentracao_setorial"] = metrics.get("motivo_concentracao_setorial", "")
    portfolio["alerta_bloco_risco"] = portfolio["peso_bloco_risco"].fillna(0) > cfg["peso_maximo_bloco_risco_preferencial"] + 1e-9
    portfolio["motivo_alerta_bloco_risco"] = metrics.get("motivo_alerta_bloco_risco", "")
    portfolio["score_aderencia_regime"] = metrics.get("score_aderencia_regime", np.nan)
    portfolio["motivo_aderencia_regime"] = metrics.get("motivo_aderencia_regime", "")
    portfolio["aderencia_carteira_ao_regime"] = metrics.get("aderencia_carteira_ao_regime", "")
    portfolio["alerta_incompatibilidade_regime"] = metrics.get("alerta_incompatibilidade_regime", False)
    portfolio["motivo_incompatibilidade_regime"] = metrics.get("motivo_incompatibilidade_regime", "")
    return portfolio, metrics, []


def _rank_key(metrics: dict) -> tuple[float, float, float, float, float, float, float, float, float]:
    cv = metrics.get("cv_carteira", np.inf)
    sharpe = metrics.get("sharpe_diario", -np.inf)
    beta = metrics.get("beta_carteira", np.nan)
    corr = metrics.get("correlacao_carteira_ibov", np.nan)
    diversification = metrics.get("diversificacao_setorial", 0)
    flex_weight = metrics.get("peso_total_watchlist_flexivel", 0)
    max_sector = metrics.get("maior_peso_setorial", metrics.get("maior_concentracao_setorial", np.inf))
    max_block = metrics.get("maior_peso_bloco_risco", np.inf)
    adherent_penalty = 0 if bool(metrics.get("carteira_aderente_ao_regime", False)) else 1
    sector_penalty = 0 if bool(metrics.get("carteira_respeita_limite_setorial", True)) else 1
    block_penalty = 0 if bool(metrics.get("carteira_respeita_bloco_risco", True)) else 1
    favorable = str(metrics.get("regime_mercado_data_base", "")).strip().lower() == "mercado favoravel"
    beta_tie = -float(beta) if favorable and not pd.isna(beta) else (float(beta) if not pd.isna(beta) else np.inf)
    corr_tie = -float(corr) if favorable and not pd.isna(corr) else (float(corr) if not pd.isna(corr) else np.inf)
    return (
        adherent_penalty,
        sector_penalty,
        block_penalty,
        float(flex_weight) if not pd.isna(flex_weight) else np.inf,
        float(max_sector) if not pd.isna(max_sector) else np.inf,
        float(max_block) if not pd.isna(max_block) else np.inf,
        -float(diversification),
        float(cv) if not pd.isna(cv) else np.inf,
        -float(sharpe) if not pd.isna(sharpe) else np.inf,
        beta_tie,
        corr_tie,
    )

def _choose_final_portfolio(valid_results: list[tuple[int, pd.DataFrame, dict]], settings: dict) -> tuple[int, pd.DataFrame, dict, str]:
    cfg = _portfolio_config(settings)
    if not valid_results:
        raise ValueError("nenhuma carteira valida para escolher")
    eligible_results = [item for item in valid_results if bool(item[2].get("carteira_elegivel_para_escolha_final", False))]
    adherent_results = [item for item in valid_results if bool(item[2].get("carteira_aderente_ao_regime", False))]
    selection_pool = eligible_results if eligible_results else (adherent_results if adherent_results else valid_results)
    min_cv = min(float(metrics.get("cv_carteira", np.inf)) for _, _, metrics in selection_pool)
    preferred_counts = set(cfg["diversification_preferred_counts"])
    diversified = [item for item in selection_pool if item[0] in preferred_counts and float(item[2].get("cv_carteira", np.inf)) <= min_cv * (1 + cfg["tolerancia_cv_para_maior_diversificacao"])]
    if diversified:
        chosen = sorted(diversified, key=lambda item: _rank_key(item[2]))[0]
        prefix = "escolhida entre carteiras elegiveis por regime/setor/bloco" if eligible_results else ("escolhida entre carteiras aderentes ao regime" if adherent_results else "escolhida sem alternativa aderente ao regime")
        return chosen[0], chosen[1], chosen[2], f"{prefix}: 6/8 acoes preferida por diversificacao; CV dentro da tolerancia configurada"
    chosen = sorted(selection_pool, key=lambda item: _rank_key(item[2]))[0]
    if eligible_results:
        return chosen[0], chosen[1], chosen[2], "escolhida entre carteiras elegiveis por regime/setor/bloco: diversificacao antes do CV, depois Sharpe e beta/correlacao"
    if adherent_results:
        return chosen[0], chosen[1], chosen[2], "escolhida entre carteiras aderentes ao regime, mas com alerta de setor/bloco: diversificacao antes do CV"
    return chosen[0], chosen[1], chosen[2], "escolhida sem alternativa aderente ao regime: melhor carteira parcialmente aderente com alerta explicito"
def _count_is_diagnostic_only(count: int, settings: dict) -> bool:
    if count != 5:
        return False
    if bool(settings.get("_runtime_historical_simulation", False)) and bool(settings.get("_runtime_sem_look_ahead_bias", False)):
        return False
    market_regime = settings.get("market_regime", {})
    return not (
        settings.get("_runtime_market_class") == "mercado fraco/desfavoravel"
        and bool(market_regime.get("allow_selective_portfolio_in_weak_market", False))
        and int(market_regime.get("min_assets_for_selective_portfolio", 5)) <= 5
    )


def _comparison_row(count: int, metrics: dict | None, valid: bool, reason: str, attempts: int) -> dict:
    metrics = metrics or {}
    return {
        "quantidade de acoes": count,
        "tickers selecionados": metrics.get("tickers_selecionados", ""),
        "pesos": metrics.get("pesos", ""),
        "retorno esperado diario": metrics.get("retorno_carteira_diario", metrics.get("retorno_carteira", np.nan)),
        "retorno esperado mensal": metrics.get("retorno_carteira_mensal", np.nan),
        "retorno esperado anual": metrics.get("retorno_carteira_anual", np.nan),
        "retorno esperado": metrics.get("retorno_carteira", np.nan),
        "risco": metrics.get("risco_carteira", np.nan),
        "CV": metrics.get("cv_carteira", np.nan),
        "beta": metrics.get("beta_carteira", np.nan),
        "correlacao_carteira_ibov": metrics.get("correlacao_carteira_ibov", np.nan),
        "score_aderencia_regime": metrics.get("score_aderencia_regime", np.nan),
        "aderencia_carteira_ao_regime": metrics.get("aderencia_carteira_ao_regime", ""),
        "carteira_aderente_ao_regime": bool(metrics.get("carteira_aderente_ao_regime", False)) if valid else False,
        "carteira_valida_mas_incompativel_com_regime": bool(metrics.get("carteira_valida_mas_incompativel_com_regime", False)) if valid else False,
        "score_aderencia_regime_minimo": metrics.get("score_aderencia_regime_minimo", np.nan),
        "beta_carteira_minimo_exigido": metrics.get("beta_carteira_minimo_exigido", np.nan),
        "correlacao_carteira_minima_exigida": metrics.get("correlacao_carteira_minima_exigida", np.nan),
        "motivo_rejeicao_por_regime": metrics.get("motivo_rejeicao_por_regime", ""),
        "carteira_elegivel_para_escolha_final": bool(valid and metrics.get("carteira_elegivel_para_escolha_final", False)),
        "quantidade_watchlist_flexivel": metrics.get("quantidade_watchlist_flexivel", 0),
        "peso_total_watchlist_flexivel": metrics.get("peso_total_watchlist_flexivel", 0),
        "maior_peso_setorial": metrics.get("maior_peso_setorial", metrics.get("maior_concentracao_setorial", np.nan)),
        "setor_mais_concentrado": metrics.get("setor_mais_concentrado", metrics.get("setor_concentrado", "")),
        "respeita_peso_maximo_setor": bool(metrics.get("carteira_respeita_limite_setorial", False)) if valid else False,
        "carteira_respeita_limite_setorial": bool(metrics.get("carteira_respeita_limite_setorial", False)) if valid else False,
        "alerta_concentracao_setorial": bool(metrics.get("alerta_concentracao_setorial", False)) if valid else False,
        "motivo_concentracao_setorial": metrics.get("motivo_concentracao_setorial", ""),
        "quantidade_blocos_risco_duplicados": metrics.get("quantidade_blocos_risco_duplicados", 0),
        "blocos_risco_duplicados": metrics.get("blocos_risco_duplicados", ""),
        "respeita_bloco_risco": bool(metrics.get("carteira_respeita_bloco_risco", False)) if valid else False,
        "carteira_respeita_bloco_risco": bool(metrics.get("carteira_respeita_bloco_risco", False)) if valid else False,
        "maior_peso_bloco_risco": metrics.get("maior_peso_bloco_risco", np.nan),
        "motivo_alerta_bloco_risco": metrics.get("motivo_alerta_bloco_risco", ""),
        "peso_medio_por_ativo": metrics.get("peso_medio_por_ativo", np.nan),
        "maior_peso_individual": metrics.get("maior_peso_individual", np.nan),
        "carteira_preferida_por_diversificacao": False,
        "Sharpe": metrics.get("sharpe_diario", np.nan),
        "concentracao por setor": metrics.get("concentracao_por_setor", ""),
        "numero de acoes por setor": metrics.get("acoes_por_setor", ""),
        "acoes de reversao": metrics.get("acoes_reversao", 0),
        "peso reversao": metrics.get("peso_reversao", 0),
        "status de validade": "valida" if valid else "invalida",
        "motivo de escolha ou rejeicao": reason,
        "limite setorial relaxado": bool(metrics.get("limite_setorial_relaxado", False)),
        "tentativas avaliadas": attempts,
        "n_combinacoes_avaliadas": attempts,
        "histograma_rejeicoes": metrics.get("histograma_rejeicoes", "{}"),
        "cenario diagnostico": _count_is_diagnostic_only(count, metrics.get("settings", {})) if metrics.get("settings") else count == 5,
    }


def _best_for_count(pool: pd.DataFrame, covariance: pd.DataFrame, settings: dict, count: int, max_attempts: int) -> tuple[pd.DataFrame | None, dict, dict]:
    cfg = _portfolio_config(settings)
    pool_context = _pool_context(pool, covariance, settings)
    precheck_enabled = bool(settings.get("strategy", {}).get("enable_optimizer_precheck", True))
    failures: list[str] = []
    failure_histogram: dict[str, int] = {}
    attempts = 0
    best_portfolio: pd.DataFrame | None = None
    best_metrics: dict = {}

    for combo in combinations(range(len(pool)), count):
        combo_indexes = tuple(combo)
        subset_context = _slice_pool_context(pool_context, combo_indexes)
        precheck_errors = _combo_precheck_errors(pool_context, combo_indexes, settings, count) if precheck_enabled else []
        if precheck_errors:
            attempts += 1
            failures.extend(precheck_errors)
            _add_failures_to_histogram(failure_histogram, precheck_errors)
            if attempts >= max_attempts:
                break
            continue
        selected = pool.iloc[list(combo_indexes)].copy()
        trial_limits = [
            (cfg["peso_maximo_setor_preferencial"], cfg["peso_maximo_bloco_risco_preferencial"], False),
            (cfg["peso_maximo_setor_tolerado"], cfg["peso_maximo_bloco_risco_preferencial"], True),
            (cfg["peso_maximo_setor_tolerado"], cfg["peso_maximo_bloco_risco_tolerado"], True),
        ]
        if cfg["permitir_peso_setor_excepcional"]:
            trial_limits.append((cfg["peso_maximo_setor_excepcional"], cfg["peso_maximo_bloco_risco_tolerado"], True))
        portfolio = None
        metrics = {}
        errors = []
        for sector_limit, block_limit, relaxed in trial_limits:
            portfolio, metrics, errors = _optimize_subset(selected, covariance, settings, sector_limit, block_limit, sector_relaxed=relaxed, precomputed=subset_context)
            if portfolio is not None:
                break
        attempts += 1
        if portfolio is not None:
            if best_portfolio is None or _rank_key(metrics) < _rank_key(best_metrics):
                best_portfolio = portfolio
                best_metrics = metrics
                best_metrics["tentativas_otimizacao"] = attempts
        else:
            failures.extend(errors)
            _add_failures_to_histogram(failure_histogram, errors)
        if attempts >= max_attempts:
            break

    if best_portfolio is None:
        reason = "; ".join(list(dict.fromkeys(failures))[:5]) or "nenhuma combinacao factivel encontrada"
        row = _comparison_row(count, {"histograma_rejeicoes": json.dumps(failure_histogram, ensure_ascii=False, sort_keys=True)}, False, reason, attempts)
        return None, {}, row

    best_metrics["combos_avaliados"] = attempts
    best_metrics["histograma_rejeicoes"] = json.dumps(failure_histogram, ensure_ascii=False, sort_keys=True)
    best_metrics["histograma_rejeicoes_dict"] = failure_histogram
    row = _comparison_row(count, best_metrics, True, "candidata a escolha final", attempts)
    return best_portfolio, best_metrics, row


def _candidate_counts(candidates_count: int, settings: dict) -> list[int]:
    cfg = _portfolio_config(settings)
    if cfg["candidate_counts"]:
        return cfg["candidate_counts"]
    min_required = _minimum_assets_required(candidates_count, settings)
    max_assets = min(int(settings.get("strategy", {}).get("max_assets", candidates_count)), candidates_count)
    return list(range(min_required, max_assets + 1))


def optimize_weights(candidates: pd.DataFrame, covariance: pd.DataFrame, settings: dict) -> tuple[pd.DataFrame, dict]:
    candidates = apply_regime_fields(candidates.copy(), settings)
    candidates = candidates[candidates["retorno_medio"] > 0].copy()
    candidates_count = len(candidates)
    if candidates.empty:
        counts = _portfolio_config(settings)["candidate_counts"] or [6, 8, 10]
        comparison = pd.DataFrame([_comparison_row(count, None, False, "ativos permitidos insuficientes: 0", 0) for count in counts])
        metrics = _base_metrics(0, "carteira invalida / ativos insuficientes", False, ["nenhum ativo elegivel permitido para otimizacao"])
        metrics["comparativo_carteiras"] = comparison
        metrics["carteiras_testadas"] = ", ".join(str(count) for count in counts)
        return _empty_portfolio(candidates, metrics)

    counts = _candidate_counts(candidates_count, settings)
    if not counts:
        violations = [f"ativos elegiveis insuficientes: {candidates_count}; nenhuma quantidade configurada e factivel"]
        metrics = _base_metrics(candidates_count, "carteira invalida / ativos insuficientes", False, violations)
        return _empty_portfolio(candidates, metrics)

    pool_size = int(settings.get("strategy", {}).get("optimization_candidates", candidates_count))
    pool = candidates.head(pool_size).reset_index(drop=True)
    max_evaluations = int(settings.get("strategy", {}).get("max_subset_evaluations", 120))

    best_portfolio: pd.DataFrame | None = None
    best_metrics: dict = {}
    valid_results: list[tuple[int, pd.DataFrame, dict]] = []
    comparison_rows = []
    total_attempts = 0

    for count in counts:
        if count > len(pool):
            row = _comparison_row(count, None, False, f"ativos permitidos insuficientes: {len(pool)}; necessario: {count}", 0)
            comparison_rows.append(row)
            continue
        portfolio, metrics, row = _best_for_count(pool, covariance, settings, count, max_evaluations)
        diagnostic_only = _count_is_diagnostic_only(count, settings)
        if diagnostic_only and portfolio is not None:
            row["motivo de escolha ou rejeicao"] = "cenario diagnostico; nao elegivel como recomendacao final"
            row["cenario diagnostico"] = True
            row["carteira_elegivel_para_escolha_final"] = False
        elif count == 5:
            row["cenario diagnostico"] = False
        comparison_rows.append(row)
        total_attempts += int(row.get("tentativas avaliadas", 0))
        if diagnostic_only:
            continue
        if portfolio is not None:
            valid_results.append((count, portfolio, metrics))

    if valid_results:
        chosen_count_tmp, best_portfolio, best_metrics, chosen_reason = _choose_final_portfolio(valid_results, settings)
    else:
        chosen_reason = "nenhuma carteira valida para escolha final"

    comparison = pd.DataFrame(comparison_rows)
    if best_portfolio is None:
        invalid_reasons = comparison["motivo de escolha ou rejeicao"].dropna().astype(str).tolist()
        metrics = _base_metrics(candidates_count, "carteira invalida / restricoes inviaveis", False, invalid_reasons)
        metrics["comparativo_carteiras"] = comparison
        metrics["carteiras_testadas"] = ", ".join(str(count) for count in counts)
        metrics["tentativas_otimizacao"] = total_attempts
        return _empty_portfolio(candidates, metrics)

    chosen_count = int(best_metrics["quantidade_acoes"])
    comparison.loc[comparison["quantidade de acoes"].eq(chosen_count), "motivo de escolha ou rejeicao"] = chosen_reason
    if "carteira_preferida_por_diversificacao" in comparison:
        comparison.loc[comparison["quantidade de acoes"].eq(chosen_count), "carteira_preferida_por_diversificacao"] = "diversificacao" in chosen_reason
    diagnostic_mask = comparison.get("cenario diagnostico", pd.Series(False, index=comparison.index)).fillna(False)
    comparison.loc[diagnostic_mask, "motivo de escolha ou rejeicao"] = "cenario diagnostico; nao elegivel como recomendacao final"
    if "carteira_elegivel_para_escolha_final" in comparison:
        comparison.loc[diagnostic_mask, "carteira_elegivel_para_escolha_final"] = False
    valid_non_chosen = ~comparison["quantidade de acoes"].eq(chosen_count) & comparison["status de validade"].eq("valida") & ~diagnostic_mask
    incompatible = comparison.get("carteira_valida_mas_incompativel_com_regime", pd.Series(False, index=comparison.index)).fillna(False)
    comparison.loc[valid_non_chosen & incompatible, "motivo de escolha ou rejeicao"] = "rejeitada: baixa aderencia ao regime de mercado; havia alternativa aderente"
    comparison.loc[valid_non_chosen & ~incompatible, "motivo de escolha ou rejeicao"] = "rejeitada: diversificacao/CV/Sharpe inferior ao da carteira escolhida entre carteiras aderentes"
    rejected_by_regime = comparison[valid_non_chosen & incompatible].copy()
    best_metrics["houve_rejeicao_de_carteira_por_baixa_aderencia"] = not rejected_by_regime.empty
    best_metrics["carteira_rejeitada_por_baixa_aderencia"] = "; ".join(rejected_by_regime["quantidade de acoes"].astype(str).tolist()) if not rejected_by_regime.empty else ""
    best_metrics["motivo_rejeicao_da_carteira_alternativa"] = "; ".join(rejected_by_regime.get("motivo_rejeicao_por_regime", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()) if not rejected_by_regime.empty else ""
    best_metrics["motivo_escolha_final"] = chosen_reason
    best_metrics["motivo_escolha_carteira"] = chosen_reason
    best_metrics["comparativo_carteiras"] = comparison
    best_metrics["tentativas_otimizacao"] = total_attempts
    best_metrics["carteiras_testadas"] = ", ".join(str(count) for count in counts)
    return best_portfolio, best_metrics


def validate_portfolio(portfolio: pd.DataFrame, settings: dict) -> list[str]:
    cfg = _portfolio_config(settings)
    alerts = []
    if portfolio.empty:
        return ["Carteira vazia ou invalida"]
    if "status_para_risco" in portfolio and portfolio["status_para_risco"].eq("bloqueada_para_risco").any():
        alerts.append("Carteira contem ativo bloqueado pela analise preliminar")
    if "categoria_elegibilidade" in portfolio and portfolio["categoria_elegibilidade"].eq("inelegivel").any():
        alerts.append("Carteira contem ativo inelegivel")
    if "tipo_timing" in portfolio and portfolio["tipo_timing"].eq("timing_esticado_sobrecompra").any() and not settings.get("technical_timing", {}).get("allow_overbought_entries", False):
        alerts.append("Carteira contem ativo com entrada esticada por sobrecompra")
    if "watchlist_bloqueia_otimizacao" in portfolio and portfolio["watchlist_bloqueia_otimizacao"].fillna(False).any() and not settings.get("watchlist", {}).get("allow_watchlist_entries", False):
        alerts.append("Carteira contem ativo de Watchlist bloqueante sem excecao configurada")
    total = portfolio["peso_recomendado"].sum()
    if not np.isclose(total, 1.0, atol=1e-4):
        alerts.append(f"Soma dos pesos diferente de 100%: {total:.6f}")
    if (portfolio["peso_recomendado"] < cfg["min_weight"] - 1e-6).any():
        alerts.append("Peso abaixo do minimo")
    if (portfolio["peso_recomendado"] > cfg["max_weight"] + 1e-6).any():
        alerts.append("Peso acima do maximo")
    sector_counts = portfolio["setor"].fillna("Outros").value_counts()
    if (sector_counts > cfg["max_assets_per_sector"]).any():
        alerts.append("Maximo de acoes por setor excedido")
    sector_weights = portfolio.groupby("setor")["peso_recomendado"].sum()
    if (sector_weights > cfg["hard_max_sector_weight"] + 1e-6).any():
        alerts.append("Limite setorial maximo tolerado excedido")
    elif (sector_weights > cfg["preferred_max_sector_weight"] + 1e-6).any():
        alerts.append("Limite setorial preferencial relaxado")
    if "tipo_timing" in portfolio:
        reversal = portfolio[portfolio["tipo_timing"].eq("timing_reversao_oportunidade")]
        if len(reversal) > cfg["max_reversal_assets"]:
            alerts.append("Maximo de acoes de reversao excedido")
        if not reversal.empty and reversal["peso_recomendado"].sum() > cfg["max_reversal_weight"] + 1e-6:
            alerts.append("Peso maximo de reversao excedido")
    if "tipo_watchlist" in portfolio:
        flex = portfolio[portfolio["tipo_watchlist"].eq("watchlist_flexivel")]
        if len(flex) > cfg["max_ativos_watchlist_flexivel"]:
            alerts.append("Maximo de ativos em watchlist flexivel excedido")
        if not flex.empty and flex["peso_recomendado"].sum() > cfg["max_peso_total_watchlist_flexivel"] + 1e-6:
            alerts.append("Peso maximo em watchlist flexivel excedido")
        if not flex.empty and (flex["peso_recomendado"] > cfg["peso_maximo_individual_watchlist_flexivel"] + 1e-6).any():
            alerts.append("Peso individual de watchlist flexivel acima do limite")
    if metrics_alert := portfolio.get("alerta_incompatibilidade_regime", pd.Series(False, index=portfolio.index)).fillna(False).any() if "alerta_incompatibilidade_regime" in portfolio else False:
        alerts.append("Carteira com alerta de incompatibilidade ao regime")
    invalid_return = portfolio["retorno_medio"] <= 0
    if invalid_return.any():
        alerts.append("Carteira contem ativo com retorno medio nao positivo")
    return alerts


def validation_summary(portfolio: pd.DataFrame, metrics: dict, settings: dict, alerts: list[str]) -> pd.DataFrame:
    cfg = _portfolio_config(settings)
    if portfolio.empty:
        total = 0.0
        max_weight = np.nan
        sector_name = ""
        sector_weight = np.nan
        n_assets = 0
    else:
        total = float(portfolio["peso_recomendado"].sum())
        max_weight = float(portfolio["peso_recomendado"].max())
        sector_weights = portfolio.groupby("setor")["peso_recomendado"].sum().sort_values(ascending=False)
        sector_name = str(sector_weights.index[0])
        sector_weight = float(sector_weights.iloc[0])
        n_assets = len(portfolio)
    rows = [
        {"metrica": "status da carteira", "valor": metrics.get("status_carteira", "indefinido")},
        {"metrica": "justificativa da carteira", "valor": metrics.get("justificativa_carteira", "")},
        {"metrica": "classificacao de mercado", "valor": metrics.get("mercado_classificacao", "")},
        {"metrica": "regime_mercado_data_base", "valor": metrics.get("regime_mercado_data_base", metrics.get("mercado_classificacao", ""))},
        {"metrica": "aderencia_carteira_ao_regime", "valor": metrics.get("aderencia_carteira_ao_regime", "")},
        {"metrica": "score_aderencia_regime", "valor": metrics.get("score_aderencia_regime", "")},
        {"metrica": "beta_carteira", "valor": metrics.get("beta_carteira", "")},
        {"metrica": "correlacao_carteira_ibov", "valor": metrics.get("correlacao_carteira_ibov", "")},
        {"metrica": "maior_peso_setorial", "valor": metrics.get("maior_peso_setorial", metrics.get("maior_concentracao_setorial", ""))},
        {"metrica": "setor_mais_concentrado", "valor": metrics.get("setor_mais_concentrado", metrics.get("setor_concentrado", ""))},
        {"metrica": "carteira_respeita_limite_setorial", "valor": metrics.get("carteira_respeita_limite_setorial", "")},
        {"metrica": "alerta_concentracao_setorial", "valor": metrics.get("alerta_concentracao_setorial", "")},
        {"metrica": "motivo_concentracao_setorial", "valor": metrics.get("motivo_concentracao_setorial", "")},
        {"metrica": "maior_peso_bloco_risco", "valor": metrics.get("maior_peso_bloco_risco", "")},
        {"metrica": "quantidade_blocos_risco_duplicados", "valor": metrics.get("quantidade_blocos_risco_duplicados", "")},
        {"metrica": "blocos_risco_duplicados", "valor": metrics.get("blocos_risco_duplicados", "")},
        {"metrica": "carteira_respeita_bloco_risco", "valor": metrics.get("carteira_respeita_bloco_risco", "")},
        {"metrica": "motivo_alerta_bloco_risco", "valor": metrics.get("motivo_alerta_bloco_risco", "")},
        {"metrica": "motivo_rejeicao_carteira", "valor": metrics.get("motivo_rejeicao_carteira", "")},
        {"metrica": "carteira_aderente_ao_regime", "valor": metrics.get("carteira_aderente_ao_regime", "")},
        {"metrica": "carteira_valida_mas_incompativel_com_regime", "valor": metrics.get("carteira_valida_mas_incompativel_com_regime", "")},
        {"metrica": "score_aderencia_regime_minimo", "valor": metrics.get("score_aderencia_regime_minimo", "")},
        {"metrica": "beta_carteira_minimo_exigido", "valor": metrics.get("beta_carteira_minimo_exigido", "")},
        {"metrica": "correlacao_carteira_minima_exigida", "valor": metrics.get("correlacao_carteira_minima_exigida", "")},
        {"metrica": "houve_rejeicao_de_carteira_por_baixa_aderencia", "valor": metrics.get("houve_rejeicao_de_carteira_por_baixa_aderencia", "")},
        {"metrica": "carteira_rejeitada_por_baixa_aderencia", "valor": metrics.get("carteira_rejeitada_por_baixa_aderencia", "")},
        {"metrica": "motivo_rejeicao_da_carteira_alternativa", "valor": metrics.get("motivo_rejeicao_da_carteira_alternativa", "")},
        {"metrica": "motivo_escolha_final", "valor": metrics.get("motivo_escolha_final", metrics.get("motivo_escolha_carteira", ""))},
        {"metrica": "alerta_incompatibilidade_regime", "valor": metrics.get("alerta_incompatibilidade_regime", "")},
        {"metrica": "motivo_incompatibilidade_regime", "valor": metrics.get("motivo_incompatibilidade_regime", "")},
        {"metrica": "criterio de formacao", "valor": metrics.get("criterio_formacao", "")},
        {"metrica": "carteira valida", "valor": bool(metrics.get("carteira_valida", False))},
        {"metrica": "numero de ativos", "valor": n_assets},
        {"metrica": "aprovadas para risco", "valor": metrics.get("aprovadas_para_risco", "")},
        {"metrica": "moderadas para risco", "valor": metrics.get("moderadas_para_risco", "")},
        {"metrica": "bloqueadas para risco", "valor": metrics.get("bloqueadas_para_risco", "")},
        {"metrica": "ativos permitidos para otimizacao", "valor": metrics.get("ativos_permitidos_otimizacao", "")},
        {"metrica": "ativos bloqueados com peso", "valor": metrics.get("ativos_bloqueados_com_peso", "")},
        {"metrica": "ativos de Watchlist na carteira", "valor": metrics.get("watchlist_na_carteira", "")},
        {"metrica": "ativos liberados otimizacao antes refino", "valor": metrics.get("ativos_liberados_otimizacao_antes_refino", "")},
        {"metrica": "watchlist bloqueante", "valor": metrics.get("watchlist_bloqueante", "")},
        {"metrica": "watchlist flexivel", "valor": metrics.get("watchlist_flexivel", "")},
        {"metrica": "watchlist monitoramento", "valor": metrics.get("watchlist_monitoramento", "")},
        {"metrica": "ativos alerta sinal tardio", "valor": metrics.get("ativos_alerta_sinal_tardio", "")},
        {"metrica": "ativos timing tardio", "valor": metrics.get("ativos_timing_tardio", "")},
        {"metrica": "convertidos para watchlist flexivel", "valor": metrics.get("ativos_convertidos_watchlist_flexivel", "")},
        {"metrica": "mantidos bloqueados por timing", "valor": metrics.get("ativos_mantidos_bloqueados_timing", "")},
        {"metrica": "ativos com forca relativa forte", "valor": metrics.get("forca_relativa_forte", "")},
        {"metrica": "ativos com forca relativa moderada", "valor": metrics.get("forca_relativa_moderada", "")},
        {"metrica": "ativos com forca relativa positiva relevante", "valor": metrics.get("forca_relativa_positiva_relevante", "")},
        {"metrica": "quantidades testadas", "valor": metrics.get("carteiras_testadas", "")},
        {"metrica": "observacao_execucao", "valor": metrics.get("observacao_execucao", "")},
        {"metrica": "janela_risco_inicio", "valor": metrics.get("janela_risco_inicio", "")},
        {"metrica": "janela_risco_fim", "valor": metrics.get("janela_risco_fim", "")},
        {"metrica": "janela_risco_meses", "valor": metrics.get("janela_risco_meses", "")},
        {"metrica": "periodicidade_risco", "valor": metrics.get("periodicidade_risco", "")},
        {"metrica": "tipo_retorno_risco", "valor": metrics.get("tipo_retorno_risco", "")},
        {"metrica": "quantidade_observacoes_risco", "valor": metrics.get("quantidade_observacoes_risco", "")},
        {"metrica": "calendario mercado", "valor": metrics.get("calendario_mercado", "")},
        {"metrica": "calendario fonte", "valor": metrics.get("calendario_fonte", "")},
        {"metrica": "calendario status", "valor": metrics.get("calendario_status", "")},
        {"metrica": "primeiro pregao do mes", "valor": metrics.get("primeiro_pregao_mes", "")},
        {"metrica": "ultimo pregao do mes", "valor": metrics.get("ultimo_pregao_mes", "")},
        {"metrica": "soma dos pesos", "valor": total},
        {"metrica": "maior peso individual", "valor": max_weight},
        {"metrica": "setor mais concentrado", "valor": sector_name},
        {"metrica": "peso do setor mais concentrado", "valor": sector_weight},
        {"metrica": "retorno diario da carteira", "valor": metrics.get("retorno_carteira_diario", metrics.get("retorno_carteira", np.nan))},
        {"metrica": "retorno mensal da carteira", "valor": metrics.get("retorno_carteira_mensal", np.nan)},
        {"metrica": "retorno anual da carteira", "valor": metrics.get("retorno_carteira_anual", metrics.get("retorno_anual", np.nan))},
        {"metrica": "retorno da carteira", "valor": metrics.get("retorno_carteira", np.nan)},
        {"metrica": "risco diario da carteira", "valor": metrics.get("risco_carteira_diario", metrics.get("risco_carteira", np.nan))},
        {"metrica": "risco mensal da carteira", "valor": metrics.get("risco_carteira_mensal", np.nan)},
        {"metrica": "risco anual da carteira", "valor": metrics.get("risco_carteira_anual", metrics.get("risco_anual", np.nan))},
        {"metrica": "risco da carteira", "valor": metrics.get("risco_carteira", np.nan)},
        {"metrica": "CV da carteira", "valor": metrics.get("cv_carteira", np.nan)},
        {"metrica": "beta da carteira", "valor": metrics.get("beta_carteira", np.nan)},
        {"metrica": "beta_medio_ponderado", "valor": metrics.get("beta_medio_ponderado", metrics.get("beta_carteira", np.nan))},
        {"metrica": "correlacao_carteira_ibov", "valor": metrics.get("correlacao_carteira_ibov", np.nan)},
        {"metrica": "correlacao_media_ponderada_ibov", "valor": metrics.get("correlacao_media_ponderada_ibov", metrics.get("correlacao_carteira_ibov", np.nan))},
        {"metrica": "Sharpe", "valor": metrics.get("sharpe_diario", np.nan)},
        {"metrica": "quantidade_watchlist_flexivel_carteira", "valor": metrics.get("quantidade_watchlist_flexivel", "")},
        {"metrica": "peso_total_watchlist_flexivel", "valor": metrics.get("peso_total_watchlist_flexivel", "")},
        {"metrica": "motivo_escolha_carteira", "valor": metrics.get("motivo_escolha_carteira", "")},
        {"metrica": "concentracao por setor", "valor": metrics.get("concentracao_por_setor", "")},
        {"metrica": "acoes por setor", "valor": metrics.get("acoes_por_setor", "")},
        {"metrica": "acoes de reversao", "valor": metrics.get("acoes_reversao", 0)},
        {"metrica": "peso total em reversao", "valor": metrics.get("peso_reversao", 0)},
        {"metrica": "restricoes violadas", "valor": "; ".join(alerts) or metrics.get("restricoes_violadas", "")},
        {"metrica": "peso minimo configurado", "valor": cfg["min_weight"]},
        {"metrica": "peso maximo configurado", "valor": cfg["max_weight"]},
        {"metrica": "max ativos watchlist flexivel", "valor": cfg["max_ativos_watchlist_flexivel"]},
        {"metrica": "max peso total watchlist flexivel", "valor": cfg["max_peso_total_watchlist_flexivel"]},
        {"metrica": "peso maximo individual watchlist flexivel", "valor": cfg["peso_maximo_individual_watchlist_flexivel"]},
        {"metrica": "tolerancia CV maior diversificacao", "valor": cfg["tolerancia_cv_para_maior_diversificacao"]},
        {"metrica": "limite setorial preferencial", "valor": cfg["preferred_max_sector_weight"]},
        {"metrica": "limite setorial maximo tolerado", "valor": cfg["hard_max_sector_weight"]},
        {"metrica": "maximo de acoes por setor", "valor": cfg["max_assets_per_sector"]},
        {"metrica": "maximo de acoes de reversao", "valor": cfg["max_reversal_assets"]},
        {"metrica": "peso maximo em reversao", "valor": cfg["max_reversal_weight"]},
    ]
    frame = pd.DataFrame(rows)

    def format_value(value: object) -> str:
        if isinstance(value, (bool, np.bool_)):
            return "sim" if value else "nao"
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        if isinstance(value, (int, float, np.integer, np.floating)):
            text = f"{float(value):.8f}".rstrip("0").rstrip(".")
            return text if text else "0"
        return str(value)

    frame["valor"] = frame["valor"].map(format_value)
    return frame

































