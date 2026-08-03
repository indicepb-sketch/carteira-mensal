from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils import CollectionRecord, ROOT, date_window, now_iso

LOGGER = logging.getLogger(__name__)


def load_assets(path: Path | None = None) -> pd.DataFrame:
    path = path or ROOT / "config" / "ativos.csv"
    return pd.read_csv(path)


def _price_column(df: pd.DataFrame, adjusted: bool) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    if isinstance(df.columns, pd.MultiIndex):
        wanted = "Adj Close" if adjusted else "Close"
        for col in df.columns:
            if col[0] == wanted:
                return df[col]
        for col in df.columns:
            if col[0] == "Close":
                return df[col]
        return pd.Series(dtype=float)
    if adjusted and "Adj Close" in df:
        return df["Adj Close"]
    if "Close" in df:
        return df["Close"]
    return pd.Series(dtype=float)


def _series_from_batch(df: pd.DataFrame, ticker: str, adjusted: bool) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float, name=ticker)
    wanted = "Adj Close" if adjusted else "Close"
    if isinstance(df.columns, pd.MultiIndex):
        if (ticker, wanted) in df.columns:
            return df[(ticker, wanted)].dropna().rename(ticker)
        if (wanted, ticker) in df.columns:
            return df[(wanted, ticker)].dropna().rename(ticker)
        if ticker in df.columns.get_level_values(0):
            return _price_column(df[ticker], adjusted).dropna().rename(ticker)
        if ticker in df.columns.get_level_values(1):
            sub = df.xs(ticker, axis=1, level=1, drop_level=True)
            return _price_column(sub, adjusted).dropna().rename(ticker)
        return pd.Series(dtype=float, name=ticker)
    return _price_column(df, adjusted).dropna().rename(ticker)


def _download_yfinance(yf: Any, ticker: str, start: pd.Timestamp, end: pd.Timestamp, retries: int) -> tuple[pd.DataFrame, str]:
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            df = yf.download(
                ticker,
                start=start.date(),
                end=(end + pd.Timedelta(days=1)).date(),
                progress=False,
                auto_adjust=False,
                threads=False,
            )
            if not df.empty:
                return df, f"ok tentativa {attempt}"
            last_error = f"serie vazia na tentativa {attempt}"
        except Exception as exc:  # noqa: BLE001 - collection must keep going
            last_error = f"tentativa {attempt}: {exc}"
            LOGGER.warning("Falha ao coletar %s no yfinance: %s", ticker, exc)
        time.sleep(0.5 * attempt)
    return pd.DataFrame(), last_error or "serie vazia"


def _download_yfinance_batch(yf: Any, tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, str]:
    if not tickers:
        return pd.DataFrame(), "sem tickers"
    try:
        df = yf.download(
            tickers=" ".join(tickers),
            start=start.date(),
            end=(end + pd.Timedelta(days=1)).date(),
            progress=False,
            auto_adjust=False,
            group_by="ticker",
            threads=True,
        )
        return df, "download em lote"
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Falha no download em lote do yfinance: %s", exc)
        return pd.DataFrame(), str(exc)


def _fetch_price_series(
    yf: Any,
    requested_ticker: str,
    months: int,
    adjusted: bool,
    retries: int,
    fallbacks: list[str] | None = None,
    min_rows: int = 1,
) -> tuple[pd.Series, list[dict[str, Any]], str | None]:
    records: list[dict[str, Any]] = []
    start, end = date_window(months)
    candidates = [requested_ticker] + [ticker for ticker in (fallbacks or []) if ticker != requested_ticker]
    for candidate in candidates:
        df, message = _download_yfinance(yf, candidate, start, end, retries)
        series = _price_column(df, adjusted).dropna()
        status = "ok" if len(series) >= min_rows else "insufficient" if len(series) else "missing"
        records.append(
            CollectionRecord(
                requested_ticker,
                "preco_ajustado",
                f"yfinance:{candidate}",
                now_iso(),
                float(series.iloc[-1]) if len(series) else np.nan,
                status,
                message if candidate == requested_ticker else f"fallback {candidate}: {message}",
            ).to_dict()
        )
        if len(series) >= min_rows:
            series = series.sort_index()
            series.name = requested_ticker
            raw_path = ROOT / "data" / "raw" / f"prices_{requested_ticker.replace('.', '_')}_via_{candidate.replace('.', '_').replace('^', '')}.csv"
            df.to_csv(raw_path)
            return series, records, candidate
    return pd.Series(dtype=float, name=requested_ticker), records, None


