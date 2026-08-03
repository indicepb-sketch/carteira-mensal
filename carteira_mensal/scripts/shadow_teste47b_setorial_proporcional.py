from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_FILE = EXCEL_DIR / "shadow_teste46_carteira_executavel.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste47b_setorial_proporcional.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste47b_setorial_proporcional.log"
CAPITAL_BASE = 10_000.0

SCENARIOS = [
    {"cenario": "ATUAL_T46", "max_acoes": None, "max_setor": 2, "descricao": "controle fiel do T46 executavel"},
    {"cenario": "TOP10_SETOR2", "max_acoes": 10, "max_setor": 2, "descricao": "top 10 com limite rigido 2/setor"},
    {"cenario": "TOP10_SETOR3", "max_acoes": 10, "max_setor": 3, "descricao": "top 10 com limite proporcional ate 3/setor"},
    {"cenario": "TOP12_SETOR2", "max_acoes": 12, "max_setor": 2, "descricao": "top 12 com limite rigido 2/setor"},
    {"cenario": "TOP12_SETOR3", "max_acoes": 12, "max_setor": 3, "descricao": "top 12 com limite proporcional ate 3/setor"},
    {"cenario": "TOP15_SETOR2", "max_acoes": 15, "max_setor": 2, "descricao": "top 15 com limite rigido 2/setor"},
    {"cenario": "TOP15_SETOR3", "max_acoes": 15, "max_setor": 3, "descricao": "top 15 com limite proporcional ate 3/setor"},
]


