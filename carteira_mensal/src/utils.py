from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from dateutil.relativedelta import relativedelta


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CollectionRecord:
    item: str
    field: str
    source: str
    collected_at: str
    value: Any
    status: str
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if isinstance(data["value"], (pd.Timestamp, datetime)):
            data["value"] = str(data["value"])
        return data


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def load_settings(path: Path | None = None) -> dict[str, Any]:
    path = path or ROOT / "config" / "settings.yaml"
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def setup_logging() -> Path:
    log_dir = ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"execucao_{datetime.now():%Y_%m_%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )
    return log_path


def first_business_day(date: datetime | None = None) -> pd.Timestamp:
    ref = pd.Timestamp(date or datetime.today()).replace(day=1)
    while ref.weekday() >= 5:
        ref += pd.Timedelta(days=1)
    return ref.normalize()


def date_window(months: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    end = pd.Timestamp.today().normalize()
    start = end - relativedelta(months=months)
    return start, end


def safe_float(value: Any) -> float:
    if value is None:
        return np.nan
    if isinstance(value, str):
        value = value.replace("%", "").replace(".", "").replace(",", ".").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def normalize_ticker(ticker: str) -> str:
    return ticker.upper().replace(".SA", "")


def alert_join(values: list[str]) -> str:
    cleaned = []
    for value in values:
        if value is None or pd.isna(value):
            continue
        text = str(value).strip()
        if text:
            cleaned.append(text)
    return "; ".join(cleaned)

