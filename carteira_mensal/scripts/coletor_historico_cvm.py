"""Gera as bases mensais historicas (formacao oficial) com fundamentos da CVM, metodologia forward-13b-v6.

- Corte: dados ate o ULTIMO pregao do mes anterior a formacao (como na formacao mensal de producao).
- Fundamentos: `coletor_fundamentos_cvm.historical_fundamentals_ttm` (12 meses moveis, base do controlador, ROE sobre PL medio, ROIC = NOPAT/(passivo oneroso + PL),
  bancos e holdings pelo plano de contas); so documentos ja divulgados na data. O codigo CVM de cada papel vem de config/cvm_codigos.csv (sem consultar o Fundamentus).
- Um unico download de precos (cache em disco), um unico processo para reaproveitar os zips da CVM, retomavel (pula meses ja gerados).
- Saida: output/excel/carteira_historica_AAAA_MM_corrigida2.xlsx, lidos por scripts/historico_v2.py.
Uso: python scripts/coletor_historico_cvm.py --de 2020-01 --ate 2026-10
"""
from __future__ import annotations

import argparse
import dataclasses
import pickle
import time
import traceback
from pathlib import Path

import pandas as pd

import coletor_historico_2025 as col
import coletor_fundamentos_cvm as fix

ROOT = Path(__file__).resolve().parents[1]
PRICE_CACHE = ROOT / "data" / "cvm_cache" / "coletor_precos_full.pkl"
FULL_START, FULL_END = pd.Timestamp("2015-12-01"), pd.Timestamp("2026-11-05")
ORIG_DOWNLOAD, ORIG_CONTEXT, ORIG_WRITE = col.download_yfinance, col.build_month_context, col.write_workbook
_cache: dict[tuple, pd.DataFrame] = pickle.loads(PRICE_CACHE.read_bytes()) if PRICE_CACHE.exists() else {}


def cached_download(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    key = tuple(sorted(tickers))
    if key not in _cache:
        _cache[key] = ORIG_DOWNLOAD(list(key), FULL_START, FULL_END)
        PRICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PRICE_CACHE.write_bytes(pickle.dumps(_cache))
    frame = _cache[key]
    return frame.loc[(frame.index >= start) & (frame.index <= end)].reindex(columns=list(tickers))


def corrected_context(settings, prices, index_prices, year, month):
    ctx = ORIG_CONTEXT(settings, prices, index_prices, year, month)
    previous = prices.dropna(how="all").loc[: ctx.formation_date - pd.Timedelta(days=1)]
    selection_end = pd.Timestamp(previous.index.max()).normalize()
    risk_start = selection_end - pd.DateOffset(months=int(settings["data"].get("risk_window_months", 4)))
    return dataclasses.replace(ctx, selection_end=selection_end, risk_start=risk_start)


SUFFIX = "_corrigida2"


def corrected_write(path: Path, tables: dict) -> None:
    ORIG_WRITE(path.with_name(path.name.replace(".xlsx", SUFFIX + ".xlsx")), tables)


CODES_CSV = ROOT / "config" / "cvm_codigos.csv"


def cached_metadata(tickers: list[str]) -> pd.DataFrame:
    """Codigo CVM de cada ticker, do arquivo versionado do projeto (nenhuma consulta ao Fundamentus)."""
    codes = pd.read_csv(CODES_CSV, dtype={"ticker": str}).set_index("ticker")
    rows = []
    for ticker in tickers:
        cd = codes["cd_cvm"].get(ticker)
        rows.append({"ticker": ticker, "ticker_base": str(ticker).replace(".SA", ""), "cd_cvm": cd, "nome_fundamentus": codes["nome_cvm_referencia"].get(ticker, ""),
                     "status_codcvm": "ok" if pd.notna(cd) else "sem_codigo", "fonte_codcvm": "config/cvm_codigos.csv"})
    return pd.DataFrame(rows)


col.fundamentus_metadata = cached_metadata
col.download_yfinance = cached_download
col.build_month_context = corrected_context
col.write_workbook = corrected_write


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--de", default="2020-01")
    parser.add_argument("--ate", default="2026-10")
    args = parser.parse_args()
    col.historical_fundamentals = fix.historical_fundamentals_ttm
    months = list(pd.period_range(args.de, args.ate, freq="M").astype(str))
    done, failed = 0, []
    for month in months:
        year, number = month.split("-")
        target = ROOT / "output" / "excel" / f"carteira_historica_{year}_{number}{SUFFIX}.xlsx"
        if target.exists():
            done += 1
            continue
        started = time.time()
        try:
            result = col.build_month(month)
            ctx = result["ctx"]
            print(f"[{month}] ok em {time.time() - started:.0f}s | formacao {ctx.formation_date.date()} | corte {ctx.selection_end.date()} | "
                  f"insuficientes {len(result['insufficient'])} | violacoes anti-lookahead {col.count_anti_lookahead_violations(result['fund_audit'])}", flush=True)
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(month)
            print(f"[{month}] FALHOU: {exc}", flush=True)
            traceback.print_exc()
    print(f"\nconcluidos: {done}/{len(months)} | falharam: {failed}")


if __name__ == "__main__":
    main()
