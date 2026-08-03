from __future__ import annotations

import re
import unicodedata

import pandas as pd

from technical_indicators import calculate_technical_snapshot


def _normalize(value: object) -> str:
    text = "" if pd.isna(value) else str(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def classify_sector(row: pd.Series) -> str:
    positive = 0
    negative = 0
    if row.get("retorno_ytd", 0) > 0:
        positive += 1
    elif row.get("retorno_ytd", 0) < 0:
        negative += 1
    if row.get("mm9", 0) > row.get("mm21", 0):
        positive += 1
    elif row.get("mm9", 0) < row.get("mm21", 0):
        negative += 1
    if row.get("preco_atual", 0) > row.get("mm50", 0):
        positive += 1
    elif row.get("preco_atual", 0) < row.get("mm50", 0):
        negative += 1
    if positive >= 2:
        return "alta"
    if negative >= 2:
        return "baixa"
    return "neutro"


def infer_sector_index(setor: object, subsetor: object, settings: dict) -> tuple[str, bool]:
    text = _normalize(f"{setor or ''} {subsetor or ''}")
    if any(key in text for key in ["banco", "financeir", "segur", "previdencia", "servicos financeiros"]):
        return "IFNC", False
    if any(key in text for key in ["energia eletrica", "petroleo", "gas", "biocombust", "saneamento", "agua"]):
        return "IEEX", False
    if any(key in text for key in ["miner", "sider", "metal", "papel", "celulose", "madeira", "quimic", "petroquimic"]):
        return "IMAT", False
    if any(key in text for key in ["bebida", "comerc", "varejo", "alimento", "saude", "medic", "transporte", "educ", "aluguel", "construcao", "tecido", "vestuario", "calcado", "diversos"]):
        return "ICON", False
    if any(key in text for key in ["imove", "exploracao de imove"]):
        return "IFIX", False
    configured = settings.get("data", {}).get("sector_index_map", {})
    direct = configured.get(str(setor), None)
    if direct:
        return direct, direct == "IBOV"
    return "IBOV", True


def analyze_sector_indexes(index_prices: pd.DataFrame, settings: dict) -> pd.DataFrame:
    reverse = {ticker: name for name, ticker in settings["data"]["indexes"].items()}
    rows = []
    for ticker in index_prices.columns:
        snapshot = calculate_technical_snapshot(index_prices[ticker].dropna(), settings)
        snapshot["indice"] = reverse.get(ticker, ticker)
        rows.append(snapshot)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["tendencia_setorial"] = frame.apply(classify_sector, axis=1)
    return frame


def apply_sector_mapping(frame: pd.DataFrame, sector_frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    mapped = frame.copy()
    source_sector = mapped.get("setor_fundamentus", mapped.get("setor", "Outros")).fillna(mapped.get("setor", "Outros"))
    source_subsetor = mapped.get("subsetor_fundamentus", mapped.get("subsetor", "")).fillna(mapped.get("subsetor", ""))
    inferred = [infer_sector_index(setor, subsetor, settings) for setor, subsetor in zip(source_sector, source_subsetor)]
    mapped["indice_setorial"] = [item[0] for item in inferred]
    mapped["indice_setorial_fallback_ibov"] = [item[1] for item in inferred]
    trend_by_index = dict(zip(sector_frame.get("indice", []), sector_frame.get("tendencia_setorial", [])))
    mapped["tendencia_setorial"] = mapped["indice_setorial"].map(trend_by_index).fillna("neutro")
    return mapped


def map_asset_sector_trend(assets: pd.DataFrame, sector_frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    mapped = assets.copy()
    inferred = [infer_sector_index(row.get("setor", "Outros"), row.get("subsetor", ""), settings) for _, row in mapped.iterrows()]
    mapped["indice_setorial"] = [item[0] for item in inferred]
    mapped["indice_setorial_fallback_ibov"] = [item[1] for item in inferred]
    trend_by_index = dict(zip(sector_frame.get("indice", []), sector_frame.get("tendencia_setorial", [])))
    mapped["tendencia_setorial"] = mapped["indice_setorial"].map(trend_by_index).fillna("neutro")
    return mapped
