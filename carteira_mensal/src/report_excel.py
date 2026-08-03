from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from utils import ROOT


def _sheet_name(name: str) -> str:
    return name[:31]


def _next_versioned_path(directory: Path, prefix: str, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(prefix)}_v(\d+){re.escape(suffix)}$")
    versions = []
    for file in directory.glob(f"{prefix}_v*{suffix}"):
        match = pattern.match(file.name)
        if match:
            versions.append(int(match.group(1)))
    return directory / f"{prefix}_v{max(versions, default=0) + 1}{suffix}"


def _writable_excel_path(path: Path) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        return path
    except FileExistsError:
        prefix = re.sub(r"_v\d+$", "", path.stem)
        return _writable_excel_path(_next_versioned_path(path.parent, prefix, path.suffix))
    except PermissionError:
        prefix = re.sub(r"_v\d+$", "", path.stem)
        return _writable_excel_path(_next_versioned_path(path.parent, prefix, path.suffix))


def write_excel(tables: dict[str, pd.DataFrame], year_month: str) -> Path:
    output_dir = ROOT / "output" / "excel"
    base_path = _next_versioned_path(output_dir, f"carteira_recomendada_{year_month}", ".xlsx")
    path = _writable_excel_path(base_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        for name, frame in tables.items():
            safe = _sheet_name(name)
            data = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
            data = data.loc[:, ~data.columns.duplicated()].copy()
            data.to_excel(writer, sheet_name=safe, index=False)
            worksheet = writer.sheets[safe]
            for idx, col in enumerate(data.columns):
                width = min(max(len(str(col)) + 2, 12), 40)
                worksheet.set_column(idx, idx, width)
    return path