def pct(value: Any) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


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
    out = stocks.copy()
    for col in ["nota_final", "peso_executavel_total", "contribuicao_executavel", "beta"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["_rank_nota"] = out.get("nota_final", pd.Series(index=out.index, dtype=float)).fillna(-999)
    out["_rank_peso"] = out.get("peso_executavel_total", pd.Series(index=out.index, dtype=float)).fillna(0)
    out["_rank_contrib"] = out.get("contribuicao_executavel", pd.Series(index=out.index, dtype=float)).fillna(-999)
    return out.sort_values(["_rank_nota", "_rank_peso", "_rank_contrib", "ticker"], ascending=[False, False, False, True])


def select_with_sector_limit(stocks: pd.DataFrame, max_acoes: int | None, max_setor: int) -> pd.DataFrame:
    ranked = rank_stocks(stocks)
    selected = []
    sector_counts: dict[str, int] = {}
    for _, row in ranked.iterrows():
        sector = str(row.get("setor", "Outros") or "Outros")
        if sector_counts.get(sector, 0) >= max_setor:
            continue
        selected.append(row)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if max_acoes is not None and len(selected) >= max_acoes:
            break
    if not selected:
        return ranked.iloc[0:0].copy()
    return pd.DataFrame(selected).reset_index(drop=True)


def baseline_month(month: str, rows: pd.DataFrame, perf_row: pd.Series, scenario: str, max_setor: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = rows.copy()
    stocks = out[out["ticker"].astype(str).str.upper().ne("CDI")].copy()
    out["cenario"] = scenario
    out["max_acoes"] = len(stocks)
    out["max_setor"] = max_setor
    out["quantidade_simulada"] = pd.to_numeric(out.get("quantidade"), errors="coerce")
    out["valor_executado_simulado"] = pd.to_numeric(out.get("valor_executado"), errors="coerce")
    out["peso_final"] = pd.to_numeric(out.get("peso_executavel_total"), errors="coerce")
    out["contribuicao_final"] = pd.to_numeric(out.get("contribuicao_executavel"), errors="coerce")
    retorno = float(pd.to_numeric(perf_row.get("retorno_executavel"), errors="coerce"))
    ibov = float(pd.to_numeric(perf_row.get("retorno_ibov"), errors="coerce"))
    meta = {
        "mes": month,
        "cenario": scenario,
        "max_acoes": len(stocks),
        "max_setor": max_setor,
        "qtd_acoes": len(stocks),
        "max_setor_observado": int(stocks.groupby("setor")["ticker"].count().max()) if not stocks.empty else 0,
        "peso_acoes": float(out.loc[out["ticker"].astype(str).str.upper().ne("CDI"), "peso_final"].sum()),
        "peso_cdi": float(out.loc[out["ticker"].astype(str).str.upper().eq("CDI"), "peso_final"].sum()),
        "retorno": retorno,
        "retorno_ibov": ibov,
        "alfa_vs_ibov": retorno - ibov,
        "bateu_ibov": retorno > ibov,
        "fonte_base": "T46 final executavel",
    }
    return out, meta


def simulate_month(month: str, rows: pd.DataFrame, perf_row: pd.Series, scenario: str, max_acoes: int, max_setor: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    stocks = rows[rows["ticker"].astype(str).str.upper().ne("CDI")].copy()
    cdi = rows[rows["ticker"].astype(str).str.upper().eq("CDI")].copy()
    cdi_return = float(pd.to_numeric(cdi["retorno_periodo"], errors="coerce").dropna().iloc[0]) if not cdi.empty else 0.0
    original_stock_weight = float(pd.to_numeric(stocks["peso_executavel_total"], errors="coerce").sum())

    selected = select_with_sector_limit(stocks, max_acoes=max_acoes, max_setor=max_setor)
    base_weights = pd.to_numeric(selected.get("peso_executavel_total"), errors="coerce").fillna(0.0)
    if selected.empty or base_weights.sum() <= 0 or original_stock_weight <= 0:
        selected = selected.iloc[0:0].copy()
        stock_value = 0.0
    else:
        selected["peso_modelo_setorial"] = base_weights / base_weights.sum() * original_stock_weight
        selected["valor_alvo_simulado"] = selected["peso_modelo_setorial"] * CAPITAL_BASE
        selected["preco_entrada"] = pd.to_numeric(selected["preco_entrada"], errors="coerce")
        selected["quantidade_simulada"] = np.floor(selected["valor_alvo_simulado"] / selected["preco_entrada"]).replace([np.inf, -np.inf], np.nan).fillna(0).clip(lower=0).astype(int)
        selected["valor_executado_simulado"] = selected["quantidade_simulada"] * selected["preco_entrada"]
        selected = selected[selected["quantidade_simulada"] > 0].copy()
        stock_value = float(selected["valor_executado_simulado"].sum())

    if not selected.empty:
        selected["cenario"] = scenario
        selected["max_acoes"] = max_acoes
        selected["max_setor"] = max_setor
        selected["peso_final"] = selected["valor_executado_simulado"] / CAPITAL_BASE
        selected["retorno_periodo"] = pd.to_numeric(selected["retorno_periodo"], errors="coerce")
        selected["contribuicao_final"] = selected["peso_final"] * selected["retorno_periodo"]
    else:
        for col in ["cenario", "max_acoes", "max_setor", "peso_final", "contribuicao_final"]:
            selected[col] = np.nan

    cdi_value = max(CAPITAL_BASE - stock_value, 0.0)
    cdi_row = pd.DataFrame([{
        "mes": month,
        "cenario": scenario,
        "max_acoes": max_acoes,
        "max_setor": max_setor,
        "ticker": "CDI",
        "nome": "Reserva/CDI liquido",
        "setor": "Protecao",
        "tipo_alocacao": "cdi_residual",
        "preco_entrada": np.nan,
        "quantidade_simulada": np.nan,
        "valor_executado_simulado": cdi_value,
        "peso_final": cdi_value / CAPITAL_BASE,
        "retorno_periodo": cdi_return,
        "contribuicao_final": (cdi_value / CAPITAL_BASE) * cdi_return,
        "nota_final": np.nan,
        "beta": np.nan,
    }])
    cols = ["mes", "cenario", "max_acoes", "max_setor", "ticker", "nome", "setor", "tipo_alocacao", "preco_entrada", "quantidade_simulada", "valor_executado_simulado", "peso_final", "retorno_periodo", "contribuicao_final", "nota_final", "beta"]
    for col in cols:
        if col not in selected.columns:
            selected[col] = np.nan
    portfolio = pd.concat([selected[cols], cdi_row[cols]], ignore_index=True)
    retorno = float(pd.to_numeric(portfolio["contribuicao_final"], errors="coerce").fillna(0).sum())
    ibov = float(pd.to_numeric(perf_row.get("retorno_ibov"), errors="coerce"))
    stock_port = portfolio[portfolio["ticker"].astype(str).str.upper().ne("CDI")]
    meta = {
        "mes": month,
        "cenario": scenario,
        "max_acoes": max_acoes,
        "max_setor": max_setor,
        "qtd_acoes": int(len(stock_port)),
        "max_setor_observado": int(stock_port.groupby("setor")["ticker"].count().max()) if not stock_port.empty else 0,
        "peso_acoes": float(stock_port["peso_final"].sum()),
        "peso_cdi": float(portfolio.loc[portfolio["ticker"].astype(str).str.upper().eq("CDI"), "peso_final"].sum()),
        "retorno": retorno,
        "retorno_ibov": ibov,
        "alfa_vs_ibov": retorno - ibov,
        "bateu_ibov": retorno > ibov,
        "fonte_base": "T46 final executavel; nao reabre ativos removidos antes da carteira final",
    }
    return portfolio, meta


def summarize(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario, data in monthly.groupby("cenario"):
        data = data.sort_values("mes")
        model = compound(data["retorno"])
        ibov = compound(data["retorno_ibov"])
        rows.append({
            "cenario": scenario,
            "meses": len(data),
            "retorno_modelo": model,
            "retorno_ibov": ibov,
            "alfa_vs_ibov": model - ibov,
            "taxa_acerto": pd.to_numeric(data["bateu_ibov"], errors="coerce").mean(),
            "drawdown": max_drawdown(data["retorno"]),
            "qtd_acoes_media": pd.to_numeric(data["qtd_acoes"], errors="coerce").mean(),
            "qtd_acoes_min": pd.to_numeric(data["qtd_acoes"], errors="coerce").min(),
            "qtd_acoes_max": pd.to_numeric(data["qtd_acoes"], errors="coerce").max(),
            "max_setor_observado_medio": pd.to_numeric(data["max_setor_observado"], errors="coerce").mean(),
            "peso_acoes_medio": pd.to_numeric(data["peso_acoes"], errors="coerce").mean(),
        })
    return pd.DataFrame(rows).sort_values("cenario")


def summarize_year(monthly: pd.DataFrame) -> pd.DataFrame:
    df = monthly.copy()
    df["ano"] = df["mes"].astype(str).str[:4]
    rows = []
    for (scenario, year), data in df.groupby(["cenario", "ano"]):
        model = compound(data["retorno"])
        ibov = compound(data["retorno_ibov"])
        rows.append({
            "cenario": scenario,
            "ano": year,
            "meses": len(data),
            "retorno_modelo": model,
            "retorno_ibov": ibov,
            "alfa_vs_ibov": model - ibov,
            "taxa_acerto": pd.to_numeric(data["bateu_ibov"], errors="coerce").mean(),
            "qtd_acoes_media": pd.to_numeric(data["qtd_acoes"], errors="coerce").mean(),
            "max_setor_observado_medio": pd.to_numeric(data["max_setor_observado"], errors="coerce").mean(),
        })
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
        for cfg in SCENARIOS:
            if cfg["cenario"] == "ATUAL_T46":
                portfolio, meta = baseline_month(month, month_rows, perf_row, cfg["cenario"], cfg["max_setor"])
            else:
                portfolio, meta = simulate_month(month, month_rows, perf_row, cfg["cenario"], int(cfg["max_acoes"]), int(cfg["max_setor"]))
            all_portfolios.append(portfolio)
            monthly_rows.append(meta | {"descricao": cfg["descricao"]})

    portfolios_out = pd.concat(all_portfolios, ignore_index=True)
    monthly = pd.DataFrame(monthly_rows)
    summary = summarize(monthly)
    yearly = summarize_year(monthly)

    # Diagnostico de vinculo: se setor3 == setor2 em todos os meses, a base final ja esta capada antes do teste.
    pairs = []
    for n in [10, 12, 15]:
        a = monthly[monthly["cenario"].eq(f"TOP{n}_SETOR2")].set_index("mes")
        b = monthly[monthly["cenario"].eq(f"TOP{n}_SETOR3")].set_index("mes")
        joined = a[["retorno", "qtd_acoes", "max_setor_observado"]].join(b[["retorno", "qtd_acoes", "max_setor_observado"]], lsuffix="_setor2", rsuffix="_setor3")
        pairs.append({
            "max_acoes": n,
            "meses_com_diferenca_retorno": int((joined["retorno_setor2"].round(12) != joined["retorno_setor3"].round(12)).sum()),
            "meses_com_3_acoes_mesmo_setor": int((joined["max_setor_observado_setor3"] >= 3).sum()),
            "observacao": "sem efeito na base T46" if int((joined["retorno_setor2"].round(12) != joined["retorno_setor3"].round(12)).sum()) == 0 else "houve efeito",
        })
    diagnostics = pd.DataFrame(pairs)

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo", index=False)
        yearly.to_excel(writer, sheet_name="Resumo Ano", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        diagnostics.to_excel(writer, sheet_name="Diagnostico Setorial", index=False)
        portfolios_out.to_excel(writer, sheet_name="Carteiras", index=False)

    lines = ["Teste 47B - Sensibilidade do numero de acoes com limite setorial proporcional", f"Entrada: {INPUT_FILE.name}", ""]
    for _, row in summary.sort_values("retorno_modelo", ascending=False).iterrows():
        lines.append(
            f"{row['cenario']}: retorno={pct(row['retorno_modelo'])}; IBOV={pct(row['retorno_ibov'])}; "
            f"alfa={pct(row['alfa_vs_ibov'])}; acerto={pct(row['taxa_acerto'])}; "
            f"drawdown={pct(row['drawdown'])}; qtd_media={row['qtd_acoes_media']:.1f}; max_setor_medio={row['max_setor_observado_medio']:.1f}"
        )
    lines.extend(["", "Diagnostico:"])
    for _, row in diagnostics.iterrows():
        lines.append(
            f"Top {int(row['max_acoes'])}: meses_com_diferenca={int(row['meses_com_diferenca_retorno'])}; "
            f"meses_com_3_setor={int(row['meses_com_3_acoes_mesmo_setor'])}; {row['observacao']}"
        )
    lines.extend(["", "Observacao metodologica: este 47B usa a carteira executavel T46 como base. Como essa base ja chega limitada a 2 ativos por setor, setor3 so teria efeito em um teste upstream no funil/otimizador.", f"Arquivo gerado: {OUTPUT_FILE}", f"Log gerado: {LOG_FILE}"])
    LOG_FILE.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
