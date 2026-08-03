from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from data_loader import load_assets, _series_from_batch
from utils import ROOT, now_iso

LOGGER = logging.getLogger(__name__)

B3_IBOV_URL = "https://sistemaswebb3-listados.b3.com.br/indexProxy/indexCall/GetPortfolioDay/"


def _b3_payload(page_number: int = 1, page_size: int = 120) -> str:
    payload = {
        "language": "pt-br",
        "pageNumber": page_number,
        "pageSize": page_size,
        "index": "IBOV",
        "segment": "1",
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _ticker_to_yfinance(ticker: str) -> str:
    return f"{str(ticker).strip().upper()}.SA"


def _empty_universe_summary(mode: str, source: str, alert: str = "") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"metrica": "modo_configurado", "valor": mode},
            {"metrica": "fonte_do_universo", "valor": source},
            {"metrica": "quantidade_ativos_coletados", "valor": 0},
            {"metrica": "quantidade_ativos_validados", "valor": 0},
            {"metrica": "ativos_removidos_sem_cotacao", "valor": 0},
            {"metrica": "ativos_removidos_falha_temporaria_cotacao", "valor": 0},
            {"metrica": "data_coleta", "valor": now_iso()},
            {"metrica": "alerta_universo", "valor": alert},
        ]
    )


def fetch_ibovespa_theoretical_portfolio(timeout: int = 30) -> pd.DataFrame:
    url = B3_IBOV_URL + _b3_payload()
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        },
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("results", [])
    if not rows:
        raise RuntimeError("Carteira teorica do IBOV veio vazia no endpoint da B3")
    collected_at = now_iso()
    frame = pd.DataFrame(rows)
    frame = frame.rename(columns={"cod": "ticker_original", "asset": "nome"})
    frame["ticker_original"] = frame["ticker_original"].astype(str).str.strip().str.upper()
    frame["ticker_yfinance"] = frame["ticker_original"].map(_ticker_to_yfinance)
    frame["setor"] = np.nan
    frame["subsetor"] = np.nan
    frame["fonte"] = "B3 carteira teorica IBOV"
    frame["data_coleta"] = collected_at
    frame["status_validacao"] = "nao_validado"
    frame = frame.drop_duplicates(subset=["ticker_yfinance"]).reset_index(drop=True)
    return frame[
        [
            "ticker_original",
            "ticker_yfinance",
            "nome",
            "setor",
            "subsetor",
            "fonte",
            "data_coleta",
            "status_validacao",
        ]
    ]


def _validate_with_yfinance(
    tickers: list[str],
    fallback_map: dict[str, list[str]] | None = None,
    retries: int = 3,
) -> dict[str, str]:
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Nao foi possivel importar yfinance para validar universo: %s", exc)
        return {ticker: f"erro_validacao: {exc}" for ticker in tickers}

    fallback_map = fallback_map or {}
    unique_tickers = list(dict.fromkeys(tickers))
    statuses: dict[str, str] = {ticker: "sem_cotacao" for ticker in unique_tickers}
    try:
        data = yf.download(
            tickers=" ".join(unique_tickers),
            period="2mo",
            interval="1d",
            progress=False,
            auto_adjust=False,
            group_by="ticker",
            threads=True,
        )
        for ticker in unique_tickers:
            series = _series_from_batch(data, ticker, adjusted=False)
            if len(series.dropna()) > 0:
                statuses[ticker] = "validado"
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Falha na validacao em lote do universo via yfinance: %s", exc)
        statuses = {ticker: f"erro_validacao_lote: {exc}" for ticker in unique_tickers}

    missing = [ticker for ticker, status in statuses.items() if not str(status).startswith("validado")]
    for ticker in missing:
        last_error = str(statuses.get(ticker, "sem_cotacao"))
        for attempt in range(1, max(1, retries) + 1):
            try:
                data = yf.download(ticker, period="6mo", interval="1d", progress=False, auto_adjust=False, threads=False)
                series = _series_from_batch(data, ticker, adjusted=False)
                if len(series.dropna()) > 0:
                    statuses[ticker] = f"validado_retry_individual_tentativa_{attempt}"
                    LOGGER.info("Ticker %s validado em retry individual na tentativa %s", ticker, attempt)
                    break
                last_error = f"retry_individual_{attempt}: serie vazia"
            except Exception as exc:  # noqa: BLE001
                last_error = f"retry_individual_{attempt}: {exc}"
                LOGGER.warning("Falha temporaria ao validar %s no yfinance: %s", ticker, exc)
            time.sleep(0.4 * attempt)
        if str(statuses.get(ticker, "")).startswith("validado"):
            continue
        for candidate in fallback_map.get(ticker, []):
            try:
                data = yf.download(candidate, period="6mo", interval="1d", progress=False, auto_adjust=False, threads=False)
                if not data.empty:
                    statuses[ticker] = f"validado_por_fallback:{candidate}"
                    break
                last_error = f"fallback {candidate}: serie vazia"
            except Exception as exc:  # noqa: BLE001
                last_error = f"fallback {candidate}: {exc}"
        if not str(statuses.get(ticker, "")).startswith("validado"):
            statuses[ticker] = f"falha_temporaria_cotacao: {last_error}"
    return statuses

