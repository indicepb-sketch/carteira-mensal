"""Precos NOMINAIS (o que foi cotado na epoca) para a regra de 1 acao e para as quantidades do historico.

O historico usa o fechamento ajustado do Yahoo (`Adj Close`: ajustado por desdobramentos, grupamentos e dividendos) para calcular RETORNOS; isso esta certo. Mas para saber se o capital de
referencia compra 1 acao, e quantas, e preciso o preco que estava na tela: um grupamento posterior multiplica o preco antigo no ajustado (HAPV3 aparecia a R$ 192 em nov/2020) e os dividendos o reduzem.

Reconstrucao: o `Close` do Yahoo ja vem ajustado so por desdobramentos/grupamentos; multiplicando-o pelo produto das razoes dos eventos POSTERIORES a cada data obtem-se o nominal:
    nominal(d) = Close(d) x produto(razao dos eventos com data > d)       (razao 2,0 = desdobramento de 2 por 1; 0,1 = grupamento de 10 por 1)
Saida: data/history_cache/precos_nominais.csv (mesma forma de precos_ajustados.csv). Uso: python scripts/precos_nominais.py --atualizar
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ADJUSTED_CSV = ROOT / "data" / "history_cache" / "precos_ajustados.csv"
NOMINAL_CSV = ROOT / "data" / "history_cache" / "precos_nominais.csv"


def nominal_from_split_adjusted(close: pd.Series, splits: pd.Series) -> pd.Series:
    """Fechamento ajustado so por eventos -> preco nominal da epoca. `splits`: razao por data do evento (indice datetime)."""
    close = close.astype(float)
    if splits is None or len(splits) == 0:
        return close
    ratios = pd.Series(splits).astype(float).sort_index()
    ratios.index = pd.to_datetime(ratios.index).tz_localize(None).normalize()
    ratios = ratios.groupby(level=0).prod()
    ratios = ratios[(ratios > 0) & np.isfinite(ratios)]
    if ratios.empty:
        return close
    cumulative = ratios.cumprod()
    until_date = cumulative.reindex(close.index, method="ffill").fillna(1.0)       # produto dos eventos com data <= d
    future = float(cumulative.iloc[-1]) / until_date                                # produto dos eventos com data > d
    return close * future


def build(tickers: list[str], start: str = "2015-12-01", end: str = "2026-12-31") -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(tickers, start=start, end=end, auto_adjust=False, progress=False, group_by="column", threads=True)
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    close.index = pd.to_datetime(close.index).tz_localize(None)
    out = {}
    for ticker in tickers:
        if ticker not in close.columns:
            continue
        try:
            splits = yf.Ticker(ticker).splits
        except Exception:  # noqa: BLE001  sem eventos conhecidos: o fechamento ajustado so por eventos e o proprio nominal
            splits = pd.Series(dtype=float)
        out[ticker] = nominal_from_split_adjusted(close[ticker].dropna(), splits)
    return pd.DataFrame(out).sort_index()


def load_nominal() -> pd.DataFrame:
    frame = pd.read_csv(NOMINAL_CSV, index_col=0, parse_dates=True)
    frame.columns = [str(c).upper() for c in frame.columns]
    return frame


def main() -> None:
    if "--atualizar" not in sys.argv:
        raise SystemExit("Use --atualizar para reconstruir data/history_cache/precos_nominais.csv (consulta o Yahoo).")
    tickers = [str(c) for c in pd.read_csv(ADJUSTED_CSV, nrows=1, index_col=0).columns]
    frame = build(tickers)
    NOMINAL_CSV.parent.mkdir(parents=True, exist_ok=True)
    frame.round(4).to_csv(NOMINAL_CSV)
    print(f"Gerado {NOMINAL_CSV} com {frame.shape[1]} papeis e {len(frame)} pregoes")


if __name__ == "__main__":
    main()
