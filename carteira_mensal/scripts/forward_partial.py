from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
RAW_DIR = ROOT / "data" / "raw"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calcula parcial do forward-test mensal.")
    parser.add_argument("--mes", required=True, help="Mes no formato YYYY-MM")
    parser.add_argument("--arquivo", default=None, help="Arquivo forward especifico opcional")
    parser.add_argument("--data-avaliacao", default=None, help="Data final da avaliacao no formato YYYY-MM-DD. Se omitida, usa a data atual.")
    parser.add_argument("--allow-network", action="store_true", help="Autoriza consulta ao yfinance com os tickers da carteira. Sem esta flag, usa apenas cache local para precos.")
    parser.add_argument("--cdi-auto", action="store_true", help="Busca CDI diario automaticamente no Banco Central/SGS e calcula retorno liquido de IR da parcela defensiva. Nao envia tickers.")
    return parser.parse_args()


def ir_rate_by_days(days: int) -> float:
    if days <= 180:
        return 0.225
    if days <= 360:
        return 0.20
    if days <= 720:
        return 0.175
    return 0.15


def fetch_cdi_gross_return(start: pd.Timestamp, end: pd.Timestamp) -> tuple[float, pd.DataFrame, str]:
    import json
    from urllib.parse import urlencode
    from urllib.request import urlopen

    start = start.normalize()
    end = end.normalize()
    url = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
    params = {
        "formato": "json",
        "dataInicial": start.strftime("%d/%m/%Y"),
        "dataFinal": end.strftime("%d/%m/%Y"),
    }
    with urlopen(f"{url}?{urlencode(params)}", timeout=20) as response:
        rows = json.loads(response.read().decode("utf-8"))
    if not rows:
        return np.nan, pd.DataFrame(columns=["data", "cdi_diario_pct", "cdi_diario_decimal"]), "sem_dados_bcb_sgs_12"
    frame = pd.DataFrame(rows)
    frame["data"] = pd.to_datetime(frame["data"], dayfirst=True, errors="coerce")
    frame["cdi_diario_pct"] = pd.to_numeric(frame["valor"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    frame = frame.dropna(subset=["data", "cdi_diario_pct"]).sort_values("data")
    frame["cdi_diario_decimal"] = frame["cdi_diario_pct"] / 100.0
    gross = float((1.0 + frame["cdi_diario_decimal"]).prod() - 1.0) if not frame.empty else np.nan
    return gross, frame[["data", "cdi_diario_pct", "cdi_diario_decimal"]], "bcb_sgs_12"


def cdi_net_return(gross_return: float | None, start: pd.Timestamp, end: pd.Timestamp) -> tuple[float, float, int]:
    days = max(int((end.normalize() - start.normalize()).days), 1)
    tax_rate = ir_rate_by_days(days)
    if gross_return is None or pd.isna(gross_return):
        return np.nan, tax_rate, days
    gross = float(gross_return)
    tax = max(gross, 0.0) * tax_rate
    return gross - tax, tax_rate, days


def parse_month(value: str) -> tuple[int, int, str]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", value.strip())
    if not match:
        raise ValueError("--mes deve estar no formato YYYY-MM")
    year, month = int(match.group(1)), int(match.group(2))
    return year, month, f"{year:04d}-{month:02d}"


def latest_forward_file(year: int, month: int) -> Path:
    files = sorted(
        EXCEL_DIR.glob(f"carteira_forward_{year:04d}_{month:02d}*.xlsx"),
        key=lambda p: p.stat().st_mtime,
    )
    files = [p for p in files if not p.name.startswith("parcial_")]
    if not files:
        raise FileNotFoundError(f"carteira_forward_{year:04d}_{month:02d}*.xlsx nao encontrado")
    return files[-1]


def read_fields(path: Path, sheet: str) -> dict[str, Any]:
    try:
        frame = pd.read_excel(path, sheet_name=sheet)
    except ValueError:
        return {}
    if {"campo", "valor"}.issubset(frame.columns):
        return dict(zip(frame["campo"].astype(str), frame["valor"]))
    if {"metrica", "valor"}.issubset(frame.columns):
        return dict(zip(frame["metrica"].astype(str), frame["valor"]))
    return {}


def load_portfolio(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_excel(path, sheet_name="Carteira Aplicada")
    except ValueError:
        frame = pd.read_excel(path, sheet_name="Carteira Forward")
    if "ticker" not in frame or "peso_recomendado" not in frame:
        raise ValueError("Arquivo forward nao contem ticker/peso_recomendado")
    return frame.copy()


def download_prices(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    import yfinance as yf

    if not tickers:
        return pd.DataFrame()
    raw = yf.download(
        tickers=" ".join(tickers),
        start=(start - pd.Timedelta(days=5)).date(),
        end=(end + pd.Timedelta(days=1)).date(),
        auto_adjust=False,
        group_by="ticker",
        threads=True,
        progress=False,
    )
    out: dict[str, pd.Series] = {}
    for ticker in tickers:
        if isinstance(raw.columns, pd.MultiIndex):
            if (ticker, "Adj Close") in raw.columns:
                series = raw[(ticker, "Adj Close")]
            elif (ticker, "Close") in raw.columns:
                series = raw[(ticker, "Close")]
            else:
                series = pd.Series(dtype=float)
        else:
            col = "Adj Close" if "Adj Close" in raw.columns else "Close" if "Close" in raw.columns else None
            series = raw[col] if col else pd.Series(dtype=float)
        out[ticker] = pd.to_numeric(series, errors="coerce").dropna()
    return pd.DataFrame(out)



def load_local_price_series(ticker: str) -> pd.Series:
    safe = ticker.replace(".", "_")
    candidates = [
        RAW_DIR / f"prices_{safe}.csv",
        RAW_DIR / f"prices_{safe}_via_{safe}.csv",
    ]
    if ticker == "^BVSP":
        candidates = [RAW_DIR / "prices_^BVSP_via_BVSP.csv", RAW_DIR / "prices_^BVSP.csv"]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            frame = pd.read_csv(candidate, skiprows=[1, 2])
        except Exception:
            continue
        date_col = "Price" if "Price" in frame.columns else "Date" if "Date" in frame.columns else frame.columns[0]
        value_col = "Adj Close" if "Adj Close" in frame.columns else "Close" if "Close" in frame.columns else None
        if value_col is None:
            continue
        dates = pd.to_datetime(frame[date_col], errors="coerce")
        values = pd.to_numeric(frame[value_col], errors="coerce")
        series = pd.Series(values.to_numpy(), index=dates).dropna()
        series = series[series.index.notna()].sort_index()
        if not series.empty:
            return series
    return pd.Series(dtype=float)


def add_local_benchmark(prices: pd.DataFrame) -> pd.DataFrame:
    if "^BVSP" in prices.columns:
        return prices
    ibov = load_local_price_series("^BVSP")
    if ibov.empty:
        return prices
    out = prices.copy()
    out["^BVSP"] = ibov
    return out

def cache_prices_from_old_partial(year: int, month: int) -> pd.DataFrame:
    path = EXCEL_DIR / f"parcial_carteira_forward_{year:04d}_{month:02d}.xlsx"
    if not path.exists():
        return pd.DataFrame()
    try:
        assets = pd.read_excel(path, sheet_name="Ativos")
    except Exception:
        return pd.DataFrame()
    if not {"ticker", "preco_atual", "data_atual"}.issubset(assets.columns):
        return pd.DataFrame()
    data_atual = pd.to_datetime(assets["data_atual"], errors="coerce").dropna()
    if data_atual.empty:
        return pd.DataFrame()
    date = data_atual.max().normalize()
    return pd.DataFrame(
        {
            str(row["ticker"]): pd.Series(
                [pd.to_numeric(row.get("preco_atual"), errors="coerce")],
                index=[date],
            )
            for _, row in assets.iterrows()
            if str(row.get("ticker", "")).upper() not in {"CAIXA", "CDI"}
        }
    )


def last_price(series: pd.Series, date: pd.Timestamp) -> tuple[float, pd.Timestamp | None]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    clean = clean[clean.index <= date]
    if clean.empty:
        return np.nan, None
    return float(clean.iloc[-1]), pd.Timestamp(clean.index[-1]).normalize()


def main() -> None:
    args = parse_args()
    year, month, mes_key = parse_month(args.mes)
    forward = Path(args.arquivo) if args.arquivo else latest_forward_file(year, month)
    if not forward.is_absolute():
        forward = ROOT / forward

    portfolio = load_portfolio(forward)
    fields = read_fields(forward, "Resumo Forward")
    entry_date = pd.to_datetime(fields.get("data_limite_dados_selecao", fields.get("data_preco_entrada", ""))).normalize()
    if pd.isna(entry_date):
        entry_date = pd.to_datetime(portfolio.get("data_preco_entrada", pd.Series(dtype=str)).dropna().iloc[0]).normalize()
    today = pd.to_datetime(args.data_avaliacao).normalize() if args.data_avaliacao else pd.Timestamp(datetime.today()).normalize()
    if pd.isna(today):
        raise ValueError("--data-avaliacao deve estar no formato YYYY-MM-DD")
    month_end = (pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)).normalize()
    period_status = "fechamento_mes" if today >= month_end else "parcial_mes_em_andamento"
    defensive_tickers = {"CAIXA", "CDI"}
    tickers = [t for t in portfolio["ticker"].astype(str).str.upper().tolist() if t not in defensive_tickers]

    if args.allow_network:
        prices = download_prices(tickers + ["^BVSP"], entry_date, today)
        source = "yfinance"
        if prices.empty or prices[tickers].dropna(how="all").empty:
            prices = cache_prices_from_old_partial(year, month)
            source = "cache_parcial_anterior"
        prices = add_local_benchmark(prices)
    else:
        prices = cache_prices_from_old_partial(year, month)
        source = "cache_parcial_anterior_sem_rede"
        prices = add_local_benchmark(prices)
        if prices.empty:
            raise RuntimeError("Sem cache local de parcial. Rode novamente com --allow-network se autorizar consulta ao yfinance.")

    cdi_gross = np.nan
    cdi_source = "nao_consultado"
    cdi_daily = pd.DataFrame()
    if args.cdi_auto:
        try:
            cdi_gross, cdi_daily, cdi_source = fetch_cdi_gross_return(entry_date + pd.Timedelta(days=1), today)
        except Exception as exc:
            cdi_source = f"erro_bcb_sgs_12: {exc}"
    cdi_net, cdi_ir_rate, cdi_days = cdi_net_return(cdi_gross, entry_date, today)

    rows = []
    for _, row in portfolio.iterrows():
        ticker = str(row.get("ticker", "")).upper()
        weight = float(row.get("peso_recomendado", 0.0) or 0.0)
        if ticker in defensive_tickers:
            defensive_return = cdi_net if pd.notna(cdi_net) else 0.0
            rows.append(
                {
                    "ticker": "CDI",
                    "peso_recomendado": weight,
                    "preco_entrada": np.nan,
                    "preco_atual": np.nan,
                    "data_avaliacao": today.date().isoformat(),
                    "retorno_periodo": defensive_return,
                    "contribuicao": defensive_return * weight,
                    "fonte_preco": cdi_source,
                    "retorno_cdi_bruto_periodo": cdi_gross,
                    "aliquota_ir_cdi": cdi_ir_rate,
                    "dias_corridos_cdi": cdi_days,
                    "retorno_cdi_liquido_periodo": cdi_net,
                }
            )
            continue
        entry = row.get("preco_entrada_fechamento_mes_anterior", np.nan)
        if pd.isna(entry) and ticker in prices:
            entry, _ = last_price(prices[ticker], entry_date)
        current, price_date = last_price(prices[ticker], today) if ticker in prices else (np.nan, None)
        ret = (current / float(entry) - 1.0) if pd.notna(entry) and pd.notna(current) and float(entry) else np.nan
        rows.append(
            {
                "ticker": ticker,
                "peso_recomendado": weight,
                "preco_entrada": entry,
                "preco_atual": current,
                "data_avaliacao": price_date.date().isoformat() if price_date is not None else "",
                "retorno_periodo": ret,
                "contribuicao": ret * weight if pd.notna(ret) else np.nan,
                "fonte_preco": source,
                "retorno_cdi_bruto_periodo": np.nan,
                "aliquota_ir_cdi": np.nan,
                "dias_corridos_cdi": np.nan,
                "retorno_cdi_liquido_periodo": np.nan,
            }
        )

    assets = pd.DataFrame(rows)
    ibov_entry = np.nan
    ibov_current = np.nan
    ibov_date = None
    if "^BVSP" in prices:
        ibov_entry, _ = last_price(prices["^BVSP"], entry_date)
        ibov_current, ibov_date = last_price(prices["^BVSP"], today)
    ibov_return = (ibov_current / ibov_entry - 1.0) if pd.notna(ibov_entry) and pd.notna(ibov_current) and ibov_entry else np.nan
    portfolio_return = pd.to_numeric(assets["contribuicao"], errors="coerce").fillna(0.0).sum()

    summary = pd.DataFrame(
        [
            {"metrica": "mes", "valor": mes_key},
            {"metrica": "arquivo_forward_usado", "valor": forward.name},
            {"metrica": "data_entrada", "valor": entry_date.date().isoformat()},
            {"metrica": "data_avaliacao_parcial", "valor": ibov_date.date().isoformat() if ibov_date is not None else today.date().isoformat()},
            {"metrica": "status", "valor": period_status},
            {"metrica": "fonte_precos", "valor": source},
            {"metrica": "consulta_rede_autorizada", "valor": bool(args.allow_network)},
            {"metrica": "retorno_carteira_parcial_aplicada", "valor": portfolio_return},
            {"metrica": "retorno_ibov_parcial", "valor": ibov_return},
            {"metrica": "alfa_parcial_vs_ibov", "valor": portfolio_return - ibov_return if pd.notna(ibov_return) else np.nan},
            {"metrica": "exposicao_acoes", "valor": assets.loc[~assets["ticker"].isin(["CAIXA", "CDI"]), "peso_recomendado"].sum()},
            {"metrica": "peso_defensivo_cdi", "valor": assets.loc[assets["ticker"].isin(["CAIXA", "CDI"]), "peso_recomendado"].sum()},
            {"metrica": "retorno_cdi_bruto_periodo", "valor": cdi_gross},
            {"metrica": "aliquota_ir_cdi", "valor": cdi_ir_rate},
            {"metrica": "retorno_cdi_liquido_periodo", "valor": cdi_net},
            {"metrica": "fonte_cdi", "valor": cdi_source},
        ]
    )

    output = EXCEL_DIR / f"parcial_carteira_forward_{year:04d}_{month:02d}_v{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo Parcial", index=False)
        assets.to_excel(writer, sheet_name="Ativos", index=False)
        cdi_daily.to_excel(writer, sheet_name="CDI Diario", index=False)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"forward_partial_{year:04d}_{month:02d}_{datetime.now():%Y%m%d_%H%M%S}.log"
    lines = [
        f"Parcial forward {mes_key}",
        f"Arquivo forward usado: {forward.name}",
        f"Fonte precos: {source}",
        f"Data entrada: {entry_date.date().isoformat()}",
        f"CDI fonte: {cdi_source}",
        f"CDI bruto periodo: {cdi_gross:.4%}" if pd.notna(cdi_gross) else "CDI bruto periodo: n/a",
        f"IR CDI: {cdi_ir_rate:.2%}; CDI liquido periodo: {cdi_net:.4%}" if pd.notna(cdi_net) else f"IR CDI: {cdi_ir_rate:.2%}; CDI liquido periodo: n/a",
        f"Retorno carteira aplicada: {portfolio_return:.2%}",
        f"Retorno IBOV parcial: {ibov_return:.2%}" if pd.notna(ibov_return) else "Retorno IBOV parcial: n/a",
        f"Alfa parcial: {portfolio_return - ibov_return:.2%}" if pd.notna(ibov_return) else "Alfa parcial: n/a",
        f"Arquivo gerado: {output}",
    ]
    log_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"Log gerado: {log_path}")


if __name__ == "__main__":
    main()