def _assets_from_universe(universe: pd.DataFrame) -> pd.DataFrame:
    valid = universe[universe["status_validacao"].astype(str).str.startswith("validado")].copy()
    return pd.DataFrame(
        {
            "ticker": valid["ticker_yfinance"],
            "nome": valid["nome"],
            "setor": valid["setor"].fillna("Outros"),
            "subsetor": valid["subsetor"].fillna(""),
        }
    ).drop_duplicates(subset=["ticker"]).reset_index(drop=True)


def _csv_to_universe(assets: pd.DataFrame, source: str, status: str = "fallback_csv") -> pd.DataFrame:
    collected_at = now_iso()
    frame = pd.DataFrame(
        {
            "ticker_original": assets["ticker"].astype(str).str.replace(".SA", "", regex=False),
            "ticker_yfinance": assets["ticker"].astype(str),
            "nome": assets.get("nome", pd.Series([""] * len(assets))),
            "setor": assets.get("setor", pd.Series(["Outros"] * len(assets))),
            "subsetor": assets.get("subsetor", pd.Series([""] * len(assets))),
            "fonte": source,
            "data_coleta": collected_at,
            "status_validacao": status,
        }
    )
    return frame


def _save_universe(universe: pd.DataFrame, settings: dict) -> Path | None:
    if not settings.get("universe", {}).get("save_downloaded_universe", True):
        return None
    year_month = datetime.today().strftime("%Y_%m")
    path = ROOT / "data" / "processed" / f"universo_ibovespa_{year_month}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    universe.to_csv(path, index=False)
    return path


def _summary(universe: pd.DataFrame, mode: str, source: str, alert: str = "", saved_path: Path | None = None) -> pd.DataFrame:
    validated = universe["status_validacao"].astype(str).str.startswith("validado")
    removed = (~validated).sum()
    return pd.DataFrame(
        [
            {"metrica": "modo_configurado", "valor": mode},
            {"metrica": "fonte_do_universo", "valor": source},
            {"metrica": "quantidade_ativos_coletados", "valor": int(len(universe))},
            {"metrica": "quantidade_ativos_validados", "valor": int(validated.sum())},
            {"metrica": "ativos_removidos_sem_cotacao", "valor": int(removed)},
            {"metrica": "ativos_removidos_falha_temporaria_cotacao", "valor": int(universe["status_validacao"].astype(str).str.startswith("falha_temporaria_cotacao").sum())},
            {"metrica": "data_coleta", "valor": now_iso()},
            {"metrica": "arquivo_universo_salvo", "valor": str(saved_path) if saved_path else ""},
            {"metrica": "alerta_universo", "valor": alert},
            {"metrica": "tickers_usados_na_analise", "valor": ", ".join(universe.loc[validated, "ticker_yfinance"].tolist())},
        ]
    )


def load_universe(settings: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
    universe_settings = settings.get("universe", {})
    mode = universe_settings.get("mode", "custom_csv")
    custom_csv_path = ROOT / universe_settings.get("custom_csv_path", "config/ativos.csv")
    alerts: list[str] = []

    if mode == "custom_csv":
        assets = load_assets(custom_csv_path)
        universe = _csv_to_universe(assets, "config/ativos.csv", "custom_csv")
        summary = _summary(universe, mode, "config/ativos.csv")
        return assets, universe, summary, alerts

    if mode != "ibovespa_online":
        raise ValueError(f"Modo de universo nao suportado: {mode}")

    try:
        universe = fetch_ibovespa_theoretical_portfolio()
        statuses = _validate_with_yfinance(universe["ticker_yfinance"].tolist(), settings.get("data", {}).get("ticker_fallbacks", {}), retries=int(settings.get("data", {}).get("download_retries", 3)))
        universe["status_validacao"] = universe["ticker_yfinance"].map(statuses).fillna("sem_cotacao")
        saved_path = _save_universe(universe, settings)
        assets = _assets_from_universe(universe)
        if assets.empty:
            raise RuntimeError("Nenhum ativo da carteira online do IBOV foi validado com cotacao")
        summary = _summary(universe, mode, "B3 carteira teorica IBOV", saved_path=saved_path)
        return assets, universe, summary, alerts
    except Exception as exc:  # noqa: BLE001 - fallback is part of the methodology
        message = f"Falha ao coletar universo online do Ibovespa: {exc}"
        LOGGER.warning(message)
        alerts.append(message)
        if not universe_settings.get("fallback_to_custom_csv", True):
            summary = _empty_universe_summary(mode, "B3 carteira teorica IBOV", message)
            return pd.DataFrame(columns=["ticker", "nome", "setor", "subsetor"]), pd.DataFrame(), summary, alerts
        assets = load_assets(custom_csv_path)
        universe = _csv_to_universe(assets, f"fallback:{custom_csv_path}", "fallback_csv")
        saved_path = _save_universe(universe, settings)
        fallback_alert = message + "; usando config/ativos.csv como fallback reduzido"
        summary = _summary(universe, mode, f"fallback:{custom_csv_path}", fallback_alert, saved_path)
        alerts.append("Universo reduzido de fallback usado")
        return assets, universe, summary, alerts



