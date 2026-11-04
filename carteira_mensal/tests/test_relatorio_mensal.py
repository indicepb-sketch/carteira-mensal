from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import relatorio_mensal as rm  # noqa: E402


def synthetic_prices(start: str = "2023-01-02", end: str = "2026-10-05", seed: int = 7) -> pd.Series:
    index = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    return pd.Series(20 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, len(index)))), index=index)


def test_formatacao_pt_br():
    assert rm.fmt_pct(0.01006, 2, True) == "+1,01%"
    assert rm.fmt_pct(-0.0403, 2) == "-4,03%"
    assert rm.fmt_pp(-0.04022) == "-4,02 p.p."
    assert rm.fmt_brl(7135.333) == "R$ 7.135,33"
    assert rm.fmt_pct(float("nan")) == "-"
    assert rm.month_label("2026-09") == "Setembro / 2026"


def test_pt_fix_acentua_textos_das_planilhas():
    assert rm.pt_fix("zona favoravel") == "zona favorável"
    assert rm.pt_fix("favoravel com atencao") == "favorável com atenção"
    assert rm.pt_fix("queda_forte: 30% em acoes e 70% em CDI liquido") == "queda forte: 30% em ações e 70% em CDI líquido"
    assert rm.pt_fix(float("nan")) == "-"
    assert rm.title_name("BBSEGURIDADE") == "BB Seguridade"


def test_historico_estatisticas():
    history = pd.DataFrame({
        "mes": ["2026-01", "2026-02", "2026-03", "2026-04"],
        "modelo": [0.10, -0.05, 0.02, 0.03],
        "ibov": [0.05, 0.01, -0.02, 0.04],
        "cdi": [0.01, 0.01, 0.01, 0.01],
    })
    stats = rm.history_stats(history)
    assert stats["meses"] == 4
    assert stats["meses_positivos"] == 3
    assert stats["bateu_ibov"] == 2
    assert stats["acum_modelo"] == pytest.approx(1.10 * 0.95 * 1.02 * 1.03 - 1)
    assert stats["drawdown"] == pytest.approx(-0.05)
    assert stats["melhor"][0] == "2026-01"


def test_heatmap_aceita_ano_incompleto():
    history = pd.DataFrame({"mes": ["2025-12", "2026-01", "2026-02"], "modelo": [0.02, -0.01, 0.03],
                            "ibov": [0.01, 0.02, -0.01], "cdi": [0.01] * 3})
    table = rm.heatmap_table(history)
    assert len(table._cellvalues) == 1 + 2 * 2  # cabecalho + (modelo, IBOV) por ano


def test_snapshot_tecnico_usa_regras_do_robo():
    prices = synthetic_prices()
    cutoff = pd.Timestamp("2026-08-31")
    weekly = rm.weekly_close(prices[prices.index <= cutoff])
    series = {
        "prices": prices, "weekly": weekly, "last_date": prices[prices.index <= cutoff].index[-1],
        "ma": {w: rm.moving_average(weekly, w) for w in rm.MA_WINDOWS},
        "bands": rm.bollinger_bands(weekly, 20, 2.0), "rsi": rm.rsi(weekly, 14),
    }
    snapshot = rm.tech_snapshot(series)
    assert 0 <= snapshot["rsi"] <= 100
    assert snapshot["rsi_status"] == rm.classify_rsi(snapshot["rsi"])[0]
    assert snapshot["tendencia"] in {"Descarte", "Forte alta", "Aceitavel", "Fraca", "Neutra"}
    assert -1 <= snapshot["bollinger_position"] <= 2


@pytest.mark.skipif(not (ROOT / "output" / "excel" / "carteira_forward_2026_09.xlsx").exists(),
                    reason="Carteira de setembro/2026 nao esta no checkout")
def test_relatorio_completo_gera_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "load_prices", lambda ticker, start, end, allow_network=True: synthetic_prices())
    output = rm.build_report("2026-09", tmp_path / "relatorio.pdf", allow_network=False)
    content = output.read_bytes()
    assert content.startswith(b"%PDF")
    assert len(content) > 100_000
