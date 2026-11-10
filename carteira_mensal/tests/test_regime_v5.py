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
from production_registry import load_methodology  # noqa: E402


def inputs(trend, pos=np.nan, above50=np.nan):
    return {"pct_tendencia_favoravel": trend, "pct_ativos_positivos_1m": pos, "pct_preco_acima_mm50": above50, "pct_rsi_50_70": np.nan,
            "ibov_ret_1m_formacao": 0.01, "rsi_ibov": np.nan, "bollinger_ibov": "", "subtipo_favoravel_producao": ""}


def test_v5_changes_only_the_defense_parameters_over_v4():
    v4, v5 = load_methodology("forward-13b-v4", ROOT), load_methodology("forward-13b-v5", ROOT)
    assert v5["selection"]["regime_trigger"]["trend_favorable_below"] == 0.20 and "regime_trigger" not in v4["selection"]
    assert v5["selection"]["exposure_by_regime"]["queda_forte"] == 0.20 and v4["selection"]["exposure_by_regime"]["queda_forte"] == 0.30
    rest = lambda m: {k: v for k, v in m["selection"].items() if k not in {"regime_trigger", "exposure_by_regime"}}  # noqa: E731
    assert rest(v5) == rest(v4) and v5["execution"] == v4["execution"]
    assert {k: v for k, v in v5["selection"]["exposure_by_regime"].items() if k != "queda_forte"} == {k: v for k, v in v4["selection"]["exposure_by_regime"].items() if k != "queda_forte"}


def test_policy_applies_from_the_effective_month_and_force_ignores_the_date():
    assert ft.regime_policy("2026-10") is None
    for month in ("2026-11", "2027-01"):
        cfg = ft.regime_policy(month)
        assert cfg["trend_below"] == 0.20 and cfg["exposure_queda_forte"] == 0.20 and cfg["version"] == "forward-13b-v7"
    assert ft.regime_policy("2024-05", force=True)["trend_below"] == 0.20


def test_trend_trigger_uses_the_policy_threshold():
    # 28% de tendencia: antes (35%) era queda forte; na v5 (20%) deixa de ser; abaixo de 20% continua sendo
    assert ft.classify_regime(inputs(0.28), "2026-10")[0] == "queda_forte"
    assert ft.classify_regime(inputs(0.28), "2026-11")[0] != "queda_forte"
    assert ft.classify_regime(inputs(0.19), "2026-11")[0] == "queda_forte"
    assert ft.classify_regime(inputs(0.28), "2024-05", force=True)[0] != "queda_forte"


def test_second_rule_still_triggers_defense_in_v5():
    # amplitude de 1 mes < 35% com tendencia/preco acima da MM50 enfraquecidos continua queda forte, mesmo com o termometro alto
    assert ft.classify_regime(inputs(0.56, pos=0.33, above50=0.64), "2026-11")[0] == "queda_forte"
    assert ft.classify_regime(inputs(0.56, pos=0.50, above50=0.64), "2026-11")[0] != "queda_forte"


def test_exposure_in_deep_decline_follows_the_policy():
    metrics = {"subtipo_queda": "queda_forte"}
    assert ft.exposure_for_metrics(metrics)[0] == 0.30                               # sem mes: comportamento original
    assert ft.exposure_for_metrics(metrics, "2026-10")[0] == 0.30                    # antes da vigencia
    assert ft.exposure_for_metrics(metrics, "2026-11")[0] == 0.20
    assert "20%" in ft.exposure_for_metrics(metrics, "2026-11")[1] and "80%" in ft.exposure_for_metrics(metrics, "2026-11")[1]
    assert ft.exposure_for_metrics({"subtipo_queda": "alta"}, "2026-11")[0] == 1.0
    assert ft.exposure_for_metrics(metrics, "2024-05", force=True)[0] == 0.20


def test_reconstruction_of_breadth_inputs_from_the_stock_sheet(tmp_path):
    workbook = tmp_path / "base.xlsx"
    stocks = pd.DataFrame({"ticker": list("ABCD"), "retorno_acumulado_1m": [0.05, -0.02, 0.01, -0.10], "preco_atual": [10, 8, 12, 5], "mm50": [9, 9, 10, 6], "rsi": [55, 45, 65, 30]})
    with pd.ExcelWriter(workbook) as writer:
        stocks.to_excel(writer, sheet_name="Analise Preliminar", index=False)
    rec = ft.reconstruct_regime_inputs(workbook, pd.Timestamp("2026-10-30"))
    assert rec["pct_ativos_positivos_1m"] == pytest.approx(0.5)
    assert rec["pct_preco_acima_mm50"] == pytest.approx(0.5)
    assert rec["pct_rsi_50_70"] == pytest.approx(0.5)


def test_reconstruction_matches_the_stored_values_in_2026():
    """Validacao: nos meses de 2026 (campos guardados) a reconstrucao concorda com o guardado dentro de 0,02."""
    workbook = sorted((ROOT / "output" / "excel").glob("carteira_recomendada_2026_05_v*.xlsx"))
    if not workbook:
        pytest.skip("base de maio/2026 ausente")
    stored = ft.forward_13b_inputs("2026-05", workbook[-1])
    rec = ft.reconstruct_regime_inputs(workbook[-1], pd.Timestamp("2026-04-30"))
    for key in ("pct_ativos_positivos_1m", "pct_preco_acima_mm50", "pct_rsi_50_70"):
        assert rec[key] == pytest.approx(float(stored[key]), abs=0.02)