def _proxy_from_fallbacks(
    yf: Any,
    requested_ticker: str,
    fallback_tickers: list[str],
    months: int,
    adjusted: bool,
    retries: int,
    min_rows: int,
) -> tuple[pd.Series, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    components = []
    for ticker in fallback_tickers:
        series, ticker_records, used = _fetch_price_series(yf, ticker, months, adjusted, retries, [], min_rows)
        records.extend(ticker_records)
        if used and len(series) >= min_rows:
            normalized = series / series.dropna().iloc[0] * 100
            normalized.name = ticker
            components.append(normalized)
    if not components:
        records.append(CollectionRecord(requested_ticker, "proxy_setorial", "yfinance:fallbacks", now_iso(), np.nan, "error", "Nenhum fallback com dados suficientes").to_dict())
        return pd.Series(dtype=float, name=requested_ticker), records
    proxy = pd.concat(components, axis=1).dropna(how="all").mean(axis=1).dropna()
    proxy.name = requested_ticker
    records.append(
        CollectionRecord(
            requested_ticker,
            "proxy_setorial",
            "yfinance:" + ",".join([item.name for item in components]),
            now_iso(),
            float(proxy.iloc[-1]) if len(proxy) else np.nan,
            "ok" if len(proxy) >= min_rows else "insufficient",
            "Proxy equal-weight usado para indice sem historico suficiente",
        ).to_dict()
    )
    return proxy, records


def fetch_yfinance_prices(
    tickers: list[str],
    months: int,
    adjusted: bool = True,
    fallback_map: dict[str, list[str]] | None = None,
    retries: int = 3,
    min_rows: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    records: list[dict[str, Any]] = []
    prices: dict[str, pd.Series] = {}
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        for ticker in tickers:
            records.append(CollectionRecord(ticker, "preco_ajustado", "yfinance", now_iso(), np.nan, "error", str(exc)).to_dict())
        return pd.DataFrame(), pd.DataFrame(records)

    fallback_map = fallback_map or {}
    unique_tickers = list(dict.fromkeys(tickers))
    start, end = date_window(months)
    batch, batch_message = _download_yfinance_batch(yf, unique_tickers, start, end)
    missing: list[str] = []
    for ticker in unique_tickers:
        series = _series_from_batch(batch, ticker, adjusted).dropna().sort_index()
        status = "ok" if len(series) >= min_rows else "insufficient" if len(series) else "missing"
        records.append(
            CollectionRecord(
                ticker,
                "preco_ajustado",
                f"yfinance:{ticker}:batch",
                now_iso(),
                float(series.iloc[-1]) if len(series) else np.nan,
                status,
                batch_message,
            ).to_dict()
        )
        if len(series) >= min_rows:
            prices[ticker] = series.rename(ticker)
        else:
            missing.append(ticker)

    for ticker in missing:
        series, ticker_records, _ = _fetch_price_series(yf, ticker, months, adjusted, retries, fallback_map.get(ticker, []), min_rows)
        records.extend(ticker_records)
        if len(series) >= min_rows:
            prices[ticker] = series
        else:
            records.append(CollectionRecord(ticker, "preco_ajustado", "yfinance", now_iso(), np.nan, "error", "Sem dados suficientes apos lote, retries e fallback").to_dict())
    if not prices:
        return pd.DataFrame(), pd.DataFrame(records)
    return pd.concat(prices.values(), axis=1).sort_index(), pd.DataFrame(records)


def fetch_index_prices(index_map: dict[str, str], months: int, adjusted: bool = True, settings: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    settings = settings or {}
    data_settings = settings.get("data", {})
    fallbacks_by_index = data_settings.get("index_fallbacks", {})
    retries = int(data_settings.get("download_retries", 3))
    min_rows = int(data_settings.get("min_price_rows", 120))
    records: list[dict[str, Any]] = []
    prices: dict[str, pd.Series] = {}
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        for name, ticker in index_map.items():
            records.append(CollectionRecord(ticker, "preco_indice", "yfinance", now_iso(), np.nan, "error", f"{name}: {exc}").to_dict())
        return pd.DataFrame(), pd.DataFrame(records)

    for name, ticker in index_map.items():
        series, ticker_records, used = _fetch_price_series(yf, ticker, months, adjusted, retries, [], min_rows)
        records.extend(ticker_records)
        if not used:
            proxy, proxy_records = _proxy_from_fallbacks(yf, ticker, fallbacks_by_index.get(name, []), months, adjusted, retries, min_rows)
            records.extend(proxy_records)
            series = proxy
        if len(series) >= min_rows:
            series.name = ticker
            prices[ticker] = series
    if not prices:
        return pd.DataFrame(), pd.DataFrame(records)
    return pd.concat(prices.values(), axis=1).sort_index(), pd.DataFrame(records)


def daily_risk_free_rate(settings: dict) -> float:
    annual = float(settings["risk_free_rate"].get("annual_rate", 0.0))
    days = int(settings["risk"]["trading_days_year"])
    return (1 + annual) ** (1 / days) - 1
