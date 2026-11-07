from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import forward_test as ft  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
from production_registry import load_active, load_methodology, load_month  # noqa: E402
from utils import load_settings  # noqa: E402

SEPTEMBER = ROOT / "output" / "excel" / "carteira_recomendada_2026_09_v1.xlsx"
SEPTEMBER_FORWARD = ROOT / "output" / "excel" / "carteira_forward_2026_09.xlsx"
SCORE_COLUMNS = sh.SCORE_FEATURE_COLUMNS
needs_september = pytest.mark.skipif(not (SEPTEMBER.exists() and SEPTEMBER_FORWARD.exists()), reason="bases de setembro/2026 ausentes")


@pytest.fixture(autouse=True)
def restore_flag():
    original = sh.COMPLETE_CANDIDATE_FEATURES
    yield
    sh.COMPLETE_CANDIDATE_FEATURES = original


def test_completion_only_fills_absent_columns(tmp_path):
    path = tmp_path / "base.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"ticker": ["AAA3.SA", "BBB3.SA"], "mm9": [10.0, 20.0], "roe": [0.30, 0.05],
                      "tendencia_setorial": ["alta", "neutro"]}).to_excel(writer, sheet_name="Analise Preliminar", index=False)
    candidates = pd.DataFrame({"ticker": ["AAA3.SA", "BBB3.SA"], "roe": [0.99, np.nan]})   # roe ja existe: nao e sobrescrita
    out = sh.complete_candidate_features(candidates, path)
    assert out["mm9"].tolist() == [10.0, 20.0]
    assert out["tendencia_setorial"].tolist() == ["alta", "neutro"]
    assert out["roe"].iloc[0] == 0.99 and np.isnan(out["roe"].iloc[1])


def test_completion_is_noop_when_columns_exist(tmp_path):
    frame = pd.DataFrame({"ticker": ["AAA3.SA"], **{c: [1.0] for c in SCORE_COLUMNS}})
    assert sh.complete_candidate_features(frame, tmp_path / "inexistente.xlsx").equals(frame)


@needs_september
def test_flag_off_keeps_current_behaviour_and_on_adds_score_columns():
    settings = load_settings()
    sh.COMPLETE_CANDIDATE_FEATURES = False
    off = sh.load_candidate_input(SEPTEMBER, settings)
    assert not set(SCORE_COLUMNS) & set(off.columns)            # comportamento atual: colunas ausentes
    sh.COMPLETE_CANDIDATE_FEATURES = True
    on = sh.load_candidate_input(SEPTEMBER, settings)
    for column in SCORE_COLUMNS:
        assert column in on.columns and on[column].notna().any(), column
    assert on["ticker"].tolist() == off["ticker"].tolist()


def test_policy_enables_completion_only_from_the_effective_month():
    assert ft.candidate_completion_enabled("2026-09") is False
    assert ft.candidate_completion_enabled("2026-10") is False          # outubro ja formado na v2 (manifesto proprio)
    assert ft.candidate_completion_enabled("2026-11") is True
    assert ft.candidate_completion_enabled("2027-03") is True
    assert ft.candidate_completion_enabled("2027-03") is True
    assert load_methodology("forward-13b-v1", ROOT)["selection"].get("complete_candidate_features") is None
    assert load_methodology("forward-13b-v2", ROOT)["selection"]["complete_candidate_features"] is True


def test_each_version_only_adds_its_own_flag_to_the_previous_definition():
    v1, v2, v3, v4 = (load_methodology(f"forward-13b-v{i}", ROOT) for i in (1, 2, 3, 4))
    assert {k: v for k, v in v2["selection"].items() if k != "complete_candidate_features"} == v1["selection"]
    assert {k: v for k, v in v3["selection"].items() if k != "min_one_share_per_position"} == v2["selection"]
    assert v3["selection"]["min_one_share_per_position"] is True and "min_one_share_per_position" not in v2["selection"]
    assert {k: v for k, v in v4["selection"].items() if k != "weighting"} == v3["selection"]
    assert v4["selection"]["weighting"]["method"] == "min_variance_lw" and "weighting" not in v3["selection"]
    assert v2["execution"] == v1["execution"] == v3["execution"] == v4["execution"] and v2["engine"] == v1["engine"] == v3["engine"] == v4["engine"]


def test_registry_serves_the_right_version_per_month_and_keeps_the_history():
    active_path, active = load_active(ROOT)
    assert active["version"] == "forward-13b-v4" and active["month"] == "2026-11"
    expected = {"2026-10": ("forward-13b-v2", "forward-13b-v1"), "2026-11": ("forward-13b-v4", "forward-13b-v3")}
    for month, (version, previous) in expected.items():
        _, manifest = load_month(month, ROOT)
        assert manifest["version"] == version and manifest["supersedes"]["version"] == previous
        archived = ROOT / "output" / "production" / "superseded" / f"{month}_{previous}.json"
        assert archived.is_file() and (ROOT / "output" / "excel" / manifest["supersedes"]["file"]).is_file()
    assert (ROOT / "output" / "production" / "superseded" / "2026-11_forward-13b-v1.json").is_file()   # historico completo preservado


@needs_september
def test_september_formation_unchanged_after_change_and_regime_unaffected_by_v2():
    """Setembro (queda forte, Sinal A): a carteira reproduz a de producao com v1 e nao muda com o score completo."""
    settings = load_settings()
    production = pd.read_excel(SEPTEMBER_FORWARD, sheet_name="Carteira Aplicada")
    production = production[~production["ticker"].astype(str).str.contains("CDI", case=False)]
    expected = production.set_index("ticker")["peso_modelo_100pct"].astype(float)

    result_v1 = ft.run_forward_portfolio("2026-09", SEPTEMBER, settings)       # politica: v2 so vale a partir de 2026-10
    weights_v1 = result_v1["portfolio"].set_index("ticker")["peso_recomendado"].astype(float)
    assert set(weights_v1.index) == set(expected.index)
    assert (weights_v1.reindex(expected.index) - expected).abs().max() < 1e-6
    assert float(result_v1["portfolio"]["score_tendencia"].sum()) == 0.0          # v1: tendencia zerada (comportamento original)

    original = ft.candidate_completion_enabled
    ft.candidate_completion_enabled = lambda month: True
    try:
        result_v2 = ft.run_forward_portfolio("2026-09", SEPTEMBER, settings)
    finally:
        ft.candidate_completion_enabled = original
    assert result_v2["metrics"]["forward_13b_bucket"] == result_v1["metrics"]["forward_13b_bucket"]
    assert float(result_v2["portfolio"]["score_tendencia"].sum()) > 0              # v2: nota com 5 componentes
    weights_v2 = result_v2["portfolio"].set_index("ticker")["peso_recomendado"].astype(float)
    assert (weights_v2.reindex(expected.index) - expected).abs().max() < 1e-6      # queda: Sinal A nao depende da nota
    assert sh.COMPLETE_CANDIDATE_FEATURES is False                                  # flag restaurada apos a execucao
