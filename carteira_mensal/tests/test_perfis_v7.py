"""Metodologia v7: dois perfis (Moderado e Arrojado) e funil de 35 candidatas."""
import shutil
from pathlib import Path

import pytest

import forward_test as ft
import perfil as profiles
from production_registry import activate_month, load_active, load_methodology
from test_production_registry import candidate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def moderate_profile_after_each_test():
    yield
    ft.set_profile("moderado")


def test_moderate_profile_keeps_the_legacy_paths_and_arrojado_gets_its_own_folders():
    for perfil_dir in (profiles.excel_dir, profiles.production_dir, profiles.pdf_dir):
        legacy, scoped = perfil_dir("moderado", ROOT), perfil_dir("arrojado", ROOT)
        assert perfil_dir(None, ROOT) == legacy and scoped == legacy / "arrojado"
    assert profiles.history_file("arrojado", ROOT).parent == profiles.excel_dir("arrojado", ROOT)
    assert profiles.display_name("ARROJADO") == "Arrojado" and profiles.display_name(None) == "Moderado"
    with pytest.raises(ValueError):
        profiles.normalize("agressivo")


def test_v7_only_adds_the_profiles_and_the_funnel_to_the_v6_selection():
    v6, v7 = load_methodology("forward-13b-v6", ROOT), load_methodology("forward-13b-v7", ROOT)
    extra = {"pre_risk_candidates", "profiles", "default_profile"}
    assert {k: v for k, v in v7["selection"].items() if k not in extra} == v6["selection"] and v7["execution"] == v6["execution"]
    assert v7["selection"]["pre_risk_candidates"] == 35 and v7["changelog"]["vs"] == "forward-13b-v6"
    assert set(v7["selection"]["profiles"]) == {"moderado", "arrojado"}


def test_profiles_differ_only_in_the_exposure_during_a_strong_decline():
    assert ft.regime_policy("2026-11")["exposure_queda_forte"] == 0.20                  # perfil padrao: Moderado
    ft.set_profile("arrojado")
    policy = ft.regime_policy("2026-11")
    assert policy["exposure_queda_forte"] == 1.00 and policy["trend_below"] == 0.20     # mesmo gatilho do regime
    assert ft.exposure_for_metrics({"subtipo_queda": "queda_forte"}, "2026-11")[0] == 1.00
    assert ft.exposure_for_metrics({"subtipo_queda": "alta"}, "2026-11")[0] == 1.00
    ft.set_profile("moderado")
    assert ft.exposure_for_metrics({"subtipo_queda": "queda_forte"}, "2026-11")[0] == 0.20


def test_unknown_profile_is_refused():
    with pytest.raises(ValueError):
        ft.set_profile("agressivo")


def test_months_before_the_effective_month_form_only_the_moderate_profile_and_keep_the_old_funnel():
    assert ft.profiles_for_month("2026-10") == ["moderado"] and ft.candidate_funnel_size("2026-10") is None
    assert ft.profiles_for_month("2026-11") == ["moderado", "arrojado"] and ft.candidate_funnel_size("2026-11") == 35
    assert ft.profiles_for_month("2026-03", force=True) == ["moderado", "arrojado"]


def test_arrojado_portfolio_is_registered_apart_from_the_moderate_one(tmp_path):
    moderate_file = candidate(tmp_path)
    moderate = activate_month("2026-10", moderate_file, tmp_path)
    arrojado_dir = profiles.excel_dir("arrojado", tmp_path)
    arrojado_dir.mkdir(parents=True)
    arrojado_file = arrojado_dir / moderate_file.name                      # mesmo nome, pasta propria
    shutil.copy(moderate_file, arrojado_file)
    with pytest.raises(ValueError, match="deve estar em"):
        activate_month("2026-10", moderate_file, tmp_path, perfil="arrojado")     # nao se ativa o arquivo do Moderado como Arrojado
    manifest = activate_month("2026-10", arrojado_file, tmp_path, perfil="arrojado")
    assert manifest["perfil"] == "arrojado" and profiles.production_dir("arrojado", tmp_path).joinpath("active.json").is_file()
    assert load_active(tmp_path, "arrojado") == (arrojado_file, manifest)
    assert load_active(tmp_path) == (moderate_file, moderate) and "perfil" not in moderate


def test_official_november_portfolios_of_both_profiles_share_the_stocks_and_differ_only_in_exposure():
    import pandas as pd

    moderate_path, moderate = load_active(ROOT)
    arrojado_path, arrojado = load_active(ROOT, "arrojado")
    assert moderate["version"] == arrojado["version"] == "forward-13b-v7" and moderate["month"] == arrojado["month"] == "2026-11"
    assert arrojado["perfil"] == "arrojado" and "perfil" not in moderate
    assert moderate_path.parent == profiles.excel_dir("moderado", ROOT) and arrojado_path.parent == profiles.excel_dir("arrojado", ROOT)
    stocks = {}
    for name, path in (("moderado", moderate_path), ("arrojado", arrojado_path)):
        applied = pd.read_excel(path, sheet_name="Carteira Aplicada")
        stocks[name] = applied[applied["ticker"] != "CDI"]
    assert set(stocks["moderado"]["ticker"]) == set(stocks["arrojado"]["ticker"])
    assert float(stocks["moderado"]["peso_recomendado"].sum()) <= 0.25 and float(stocks["arrojado"]["peso_recomendado"].sum()) >= 0.90


def test_both_profile_histories_cover_the_same_82_months_and_are_labelled_by_profile():
    import pandas as pd

    frames = {}
    for perfil in ("moderado", "arrojado"):
        path = profiles.history_file(perfil, ROOT)
        frames[perfil] = pd.read_excel(path, sheet_name="Mes a Mes")
        notes = dict(zip(*pd.read_excel(path, sheet_name="Metodologia").iloc[:, :2].T.values))
        assert "forward-13b-v7" in str(notes["versao_metodologia"]) and perfil in str(notes["perfil"]).lower()
    expected = list(pd.period_range("2020-01", "2026-10", freq="M").astype(str))
    assert frames["moderado"]["mes"].tolist() == frames["arrojado"]["mes"].tolist() == expected
    assert (frames["arrojado"]["peso_acoes"] >= frames["moderado"]["peso_acoes"] - 1e-9).all()      # sem defesa: nunca menos em acoes
