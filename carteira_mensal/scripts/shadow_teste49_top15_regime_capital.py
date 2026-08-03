from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_FILE = EXCEL_DIR / "shadow_teste46_carteira_executavel.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste49_top15_regime_capital.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste49_top15_regime_capital.log"

CAPITALS = [1_000.0, 5_000.0, 10_000.0, 50_000.0]
TOP_N = 15


def pct(value: Any) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


def money(value: Any) -> str:
    if pd.isna(value):
        return "-"
    return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def compound(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return np.nan
    return float((1 + values).prod() - 1)


def max_drawdown(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return np.nan
    curve = (1 + values).cumprod()
    peak = curve.cummax()
    return float((curve / peak - 1).min())


def rank_stocks(stocks: pd.DataFrame) -> pd.DataFrame:
    ranked = stocks.copy()
    for col in ["nota_final", "peso_teorico_total", "peso_executavel_total", "contribuicao_executavel"]:
        if col in ranked.columns:
            ranked[col] = pd.to_numeric(ranked[col], errors="coerce")
    ranked["_rank_nota"] = ranked.get("nota_final", pd.Series(index=ranked.index, dtype=float)).fillna(-999)
    ranked["_rank_peso"] = ranked.get("peso_teorico_total", ranked.get("peso_executavel_total", pd.Series(index=ranked.index, dtype=float))).fillna(0)
    ranked["_rank_contrib"] = ranked.get("contribuicao_executavel", pd.Series(index=ranked.index, dtype=float)).fillna(-999)
    return ranked.sort_values(
        ["_rank_nota", "_rank_peso", "_rank_contrib", "ticker"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)


def scenario_stocks(stocks: pd.DataFrame, scenario: str) -> pd.DataFrame:
    ranked = rank_stocks(stocks)
    if scenario == "ATUAL":
        selected = ranked.copy()
        selected["rank_operacional"] = np.arange(1, len(selected) + 1)
        return selected
    selected = ranked.head(TOP_N).copy()
    selected["rank_operacional"] = np.arange(1, len(selected) + 1)
    return selected


def build_portfolio(
    month: str,
    month_rows: pd.DataFrame,
    perf_row: pd.Series,
    scenario: str,
    capital: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = month_rows.copy()
    stocks = rows[rows["ticker"].astype(str).str.upper().ne("CDI")].copy()
    cdi = rows[rows["ticker"].astype(str).str.upper().eq("CDI")].copy()

    original_stock_weight = float(pd.to_numeric(stocks["peso_teorico_total"], errors="coerce").fillna(0).sum())
    cdi_return = float(pd.to_numeric(cdi["retorno_periodo"], errors="coerce").dropna().iloc[0]) if not cdi.empty else 0.0
    ibov = float(pd.to_numeric(perf_row.get("retorno_ibov"), errors="coerce"))
    regime_real = str(perf_row.get("tipo_regime_expost", "") or "")
    regime_previsto = str(perf_row.get("bucket_regime_previsto", perf_row.get("regime_previsto_norm", "")) or "")

    selected = scenario_stocks(stocks, scenario)
    weight_base = pd.to_numeric(selected.get("peso_teorico_total"), errors="coerce").fillna(0.0)
    if selected.empty or weight_base.sum() <= 0 or original_stock_weight <= 0:
        selected = selected.iloc[0:0].copy()
        stock_value = 0.0
    else:
        selected["peso_modelo"] = weight_base / weight_base.sum() * original_stock_weight
        selected["valor_alvo"] = selected["peso_modelo"] * capital
        selected["preco_entrada"] = pd.to_numeric(selected["preco_entrada"], errors="coerce")
        selected["quantidade"] = np.floor(selected["valor_alvo"] / selected["preco_entrada"]).replace([np.inf, -np.inf], np.nan).fillna(0).clip(lower=0).astype(int)
        selected["valor_executado"] = selected["quantidade"] * selected["preco_entrada"]
        selected = selected[selected["quantidade"] > 0].copy()
        stock_value = float(selected["valor_executado"].sum())

    if not selected.empty:
        selected["mes"] = month
        selected["cenario"] = scenario
        selected["capital"] = capital
        selected["peso_final"] = selected["valor_executado"] / capital
        selected["retorno_periodo"] = pd.to_numeric(selected["retorno_periodo"], errors="coerce")
        selected["contribuicao"] = selected["peso_final"] * selected["retorno_periodo"]
        selected["tipo_linha"] = "acao"

    cdi_value = max(capital - stock_value, 0.0)
    cdi_row = pd.DataFrame([{
        "mes": month,
        "cenario": scenario,
        "capital": capital,
        "ticker": "CDI",
        "nome": "Reserva/CDI liquido",
        "setor": "Protecao",
        "tipo_linha": "cdi",
        "rank_operacional": np.nan,
        "preco_entrada": np.nan,
        "quantidade": np.nan,
        "valor_alvo": cdi_value,
        "valor_executado": cdi_value,
        "peso_final": cdi_value / capital,
        "retorno_periodo": cdi_return,
        "contribuicao": (cdi_value / capital) * cdi_return,
        "nota_final": np.nan,
        "beta": np.nan,
        "cv": np.nan,
    }])
    cols = [
        "mes", "cenario", "capital", "ticker", "nome", "setor", "tipo_linha",
        "rank_operacional", "preco_entrada", "quantidade", "valor_alvo",
        "valor_executado", "peso_final", "retorno_periodo", "contribuicao",
        "nota_final", "beta", "cv",
    ]
    for col in cols:
        if col not in selected.columns:
            selected[col] = np.nan
    portfolio = pd.concat([selected[cols], cdi_row[cols]], ignore_index=True)
    stock_port = portfolio[portfolio["ticker"].astype(str).str.upper().ne("CDI")]
    retorno = float(pd.to_numeric(portfolio["contribuicao"], errors="coerce").fillna(0).sum())
    max_sector_count = int(stock_port.groupby("setor")["ticker"].count().max()) if not stock_port.empty else 0
    max_sector_weight = float(stock_port.groupby("setor")["peso_final"].sum().max()) if not stock_port.empty else 0.0
    meta = {
        "mes": month,
        "cenario": scenario,
        "capital": capital,
        "tipo_regime_expost": regime_real,
        "regime_previsto": regime_previsto,
        "qtd_acoes": int(len(stock_port)),
        "valor_acoes": float(stock_port["valor_executado"].sum()),
        "valor_cdi": cdi_value,
        "peso_acoes": float(stock_port["peso_final"].sum()),
        "peso_cdi": float(cdi_value / capital),
        "retorno": retorno,
        "retorno_ibov": ibov,
        "alfa_vs_ibov": retorno - ibov,
        "bateu_ibov": retorno > ibov,
        "max_acoes_setor": max_sector_count,
        "max_peso_setor": max_sector_weight,
        "soma_pesos": float(portfolio["peso_final"].sum()),
    }
    return portfolio, meta


def summarize(grouped: pd.core.groupby.generic.DataFrameGroupBy, keys: list[str]) -> pd.DataFrame:
    rows = []
    for key, data in grouped:
        if not isinstance(key, tuple):
            key = (key,)
        record = dict(zip(keys, key))
        model = compound(data["retorno"])
        ibov = compound(data["retorno_ibov"])
        record.update({
            "meses": len(data),
            "retorno_modelo": model,
            "retorno_ibov": ibov,
            "alfa_vs_ibov": model - ibov,
            "taxa_acerto": pd.to_numeric(data["bateu_ibov"], errors="coerce").mean(),
            "drawdown": max_drawdown(data["retorno"]),
            "qtd_acoes_media": pd.to_numeric(data["qtd_acoes"], errors="coerce").mean(),
            "qtd_acoes_min": pd.to_numeric(data["qtd_acoes"], errors="coerce").min(),
            "qtd_acoes_max": pd.to_numeric(data["qtd_acoes"], errors="coerce").max(),
            "peso_acoes_medio": pd.to_numeric(data["peso_acoes"], errors="coerce").mean(),
            "peso_cdi_medio": pd.to_numeric(data["peso_cdi"], errors="coerce").mean(),
        })
        rows.append(record)
    return pd.DataFrame(rows)


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
        for capital in CAPITALS:
            for scenario in ["ATUAL", "TOP15"]:
                portfolio, meta = build_portfolio(month, month_rows, perf_row, scenario, capital)
                all_portfolios.append(portfolio)
                monthly_rows.append(meta)

    portfolio_df = pd.concat(all_portfolios, ignore_index=True)
    monthly = pd.DataFrame(monthly_rows)
    monthly["ano"] = monthly["mes"].astype(str).str[:4]
    monthly["capital_label"] = monthly["capital"].map(money)
    summary = summarize(monthly.groupby(["cenario", "capital"]), ["cenario", "capital"])
    by_year = summarize(monthly.groupby(["cenario", "capital", "ano"]), ["cenario", "capital", "ano"])
    by_regime = summarize(monthly.groupby(["cenario", "capital", "tipo_regime_expost"]), ["cenario", "capital", "tipo_regime_expost"])

    comp = monthly.pivot_table(index=["mes", "capital"], columns="cenario", values=["retorno", "alfa_vs_ibov", "qtd_acoes", "peso_cdi"], aggfunc="first").reset_index()
    comp.columns = ["_".join([str(x) for x in col if str(x) != ""]) for col in comp.columns.to_flat_index()]
    if {"retorno_TOP15", "retorno_ATUAL"}.issubset(comp.columns):
        comp["delta_top15_vs_atual"] = comp["retorno_TOP15"] - comp["retorno_ATUAL"]
        comp["top15_ajudou"] = comp["delta_top15_vs_atual"] > 0

    validation = monthly.copy()
    validation["pesos_ok"] = (pd.to_numeric(validation["soma_pesos"], errors="coerce") - 1.0).abs() <= 1e-8
    validation["qtd_ok_top15"] = np.where(validation["cenario"].eq("TOP15"), validation["qtd_acoes"] <= TOP_N, True)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo Capital", index=False)
        by_year.to_excel(writer, sheet_name="Resumo Ano", index=False)
        by_regime.to_excel(writer, sheet_name="Resumo Regime", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        comp.to_excel(writer, sheet_name="Comparativo Mes", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)
        portfolio_df.to_excel(writer, sheet_name="Carteiras", index=False)

    lines = ["Teste 49 - Top 15 Operacional Final vs Atual por Regime e Capital", f"Entrada: {INPUT_FILE.name}", ""]
    for _, row in summary.sort_values(["capital", "cenario"]).iterrows():
        lines.append(
            f"{row['cenario']} | {money(row['capital'])}: retorno={pct(row['retorno_modelo'])}; "
            f"IBOV={pct(row['retorno_ibov'])}; alfa={pct(row['alfa_vs_ibov'])}; "
            f"acerto={pct(row['taxa_acerto'])}; drawdown={pct(row['drawdown'])}; "
            f"qtd_media={row['qtd_acoes_media']:.1f}; CDI_medio={pct(row['peso_cdi_medio'])}"
        )
    if not comp.empty and "delta_top15_vs_atual" in comp.columns:
        lines.append("")
        for capital, data in comp.groupby("capital"):
            helped = int(data["top15_ajudou"].fillna(False).sum())
            hurt = int((data["delta_top15_vs_atual"] < 0).sum())
            avg_delta = float(pd.to_numeric(data["delta_top15_vs_atual"], errors="coerce").mean())
            lines.append(f"Top15 vs Atual | {money(capital)}: ajudou={helped}; prejudicou={hurt}; delta_medio_mensal={pct(avg_delta)}")
    lines.extend(["", f"Arquivo gerado: {OUTPUT_FILE}", f"Log gerado: {LOG_FILE}"])
    LOG_FILE.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
