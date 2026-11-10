"""Perfis de investidor da metodologia forward-13b-v7 (Moderado e Arrojado).

O perfil Moderado usa exatamente os caminhos de sempre. O Arrojado usa subpastas proprias (output/excel/arrojado, output/production/arrojado, output/pdf/arrojado)
com os MESMOS nomes de arquivo, de modo que nenhum padrao de busca do Moderado alcance um arquivo do Arrojado.
A base mensal (carteira_recomendada_AAAA_MM_v*.xlsx) e unica e compartilhada pelos dois perfis.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = "moderado"
PROFILES = ("moderado", "arrojado")
DISPLAY_NAMES = {"moderado": "Moderado", "arrojado": "Arrojado"}


def normalize(perfil: str | None) -> str:
    key = (perfil or DEFAULT_PROFILE).strip().lower()
    if key not in PROFILES:
        raise ValueError(f"Perfil desconhecido: {perfil!r} (validos: {', '.join(PROFILES)})")
    return key


def display_name(perfil: str | None) -> str:
    return DISPLAY_NAMES[normalize(perfil)]


def _scoped(base: Path, perfil: str | None) -> Path:
    key = normalize(perfil)
    return base if key == DEFAULT_PROFILE else base / key


def excel_dir(perfil: str | None = None, root: Path = ROOT) -> Path:
    return _scoped(Path(root) / "output" / "excel", perfil)


def production_dir(perfil: str | None = None, root: Path = ROOT) -> Path:
    return _scoped(Path(root) / "output" / "production", perfil)


def pdf_dir(perfil: str | None = None, root: Path = ROOT) -> Path:
    return _scoped(Path(root) / "output" / "pdf", perfil)


def history_file(perfil: str | None = None, root: Path = ROOT) -> Path:
    return excel_dir(perfil, root) / "historico_v2_top15_capital.xlsx"


def profiles_for_month(month: str, root: Path = ROOT, force: bool | None = None) -> list[str]:
    """Perfis formados no mes: os da metodologia vigente (v7: moderado e arrojado); antes da vigencia ou nas versoes anteriores, so o Moderado."""
    import json

    from production_registry import load_methodology

    policy = json.loads((Path(root) / "config" / "production_methodology.json").read_text(encoding="utf-8"))
    if force is None and month < str(policy.get("effective_from", "9999-12")):
        return [DEFAULT_PROFILE]
    return list(methodology_profiles(load_methodology(policy["version"], Path(root))["selection"]))


def methodology_profiles(selection: dict) -> dict:
    """Perfis definidos na metodologia (v7 em diante); metodologias anteriores tem so o Moderado, com a exposicao de `exposure_by_regime`."""
    profiles = selection.get("profiles")
    if profiles:
        return {normalize(name): dict(cfg) for name, cfg in profiles.items()}
    return {DEFAULT_PROFILE: {"nome": DISPLAY_NAMES[DEFAULT_PROFILE], "exposure_by_regime": {}}}
