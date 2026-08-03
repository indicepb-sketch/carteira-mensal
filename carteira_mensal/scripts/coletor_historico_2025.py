from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from b3_calendar import first_b3_trading_day, resolve_b3_trading_days  # noqa: E402
from risk_analysis import log_returns, risk_metrics  # noqa: E402
from scoring import score_assets  # noqa: E402
from technical_indicators import calculate_technical_snapshot  # noqa: E402
from utils import load_settings  # noqa: E402


HEADERS = {"User-Agent": "Mozilla/5.0"}
CVM_BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{kind}/DADOS/{lower}_cia_aberta_{year}.zip"
_CVM_ZIP_CACHE: dict[tuple[str, int], zipfile.ZipFile] = {}


@dataclass(frozen=True)
class MonthContext:
    year: int
    month: int
    formation_date: pd.Timestamp
    selection_end: pd.Timestamp
    evaluation_date: pd.Timestamp
    risk_start: pd.Timestamp
    calendar_source: str
    calendar_status: str
    trading_days_month: int


def parse_month(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", value.strip())
    if not match:
        raise ValueError("Use --month no formato YYYY-MM")
    return int(match.group(1)), int(match.group(2))


def normalize_ticker(ticker: Any) -> str:
    text = str(ticker or "").strip().upper()
    return text if text.endswith(".SA") else f"{text}.SA"


def ticker_base(ticker: str) -> str:
    return normalize_ticker(ticker).replace(".SA", "")


def download_yfinance(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    import yfinance as yf

    data = yf.download(
        tickers=" ".join(tickers),
        start=start.date(),
        end=(end + pd.Timedelta(days=1)).date(),
        auto_adjust=False,
        group_by="ticker",
        threads=True,
        progress=False,
    )
    prices: dict[str, pd.Series] = {}
    for ticker in tickers:
        series = pd.Series(dtype=float, name=ticker)
        if isinstance(data.columns, pd.MultiIndex):
            if (ticker, "Adj Close") in data.columns:
                series = data[(ticker, "Adj Close")]
            elif (ticker, "Close") in data.columns:
                series = data[(ticker, "Close")]
            elif ticker in data.columns.get_level_values(0):
                sub = data[ticker]
                col = "Adj Close" if "Adj Close" in sub else "Close" if "Close" in sub else None
                if col:
                    series = sub[col]
        elif "Adj Close" in data:
            series = data["Adj Close"]
        elif "Close" in data:
            series = data["Close"]
        clean = pd.to_numeric(series, errors="coerce").dropna().rename(ticker)
        if clean.empty:
            for attempt in range(2):
                try:
                    single = yf.download(
                        ticker,
                        start=start.date(),
                        end=(end + pd.Timedelta(days=1)).date(),
                        auto_adjust=False,
                        threads=False,
                        progress=False,
                    )
                    col = "Adj Close" if "Adj Close" in single else "Close" if "Close" in single else None
                    if col:
                        clean = pd.to_numeric(single[col], errors="coerce").dropna().rename(ticker)
                    if not clean.empty:
                        break
                except Exception:
                    pass
                time.sleep(0.5 * (attempt + 1))
        prices[ticker] = clean
    return pd.concat(prices.values(), axis=1).sort_index() if prices else pd.DataFrame()


def b3_current_universe() -> pd.DataFrame:
    payload = {"index": "IBOV", "language": "pt-br"}
    encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    url = f"https://sistemaswebb3-listados.b3.com.br/indexProxy/indexCall/GetPortfolioDay/{encoded}"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()
    rows = []
    for item in data.get("results", []):
        cod = str(item.get("cod", "")).strip().upper()
        if not cod:
            continue
        rows.append(
            {
                "ticker_original": cod,
                "ticker_yfinance": f"{cod}.SA",
                "ticker": f"{cod}.SA",
                "nome": item.get("asset", ""),
                "setor": "",
                "subsetor": "",
                "fonte": "B3 carteira teorica IBOV atual aplicada retroativamente",
                "data_coleta": pd.Timestamp.now().isoformat(),
                "status_validacao": "pendente",
            }
        )
    return pd.DataFrame(rows).drop_duplicates("ticker_yfinance").reset_index(drop=True)


def load_current_universe() -> pd.DataFrame:
    path = ROOT / "data" / "processed" / "universo_ibovespa_2026_06.csv"
    if path.exists():
        frame = pd.read_csv(path)
        if "ticker_yfinance" not in frame:
            frame["ticker_yfinance"] = frame.get("ticker", frame.get("ticker_original", "")).map(normalize_ticker)
        frame["ticker"] = frame["ticker_yfinance"].map(normalize_ticker)
        return frame
    return b3_current_universe()


def fundamentus_metadata(tickers: list[str]) -> pd.DataFrame:
    rows = []
    for ticker in tickers:
        base = ticker_base(ticker)
        url = f"https://www.fundamentus.com.br/balancos.php?papel={base}&tipo=1"
        status = "ok"
        codcvm = np.nan
        nome = ""
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
            response.encoding = "iso-8859-1"
            text = response.text
            match = re.search(r'name="codcvm"\s+value="(\d+)"', text)
            if match:
                codcvm = int(match.group(1))
            h2 = re.search(r"<h2>(.*?)</h2>", text, flags=re.I | re.S)
            if h2:
                nome = re.sub(r"\s+", " ", h2.group(1)).strip()
            if pd.isna(codcvm):
                status = "codcvm_nao_encontrado"
        except Exception as exc:  # noqa: BLE001
            status = f"erro_fundamentus: {exc}"
        rows.append({"ticker": ticker, "ticker_base": base, "cd_cvm": codcvm, "nome_fundamentus": nome, "status_codcvm": status, "fonte_codcvm": url})
        time.sleep(0.15)
    return pd.DataFrame(rows)


class CVMStore:
    def __init__(self, years: list[int]):
        self.zips: dict[tuple[str, int], zipfile.ZipFile] = {}
        self.frames: dict[tuple[str, int, str], pd.DataFrame] = {}
        for kind in ["DFP", "ITR"]:
            for year in years:
                key = (kind, year)
                if key not in _CVM_ZIP_CACHE:
                    lower = kind.lower()
                    url = CVM_BASE.format(kind=kind, lower=lower, year=year)
                    response = requests.get(url, timeout=90)
                    response.raise_for_status()
                    _CVM_ZIP_CACHE[key] = zipfile.ZipFile(io.BytesIO(response.content))
                self.zips[key] = _CVM_ZIP_CACHE[key]

    def read(self, kind: str, year: int, part: str | None = None) -> pd.DataFrame:
        key = (kind, year, part or "meta")
        if key in self.frames:
            return self.frames[key]
        lower = kind.lower()
        if part is None:
            name = f"{lower}_cia_aberta_{year}.csv"
        else:
            name = f"{lower}_cia_aberta_{part}_{year}.csv" if part == "composicao_capital" else f"{lower}_cia_aberta_{part}_con_{year}.csv"
        frame = pd.read_csv(self.zips[(kind, year)].open(name), sep=";", encoding="latin1", dtype={"CD_CONTA": str})
        self.frames[key] = frame
        return frame

    def all_meta(self) -> pd.DataFrame:
        frames = []
        for kind, year in self.zips:
            frame = self.read(kind, year).copy()
            frame["TIPO_DOC_CVM"] = kind
            frame["ANO_ARQUIVO_CVM"] = year
            frames.append(frame)
        meta = pd.concat(frames, ignore_index=True, sort=False)
        meta["DT_REFER"] = pd.to_datetime(meta["DT_REFER"], errors="coerce")
        meta["DT_RECEB"] = pd.to_datetime(meta["DT_RECEB"], errors="coerce")
        return meta


def latest_available_docs(store: CVMStore, cd_cvm: int, formation_date: pd.Timestamp) -> pd.DataFrame:
    meta = store.all_meta()
    docs = meta[(meta["CD_CVM"].eq(cd_cvm)) & (meta["DT_RECEB"] < formation_date)].copy()
    if docs.empty:
        return docs
    docs = docs.sort_values(["DT_REFER", "DT_RECEB", "VERSAO"], ascending=[False, False, False])
    return docs


def latest_doc_for_company(store: CVMStore, cd_cvm: Any, formation_date: pd.Timestamp) -> pd.Series | None:
    if pd.isna(cd_cvm):
        return None
    docs = latest_available_docs(store, int(cd_cvm), formation_date)
    if docs.empty:
        return None
    return docs.iloc[0]


def select_account(frame: pd.DataFrame, cd_cvm: int, ref: pd.Timestamp, code: str, *, ytd: bool = False) -> float:
    data = frame[(frame["CD_CVM"].eq(cd_cvm)) & (pd.to_datetime(frame["DT_REFER"], errors="coerce").eq(ref)) & (frame["CD_CONTA"].eq(code))].copy()
    if data.empty:
        return np.nan
    if "ORDEM_EXERC" in data:
        ordem = data["ORDEM_EXERC"].astype(str).str.strip().str.upper()
        data = data[ordem.str.contains("LTIMO", na=False) & ~ordem.str.startswith("PEN", na=False)]
    if ytd and "DT_INI_EXERC" in data:
        starts = pd.to_datetime(data["DT_INI_EXERC"], errors="coerce")
        data = data[starts.dt.month.eq(1)]
    if data.empty:
        return np.nan
    return float(pd.to_numeric(data["VL_CONTA"], errors="coerce").dropna().iloc[0])


def prefix_sum(frame: pd.DataFrame, cd_cvm: int, ref: pd.Timestamp, prefixes: list[str]) -> float:
    data = frame[(frame["CD_CVM"].eq(cd_cvm)) & (pd.to_datetime(frame["DT_REFER"], errors="coerce").eq(ref))].copy()
    if "ORDEM_EXERC" in data:
        ordem = data["ORDEM_EXERC"].astype(str).str.strip().str.upper()
        data = data[ordem.str.contains("LTIMO", na=False) & ~ordem.str.startswith("PEN", na=False)]
    mask = pd.Series(False, index=data.index)
    for prefix in prefixes:
        mask |= data["CD_CONTA"].astype(str).str.startswith(prefix)
    values = pd.to_numeric(data.loc[mask, "VL_CONTA"], errors="coerce").dropna()
    return float(values.sum()) if len(values) else np.nan


def shares_from_composition(store: CVMStore, kind: str, year: int, cnpj: str, ref: pd.Timestamp) -> float:
    try:
        comp = store.read(kind, year, "composicao_capital")
    except Exception:
        return np.nan
    data = comp[(comp["CNPJ_CIA"].astype(str).eq(str(cnpj))) & (pd.to_datetime(comp["DT_REFER"], errors="coerce").eq(ref))].copy()
    if data.empty:
        return np.nan
    if "QT_ACAO_TOTAL_CAP_INTEGR" in data:
        total = pd.to_numeric(data["QT_ACAO_TOTAL_CAP_INTEGR"], errors="coerce").fillna(0).sum()
        if "QT_ACAO_TOTAL_TESOURO" in data:
            total -= pd.to_numeric(data["QT_ACAO_TOTAL_TESOURO"], errors="coerce").fillna(0).sum()
    else:
        numeric_cols = [col for col in data.columns if col.startswith("QT_")]
        total = 0.0
        for col in numeric_cols:
            if "TESOURO" not in col.upper():
                total += pd.to_numeric(data[col], errors="coerce").fillna(0).sum()
    return float(total) if total > 0 else np.nan


def historical_fundamentals(store: CVMStore, metadata: pd.DataFrame, prices_at_formation: dict[str, float], formation_date: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    audit = []
    for _, item in metadata.iterrows():
        ticker = item["ticker"]
        cd_cvm = item.get("cd_cvm")
        doc = latest_doc_for_company(store, cd_cvm, formation_date)
        if doc is None:
            rows.append({"ticker": ticker, "dados_fundamentalistas_insuficientes": True, "motivo_dado_insuficiente": "sem documento CVM com DT_RECEB anterior a data de formacao"})
            audit.append({"ticker": ticker, "cd_cvm": cd_cvm, "status": "sem_doc_valido"})
            continue
        kind = str(doc["TIPO_DOC_CVM"])
        year = int(doc["ANO_ARQUIVO_CVM"])
        ref = pd.Timestamp(doc["DT_REFER"])
        dre = store.read(kind, year, "DRE")
        bpa = store.read(kind, year, "BPA")
        bpp = store.read(kind, year, "BPP")
        cd = int(cd_cvm)
        receita = select_account(dre, cd, ref, "3.01", ytd=True)
        lucro_bruto = select_account(dre, cd, ref, "3.03", ytd=True)
        ebit = select_account(dre, cd, ref, "3.05", ytd=True)
        if pd.isna(ebit):
            ebit = select_account(dre, cd, ref, "3.04", ytd=True)
        lucro = select_account(dre, cd, ref, "3.11", ytd=True)
        pl = select_account(bpp, cd, ref, "2.03")
        ativo_total = select_account(bpa, cd, ref, "1")
        caixa = select_account(bpa, cd, ref, "1.01.01")
        divida_bruta = prefix_sum(bpp, cd, ref, ["2.01.04", "2.02.01"])
        divida_liquida = divida_bruta - caixa if pd.notna(divida_bruta) and pd.notna(caixa) else np.nan
        capital_investido = pl + divida_liquida if pd.notna(pl) and pd.notna(divida_liquida) else np.nan
        factor = 4.0 if kind == "ITR" and ref.month == 3 else 2.0 if kind == "ITR" and ref.month == 6 else 4.0 / 3.0 if kind == "ITR" and ref.month == 9 else 1.0
        lucro_anualizado = lucro * factor if pd.notna(lucro) else np.nan
        ebit_anualizado = ebit * factor if pd.notna(ebit) else np.nan
        preco = prices_at_formation.get(ticker, np.nan)
        shares = shares_from_composition(store, kind, year, str(doc["CNPJ_CIA"]), ref)
        market_cap = preco * shares if pd.notna(preco) and pd.notna(shares) else np.nan
        row = {
            "ticker": ticker,
            "roe": lucro_anualizado / pl if pd.notna(lucro_anualizado) and pd.notna(pl) and pl != 0 else np.nan,
            "roic": ebit_anualizado / capital_investido if pd.notna(ebit_anualizado) and pd.notna(capital_investido) and capital_investido != 0 else np.nan,
            "margem_bruta": lucro_bruto / receita if pd.notna(lucro_bruto) and pd.notna(receita) and receita != 0 else np.nan,
            "margem_liquida": lucro / receita if pd.notna(lucro) and pd.notna(receita) and receita != 0 else np.nan,
            "margem_ebit": ebit / receita if pd.notna(ebit) and pd.notna(receita) and receita != 0 else np.nan,
            "pl_atual": market_cap / lucro_anualizado if pd.notna(market_cap) and pd.notna(lucro_anualizado) and lucro_anualizado != 0 else np.nan,
            "patrimonio_liquido": pl,
            "receita_liquida": receita,
            "lucro_bruto": lucro_bruto,
            "lucro_liquido": lucro,
            "lucro_liquido_anualizado": lucro_anualizado,
            "ebit": ebit,
            "ebit_anualizado": ebit_anualizado,
            "ativo_total": ativo_total,
            "divida_bruta": divida_bruta,
            "caixa": caixa,
            "divida_liquida": divida_liquida,
            "divida_liquida_patrimonio": divida_liquida / pl if pd.notna(divida_liquida) and pd.notna(pl) and pl != 0 else np.nan,
            "acoes_total_cvm": shares,
            "valor_mercado_estimado": market_cap,
            "documento_cvm_tipo": kind,
            "documento_cvm_ano_arquivo": year,
            "documento_data_referencia": ref.date().isoformat(),
            "documento_dt_receb": pd.Timestamp(doc["DT_RECEB"]).date().isoformat(),
            "anti_lookahead_ok": bool(pd.Timestamp(doc["DT_RECEB"]) < formation_date),
            "dados_fundamentalistas_insuficientes": False,
        }
        insufficient = [key for key in ["roe", "roic", "margem_bruta", "margem_liquida", "pl_atual"] if pd.isna(row[key])]
        if insufficient:
            row["dados_fundamentalistas_insuficientes"] = True
            row["motivo_dado_insuficiente"] = "campos ausentes: " + ", ".join(insufficient)
        else:
            row["motivo_dado_insuficiente"] = ""
        rows.append(row)
        audit.append(
            {
                "ticker": ticker,
                "cd_cvm": cd,
                "documento": kind,
                "data_referencia": ref.date().isoformat(),
                "dt_receb": pd.Timestamp(doc["DT_RECEB"]).date().isoformat(),
                "data_formacao": formation_date.date().isoformat(),
                "anti_lookahead_ok": bool(pd.Timestamp(doc["DT_RECEB"]) < formation_date),
                "receita_liquida": receita,
                "lucro_liquido_anualizado": lucro_anualizado,
                "patrimonio_liquido": pl,
                "roe": row["roe"],
                "preco_formacao": preco,
                "acoes_total_cvm": shares,
                "pl_atual": row["pl_atual"],
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(audit)


def price_at_or_before(prices: pd.DataFrame, ticker: str, date: pd.Timestamp) -> float:
    if ticker not in prices:
        return np.nan
    series = prices[ticker].dropna().sort_index()
    series = series[series.index <= date]
    return float(series.iloc[-1]) if len(series) else np.nan


def ytd_return_until(series: pd.Series, date: pd.Timestamp) -> float:
    clean = series.dropna().sort_index()
    clean = clean[clean.index <= date]
    year = clean[clean.index.year == date.year]
    if len(year) < 2 or year.iloc[0] == 0:
        return np.nan
    return float(year.iloc[-1] / year.iloc[0] - 1)


def cumulative_return_until(series: pd.Series, date: pd.Timestamp, months: int) -> float:
    clean = series.dropna().sort_index()
    clean = clean[clean.index <= date]
    if len(clean) < 2:
        return np.nan
    start = date - pd.DateOffset(months=months)
    window = clean[clean.index >= start]
    if len(window) < 2 or window.iloc[0] == 0:
        return np.nan
    return float(window.iloc[-1] / window.iloc[0] - 1)


def build_month_context(settings: dict, prices: pd.DataFrame, index_prices: pd.DataFrame, year: int, month: int) -> MonthContext:
    formation, source, status = first_b3_trading_day(settings, index_prices, year, month)
    days, source2, status2 = resolve_b3_trading_days(settings, index_prices, year, month)
    selection_end = pd.Timestamp(prices.dropna(how="all").loc[:formation].index.max()).normalize()
    evaluation_date = pd.Timestamp(days[-1]).normalize() if len(days) else formation + pd.offsets.MonthEnd(0)
    risk_start = selection_end - pd.DateOffset(months=int(settings["data"].get("risk_window_months", 4)))
    return MonthContext(
        year=year,
        month=month,
        formation_date=formation,
        selection_end=selection_end,
        evaluation_date=evaluation_date,
        risk_start=risk_start,
        calendar_source=source or source2,
        calendar_status=status or status2,
        trading_days_month=len(days),
    )


def technical_frame(prices: pd.DataFrame, settings: dict, end_date: pd.Timestamp) -> pd.DataFrame:
    rows = []
    sliced = prices.loc[:end_date]
    for ticker in sliced.columns:
        series = sliced[ticker].dropna()
        if series.empty:
            continue
        row = calculate_technical_snapshot(series, settings)
        row["ticker"] = ticker
        row["retorno_ytd"] = ytd_return_until(series, end_date)
        rows.append(row)
    return pd.DataFrame(rows)


def recent_returns_frame(prices: pd.DataFrame, index_prices: pd.DataFrame, settings: dict, end_date: pd.Timestamp) -> pd.DataFrame:
    ibov_ticker = settings["data"]["indexes"].get("IBOV", "^BVSP")
    ibov = index_prices[ibov_ticker] if ibov_ticker in index_prices else pd.Series(dtype=float)
    ibov_1m = cumulative_return_until(ibov, end_date, 1)
    ibov_4m = cumulative_return_until(ibov, end_date, 4)
    ibov_ytd = ytd_return_until(ibov, end_date)
    rows = []
    for ticker in prices.columns:
        series = prices[ticker]
        clean = series.dropna().sort_index()
        clean = clean[clean.index <= end_date]
        ret1 = cumulative_return_until(series, end_date, 1)
        ret4 = cumulative_return_until(series, end_date, 4)
        ytd = ytd_return_until(series, end_date)
        rel1 = ret1 - ibov_1m if pd.notna(ret1) and pd.notna(ibov_1m) else np.nan
        rel4 = ret4 - ibov_4m if pd.notna(ret4) and pd.notna(ibov_4m) else np.nan
        rely = ytd - ibov_ytd if pd.notna(ytd) and pd.notna(ibov_ytd) else np.nan
        score = int((pd.notna(rel1) and rel1 > 0)) * 2 + int((pd.notna(rel4) and rel4 > 0)) * 2 + int((pd.notna(rely) and rely > 0))
        rows.append(
            {
                "ticker": ticker,
                "cotacao_anterior": clean.iloc[-2] if len(clean) >= 2 else np.nan,
                "cotacao_atual": clean.iloc[-1] if len(clean) else np.nan,
                "retorno_acumulado_1m": ret1,
                "retorno_acumulado_4m": ret4,
                "retorno_ytd": ytd,
                "retorno_1m_ibov": ibov_1m,
                "retorno_4m_ibov": ibov_4m,
                "retorno_ytd_ibov": ibov_ytd,
                "retorno_1m_relativo_ibov": rel1,
                "retorno_4m_relativo_ibov": rel4,
                "retorno_ytd_relativo_ibov": rely,
                "forca_relativa_score": score,
                "classificacao_forca_relativa": "forte_contra_ibov" if score >= 4 else "moderada_contra_ibov" if score >= 2 else "fraca_contra_ibov",
            }
        )
    return pd.DataFrame(rows)


def classify_preliminary(row: pd.Series) -> dict[str, Any]:
    missing = bool(row.get("dados_fundamentalistas_insuficientes", False)) or pd.isna(row.get("preco_atual", np.nan))
    critical_fundamental = any(pd.notna(row.get(col, np.nan)) and row.get(col) < 0 for col in ["roe", "margem_liquida", "pl_atual"])
    monthly_ok = bool(row.get("mm9", np.nan) > row.get("mm21", np.nan) and row.get("preco_atual", np.nan) > row.get("mm21", np.nan))
    timing_ok = str(row.get("bollinger_status", "")) in {"favoravel", "oportunidade", "neutra"} and not (pd.notna(row.get("rsi", np.nan)) and row.get("rsi") > 75)
    force_ok = str(row.get("classificacao_forca_relativa", "")) != "fraca_contra_ibov"
    if missing:
        decision = "descartar_dados_insuficientes"
        status = "bloqueada_para_risco"
        category = "inelegivel"
        reason = str(row.get("motivo_dado_insuficiente", "dados essenciais ausentes"))
    elif critical_fundamental:
        decision = "descartar_fundamentalista"
        status = "bloqueada_para_risco"
        category = "inelegivel"
        reason = "fundamento_bloqueante: ROE/margem/P-L negativo"
    elif monthly_ok and timing_ok and force_ok:
        decision = "candidata_para_risco"
        status = "aprovada_para_risco"
        category = "elegivel_forte"
        reason = "tendencia mensal, timing e forca relativa favoraveis"
    elif monthly_ok or force_ok:
        decision = "candidata_com_restricao"
        status = "moderada_para_risco"
        category = "elegivel_moderado"
        reason = "sinal parcial; requer avaliacao de risco"
    else:
        decision = "descartar_tecnico"
        status = "bloqueada_para_risco"
        category = "inelegivel"
        reason = "tendencia mensal/forca relativa desfavoravel"
    return {
        "decisao_preliminar_ajustada": decision,
        "motivo_decisao_preliminar": reason,
        "status_para_risco": status,
        "motivo_status_para_risco": reason,
        "categoria_elegibilidade": category,
        "fundamento_bloqueante": bool(critical_fundamental),
        "motivo_fundamento_bloqueante": "ROE/margem/P-L negativo" if critical_fundamental else "",
    }


def build_risk(prices: pd.DataFrame, index_prices: pd.DataFrame, settings: dict, ctx: MonthContext) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    window_prices = prices.loc[(prices.index >= ctx.risk_start) & (prices.index <= ctx.selection_end)].copy()
    returns = log_returns(window_prices)
    benchmark = settings["data"]["indexes"].get("IBOV", "^BVSP")
    ibov_prices = index_prices.loc[(index_prices.index >= ctx.risk_start) & (index_prices.index <= ctx.selection_end), benchmark].dropna()
    ibov_returns = log_returns(ibov_prices)
    metrics, corr, cov = risk_metrics(returns, ibov_returns, settings)
    metrics["variancia"] = metrics["desvio_padrao"] ** 2
    metrics["janela_risco_inicio"] = ctx.risk_start.date().isoformat()
    metrics["janela_risco_fim"] = ctx.selection_end.date().isoformat()
    metrics["janela_risco_meses"] = settings["data"].get("risk_window_months", 4)
    metrics["periodicidade_risco"] = "diaria"
    metrics["tipo_retorno_risco"] = "log"
    metrics["quantidade_observacoes_risco"] = returns.notna().sum().reindex(metrics["ticker"]).to_numpy()
    return metrics, corr, cov


def append_optimization_flags(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    out = frame.copy()
    alerts = []
    for _, row in out.iterrows():
        a = []
        p = []
        if pd.notna(row.get("beta", np.nan)) and row.get("beta") > settings["risk"].get("beta_alert", 1.0):
            a.append("alerta_beta_alto")
            p.append("penalizacao_beta_alto")
        if pd.notna(row.get("correlacao_ibov", np.nan)) and row.get("correlacao_ibov") > settings["risk"].get("correlation_alert", 0.7):
            a.append("alerta_correlacao_alta")
            p.append("penalizacao_correlacao_alta")
        if pd.notna(row.get("cv", np.nan)) and row.get("cv") > settings["risk"].get("cv_limit", 11.5):
            a.append("alerta_cv_individual_alto")
            p.append("penalizacao_cv_individual_alto")
        alerts.append(("; ".join(a), "; ".join(p)))
    out["alertas_nao_bloqueantes"] = [a for a, _ in alerts]
    out["penalizacoes_otimizacao"] = [p for _, p in alerts]
    reasons = []
    for _, row in out.iterrows():
        reason = []
        if row.get("status_para_risco") not in {"aprovada_para_risco", "moderada_para_risco"}:
            reason.append("bloqueio_por_status_para_risco")
        if row.get("categoria_elegibilidade") not in {"elegivel_forte", "elegivel_moderado"}:
            reason.append("bloqueio_por_elegibilidade")
        if pd.isna(row.get("retorno_medio", np.nan)) or row.get("retorno_medio") <= 0:
            reason.append("bloqueio_por_retorno_medio_negativo")
        if bool(row.get("fundamento_bloqueante", False)):
            reason.append("bloqueio_por_fundamento_bloqueante")
        reasons.append("; ".join(dict.fromkeys(reason)))
    out["motivo_bloqueio_otimizacao"] = reasons
    out["tipo_bloqueio_otimizacao"] = np.where(out["motivo_bloqueio_otimizacao"].astype(str).str.len() > 0, "bloqueio_insumo_historico", "")
    out["bloqueado_otimizacao"] = out["motivo_bloqueio_otimizacao"].astype(str).str.len() > 0
    out["liberado_para_otimizacao"] = ~out["bloqueado_otimizacao"]
    out["decisao de entrada na carteira"] = np.where(out["liberado_para_otimizacao"], "liberado_para_otimizacao_historica", "bloqueado")
    out["peso_final"] = 0.0
    out["peso_recomendado"] = 0.0
    return out


def write_workbook(path: Path, tables: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        for sheet, frame in tables.items():
            data = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
            data = data.loc[:, ~data.columns.duplicated()].copy()
            data.to_excel(writer, sheet_name=sheet[:31], index=False)
            worksheet = writer.sheets[sheet[:31]]
            for idx, col in enumerate(data.columns):
                worksheet.set_column(idx, idx, min(max(len(str(col)) + 2, 12), 42))


def cvm_years_for_month(year: int) -> list[int]:
    # Usa anos anteriores suficientes para evitar look-ahead nos primeiros meses do ano.
    return list(range(year - 2, year + 1))



def count_anti_lookahead_violations(fund_audit: pd.DataFrame) -> int:
    if fund_audit.empty or "anti_lookahead_ok" not in fund_audit.columns:
        return 0
    truthy = {"true", "1", "1.0", "sim", "yes"}
    ok = fund_audit["anti_lookahead_ok"].map(lambda value: str(value).strip().lower() in truthy)
    has_doc = (
        fund_audit["dt_receb"].notna()
        if "dt_receb" in fund_audit.columns
        else pd.Series(False, index=fund_audit.index)
    )
    return int((has_doc & ~ok).sum())

def build_month(month: str) -> dict[str, Any]:
    settings = load_settings()
    year, month_num = parse_month(month)
    universe = load_current_universe()
    universe["ticker"] = universe["ticker_yfinance"].map(normalize_ticker)
    tickers = sorted(universe["ticker"].dropna().unique().tolist())
    start = pd.Timestamp(year=year, month=month_num, day=1) - pd.DateOffset(months=int(settings["data"].get("history_months", 60)))
    end = pd.Timestamp(year=year, month=month_num, day=1) + pd.offsets.MonthEnd(0)
    index_ticker = settings["data"]["indexes"].get("IBOV", "^BVSP")
    prices = download_yfinance(tickers, start, end)
    index_prices = download_yfinance([index_ticker], start, end)
    ctx = build_month_context(settings, prices, index_prices, year, month_num)
    prices = prices.loc[:ctx.selection_end]
    index_prices = index_prices.loc[:ctx.selection_end]
    prices_at = {ticker: price_at_or_before(prices, ticker, ctx.selection_end) for ticker in tickers}

    metadata = fundamentus_metadata(tickers)
    store = CVMStore(cvm_years_for_month(year))
    fundamentals, fund_audit = historical_fundamentals(store, metadata, prices_at, ctx.formation_date)

    tech = technical_frame(prices, settings, ctx.selection_end)
    returns = recent_returns_frame(prices, index_prices, settings, ctx.selection_end)
    risk, corr, cov = build_risk(prices, index_prices, settings, ctx)
    base = (
        universe[["ticker", "ticker_original", "ticker_yfinance", "nome", "setor", "subsetor", "fonte", "status_validacao"]]
        .merge(tech, on="ticker", how="left")
        .merge(returns, on="ticker", how="left", suffixes=("", "_ret"))
        .merge(fundamentals, on="ticker", how="left")
        .merge(risk, on="ticker", how="left")
    )
    base["nome"] = base["nome"].fillna(base["ticker"])
    base["setor"] = base["setor"].fillna("Nao mapeado")
    prelim_flags = base.apply(lambda row: pd.Series(classify_preliminary(row)), axis=1)
    base = pd.concat([base, prelim_flags], axis=1)
    base["nota preliminar"] = 0.0
    base["tipo_timing"] = np.select(
        [base["rsi"].between(50, 65, inclusive="both") & (base["mm9"] > base["mm21"]), base["rsi"] > 70, base["rsi"] < 35],
        ["timing_favoravel_tendencia", "timing_esticado_sobrecompra", "timing_reversao_oportunidade"],
        default="timing_neutro",
    )
    base["tendencia_mensal"] = np.select(
        [base["mm9"] > base["mm21"], base["mm9"] < base["mm21"]],
        ["alta_aceitavel_ou_virada", "fraca"],
        default="indefinida",
    )
    base["contexto_estrutural"] = np.select(
        [base["mm50"] > base["mm100"], base["mm50"] < base["mm100"]],
        ["estrutural_alta", "estrutural_baixa"],
        default="estrutural_indefinida",
    )
    scored = score_assets(base, settings)
    scored = append_optimization_flags(scored, settings)
    prelim = scored.sort_values(["retorno_ytd", "nota_final"], ascending=[False, False]).reset_index(drop=True)
    candidates = prelim.head(int(settings["strategy"].get("pre_risk_candidates", 25))).copy()
    optimization = candidates.copy()

    cov_sheet = cov.copy()
    cov_sheet.insert(0, "ticker", cov_sheet.index)
    corr_sheet = corr.copy()
    corr_sheet.insert(0, "ticker", corr_sheet.index)
    validation = pd.DataFrame(
        [
            {"campo": "mes_referencia", "valor": f"{year:04d}-{month_num:02d}"},
            {"campo": "data_formacao_carteira", "valor": ctx.formation_date.date().isoformat()},
            {"campo": "data_limite_dados_selecao", "valor": ctx.selection_end.date().isoformat()},
            {"campo": "data_avaliacao_carteira", "valor": ctx.evaluation_date.date().isoformat()},
            {"campo": "universo", "valor": len(tickers)},
            {"campo": "ativos_com_preco_suficiente", "valor": int(prices.notna().sum().ge(settings["data"].get("min_price_rows", 120)).sum())},
            {"campo": "dados_insuficientes", "valor": int(prelim["decisao_preliminar_ajustada"].eq("descartar_dados_insuficientes").sum())},
            {"campo": "anti_lookahead_violacoes", "valor": count_anti_lookahead_violations(fund_audit)},
            {"campo": "status_carteira", "valor": "insumos_historicos_sem_otimizacao"},
            {"campo": "observacao", "valor": "Arquivo gerado por coletor historico separado; producao/optimizer/scoring/main nao alterados."},
        ]
    )
    insufficient = prelim[prelim["decisao_preliminar_ajustada"].eq("descartar_dados_insuficientes")][["ticker", "motivo_decisao_preliminar"]].copy()
    output = ROOT / "output" / "excel" / f"carteira_historica_{year:04d}_{month_num:02d}.xlsx"
    tables = {
        "Resumo da Carteira": pd.DataFrame(columns=["ticker", "peso_recomendado"]),
        "Validacao Final": validation,
        "Universo de Ativos": universe,
        "Analise Preliminar": prelim,
        "Candidatas Risco": candidates,
        "Otimizacao": optimization,
        "Matriz de Covariancia": cov_sheet,
        "Matriz de Correlacao": corr_sheet,
        "Indicadores Tecnicos": tech,
        "Indicadores Fundamentalistas": fundamentals,
        "Auditoria Fund Historicos": fund_audit,
        "Log Faltantes": insufficient,
    }
    write_workbook(output, tables)
    return {
        "output": str(output),
        "ctx": ctx,
        "prelim": prelim,
        "fund_audit": fund_audit,
        "insufficient": insufficient,
        "validation": validation,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default="2025-01")
    args = parser.parse_args()
    result = build_month(args.month)
    ctx: MonthContext = result["ctx"]
    prelim: pd.DataFrame = result["prelim"]
    audit: pd.DataFrame = result["fund_audit"]
    print(f"arquivo={result['output']}")
    print(f"mes={args.month}")
    print(f"data_formacao_carteira={ctx.formation_date.date().isoformat()}")
    print(f"data_limite_dados_selecao={ctx.selection_end.date().isoformat()}")
    print(f"data_avaliacao_carteira={ctx.evaluation_date.date().isoformat()}")
    print(f"universo={len(prelim)}")
    print(f"dados_insuficientes={len(result['insufficient'])}")
    print(f"anti_lookahead_violacoes={count_anti_lookahead_violations(audit)}")
    print("validacao_amostra_fundamentos:")
    cols = ["ticker", "documento", "data_referencia", "dt_receb", "data_formacao", "anti_lookahead_ok", "roe", "pl_atual"]
    print(audit.reindex(columns=cols).dropna(subset=["dt_receb"]).head(3).to_string(index=False))


if __name__ == "__main__":
    main()









