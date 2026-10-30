"""Execution rules shared by the portfolio dashboard and partial generator."""

from __future__ import annotations

import numpy as np
import pandas as pd


def is_cdi(row: pd.Series) -> bool:
    text = " ".join(str(row.get(column, "")).lower() for column in
                    ("ticker", "setor", "tipo_alocacao", "nome"))
    return any(name in text for name in ("cdi", "caixa", "reserva"))


def norm(frame: pd.DataFrame, column: str = "peso_recomendado") -> pd.DataFrame:
    result = frame.copy()
    if column in result.columns:
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0.0)
        if result[column].sum() > 1.5:
            result[column] /= 100.0
    return result


def rank_top(stocks: pd.DataFrame) -> pd.DataFrame:
    ranked = stocks.copy()
    ranked["_nota"] = pd.to_numeric(ranked.get("nota_final", pd.Series(index=ranked.index)),
                                    errors="coerce").fillna(-999)
    ranked["_peso"] = pd.to_numeric(ranked.get("peso_recomendado", pd.Series(index=ranked.index)),
                                    errors="coerce").fillna(0)
    return ranked.sort_values(["_nota", "_peso"], ascending=[False, False])


def executable_portfolio(
    frame: pd.DataFrame, capital: float, min_weight: float, fractional: bool,
    max_stocks: int = 15,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame(), {"stock_value": 0.0, "cdi_value": capital}
    data = norm(frame)
    stocks = data[~data.apply(is_cdi, axis=1)].copy()
    price_column = ("preco_entrada_fechamento_mes_anterior"
                    if "preco_entrada_fechamento_mes_anterior" in stocks.columns
                    else "preco_entrada" if "preco_entrada" in stocks.columns else None)
    stocks["preco_referencia"] = pd.to_numeric(stocks[price_column], errors="coerce") if price_column else np.nan
    if len(stocks) > max_stocks:
        keep = rank_top(stocks).head(max_stocks).index
        removed_top = stocks.loc[~stocks.index.isin(keep)].copy()
        stocks = stocks.loc[keep].copy()
    else:
        removed_top = stocks.iloc[0:0].copy()
    small = stocks[pd.to_numeric(stocks["peso_recomendado"], errors="coerce").fillna(0) < min_weight].copy()
    stocks = stocks.drop(index=small.index)
    removed = pd.concat([removed_top, small], ignore_index=True)
    if stocks.empty:
        cash = pd.DataFrame([{"ticker": "CDI", "nome": "Reserva/CDI liquido", "setor": "Protecao",
                              "peso_executavel": 1.0, "valor_estimado": capital,
                              "forma": "aplicacao + sobra"}])
        return cash, removed, {"stock_value": 0.0, "cdi_value": capital}
    total_weight = pd.to_numeric(stocks["peso_recomendado"], errors="coerce").sum()
    target = capital * min(total_weight, 1.0)
    stocks["peso_na_parte_acoes"] = pd.to_numeric(stocks["peso_recomendado"], errors="coerce") / total_weight
    stocks["valor_alvo"] = target * stocks["peso_na_parte_acoes"]
    raw_quantity = stocks["valor_alvo"] / stocks["preco_referencia"]
    stocks["quantidade"] = np.floor(raw_quantity if fractional else raw_quantity / 100.0) * (1 if fractional else 100)
    stocks["valor_estimado"] = stocks["quantidade"].fillna(0) * stocks["preco_referencia"].fillna(0)
    stocks = stocks[stocks["quantidade"].fillna(0) > 0].copy()
    stock_value = float(stocks["valor_estimado"].sum())
    cdi_value = max(capital - stock_value, 0.0)
    stocks["peso_executavel"] = stocks["valor_estimado"] / capital if capital else 0.0
    stocks["peso_modelo"] = stocks["peso_recomendado"]
    stocks["forma"] = "fracionario" if fractional else "lote padrao"
    cdi = pd.DataFrame([{"ticker": "CDI", "nome": "Reserva/CDI liquido", "setor": "Protecao",
                         "peso_executavel": cdi_value / capital if capital else 0.0,
                         "valor_estimado": cdi_value, "forma": "aplicacao + sobra"}])
    return pd.concat([stocks, cdi], ignore_index=True, sort=False), removed, {
        "stock_value": stock_value, "cdi_value": cdi_value,
    }


def practical_partial_return(
    executable: pd.DataFrame, assets: pd.DataFrame, cdi_return: float,
) -> tuple[float, pd.DataFrame]:
    if executable.empty or assets.empty or not {"ticker", "retorno_periodo"}.issubset(assets.columns):
        return np.nan, pd.DataFrame()
    out = executable.copy()
    source = assets.copy()
    source["ticker_key"] = source["ticker"].astype(str).str.upper()
    if source["ticker_key"].duplicated().any():
        raise ValueError("Cotacoes duplicadas por ticker")
    out["ticker_key"] = out["ticker"].astype(str).str.upper()
    columns = [name for name in ("ticker_key", "retorno_periodo", "preco_entrada", "preco_atual")
               if name in source.columns]
    out = out.merge(source[columns], on="ticker_key", how="left")
    out["retorno_pratico"] = pd.to_numeric(out["retorno_periodo"], errors="coerce")
    cash = out.apply(is_cdi, axis=1)
    if cash.any() and pd.isna(cdi_return):
        return np.nan, pd.DataFrame()
    out.loc[cash, "retorno_pratico"] = cdi_return
    if out.loc[~cash, "retorno_pratico"].isna().any():
        return np.nan, pd.DataFrame()
    out["contribuicao_pratica"] = pd.to_numeric(out["peso_executavel"], errors="coerce").fillna(0) * out["retorno_pratico"]
    return float(out["contribuicao_pratica"].sum()), out
