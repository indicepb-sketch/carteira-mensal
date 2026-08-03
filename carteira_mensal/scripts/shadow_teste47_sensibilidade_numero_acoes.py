from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"

INPUT_FILE = EXCEL_DIR / "shadow_teste46_carteira_executavel.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste47_sensibilidade_numero_acoes.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste47_sensibilidade_numero_acoes.log"

CAPITAL_BASE = 10_000.0
SCENARIOS = {
    "ATUAL_T46": None,
    "TOP10": 10,
    "TOP12": 12,
    "TOP15": 15,
    "ADAPTATIVO_10_12_15": "adaptive",
}


def pct(value: Any) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


def compound_return(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return float("nan")
    return float((1.0 + values).prod() - 1.0)


def max_drawdown(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return float("nan")
    curve = (1.0 + values).cumprod()
    peak = curve.cummax()
    return float((curve / peak - 1.0).min())


def adaptive_limit(stock_exposure: float) -> int:
    if stock_exposure <= 0.40:
        return 10
    if stock_exposure <= 0.80:
        return 12
    return 15


def scenario_limit(name: str, value: int | str | None, stock_exposure: float) -> int | None:
    if value == "adaptive":
        return adaptive_limit(stock_exposure)
    return value


def rank_stocks(stocks: pd.DataFrame) -> pd.DataFrame:
    ranked = stocks.copy()
    for col in ["nota_final", "peso_executavel_total", "contribuicao_executavel", "beta"]:
        if col in ranked.columns:
            ranked[col] = pd.to_numeric(ranked[col], errors="coerce")
    ranked["_rank_nota"] = ranked.get("nota_final", pd.Series(index=ranked.index, dtype=float)).fillna(-999)
    ranked["_rank_peso"] = ranked.get("peso_executavel_total", pd.Series(index=ranked.index, dtype=float)).fillna(0)
    ranked["_rank_contrib"] = ranked.get("contribuicao_executavel", pd.Series(index=ranked.index, dtype=float)).fillna(-999)
    return ranked.sort_values(
        ["_rank_nota", "_rank_peso", "_rank_contrib", "ticker"],
        ascending=[False, False, False, True],
    )


def rebuild_month(month: str, scenario: str, limit: int | None, month_rows: pd.DataFrame, perf_row: pd.Series) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = month_rows.copy()
    stocks = rows[rows["ticker"].astype(str).str.upper().ne("CDI")].copy()
    cdi = rows[rows["ticker"].astype(str).str.upper().eq("CDI")].copy()

    original_stock_weight = float(pd.to_numeric(stocks["peso_executavel_total"], errors="coerce").sum())
    original_cdi_weight = float(pd.to_numeric(cdi["peso_executavel_total"], errors="coerce").sum())
    cdi_return = float(pd.to_numeric(cdi["retorno_periodo"], errors="coerce").dropna().iloc[0]) if not cdi.empty else 0.0

    if scenario == "ATUAL_T46":
        baseline = rows.copy()
        baseline["cenario"] = scenario
        baseline["limite_acoes"] = len(stocks)
        baseline["quantidade_reduzida"] = pd.to_numeric(baseline.get("quantidade"), errors="coerce")
        baseline["valor_executado_reduzido"] = pd.to_numeric(baseline.get("valor_executado"), errors="coerce")
        baseline["peso_final"] = pd.to_numeric(baseline.get("peso_executavel_total"), errors="coerce")
        baseline["contribuicao_final"] = pd.to_numeric(baseline.get("contribuicao_executavel"), errors="coerce")
        if "tipo_alocacao" not in baseline.columns:
            baseline["tipo_alocacao"] = np.where(baseline["ticker"].astype(str).str.upper().eq("CDI"), "cdi_residual", "acao")
        out_cols = [
            "mes",
            "cenario",
            "limite_acoes",
            "ticker",
            "nome",
            "setor",
            "tipo_alocacao",
            "preco_entrada",
            "quantidade_reduzida",
            "valor_executado_reduzido",
            "peso_final",
            "retorno_periodo",
            "contribuicao_final",
            "nota_final",
            "beta",
        ]
        for col in out_cols:
            if col not in baseline.columns:
                baseline[col] = np.nan
        portfolio = baseline[out_cols].copy()
        retorno = float(pd.to_numeric(perf_row.get("retorno_executavel"), errors="coerce"))
        ibov = float(pd.to_numeric(perf_row.get("retorno_ibov"), errors="coerce"))
        meta = {
            "mes": month,
            "cenario": scenario,
            "limite_acoes": len(stocks),
            "qtd_acoes": int(stocks.shape[0]),
            "peso_acoes": original_stock_weight,
            "peso_cdi": original_cdi_weight,
            "retorno": retorno,
            "retorno_ibov": ibov,
            "alfa_vs_ibov": retorno - ibov,
            "bateu_ibov": retorno > ibov,
            "qtd_original": int(len(stocks)),
            "peso_acoes_original": original_stock_weight,
            "peso_cdi_original": original_cdi_weight,
        }
        return portfolio, meta

    chosen = rank_stocks(stocks)
    if limit is not None:
        chosen = chosen.head(int(limit)).copy()

    chosen_weight_base = pd.to_numeric(chosen["peso_executavel_total"], errors="coerce").fillna(0.0)
    if chosen.empty or chosen_weight_base.sum() <= 0 or original_stock_weight <= 0:
        chosen = chosen.iloc[0:0].copy()
        stock_value = 0.0
    else:
        chosen["peso_modelo_reduzido"] = chosen_weight_base / chosen_weight_base.sum() * original_stock_weight
        chosen["valor_alvo_reduzido"] = chosen["peso_modelo_reduzido"] * CAPITAL_BASE
        chosen["preco_entrada"] = pd.to_numeric(chosen["preco_entrada"], errors="coerce")
        chosen["quantidade_reduzida"] = np.floor(chosen["valor_alvo_reduzido"] / chosen["preco_entrada"]).replace([np.inf, -np.inf], np.nan).fillna(0).clip(lower=0).astype(int)
        chosen["valor_executado_reduzido"] = chosen["quantidade_reduzida"] * chosen["preco_entrada"]
        chosen = chosen[chosen["quantidade_reduzida"] > 0].copy()
        stock_value = float(chosen["valor_executado_reduzido"].sum())

    cdi_value = max(CAPITAL_BASE - stock_value, 0.0)
    if not chosen.empty:
        chosen["cenario"] = scenario
        chosen["limite_acoes"] = limit if limit is not None else len(stocks)
        chosen["peso_final"] = chosen["valor_executado_reduzido"] / CAPITAL_BASE
        chosen["retorno_periodo"] = pd.to_numeric(chosen["retorno_periodo"], errors="coerce")
        chosen["contribuicao_final"] = chosen["peso_final"] * chosen["retorno_periodo"]
    else:
        chosen["cenario"] = scenario
        chosen["limite_acoes"] = limit
        chosen["peso_final"] = []
        chosen["contribuicao_final"] = []

    cdi_row = pd.DataFrame(
        [
            {
                "mes": month,
                "cenario": scenario,
                "limite_acoes": limit if limit is not None else len(stocks),
                "ticker": "CDI",
                "nome": "Reserva/CDI liquido",
                "setor": "Protecao",
                "tipo_alocacao": "cdi_residual",
                "preco_entrada": np.nan,
                "quantidade_reduzida": np.nan,
                "valor_executado_reduzido": cdi_value,
                "peso_final": cdi_value / CAPITAL_BASE,
                "retorno_periodo": cdi_return,
                "contribuicao_final": (cdi_value / CAPITAL_BASE) * cdi_return,
                "nota_final": np.nan,
                "beta": np.nan,
            }
        ]
    )

    out_cols = [
        "mes",
        "cenario",
        "limite_acoes",
        "ticker",
        "nome",
        "setor",
        "tipo_alocacao",
        "preco_entrada",
        "quantidade_reduzida",
        "valor_executado_reduzido",
        "peso_final",
        "retorno_periodo",
        "contribuicao_final",
        "nota_final",
        "beta",
    ]
    for col in out_cols:
        if col not in chosen.columns:
            chosen[col] = np.nan
    portfolio = pd.concat([chosen[out_cols], cdi_row[out_cols]], ignore_index=True)
    retorno = float(pd.to_numeric(portfolio["contribuicao_final"], errors="coerce").fillna(0).sum())
    ibov = float(pd.to_numeric(perf_row.get("retorno_ibov"), errors="coerce"))
    meta = {
        "mes": month,
        "cenario": scenario,
        "limite_acoes": limit if limit is not None else len(stocks),
        "qtd_acoes": int(portfolio[portfolio["ticker"].astype(str).str.upper().ne("CDI")].shape[0]),
        "peso_acoes": float(portfolio.loc[portfolio["ticker"].astype(str).str.upper().ne("CDI"), "peso_final"].sum()),
        "peso_cdi": float(portfolio.loc[portfolio["ticker"].astype(str).str.upper().eq("CDI"), "peso_final"].sum()),
        "retorno": retorno,
        "retorno_ibov": ibov,
        "alfa_vs_ibov": retorno - ibov,
        "bateu_ibov": retorno > ibov,
        "qtd_original": int(len(stocks)),
        "peso_acoes_original": original_stock_weight,
        "peso_cdi_original": original_cdi_weight,
    }
    return portfolio, meta


def summarize(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario, data in monthly.groupby("cenario"):
        data = data.sort_values("mes")
        rows.append(
            {
                "cenario": scenario,
                "meses": len(data),
                "retorno_modelo": compound_return(data["retorno"]),
                "retorno_ibov": compound_return(data["retorno_ibov"]),
                "alfa_vs_ibov": compound_return(data["retorno"]) - compound_return(data["retorno_ibov"]),
                "taxa_acerto": pd.to_numeric(data["bateu_ibov"], errors="coerce").mean(),
                "drawdown": max_drawdown(data["retorno"]),
                "qtd_acoes_media": pd.to_numeric(data["qtd_acoes"], errors="coerce").mean(),
                "qtd_acoes_min": pd.to_numeric(data["qtd_acoes"], errors="coerce").min(),
                "qtd_acoes_max": pd.to_numeric(data["qtd_acoes"], errors="coerce").max(),
                "peso_acoes_medio": pd.to_numeric(data["peso_acoes"], errors="coerce").mean(),
            }
        )
    return pd.DataFrame(rows).sort_values("cenario")


def summarize_by_year(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    df = monthly.copy()
    df["ano"] = df["mes"].astype(str).str[:4]
    for (scenario, year), data in df.groupby(["cenario", "ano"]):
        data = data.sort_values("mes")
        rows.append(
            {
                "cenario": scenario,
                "ano": year,
                "meses": len(data),
                "retorno_modelo": compound_return(data["retorno"]),
                "retorno_ibov": compound_return(data["retorno_ibov"]),
                "alfa_vs_ibov": compound_return(data["retorno"]) - compound_return(data["retorno_ibov"]),
                "taxa_acerto": pd.to_numeric(data["bateu_ibov"], errors="coerce").mean(),
                "qtd_acoes_media": pd.to_numeric(data["qtd_acoes"], errors="coerce").mean(),
            }
        )
    return pd.DataFrame(rows).sort_values(["ano", "cenario"])


def main() -> None:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(INPUT_FILE)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    perf = pd.read_excel(INPUT_FILE, sheet_name="Mes a Mes")
    portfolios = pd.read_excel(INPUT_FILE, sheet_name="Carteiras Executaveis")
    perf["mes"] = perf["mes"].astype(str).str[:7]
    portfolios["mes"] = portfolios["mes"].astype(str).str[:7]

    all_portfolios = []
    monthly_rows = []
    for _, perf_row in perf.sort_values("mes").iterrows():
        month = str(perf_row["mes"])
        month_rows = portfolios[portfolios["mes"].eq(month)].copy()
        original_stock_weight = float(pd.to_numeric(month_rows.loc[month_rows["ticker"].astype(str).str.upper().ne("CDI"), "peso_executavel_total"], errors="coerce").sum())
        for scenario, value in SCENARIOS.items():
            limit = scenario_limit(scenario, value, original_stock_weight)
            portfolio, meta = rebuild_month(month, scenario, limit, month_rows, perf_row)
            all_portfolios.append(portfolio)
            monthly_rows.append(meta)

    portfolios_out = pd.concat(all_portfolios, ignore_index=True)
    monthly = pd.DataFrame(monthly_rows)
    summary = summarize(monthly)
    yearly = summarize_by_year(monthly)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo", index=False)
        yearly.to_excel(writer, sheet_name="Resumo Ano", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        portfolios_out.to_excel(writer, sheet_name="Carteiras", index=False)

    lines = ["Teste 47 - Sensibilidade ao Numero Maximo de Acoes", f"Entrada: {INPUT_FILE.name}", ""]
    for _, row in summary.sort_values("retorno_modelo", ascending=False).iterrows():
        lines.append(
            f"{row['cenario']}: retorno={pct(row['retorno_modelo'])}; "
            f"IBOV={pct(row['retorno_ibov'])}; alfa={pct(row['alfa_vs_ibov'])}; "
            f"acerto={pct(row['taxa_acerto'])}; drawdown={pct(row['drawdown'])}; "
            f"qtd_media={row['qtd_acoes_media']:.1f}"
        )
    lines.extend(["", f"Arquivo gerado: {OUTPUT_FILE}", f"Log gerado: {LOG_FILE}"])
    LOG_FILE.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

