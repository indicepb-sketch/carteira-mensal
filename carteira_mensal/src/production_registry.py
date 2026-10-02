"""Versioned, immutable monthly portfolio activation for the user platform."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_methodology(version: str, root: Path = ROOT) -> dict:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", version):
        raise ValueError("Identificador de metodologia invalido")
    method = _read_json(root / "config" / "methodologies" / f"{version}.json")
    if (method.get("version") != version or not method.get("engine")
        or not method.get("selection") or not method.get("execution")):
        raise ValueError("Definicao versionada da metodologia incompleta")
    return method


def load_methodology_display_name(version: str, root: Path = ROOT) -> str:
    load_methodology(version, root)
    names = _read_json(root / "config" / "methodology_display_names.json")
    name = names.get(version)
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"Nome publico da metodologia ausente: {version}")
    return name


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fields(path: Path, sheet: str) -> dict:
    frame = pd.read_excel(path, sheet_name=sheet)
    if not {"campo", "valor"}.issubset(frame.columns):
        raise ValueError(f"Aba {sheet} sem campo/valor")
    return dict(zip(frame["campo"].astype(str), frame["valor"]))


def validate_workbook(path: Path, month: str) -> dict:
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", month):
        raise ValueError(f"Mes invalido: {month}")
    if not path.is_file() or not path.name.startswith(f"carteira_forward_{month.replace('-', '_')}"):
        raise ValueError("Arquivo forward nao corresponde ao mes")
    summary = _fields(path, "Resumo Forward")
    base = _fields(path, "Data Base Carteira")
    if str(summary.get("mes_forward"))[:7] != month or str(base.get("mes_referencia"))[:7] != month:
        raise ValueError("Mes interno da carteira nao confere")
    formation = str(base.get("data_formacao_carteira"))[:10]
    cutoff = str(base.get("data_limite_dados_selecao"))[:10]
    if formation[:7] != month or cutoff >= formation:
        raise ValueError("Datas de formacao/selecao invalidas")
    if str(base.get("sem_look_ahead_bias")).lower() not in ("true", "1"):
        raise ValueError("Sem comprovacao de ausencia de look-ahead")
    for name in ("Validacao", "Validacao Aplicada"):
        checks = pd.read_excel(path, sheet_name=name)
        if checks.empty or "ok" not in checks or not checks["ok"].eq(True).all():
            raise ValueError(f"Restricao reprovada em {name}")
    portfolio = pd.read_excel(path, sheet_name="Carteira Aplicada")
    if portfolio.empty or not {"ticker", "peso_recomendado"}.issubset(portfolio.columns):
        raise ValueError("Carteira aplicada ausente")
    if portfolio["ticker"].isna().any() or portfolio["ticker"].duplicated().any():
        raise ValueError("Tickers ausentes ou duplicados")
    weights = pd.to_numeric(portfolio["peso_recomendado"], errors="coerce")
    if weights.isna().any() or weights.lt(0).any() or weights.sum() > 1.000001:
        raise ValueError("Pesos aplicados invalidos")
    return {"formation_date": formation, "selection_cutoff": cutoff, "sha256": _sha256(path)}


def load_active(root: Path = ROOT) -> tuple[Path, dict]:
    production = root / "output" / "production"
    manifest = _read_json(production / "active.json")
    month = manifest["month"]
    if _read_json(production / f"{month}.json") != manifest:
        raise ValueError("Registro ativo difere do arquivo mensal imutavel")
    policy = _read_json(root / "config" / "production_methodology.json")
    if manifest["version"] != policy["version"] and month >= policy["effective_from"]:
        raise ValueError("Versao ativa nao confere com a politica vigente")
    method = load_methodology(manifest["version"], root)
    if manifest["version"] == policy["version"] and method["engine"] != policy["engine"]:
        raise ValueError("Motor da metodologia diverge da politica de producao")
    path = root / "output" / "excel" / manifest["file"]
    if path.name != manifest["file"] or validate_workbook(path, month)["sha256"] != manifest["sha256"]:
        raise ValueError("Arquivo oficial mudou ou esta invalido")
    return path, manifest


def activate_month(month: str, workbook: Path, root: Path = ROOT) -> dict:
    root = root.resolve()
    workbook = workbook.resolve()
    if workbook.parent != (root / "output" / "excel").resolve():
        raise ValueError("O arquivo deve estar em output/excel")
    policy = _read_json(root / "config" / "production_methodology.json")
    if policy.get("status") != "production" or month < policy["effective_from"]:
        raise ValueError("Mes fora da vigencia da metodologia de producao")
    if load_methodology(policy["version"], root)["engine"] != policy["engine"]:
        raise ValueError("Motor da metodologia diverge da politica de producao")
    details = validate_workbook(workbook, month)
    production = root / "output" / "production"
    monthly_path = production / f"{month}.json"
    active_path = production / "active.json"
    if active_path.exists():
        active = _read_json(active_path)
        if active["month"] > month:
            raise ValueError("Nao e permitido retroceder o mes de producao")
    if monthly_path.exists():
        manifest = _read_json(monthly_path)
        if (manifest["file"], manifest["sha256"], manifest["version"]) != (
            workbook.name, details["sha256"], policy["version"]
        ):
            raise ValueError("Mes ja ativado: crie nova versao sem sobrescrever o historico")
    else:
        manifest = {
            "month": month,
            "version": policy["version"],
            "file": workbook.name,
            **details,
            "activated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(monthly_path, manifest)
    _write_json(active_path, manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ativa uma carteira mensal validada em producao.")
    parser.add_argument("--mes", required=True)
    parser.add_argument("--arquivo", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(activate_month(arguments.mes, arguments.arquivo), ensure_ascii=False, indent=2))
