from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
RAW_DIR = ROOT / "data" / "raw"
BASE_FILE = EXCEL_DIR / "shadow_teste36_exposicao_regime_2.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste38_sensibilidade_janela_retorno.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste38_sensibilidade_janela_retorno.log"
BASE_SCENARIO = "T36C_QUALIDADE"

WINDOWS = {
    "BASE_T36C": None,
    "JANELA_2M": 2,
    "JANELA_3M": 3,
    "JANELA_4M": 4,
    "JANELA_5M": 5,
}


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float((1.0 + vals).prod() - 1.0)


def max_drawdown(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").fillna(0.0)
    equity = (1.0 + vals).cumprod()
    if equity.empty:
        return np.nan
    return float((equity / equity.cummax() - 1.0).min())


def normalize_series(values: pd.Series) -> pd.Series:
    vals = pd.to_numeric(values, errors="coerce")
    if vals.notna().sum() == 0:
        return pd.Series(0.5, index=values.index)
    lo = float(vals.min())
    hi = float(vals.max())
    if math.isclose(lo, hi):
        return pd.Series(0.5, index=values.index)
    return (vals - lo) / (hi - lo)


def price_file_candidates(ticker: str) -> list[Path]:
    safe = str(ticker).strip().replace(".", "_")
    files = list(RAW_DIR.glob(f"prices_{safe}*.csv"))
    return sorted(files, key=lambda p: p.stat().st_size, reverse=True)


@lru_cache(maxsize=None)
def load_price_series(ticker: str) -> pd.Series:
    for path in price_file_candidates(ticker):
        try:
            df = pd.read_csv(path, skiprows=[1, 2])
            if "Price" not in df.columns or "Adj Close" not in df.columns:
                continue
            dates = pd.to_datetime(df["Price"], errors="coerce")
            adj = pd.to_numeric(df["Adj Close"], errors="coerce")
            out = pd.Series(adj.values, index=dates).dropna()
            out = out[~out.index.isna()].sort_index()
            if not out.empty:
                return out
        except Exception:
            continue
    return pd.Series(dtype=float)


def trailing_return(ticker: str, formation_date: Any, months: int) -> float:
    series = load_price_series(str(ticker).strip())
    if series.empty or pd.isna(formation_date):
        return np.nan
    end_date = pd.to_datetime(formation_date)
    start_date = end_date - pd.DateOffset(months=months)
    before_end = series[series.index <= end_date]
    before_start = series[series.index <= start_date]
    if before_end.empty or before_start.empty:
        return np.nan
    end_price = float(before_end.iloc[-1])
    start_price = float(before_start.iloc[-1])
    if start_price <= 0:
        return np.nan
    return end_price / start_price - 1.0


def capped_proportional_weights(scores: pd.Series, cap: float = 0.25) -> pd.Series:
    raw = pd.to_numeric(scores, errors="coerce").fillna(0.0).clip(lower=0.0)
    if raw.sum() <= 0:
        raw = pd.Series(1.0, index=scores.index)
    weights = pd.Series(0.0, index=scores.index, dtype=float)
    remaining = 1.0
    active = raw.copy()
    while remaining > 1e-12 and len(active) > 0:
        total = float(active.sum())
        if total <= 0:
            tentative = pd.Series(remaining / len(active), index=active.index)
        else:
            tentative = active / total * remaining
        over = tentative[tentative > cap]
        if over.empty:
            weights.loc[tentative.index] += tentative
            remaining = 0.0
            break
        for idx, _ in over.items():
            add = max(0.0, cap - float(weights.loc[idx]))
            weights.loc[idx] += add
            remaining -= add
            active = active.drop(idx)
    if weights.sum() > 0:
        weights = weights / weights.sum()
    return weights




def load_date_map() -> pd.DataFrame:
    frames = []
    t35 = EXCEL_DIR / "shadow_teste35_modelo_consolidado_operacional_2022_2026.xlsx"
    if t35.exists():
        df = pd.read_excel(t35, sheet_name="Mes a Mes", usecols=lambda c: c in {"mes", "data_inicio_performance", "data_avaliacao"})
        frames.append(df)
    for year in [2022, 2023, 2024, 2025]:
        path = EXCEL_DIR / f"shadow_backtest_{year}.xlsx"
        if path.exists():
            df = pd.read_excel(path, sheet_name="expost_universo", usecols=lambda c: c in {"mes", "data_inicio_performance", "data_avaliacao"})
            frames.append(df.drop_duplicates("mes"))
    expost = EXCEL_DIR / "universo_expost_consolidado.xlsx"
    if expost.exists():
        df = pd.read_excel(expost, sheet_name="Universo Expost", usecols=lambda c: c in {"mes", "data_inicio_performance", "data_avaliacao"})
        frames.append(df.drop_duplicates("mes"))
    for path in sorted(EXCEL_DIR.glob("carteira_recomendada_2026_*_v*.xlsx")):
        try:
            df = pd.read_excel(path, sheet_name="Performance Realizada", nrows=1)
            if not df.empty and "data_inicio_performance" in df.columns:
                m = re.search(r"carteira_recomendada_(\d{4})_(\d{2})_v", path.name)
                if m:
                    frames.append(
                        pd.DataFrame(
                            [
                                {
                                    "mes": f"{m.group(1)}-{m.group(2)}",
                                    "data_inicio_performance": df.get("data_inicio_performance", pd.Series([np.nan])).iloc[0],
                                    "data_avaliacao": df.get("data_avaliacao_carteira", pd.Series([np.nan])).iloc[0],
                                }
                            ]
                        )
                    )
        except Exception:
            continue
    if not frames:
        return pd.DataFrame(columns=["mes", "data_inicio_performance", "data_avaliacao"])
    out = pd.concat(frames, ignore_index=True)
    out["mes"] = out["mes"].astype(str)
    out["data_inicio_performance"] = pd.to_datetime(out["data_inicio_performance"], errors="coerce")
    out["data_avaliacao"] = pd.to_datetime(out["data_avaliacao"], errors="coerce")
    out = out.sort_values("data_inicio_performance", na_position="last").drop_duplicates("mes", keep="first")
    return out

def load_base() -> tuple[pd.DataFrame, pd.DataFrame]:
    monthly = pd.read_excel(BASE_FILE, sheet_name="Mes a Mes")
    portfolios = pd.read_excel(BASE_FILE, sheet_name="Carteiras Por Cenario")
    monthly = monthly[monthly["cenario_teste36"].astype(str).eq(BASE_SCENARIO)].copy()
    portfolios = portfolios[portfolios["cenario_teste36"].astype(str).eq(BASE_SCENARIO)].copy()
    monthly["mes"] = monthly["mes"].astype(str)
    portfolios["mes"] = portfolios["mes"].astype(str)
    date_map = load_date_map()
    if not date_map.empty:
        monthly = monthly.merge(date_map, on="mes", how="left", suffixes=("", "_map"))
        for col in ["data_inicio_performance", "data_avaliacao"]:
            map_col = f"{col}_map"
            if map_col in monthly.columns:
                monthly[col] = monthly[col].where(monthly[col].notna(), monthly[map_col])
                monthly = monthly.drop(columns=[map_col])
    return monthly, portfolios


def build_window_portfolio(monthly: pd.DataFrame, portfolios: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    monthly_idx = monthly.set_index("mes")
    rows = []
    calc_rows = []
    missing_rows = []

    for scenario, months_window in WINDOWS.items():
        for mes, group in portfolios.groupby("mes", sort=True):
            if mes not in monthly_idx.index:
                continue
            m = monthly_idx.loc[mes]
            stocks = group[~group["tipo_alocacao"].astype(str).str.contains("cdi", case=False, na=False)].copy()
            cdi = group[group["tipo_alocacao"].astype(str).str.contains("cdi", case=False, na=False)].copy()
            exposure = float(m.get("multiplicador_exposicao_regime", 1.0))
            if scenario == "BASE_T36C":
                stock_weights = pd.to_numeric(stocks["peso_bruto_acao"], errors="coerce").fillna(0.0)
            else:
                formation_date = m.get("data_inicio_performance")
                ibov_ret = trailing_return("^BVSP", formation_date, int(months_window))
                stock_returns = []
                for _, row in stocks.iterrows():
                    ticker = str(row["ticker"]).strip()
                    ret = trailing_return(ticker, formation_date, int(months_window))
                    stock_returns.append(ret)
                    if pd.isna(ret):
                        missing_rows.append(
                            {
                                "cenario_t38": scenario,
                                "mes": mes,
                                "ticker": ticker,
                                "motivo": f"sem_preco_para_janela_{months_window}m",
                            }
                        )
                stocks = stocks.copy()
                stocks["retorno_janela"] = stock_returns
                stocks["retorno_ibov_janela"] = ibov_ret
                stocks["retorno_relativo_janela"] = stocks["retorno_janela"] - ibov_ret
                note_norm = normalize_series(stocks["nota_final"])
                ret_norm = normalize_series(stocks["retorno_relativo_janela"])
                signal = (0.50 * note_norm + 0.50 * ret_norm).fillna(0.0)
                stock_budget = float(pd.to_numeric(stocks["peso_bruto_acao"], errors="coerce").fillna(0.0).sum())
                stock_weights = capped_proportional_weights(signal, cap=0.25) * stock_budget
                calc_rows.extend(
                    stocks.assign(
                        cenario_t38=scenario,
                        janela_meses=months_window,
                        nota_norm=note_norm,
                        retorno_relativo_norm=ret_norm,
                        sinal_peso_t38=signal,
                        peso_bruto_t38=stock_weights.values,
                    )[
                        [
                            "cenario_t38",
                            "mes",
                            "ticker",
                            "nome",
                            "setor",
                            "janela_meses",
                            "nota_final",
                            "retorno_janela",
                            "retorno_ibov_janela",
                            "retorno_relativo_janela",
                            "nota_norm",
                            "retorno_relativo_norm",
                            "sinal_peso_t38",
                            "peso_bruto_t38",
                            "peso_bruto_acao",
                        ]
                    ].to_dict("records")
                )
            stock_weights = pd.Series(stock_weights.values, index=stocks.index, dtype=float)
            for idx, row in stocks.iterrows():
                w_stock = float(stock_weights.loc[idx])
                ret_period = float(row.get("retorno_periodo", np.nan))
                rows.append(
                    {
                        "cenario_t38": scenario,
                        "janela_meses": months_window if months_window is not None else "base",
                        "mes": mes,
                        "ticker": row.get("ticker"),
                        "nome": row.get("nome"),
                        "setor": row.get("setor"),
                        "tipo_alocacao": "acao",
                        "peso_dentro_da_parte_acoes": w_stock,
                        "multiplicador_exposicao_regime": exposure,
                        "peso_efetivo_carteira_total": w_stock * exposure,
                        "retorno_periodo": ret_period,
                        "contribuicao_retorno_total": w_stock * exposure * ret_period if pd.notna(ret_period) else np.nan,
                        "nota_final": row.get("nota_final", np.nan),
                        "beta": row.get("beta", np.nan),
                        "cv": row.get("cv", np.nan),
                        "regime_previsto_norm": m.get("regime_previsto_norm", ""),
                        "tipo_regime_expost": m.get("tipo_regime_expost", ""),
                    }
                )
            stock_budget_effective = float(pd.Series(stock_weights).sum()) * exposure
            cdi_weight = 1.0 - stock_budget_effective
            cdi_ret = float(cdi["retorno_periodo"].iloc[0]) if not cdi.empty else float(m.get("retorno_cdi_liquido_periodo", 0.0))
            rows.append(
                {
                    "cenario_t38": scenario,
                    "janela_meses": months_window if months_window is not None else "base",
                    "mes": mes,
                    "ticker": "CDI",
                    "nome": "CDI liquido de IR no residual de exposicao",
                    "setor": "Caixa/CDI",
                    "tipo_alocacao": "cdi_residual",
                    "peso_dentro_da_parte_acoes": np.nan,
                    "multiplicador_exposicao_regime": exposure,
                    "peso_efetivo_carteira_total": cdi_weight,
                    "retorno_periodo": cdi_ret,
                    "contribuicao_retorno_total": cdi_weight * cdi_ret,
                    "nota_final": np.nan,
                    "beta": 0.0,
                    "cv": np.nan,
                    "regime_previsto_norm": m.get("regime_previsto_norm", ""),
                    "tipo_regime_expost": m.get("tipo_regime_expost", ""),
                }
            )

    return pd.DataFrame(rows), pd.DataFrame(calc_rows), pd.DataFrame(missing_rows)


def build_monthly(portfolio: pd.DataFrame, base_monthly: pd.DataFrame) -> pd.DataFrame:
    ret = portfolio.groupby(["cenario_t38", "mes"], as_index=False).agg(
        retorno_total=("contribuicao_retorno_total", "sum"),
        soma_pesos=("peso_efetivo_carteira_total", "sum"),
        maior_peso=("peso_efetivo_carteira_total", "max"),
        n_linhas=("ticker", "count"),
    )
    cols = [
        "mes",
        "regime_previsto_norm",
        "tipo_regime_expost",
        "retorno_expost_ibov",
        "retorno_cdi_liquido_periodo",
        "data_inicio_performance",
        "data_avaliacao",
    ]
    out = ret.merge(base_monthly[cols], on="mes", how="left")
    out["alfa_vs_ibov"] = out["retorno_total"] - out["retorno_expost_ibov"]
    out["bateu_ibov"] = out["alfa_vs_ibov"] > 0
    out["peso_acoes"] = portfolio[portfolio["tipo_alocacao"].eq("acao")].groupby(["cenario_t38", "mes"])["peso_efetivo_carteira_total"].sum().reindex(
        pd.MultiIndex.from_frame(out[["cenario_t38", "mes"]])
    ).values
    out["peso_cdi"] = 1.0 - out["peso_acoes"].fillna(0.0)
    return out.sort_values(["cenario_t38", "mes"])


def summarize(monthly: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows = []
    for keys, group in monthly.groupby(group_cols, dropna=False, sort=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        ret = compound(group["retorno_total"])
        ibov = compound(group["retorno_expost_ibov"])
        row.update(
            {
                "meses": len(group),
                "retorno_modelo": ret,
                "retorno_ibov": ibov,
                "alfa_vs_ibov": ret - ibov,
                "meses_bateu_ibov": int(group["bateu_ibov"].sum()),
                "taxa_acerto": float(group["bateu_ibov"].mean()),
                "drawdown": max_drawdown(group["retorno_total"]),
                "peso_acoes_medio": float(group["peso_acoes"].mean()),
                "maior_peso": float(group["maior_peso"].max()),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    logs = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    monthly_base, portfolios_base = load_base()
    portfolio, signal_audit, missing = build_window_portfolio(monthly_base, portfolios_base)
    monthly = build_monthly(portfolio, monthly_base)
    summary = summarize(monthly, ["cenario_t38"])
    by_year = monthly.assign(ano=monthly["mes"].astype(str).str[:4])
    summary_year = summarize(by_year, ["cenario_t38", "ano"])
    by_regime = summarize(monthly, ["cenario_t38", "tipo_regime_expost"])
    validation = monthly.assign(
        pesos_fecham_100=monthly["soma_pesos"].sub(1.0).abs().lt(1e-8),
        retorno_consistente=True,
    )

    base_alpha = float(summary.loc[summary["cenario_t38"].eq("BASE_T36C"), "alfa_vs_ibov"].iloc[0])
    summary["delta_alfa_vs_base"] = summary["alfa_vs_ibov"] - base_alpha
    base_by_year = summary_year[summary_year["cenario_t38"].eq("BASE_T36C")][["ano", "alfa_vs_ibov"]].rename(columns={"alfa_vs_ibov": "alfa_base_ano"})
    summary_year = summary_year.merge(base_by_year, on="ano", how="left")
    summary_year["delta_alfa_vs_base"] = summary_year["alfa_vs_ibov"] - summary_year["alfa_base_ano"]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo Geral", index=False)
        summary_year.to_excel(writer, sheet_name="Resumo Ano", index=False)
        by_regime.to_excel(writer, sheet_name="Resumo Regime Real", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        portfolio.to_excel(writer, sheet_name="Carteiras", index=False)
        signal_audit.to_excel(writer, sheet_name="Auditoria Sinais", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)
        missing.to_excel(writer, sheet_name="Log Faltantes", index=False)

    log("Teste 38 - Sensibilidade da Janela de Retorno Acumulado")
    log("Escopo: mesmos ativos mensais do T36C; recalcula pesos da parte em acoes por nota_final + retorno relativo ao IBOV na janela N.")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log("")
    log("Resumo geral:")
    log(summary[["cenario_t38", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "drawdown", "delta_alfa_vs_base"]].to_string(index=False))
    log("")
    log("Resumo por ano:")
    log(summary_year[["cenario_t38", "ano", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "delta_alfa_vs_base"]].to_string(index=False))
    invalid = validation[~validation["pesos_fecham_100"]]
    log("")
    log(f"Validacao pesos: {'OK' if invalid.empty else 'FALHAS=' + str(len(invalid))}")
    log(f"Faltantes de preco para janelas: {len(missing)}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()




