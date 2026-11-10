"""Fundamentos da CVM para a formacao mensal oficial (metodologia forward-13b-v6 em diante).

Entrega, no formato de `src/fundamentals.collect_fundamentals`, os indicadores calculados por `coletor_fundamentos_cvm.historical_fundamentals_ttm`
(12 meses moveis, base do controlador, ROE sobre PL medio, ROIC = NOPAT / (passivo oneroso + PL), bancos e holdings pelo plano de contas),
usando apenas documentos ja divulgados antes da data de formacao.

Campos que a CVM nao fornece (dividend yield, crescimento da receita em 5 anos, liquidez media, pl_anterior) ficam vazios: eles so alimentam comparacoes
setoriais e alertas informativos, nunca a nota. O setor de cada papel vem do mapa versionado do projeto (config/setores_papeis.csv, o mesmo usado para refazer o historico);
sem ele todos os papeis cairiam em "Outros" e a regra de no maximo 2 acoes por setor esvaziaria a carteira.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

CODES_CSV = ROOT / "config" / "cvm_codigos.csv"
SECTORS_CSV = ROOT / "config" / "setores_papeis.csv"

OUTPUT_COLUMNS = [
    "ticker", "roe", "roic", "margem_bruta", "margem_ebit", "margem_liquida", "pl_atual", "pl_anterior", "pvp", "dividend_yield", "divida_liquida_patrimonio",
    "crescimento_receita_5a", "valor_mercado", "liquidez_media", "setor_fundamentus", "subsetor_fundamentus", "fonte_fundamentos", "alertas_fundamentos",
    "instituicao_financeira", "dados_fundamentalistas_insuficientes", "motivo_dado_insuficiente", "documento_cvm_tipo", "documento_data_referencia", "documento_dt_receb",
]


def load_cvm_codes(path: Path = CODES_CSV) -> pd.DataFrame:
    codes = pd.read_csv(path, dtype={"ticker": str})
    codes["ticker"] = codes["ticker"].str.strip().str.upper()
    return codes[["ticker", "cd_cvm"]]


def load_sector_map(path: Path = SECTORS_CSV) -> pd.DataFrame:
    sectors = pd.read_csv(path, dtype=str).fillna("")
    sectors["ticker"] = sectors["ticker"].str.strip().str.upper()
    return sectors.set_index("ticker")


def _sa(ticker) -> str:
    key = str(ticker).strip().upper()
    return key if key.endswith(".SA") else f"{key}.SA"


def metadata_for(tickers: list[str], codes: pd.DataFrame | None = None) -> pd.DataFrame:
    """Codigo CVM de cada ticker (aceita 'PETR4' ou 'PETR4.SA'); ticker sem codigo vira linha sem documento valido."""
    codes = load_cvm_codes() if codes is None else codes
    lookup = codes.set_index("ticker")["cd_cvm"].to_dict()
    rows = []
    for ticker in tickers:
        key = str(ticker).strip().upper()
        key = key if key.endswith(".SA") else f"{key}.SA"
        rows.append({"ticker": ticker, "cd_cvm": lookup.get(key, np.nan)})
    return pd.DataFrame(rows)


def to_main_schema(fundamentals: pd.DataFrame, sectors: pd.DataFrame | None = None) -> pd.DataFrame:
    """Converte a saida de historical_fundamentals_ttm para as colunas que `src/main.py` espera de collect_fundamentals."""
    out = fundamentals.copy()
    for column in ("roe", "roic", "margem_bruta", "margem_ebit", "margem_liquida", "pl_atual", "divida_liquida_patrimonio"):
        if column not in out:
            out[column] = np.nan
    out["valor_mercado"] = out["valor_mercado_estimado"] if "valor_mercado_estimado" in out else np.nan
    equity = pd.to_numeric(out["patrimonio_liquido"], errors="coerce") if "patrimonio_liquido" in out else pd.Series(np.nan, index=out.index)
    market = pd.to_numeric(out["valor_mercado"], errors="coerce")
    out["pvp"] = np.where(equity.gt(0) & market.notna(), market / equity, np.nan)
    for column in ("pl_anterior", "dividend_yield", "crescimento_receita_5a", "liquidez_media"):
        out[column] = np.nan
    sectors = load_sector_map() if sectors is None else sectors
    keys = out["ticker"].map(_sa)
    out["setor_fundamentus"] = keys.map(sectors["setor"]).where(lambda s: s.notna() & s.ne(""), np.nan)
    out["subsetor_fundamentus"] = keys.map(sectors["subsetor"]).where(lambda s: s.notna() & s.ne(""), np.nan)
    out["fonte_fundamentos"] = "cvm"
    insufficient = out["dados_fundamentalistas_insuficientes"].fillna(True).astype(bool) if "dados_fundamentalistas_insuficientes" in out else pd.Series(True, index=out.index)
    reason = out["motivo_dado_insuficiente"].fillna("") if "motivo_dado_insuficiente" in out else pd.Series("", index=out.index)
    out["alertas_fundamentos"] = np.where(insufficient, "dados da CVM insuficientes: " + reason.astype(str), "")
    for column in OUTPUT_COLUMNS:
        if column not in out:
            out[column] = np.nan
    return out[OUTPUT_COLUMNS]


def collect_fundamentals_cvm(tickers: list[str], formation_date: pd.Timestamp, prices_at_formation: dict[str, float], store=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(fundamentos no formato de main, log de auditoria). `prices_at_formation`: ticker -> preco usado no valor de mercado (P/L e P/VP)."""
    import coletor_fundamentos_cvm as cvm
    import coletor_historico_2025 as col

    formation_date = pd.Timestamp(formation_date)
    metadata = metadata_for(tickers)
    if store is None:
        store = col.CVMStore(col.cvm_years_for_month(formation_date.year))
    result, audit = cvm.historical_fundamentals_ttm(store, metadata, prices_at_formation, formation_date)
    return to_main_schema(result), audit
