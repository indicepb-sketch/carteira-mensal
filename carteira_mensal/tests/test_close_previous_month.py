import subprocess
import sys

import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))
import atualizacao_diaria as daily


def test_previous_month_and_last_weekday_helpers():
    assert daily.previous_month("2026-11") == "2026-10"
    assert daily.previous_month("2027-01") == "2026-12"
    assert daily.last_weekday_of("2026-10") == "2026-10-30"  # 31/10/2026 e sabado
    assert daily.last_weekday_of("2026-07") == "2026-07-31"


def _partial(path, status):
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame([{"metrica": "status", "valor": status}]).to_excel(writer, sheet_name="Resumo Parcial", index=False)


def test_closed_detection_uses_status_of_any_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "EXCEL_DIR", tmp_path)
    _partial(tmp_path / "parcial_carteira_forward_2026_10_v20261029_021541.xlsx", "parcial_mes_em_andamento")
    assert not daily.previous_month_is_closed("2026-10")
    _partial(tmp_path / "parcial_carteira_forward_2026_10_v20261103_114223.xlsx", "fechamento_mes")
    assert daily.previous_month_is_closed("2026-10")


def test_close_previous_month_skips_when_closed_and_survives_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    monkeypatch.setattr(daily, "EXCEL_DIR", tmp_path / "excel")
    (tmp_path / "excel").mkdir()
    (tmp_path / "output" / "production").mkdir(parents=True)
    (tmp_path / "output" / "production" / "2026-10.json").write_text("{}")
    calls = []

    def failing(args):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(daily, "run_command", failing)
    daily.close_previous_month("2026-11")  # nao propaga o erro
    assert calls and "--data-avaliacao" in calls[0] and "2026-10-30" in calls[0]

    _partial(tmp_path / "excel" / "parcial_carteira_forward_2026_10_v20261103_114223.xlsx", "fechamento_mes")
    calls.clear()
    daily.close_previous_month("2026-11")
    assert calls == []
