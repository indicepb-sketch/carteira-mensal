import json

import pandas as pd
import pytest

from production_registry import activate_month, load_active


def candidate(root, month="2026-10", checks=True):
    (root / "config").mkdir()
    (root / "output" / "excel").mkdir(parents=True)
    (root / "config" / "production_methodology.json").write_text(
        json.dumps({"version": "forward-13b-v1", "effective_from": "2026-10", "status": "production"}),
        encoding="utf-8",
    )
    path = root / "output" / "excel" / f"carteira_forward_{month.replace('-', '_')}.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame([{"campo": "mes_forward", "valor": month}]).to_excel(writer, sheet_name="Resumo Forward", index=False)
        pd.DataFrame([
            {"campo": "mes_referencia", "valor": month},
            {"campo": "data_formacao_carteira", "valor": f"{month}-01"},
            {"campo": "data_limite_dados_selecao", "valor": "2026-09-30"},
            {"campo": "sem_look_ahead_bias", "valor": True},
        ]).to_excel(writer, sheet_name="Data Base Carteira", index=False)
        pd.DataFrame([{"restricao": "check", "ok": checks}]).to_excel(writer, sheet_name="Validacao", index=False)
        pd.DataFrame([{"restricao": "check", "ok": checks}]).to_excel(writer, sheet_name="Validacao Aplicada", index=False)
        pd.DataFrame([{"ticker": "ABEV3.SA", "peso_recomendado": 1.0}]).to_excel(writer, sheet_name="Carteira Aplicada", index=False)
    return path


def test_activation_is_pinned_and_idempotent(tmp_path):
    path = candidate(tmp_path)
    first = activate_month("2026-10", path, tmp_path)
    assert activate_month("2026-10", path, tmp_path) == first
    assert load_active(tmp_path) == (path, first)
    assert json.loads((tmp_path / "output" / "production" / "2026-10.json").read_text()) == first


def test_rejected_candidate_never_becomes_active(tmp_path):
    path = candidate(tmp_path, checks=False)
    with pytest.raises(ValueError, match="Restricao"):
        activate_month("2026-10", path, tmp_path)
    assert not (tmp_path / "output" / "production" / "active.json").exists()


def test_modified_file_is_not_served_or_reactivated(tmp_path):
    path = candidate(tmp_path)
    activate_month("2026-10", path, tmp_path)
    with path.open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="mudou|Mes ja ativado"):
        activate_month("2026-10", path, tmp_path)
    with pytest.raises(ValueError, match="mudou"):
        load_active(tmp_path)


def test_no_rollback_to_earlier_month(tmp_path):
    path = candidate(tmp_path)
    activate_month("2026-10", path, tmp_path)
    with pytest.raises(ValueError, match="vigencia"):
        activate_month("2026-09", path, tmp_path)
