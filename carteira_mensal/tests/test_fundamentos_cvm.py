"""Fundamentos da CVM na formacao mensal (metodologia v6): mapeamento de codigos, escolha da fonte por vigencia, plano de contas e fluxo de 12 meses."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import coletor_fundamentos_cvm as cvm  # noqa: E402
import forward_test as ft  # noqa: E402
import fundamentos_cvm_live as live  # noqa: E402


def frame(rows, cd=1, ref="2026-06-30"):
    """Linhas (CD_CONTA, DS_CONTA, VL_CONTA[, ORDEM, DT_INI_EXERC]) no formato dos arquivos da CVM."""
    data = []
    for row in rows:
        code, desc, value = row[:3]
        order = row[3] if len(row) > 3 else "ÚLTIMO"
        start = row[4] if len(row) > 4 else None
        data.append({"CD_CVM": cd, "DT_REFER": ref, "CD_CONTA": code, "DS_CONTA": desc, "VL_CONTA": value, "ORDEM_EXERC": order, "DT_INI_EXERC": start, "ESCALA_MOEDA": "MIL"})
    return pd.DataFrame(data)


def test_cvm_code_map_covers_the_whole_universe_with_unique_integer_codes():
    codes = live.load_cvm_codes()
    assert len(codes) >= 78 and codes["ticker"].is_unique and codes["cd_cvm"].notna().all()
    assert codes["cd_cvm"].map(lambda v: float(v).is_integer() and v > 0).all()
    assert {"ITUB4.SA", "BBAS3.SA", "WEGE3.SA", "BBSE3.SA", "PETR4.SA"}.issubset(set(codes["ticker"]))


def test_metadata_accepts_tickers_with_or_without_suffix_and_flags_unknown_ones():
    meta = live.metadata_for(["WEGE3", "WEGE3.SA", "XXXX9"])
    assert meta.loc[0, "cd_cvm"] == meta.loc[1, "cd_cvm"] and pd.notna(meta.loc[0, "cd_cvm"])
    assert pd.isna(meta.loc[2, "cd_cvm"])


def test_fundamentals_source_follows_the_effective_month_and_force_overrides_it():
    assert ft.fundamentals_source("2026-10") == "fundamentus"            # outubro ja foi formado antes da v6
    assert ft.fundamentals_source("2026-11") == "cvm"
    assert ft.fundamentals_source("2027-01") == "cvm"
    assert ft.fundamentals_source("2026-03", force=True) == "cvm"        # refazer o historico na metodologia vigente


def test_v6_methodology_keeps_every_v5_selection_rule_and_only_adds_the_cvm_source():
    from production_registry import load_methodology

    v5, v6 = load_methodology("forward-13b-v5", ROOT), load_methodology("forward-13b-v6", ROOT)
    keep = {k: v for k, v in v6["selection"].items() if k not in {"fundamentals_source", "fundamentals"}}
    assert keep == v5["selection"] and v6["execution"] == v5["execution"]
    assert v6["selection"]["fundamentals_source"] == "cvm" and v6["changelog"]["vs"] == "forward-13b-v5"


def test_main_schema_has_every_column_the_monthly_pipeline_reads_and_marks_cvm_as_source():
    raw = pd.DataFrame({"ticker": ["A.SA", "B.SA"], "roe": [0.2, np.nan], "roic": [0.1, np.nan], "margem_bruta": [0.3, np.nan], "margem_liquida": [0.1, np.nan], "pl_atual": [10.0, np.nan],
                        "patrimonio_liquido": [100.0, 50.0], "valor_mercado_estimado": [250.0, np.nan], "divida_liquida_patrimonio": [0.5, np.nan],
                        "dados_fundamentalistas_insuficientes": [False, True], "motivo_dado_insuficiente": ["", "campos ausentes: roe"]})
    out = live.to_main_schema(raw)
    assert list(out.columns) == live.OUTPUT_COLUMNS
    assert (out["fonte_fundamentos"] == "cvm").all()
    assert out.loc[0, "pvp"] == pytest.approx(2.5) and pd.isna(out.loc[1, "pvp"])
    assert out.loc[0, "alertas_fundamentos"] == "" and "insuficientes" in out.loc[1, "alertas_fundamentos"]
    assert out[["dividend_yield", "crescimento_receita_5a", "liquidez_media", "setor_fundamentus"]].isna().all().all()      # papeis fora do mapa de setores ficam sem setor


def test_sector_map_covers_the_universe_and_is_applied_to_the_monthly_schema():
    sectors, codes = live.load_sector_map(), live.load_cvm_codes()
    assert set(codes["ticker"]).issubset(set(sectors.index)) and (sectors["setor"] != "").all()
    raw = pd.DataFrame({"ticker": ["WEGE3.SA", "ITUB4"], "dados_fundamentalistas_insuficientes": [False, False], "motivo_dado_insuficiente": ["", ""]})
    out = live.to_main_schema(raw).set_index("ticker")
    assert out.loc["WEGE3.SA", "setor_fundamentus"] == sectors.loc["WEGE3.SA", "setor"] and out["setor_fundamentus"].notna().all()


def test_sector_map_is_identical_to_the_one_used_to_rebuild_the_history():
    import shadow_backtest_2025 as bt25

    history_map, sectors = bt25.load_sector_map(), live.load_sector_map()
    assert all(sectors.loc[t, "setor"] == s for t, s in history_map.items() if t in sectors.index)


def test_ifrs_bank_equity_and_profit_are_found_by_description_not_by_code():
    bpp = frame([("2.01", "Passivos Financeiros ao Valor Justo", 5), ("2.03", "Passivos Financeiros ao Custo Amortizado", 80), ("2.07", "Patrimônio Líquido", 20), ("2.07.05", "Participação dos Não Controladores", 2)])
    dre = frame([("3.01", "Receitas de Intermediação", 100), ("3.09", "Lucro ou Prejuízo Líquido Consolidado do Período", 12), ("3.09.01", "Atribuído a Sócios da Empresa Controladora", 11), ("3.09.02", "Atribuído a Sócios Não Controladores", 1)])
    ref = pd.Timestamp("2026-06-30")
    assert cvm.equity_codes(bpp, 1, ref) == ("2.07", "2.07.05", True)
    assert cvm.profit_codes(dre, 1, ref) == ("3.09", "3.09.01", "3.09.02")


def test_industrial_company_uses_the_standard_codes_and_is_not_flagged_financial():
    bpp = frame([("2.03", "Patrimônio Líquido Consolidado", 1000), ("2.03.09", "Participação dos Não Controladores", 50)])
    dre = frame([("3.11", "Lucro/Prejuízo Consolidado do Período", 100), ("3.11.01", "Atribuído a Sócios da Empresa Controladora", 90), ("3.11.02", "Atribuído a Sócios Não Controladores", 10)])
    ref = pd.Timestamp("2026-06-30")
    assert cvm.equity_codes(bpp, 1, ref) == ("2.03", "2.03.09", False)
    assert cvm.profit_codes(dre, 1, ref) == ("3.11", "3.11.01", "3.11.02")


def test_trailing_twelve_months_adds_the_last_fiscal_year_and_subtracts_the_prior_year_to_date():
    ref = pd.Timestamp("2026-06-30")
    itr = frame([("3.11", "Lucro", 70, "ÚLTIMO", "2026-01-01"), ("3.11", "Lucro", 55, "PENÚLTIMO", "2025-01-01")])           # acumulado de jan-jun de 2026 e de 2025
    dfp = frame([("3.11", "Lucro", 200)], ref="2025-12-31")                                                                  # exercicio de 2025
    store = SimpleNamespace(zips={("DFP", 2025): object()}, read=lambda kind, year, part: dfp, frames={})
    assert cvm.flow_ttm(store, "ITR", 2026, ref, 1, "3.11", itr) == pytest.approx((200 + 70 - 55) * 1000.0)
    assert cvm.flow_ttm(store, "DFP", 2025, pd.Timestamp("2025-12-31"), 1, "3.11", dfp) == pytest.approx(200 * 1000.0)


def test_trailing_twelve_months_is_missing_when_the_prior_year_data_is_absent():
    ref = pd.Timestamp("2026-06-30")
    itr = frame([("3.11", "Lucro", 70, "ÚLTIMO", "2026-01-01")])                                                              # sem o acumulado do ano anterior
    store = SimpleNamespace(zips={}, read=lambda kind, year, part: itr, frames={})
    assert np.isnan(cvm.flow_ttm(store, "ITR", 2026, ref, 1, "3.11", itr))


def _zip_bytes():
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("a.csv", "x;y\n1;2\n")
    return buffer.getvalue()


def test_cvm_download_retries_after_a_network_failure_and_caches_the_zip(tmp_path, monkeypatch):
    import coletor_historico_2025 as col
    import requests

    content, calls = _zip_bytes(), {"n": 0}

    class Response:
        def __init__(self, body):
            self.content = body

        def raise_for_status(self):
            pass

    def fake_get(url, timeout=None, headers=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("Read timed out")
        return Response(content)

    monkeypatch.setattr(col, "CVM_CACHE_DIR", tmp_path)
    monkeypatch.setattr(col.requests, "get", fake_get)
    monkeypatch.setattr(col.time, "sleep", lambda seconds: None)
    assert col.download_cvm_zip("ITR", 2026) == content and calls["n"] == 3          # duas falhas e sucesso na terceira
    assert col.download_cvm_zip("ITR", 2026) == content and calls["n"] == 3          # segunda chamada vem do cache em disco, sem rede


def test_cvm_download_falls_back_to_a_stale_cache_when_the_network_stays_down(tmp_path, monkeypatch):
    import os

    import coletor_historico_2025 as col
    import requests

    stale = tmp_path / "itr_2026.zip"
    stale.write_bytes(_zip_bytes())
    old = stale.stat().st_mtime - 3 * 24 * 3600
    os.utime(stale, (old, old))                                                      # vencido (cache de 12 h para o ano corrente)

    def always_down(*args, **kwargs):
        raise requests.ConnectionError("fora do ar")

    monkeypatch.setattr(col, "CVM_CACHE_DIR", tmp_path)
    monkeypatch.setattr(col.requests, "get", always_down)
    monkeypatch.setattr(col.time, "sleep", lambda seconds: None)
    assert col.download_cvm_zip("ITR", 2026) == stale.read_bytes()
    stale.unlink()
    with pytest.raises(RuntimeError):
        col.download_cvm_zip("ITR", 2026)                                            # sem cache e sem rede: falha explicita
