from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_FILE = EXCEL_DIR / "shadow_teste46_carteira_executavel.xlsx"
OUTPUT_FILE = INPUT_FILE
BACKUP_FILE = EXCEL_DIR / "shadow_teste46_carteira_executavel_pre_total_return.xlsx"
IBOV_FILE = ROOT / "data" / "processed" / "ibov_mensal_oficial.csv"
LOG_FILE = LOG_DIR / "recalcular_historico_executavel_total_return.log"


def compound_return(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    return float((1.0 + values).prod() - 1.0) if not values.empty else float("nan")


def max_drawdown(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return float("nan")
    curve = (1.0 + values).cumprod()
    return float((curve / curve.cummax() - 1.0).min())


def summarize(group: pd.DataFrame, label: str) -> dict[str, Any]:
    return {
        "grupo": label,
        "meses": int(len(group)),
        "retorno_executavel": compound_return(group["retorno_executavel"]),
        "retorno_teorico_t44a": compound_return(group["retorno_teorico_t44a"]),
        "retorno_ibov": compound_return(group["retorno_ibov"]),
        "alfa_executavel_vs_ibov": compound_return(group["retorno_executavel"]) - compound_return(group["retorno_ibov"]),
        "alfa_teorico_vs_ibov": compound_return(group["retorno_teorico_t44a"]) - compound_return(group["retorno_ibov"]),
        "delta_exec_vs_teorico": compound_return(group["retorno_executavel"]) - compound_return(group["retorno_teorico_t44a"]),
        "taxa_acerto_executavel": float((group["retorno_executavel"] > group["retorno_ibov"]).mean()),
        "taxa_acerto_teorico": float((group["retorno_teorico_t44a"] > group["retorno_ibov"]).mean()),
        "drawdown_executavel": max_drawdown(group["retorno_executavel"]),
        "peso_acoes_medio_executavel": float(pd.to_numeric(group["peso_acoes_executavel"], errors="coerce").mean()),
        "n_acoes_executaveis_medio": float(pd.to_numeric(group["n_acoes_executaveis"], errors="coerce").mean()),
    }


def close_at_or_before(series: pd.Series, target: Any) -> tuple[float, pd.Timestamp | None]:
    target = pd.to_datetime(target, errors="coerce")
    if pd.isna(target):
        return float("nan"), None
    series = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    available = series.loc[series.index <= target]
    if available.empty:
        return float("nan"), None
    return float(available.iloc[-1]), pd.Timestamp(available.index[-1])


def download_adjusted_prices(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.Series]:
    raw = yf.download(
        tickers=tickers,
        start=(start - pd.Timedelta(days=7)).date().isoformat(),
        end=(end + pd.Timedelta(days=2)).date().isoformat(),
        auto_adjust=False,
        progress=False,
        group_by="column",
        threads=True,
    )
    if raw.empty:
        return {}
    adjusted = raw.get("Adj Close")
    if adjusted is None:
        return {}
    if isinstance(adjusted, pd.Series):
        adjusted = adjusted.to_frame(name=tickers[0])
    return {ticker: adjusted[ticker].dropna() for ticker in tickers if ticker in adjusted.columns}


def main() -> None:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(INPUT_FILE)

    sheets = pd.read_excel(INPUT_FILE, sheet_name=None)
    monthly = sheets["Mes a Mes"].copy()
    portfolios = sheets["Carteiras Executaveis"].copy()
    monthly["mes"] = monthly["mes"].astype(str).str[:7]
    portfolios["mes"] = portfolios["mes"].astype(str).str[:7]
    monthly["data_inicio_performance"] = pd.to_datetime(monthly["data_inicio_performance"], errors="coerce")
    monthly["data_avaliacao"] = pd.to_datetime(monthly["data_avaliacao"], errors="coerce")

    equity = portfolios[portfolios["tipo_alocacao"].astype(str).eq("acao")].copy()
    tickers = sorted(equity["ticker"].dropna().astype(str).unique().tolist())
    start = monthly["data_inicio_performance"].min()
    end = monthly["data_avaliacao"].max()
    prices = download_adjusted_prices(tickers, start, end)

    if not BACKUP_FILE.exists():
        shutil.copy2(INPUT_FILE, BACKUP_FILE)

    problems: list[dict[str, Any]] = []
    for idx, row in equity.iterrows():
        ticker = str(row["ticker"])
        month_row = monthly.loc[monthly["mes"].eq(str(row["mes"]))].iloc[0]
        series = prices.get(ticker, pd.Series(dtype=float))
        entry, entry_date = close_at_or_before(series, month_row["data_inicio_performance"])
        exit_price, exit_date = close_at_or_before(series, month_row["data_avaliacao"])
        old_return = pd.to_numeric(row.get("retorno_periodo"), errors="coerce")
        if pd.isna(entry) or pd.isna(exit_price) or entry <= 0:
            new_return = np.nan
            problems.append({"mes": row["mes"], "ticker": ticker, "motivo": "preco_ajustado_indisponivel", "data_inicio": month_row["data_inicio_performance"], "data_avaliacao": month_row["data_avaliacao"]})
        else:
            new_return = exit_price / entry - 1.0
        mask = (portfolios["mes"].astype(str).str[:7].eq(str(row["mes"]))) & portfolios["ticker"].astype(str).eq(ticker)
        portfolios.loc[mask, "retorno_periodo_original"] = old_return
        portfolios.loc[mask, "preco_retorno_entrada_ajustado"] = entry
        portfolios.loc[mask, "data_retorno_entrada"] = entry_date.date().isoformat() if entry_date is not None else ""
        portfolios.loc[mask, "preco_retorno_avaliacao_ajustado"] = exit_price
        portfolios.loc[mask, "data_retorno_avaliacao"] = exit_date.date().isoformat() if exit_date is not None else ""
        portfolios.loc[mask, "base_retorno"] = "yfinance_adj_close_mesma_consulta"
        portfolios.loc[mask, "retorno_periodo"] = new_return

    portfolios["peso_executavel_total"] = pd.to_numeric(portfolios["peso_executavel_total"], errors="coerce")
    portfolios["retorno_periodo"] = pd.to_numeric(portfolios["retorno_periodo"], errors="coerce")
    portfolios["contribuicao_executavel"] = portfolios["peso_executavel_total"] * portfolios["retorno_periodo"]

    official_ibov = pd.read_csv(IBOV_FILE)
    official_ibov["mes"] = official_ibov["mes"].astype(str).str[:7]
    official_map = official_ibov.set_index("mes")["retorno_ibov_oficial"]
    monthly["retorno_ibov"] = monthly["mes"].map(official_map).fillna(pd.to_numeric(monthly["retorno_ibov"], errors="coerce"))

    month_return = portfolios.groupby("mes")["contribuicao_executavel"].sum(min_count=1)
    incomplete_months = {item["mes"] for item in problems}
    monthly["retorno_executavel"] = monthly["mes"].map(month_return)
    monthly.loc[monthly["mes"].isin(incomplete_months), "retorno_executavel"] = np.nan
    monthly["alfa_executavel"] = monthly["retorno_executavel"] - monthly["retorno_ibov"]
    monthly["delta_retorno_vs_teorico"] = monthly["retorno_executavel"] - pd.to_numeric(monthly["retorno_teorico_t44a"], errors="coerce")
    monthly["ano"] = monthly["mes"].str[:4].astype(int)
    monthly["bateu_ibov_executavel"] = monthly["retorno_executavel"] > monthly["retorno_ibov"]

    summary = pd.DataFrame([summarize(monthly, "2022-2026"), summarize(monthly[monthly["mes"] >= "2023-01"], "2023-2026")])
    summary_year = pd.DataFrame([summarize(group, str(year)) for year, group in monthly.groupby("ano")])
    summary_regime = pd.DataFrame([summarize(group, str(regime)) for regime, group in monthly.groupby("tipo_regime_expost")])
    validation = monthly[["mes", "retorno_executavel", "retorno_teorico_t44a", "retorno_ibov", "delta_retorno_vs_teorico", "peso_acoes_executavel", "peso_cdi_executavel", "n_acoes_executaveis"]].copy()
    validation["pesos_fecham_100"] = (validation["peso_acoes_executavel"] + validation["peso_cdi_executavel"] - 1.0).abs() < 1e-9
    contributions = portfolios.groupby("mes")["contribuicao_executavel"].sum(min_count=1)
    validation["diferenca_retorno_recalculado"] = validation["mes"].map(contributions) - validation["retorno_executavel"]
    validation["retorno_bate_contribuicoes"] = validation["diferenca_retorno_recalculado"].abs() < 1e-9

    sheets["Resumo Geral"] = summary
    sheets["Resumo Ano"] = summary_year
    sheets["Resumo Regime Real"] = summary_regime
    sheets["Mes a Mes"] = monthly
    sheets["Carteiras Executaveis"] = portfolios
    sheets["Validacao"] = validation
    sheets["Log Precos"] = pd.DataFrame(problems)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    before = pd.read_excel(BACKUP_FILE, sheet_name="Resumo Geral")
    lines = [
        "Reprocessamento de retorno total da carteira executavel",
        "Base: preco de entrada e saida na mesma serie yfinance Adj Close.",
        f"Acoes processadas: {len(equity)}; tickers: {len(tickers)}; meses: {len(monthly)}.",
        f"Pontos de cotacao faltantes: {len(problems)}.",
    ]
    for label, current in [("antes", before.iloc[0]), ("depois", summary.iloc[0])]:
        lines.append(f"{label}: retorno={current['retorno_executavel']:.2%}; ibov={current['retorno_ibov']:.2%}; alfa={current['alfa_executavel_vs_ibov']:.2%}; acerto={current['taxa_acerto_executavel']:.2%}")
    LOG_FILE.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"Arquivo: {OUTPUT_FILE}")
    print(f"Backup: {BACKUP_FILE}")
    print(f"Log: {LOG_FILE}")


if __name__ == "__main__":
    main()


