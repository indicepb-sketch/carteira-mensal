from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from utils import CollectionRecord, normalize_ticker, now_iso, safe_float

LOGGER = logging.getLogger(__name__)

EXPECTED_FIELDS = [
    "roe",
    "roic",
    "margem_bruta",
    "pl_atual",
    "liquidez_media",
    "setor_fundamentus",
    "subsetor_fundamentus",
]

LABEL_MAP = {
    "roe": "roe",
    "roic": "roic",
    "marg bruta": "margem_bruta",
    "marg ebit": "margem_ebit",
    "marg liquida": "margem_liquida",
    "p/l": "pl_atual",
    "p/vp": "pvp",
    "div yield": "dividend_yield",
    "div liquida/patrim": "divida_liquida_patrimonio",
    "div br/patrim": "divida_liquida_patrimonio",
    "cresc rec (5a)": "crescimento_receita_5a",
    "valor de mercado": "valor_mercado",
    "vol $ med (2m)": "liquidez_media",
    "setor": "setor_fundamentus",
    "subsetor": "subsetor_fundamentus",
}


def _normalize_label(text: str) -> str:
    text = text.replace("?", " ").replace(":", " ").replace(".", " ").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_percent(text: str) -> float:
    value = safe_float(text)
    return value / 100 if not pd.isna(value) else np.nan


def _parse_value(key: str, raw: str) -> Any:
    if key in {"roe", "roic", "margem_bruta", "margem_ebit", "margem_liquida", "dividend_yield", "crescimento_receita_5a"}:
        return _parse_percent(raw)
    if key in {"pl_atual", "pl_anterior", "pvp", "liquidez_media", "divida_liquida_patrimonio", "valor_mercado"}:
        return safe_float(raw)
    return raw.strip() if raw and raw.strip() else np.nan


def _empty_result(ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "roe": np.nan,
        "roic": np.nan,
        "margem_bruta": np.nan,
        "margem_ebit": np.nan,
        "margem_liquida": np.nan,
        "pl_atual": np.nan,
        "pl_anterior": np.nan,
        "pvp": np.nan,
        "dividend_yield": np.nan,
        "divida_liquida_patrimonio": np.nan,
        "crescimento_receita_5a": np.nan,
        "valor_mercado": np.nan,
        "liquidez_media": np.nan,
        "setor_fundamentus": np.nan,
        "subsetor_fundamentus": np.nan,
        "fonte_fundamentos": "fundamentus",
        "alertas_fundamentos": "",
    }


def fetch_fundamentus(ticker: str, timeout: int = 20) -> tuple[dict[str, Any], list[CollectionRecord]]:
    base = normalize_ticker(ticker)
    url = f"https://www.fundamentus.com.br/detalhes.php?papel={base}"
    records: list[CollectionRecord] = []
    result = _empty_result(ticker)
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        cells = [td.get_text(" ", strip=True) for td in soup.find_all("td")]
        found: set[str] = set()
        for idx, label in enumerate(cells[:-1]):
            key = LABEL_MAP.get(_normalize_label(label))
            if not key or key in found:
                continue
            value = _parse_value(key, cells[idx + 1])
            result[key] = value
            found.add(key)
            records.append(CollectionRecord(ticker, key, "fundamentus", now_iso(), value, "ok", url))

        # Fundamentus exposes current P/L, but not a reliable previous P/L on this page.
        if pd.isna(result["pl_anterior"]):
            records.append(CollectionRecord(ticker, "pl_anterior", "fundamentus", now_iso(), np.nan, "missing", "Campo nao disponivel na pagina de detalhes"))

        missing = [field for field in EXPECTED_FIELDS if pd.isna(result[field])]
        if missing:
            result["alertas_fundamentos"] = "Dados ausentes: " + ", ".join(missing)
            already_logged = {record.field for record in records}
            for field in missing:
                if field not in already_logged:
                    records.append(CollectionRecord(ticker, field, "fundamentus", now_iso(), np.nan, "missing", "Campo nao encontrado"))
    except Exception as exc:  # noqa: BLE001 - collection must continue for other assets
        LOGGER.warning("Falha ao coletar fundamentos de %s: %s", ticker, exc)
        result["alertas_fundamentos"] = f"Falha na coleta de fundamentos: {exc}"
        for field in EXPECTED_FIELDS:
            records.append(CollectionRecord(ticker, field, "fundamentus", now_iso(), np.nan, "error", str(exc)))
    return result, records


def collect_fundamentals(tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    records: list[dict[str, Any]] = []
    for ticker in tickers:
        data, audit = fetch_fundamentus(ticker)
        rows.append(data)
        records.extend(record.to_dict() for record in audit)
    return pd.DataFrame(rows), pd.DataFrame(records)





