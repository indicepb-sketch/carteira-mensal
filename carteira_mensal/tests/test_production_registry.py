import json

import pandas as pd
import pytest

from production_registry import activate_month, load_active, load_methodology, load_methodology_display_name


def candidate(root, month="2026-10", checks=True):
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "methodologies").mkdir(exist_ok=True)
    (root / "output" / "excel").mkdir(parents=True, exist_ok=True)
    (root / "config" / "production_methodology.json").write_text(
        json.dumps({"version": "forward-13b-v1", "effective_from": "2026-10", "status": "production", "engine": "shadow.forward_test"}),
        encoding="utf-8",
    )
    (root / "config" / "methodologies" / "forward-13b-v1.json").write_text(
        json.dumps({"version": "forward-13b-v1", "engine": "shadow.forward_test",
                    "selection": {"portfolio_size": "unrestricted"},
                    "execution": {"scenario": "TOP15", "max_stocks": 15}}),
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


def test_active_month_requires_its_single_version_definition(tmp_path):
    path = candidate(tmp_path)
    activate_month("2026-10", path, tmp_path)
    method = load_methodology("forward-13b-v1", tmp_path)
    assert method["selection"]["portfolio_size"] == "unrestricted"
    assert method["execution"]["max_stocks"] == 15
    (tmp_path / "config" / "methodologies" / "forward-13b-v1.json").unlink()
    with pytest.raises(FileNotFoundError):
        load_active(tmp_path)


def test_public_name_keeps_the_historical_identifier(tmp_path):
    candidate(tmp_path)
    (tmp_path / "config" / "methodology_display_names.json").write_text(
        json.dumps({"forward-13b-v1": "Modelo v1.0.0"}), encoding="utf-8"
    )
    assert load_methodology_display_name("forward-13b-v1", tmp_path) == "Modelo v1.0.0"
    assert load_methodology("forward-13b-v1", tmp_path)["version"] == "forward-13b-v1"


def test_previous_month_stays_loadable_after_new_month_is_active(tmp_path):
    from production_registry import load_month

    october = candidate(tmp_path, month="2026-10")
    first = activate_month("2026-10", october, tmp_path)
    november = candidate(tmp_path, month="2026-11")
    activate_month("2026-11", november, tmp_path)
    assert load_active(tmp_path)[1]["month"] == "2026-11"
    assert load_month("2026-10", tmp_path) == (october, first)


def make_policy_v2(root, month="2026-11"):
    """Politica v2 vigente em `month`; a definicao v1 continua disponivel para os meses ja formados."""
    (root / "config" / "production_methodology.json").write_text(
        json.dumps({"version": "forward-13b-v2", "effective_from": month, "status": "production", "engine": "shadow.forward_test"}),
        encoding="utf-8",
    )
    (root / "config" / "methodologies" / "forward-13b-v2.json").write_text(
        json.dumps({"version": "forward-13b-v2", "engine": "shadow.forward_test", "selection": {"portfolio_size": "unrestricted"},
                    "execution": {"scenario": "TOP15", "max_stocks": 15}}),
        encoding="utf-8",
    )


def new_version_of(path):
    replacement = path.with_name("carteira_forward_2026_11_v20261104_233545.xlsx")
    replacement.write_bytes(path.read_bytes())
    return replacement


def test_supersede_month_archives_previous_manifest_and_keeps_history(tmp_path):
    from production_registry import supersede_month

    old = candidate(tmp_path, "2026-11")
    activate_month("2026-11", old, tmp_path)                       # v1 ativada
    make_policy_v2(tmp_path)
    replacement = new_version_of(old)
    with pytest.raises(ValueError, match="Versao ativa"):          # politica v2 ja vale: a v1 do mes so volta a validar apos a substituicao
        load_active(tmp_path)
    manifest = supersede_month("2026-11", replacement, "score completo (v2)", tmp_path)
    assert manifest["version"] == "forward-13b-v2" and manifest["file"] == replacement.name
    assert manifest["supersedes"] == {"file": old.name, "version": "forward-13b-v1", "sha256": json.loads(
        (tmp_path / "output" / "production" / "superseded" / "2026-11_forward-13b-v1.json").read_text())["sha256"]}
    archived = json.loads((tmp_path / "output" / "production" / "superseded" / "2026-11_forward-13b-v1.json").read_text())
    assert archived["version"] == "forward-13b-v1" and archived["reason"] == "score completo (v2)"
    assert archived["superseded_by_file"] == replacement.name
    assert load_active(tmp_path) == (replacement, manifest)
    assert old.exists()                                            # historico preservado em output/excel
    with pytest.raises(ValueError, match="ja esta na versao vigente"):
        supersede_month("2026-11", replacement, "de novo", tmp_path)


def test_supersede_month_rejects_invalid_requests(tmp_path):
    from production_registry import supersede_month

    old = candidate(tmp_path, "2026-11")
    activate_month("2026-11", old, tmp_path)
    make_policy_v2(tmp_path)
    other = new_version_of(old)
    with pytest.raises(ValueError, match="motivo"):
        supersede_month("2026-11", other, "  ", tmp_path)
    with pytest.raises(ValueError, match="vigencia"):              # mes anterior a vigencia da politica
        supersede_month("2026-10", other, "x", tmp_path)
    with pytest.raises(ValueError, match="sem manifesto"):         # mes que nunca foi ativado
        supersede_month("2026-12", other, "x", tmp_path)
    with pytest.raises(ValueError, match="diferente"):
        supersede_month("2026-11", old, "x", tmp_path)
    assert not (tmp_path / "output" / "production" / "superseded").exists()
    assert json.loads((tmp_path / "output" / "production" / "2026-11.json").read_text())["version"] == "forward-13b-v1"
