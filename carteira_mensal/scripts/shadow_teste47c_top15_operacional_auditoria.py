from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_FILE = EXCEL_DIR / "shadow_teste46_carteira_executavel.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste47c_top15_operacional_auditoria.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste47c_top15_operacional_auditoria.log"
CAPITAL_BASE = 10_000.0
TOP_N = 15


def pct(value: Any) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


def compound(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return np.nan
    return float((1.0 + values).prod() - 1.0)


def max_drawdown(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return np.nan
    curve = (1.0 + values).cumprod()
    peak = curve.cummax()
    return float((curve / peak - 1.0).min())


def rank_stocks(stocks: pd.DataFrame) -> pd.DataFrame:
    ranked = stocks.copy()
    for col in ["nota_final", "peso_executavel_total", "contribuicao_executavel", "beta", "retorno_periodo"]:
        if col in ranked.columns:
            ranked[col] = pd.to_numeric(ranked[col], errors="coerce")
    ranked["_rank_nota"] = ranked.get("nota_final", pd.Series(index=ranked.index, dtype=float)).fillna(-999)
    ranked["_rank_peso"] = ranked.get("peso_executavel_total", pd.Series(index=ranked.index, dtype=float)).fillna(0)
    ranked["_rank_contrib"] = ranked.get("contribuicao_executavel", pd.Series(index=ranked.index, dtype=float)).fillna(-999)
    return ranked.sort_values(
        ["_rank_nota", "_rank_peso", "_rank_contrib", "ticker"],
        ascending=[False, False, False, True],
    )


def baseline_month(month: str, rows: pd.DataFrame, perf_row: pd.Series) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = rows.copy()
    stocks = out[out["ticker"].astype(str).str.upper().ne("CDI")].copy()
    out["cenario"] = "ATUAL_T46"
    out["rank_top15"] = np.nan
    out["acao_auditoria"] = "mantida_original"
    out["quantidade_top15"] = pd.to_numeric(out.get("quantidade"), errors="coerce")
    out["valor_executado_top15"] = pd.to_numeric(out.get("valor_executado"), errors="coerce")
    out["peso_final"] = pd.to_numeric(out.get("peso_executavel_total"), errors="coerce")
    out["contribuicao_final"] = pd.to_numeric(out.get("contribuicao_executavel"), errors="coerce")
    retorno = float(pd.to_numeric(perf_row.get("retorno_executavel"), errors="coerce"))
    ibov = float(pd.to_numeric(perf_row.get("retorno_ibov"), errors="coerce"))
    meta = {
        "mes": month,
        "cenario": "ATUAL_T46",
        "qtd_acoes": int(len(stocks)),
        "qtd_removidas": 0,
        "peso_removido_original": 0.0,
        "contribuicao_removida_original": 0.0,
        "peso_acoes": float(out.loc[out["ticker"].astype(str).str.upper().ne("CDI"), "peso_final"].sum()),
        "peso_cdi": float(out.loc[out["ticker"].astype(str).str.upper().eq("CDI"), "peso_final"].sum()),
        "retorno": retorno,
        "retorno_ibov": ibov,
        "alfa_vs_ibov": retorno - ibov,
        "bateu_ibov": retorno > ibov,
        "delta_retorno_vs_atual": 0.0,
        "remocao_ajudou": np.nan,
    }
    return out, meta


def top15_month(month: str, rows: pd.DataFrame, perf_row: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    stocks = rows[rows["ticker"].astype(str).str.upper().ne("CDI")].copy()
    cdi = rows[rows["ticker"].astype(str).str.upper().eq("CDI")].copy()
    original_stock_weight = float(pd.to_numeric(stocks["peso_executavel_total"], errors="coerce").sum())
    atual_return = float(pd.to_numeric(perf_row.get("retorno_executavel"), errors="coerce"))
    ibov = float(pd.to_numeric(perf_row.get("retorno_ibov"), errors="coerce"))
    cdi_return = float(pd.to_numeric(cdi["retorno_periodo"], errors="coerce").dropna().iloc[0]) if not cdi.empty else 0.0

    ranked = rank_stocks(stocks).reset_index(drop=True)
    ranked["rank_top15"] = np.arange(1, len(ranked) + 1)
    kept = ranked.head(TOP_N).copy()
    removed = ranked.iloc[TOP_N:].copy()

    weight_base = pd.to_numeric(kept["peso_executavel_total"], errors="coerce").fillna(0.0)
    if kept.empty or weight_base.sum() <= 0 or original_stock_weight <= 0:
        kept = kept.iloc[0:0].copy()
        stock_value = 0.0
    else:
        kept["peso_modelo_top15"] = weight_base / weight_base.sum() * original_stock_weight
        kept["valor_alvo_top15"] = kept["peso_modelo_top15"] * CAPITAL_BASE
        kept["preco_entrada"] = pd.to_numeric(kept["preco_entrada"], errors="coerce")
        kept["quantidade_top15"] = np.floor(kept["valor_alvo_top15"] / kept["preco_entrada"]).replace([np.inf, -np.inf], np.nan).fillna(0).clip(lower=0).astype(int)
        kept["valor_executado_top15"] = kept["quantidade_top15"] * kept["preco_entrada"]
        kept = kept[kept["quantidade_top15"] > 0].copy()
        stock_value = float(kept["valor_executado_top15"].sum())

    cdi_value = max(CAPITAL_BASE - stock_value, 0.0)
    if not kept.empty:
        kept["cenario"] = "TOP15_OPERACIONAL"
        kept["acao_auditoria"] = "mantida_top15"
        kept["peso_final"] = kept["valor_executado_top15"] / CAPITAL_BASE
        kept["retorno_periodo"] = pd.to_numeric(kept["retorno_periodo"], errors="coerce")
        kept["contribuicao_final"] = kept["peso_final"] * kept["retorno_periodo"]

    cdi_row = pd.DataFrame([{
        "mes": month,
        "cenario": "TOP15_OPERACIONAL",
        "rank_top15": np.nan,
        "acao_auditoria": "cdi_residual",
        "ticker": "CDI",
        "nome": "Reserva/CDI liquido",
        "setor": "Protecao",
        "tipo_alocacao": "cdi_residual",
        "preco_entrada": np.nan,
        "quantidade_top15": np.nan,
        "valor_executado_top15": cdi_value,
        "peso_final": cdi_value / CAPITAL_BASE,
        "retorno_periodo": cdi_return,
        "contribuicao_final": (cdi_value / CAPITAL_BASE) * cdi_return,
        "nota_final": np.nan,
        "beta": np.nan,
    }])
    cols = ["mes", "cenario", "rank_top15", "acao_auditoria", "ticker", "nome", "setor", "tipo_alocacao", "preco_entrada", "quantidade_top15", "valor_executado_top15", "peso_final", "retorno_periodo", "contribuicao_final", "nota_final", "beta", "cv"]
    for col in cols:
        if col not in kept.columns:
            kept[col] = np.nan
        if col not in cdi_row.columns:
            cdi_row[col] = np.nan
    portfolio = pd.concat([kept[cols], cdi_row[cols]], ignore_index=True)
    retorno = float(pd.to_numeric(portfolio["contribuicao_final"], errors="coerce").fillna(0).sum())

    audit_removed = removed.copy()
    if not audit_removed.empty:
        audit_removed["mes"] = month
        audit_removed["cenario"] = "TOP15_OPERACIONAL"
        audit_removed["acao_auditoria"] = "removida_por_top15"
        audit_removed["peso_removido_original"] = pd.to_numeric(audit_removed.get("peso_executavel_total"), errors="coerce")
        audit_removed["retorno_periodo"] = pd.to_numeric(audit_removed.get("retorno_periodo"), errors="coerce")
        audit_removed["contribuicao_removida_original"] = pd.to_numeric(audit_removed.get("contribuicao_executavel"), errors="coerce")
        audit_removed["bateu_ibov_ativo"] = audit_removed["retorno_periodo"] > ibov
        audit_removed["retorno_relativo_vs_ibov"] = audit_removed["retorno_periodo"] - ibov
    else:
        audit_removed = pd.DataFrame(columns=["mes", "cenario", "acao_auditoria", "ticker"])

    stock_port = portfolio[portfolio["ticker"].astype(str).str.upper().ne("CDI")]
    meta = {
        "mes": month,
        "cenario": "TOP15_OPERACIONAL",
        "qtd_acoes": int(len(stock_port)),
        "qtd_removidas": int(len(removed)),
        "peso_removido_original": float(pd.to_numeric(removed.get("peso_executavel_total", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not removed.empty else 0.0,
        "contribuicao_removida_original": float(pd.to_numeric(removed.get("contribuicao_executavel", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not removed.empty else 0.0,
        "retorno_medio_removidas": float(pd.to_numeric(removed.get("retorno_periodo", pd.Series(dtype=float)), errors="coerce").mean()) if not removed.empty else np.nan,
        "qtd_removidas_bateram_ibov": int((pd.to_numeric(removed.get("retorno_periodo", pd.Series(dtype=float)), errors="coerce") > ibov).sum()) if not removed.empty else 0,
        "peso_acoes": float(stock_port["peso_final"].sum()),
        "peso_cdi": float(portfolio.loc[portfolio["ticker"].astype(str).str.upper().eq("CDI"), "peso_final"].sum()),
        "retorno": retorno,
        "retorno_ibov": ibov,
        "alfa_vs_ibov": retorno - ibov,
        "bateu_ibov": retorno > ibov,
        "delta_retorno_vs_atual": retorno - atual_return,
        "remocao_ajudou": retorno > atual_return,
    }
    return portfolio, audit_removed, meta


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
            "peso_acoes_medio": pd.to_numeric(data["peso_acoes"], errors="coerce").mean(),
            "meses_top15_ajudou": int(data.get("remocao_ajudou", pd.Series(False, index=data.index)).fillna(False).astype(bool).sum()),
            "meses_top15_prejudicou": int((data.get("remocao_ajudou", pd.Series(np.nan, index=data.index)) == False).sum()),
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
            "delta_soma_mensal_vs_atual": pd.to_numeric(data["delta_retorno_vs_atual"], errors="coerce").sum(),
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
    all_removed = []
    monthly_rows = []
    for _, perf_row in perf.sort_values("mes").iterrows():
        month = str(perf_row["mes"])
        rows = portfolios[portfolios["mes"].eq(month)].copy()
        base_port, base_meta = baseline_month(month, rows, perf_row)
        top_port, removed, top_meta = top15_month(month, rows, perf_row)
        all_portfolios.extend([base_port, top_port])
        if not removed.empty:
            all_removed.append(removed)
        monthly_rows.extend([base_meta, top_meta])

    portfolios_out = pd.concat(all_portfolios, ignore_index=True)
    removed_out = pd.concat(all_removed, ignore_index=True) if all_removed else pd.DataFrame()
    monthly = pd.DataFrame(monthly_rows)
    summary = summarize(monthly)
    yearly = summarize_year(monthly)
    top_months = monthly[monthly["cenario"].eq("TOP15_OPERACIONAL")].copy()
    top_months["leitura_delta"] = np.where(top_months["delta_retorno_vs_atual"] > 0, "ajudou", np.where(top_months["delta_retorno_vs_atual"] < 0, "prejudicou", "neutro"))

    if not removed_out.empty:
        removed_summary = removed_out.groupby("ticker", as_index=False).agg(
            vezes_removida=("mes", "count"),
            peso_medio_removido=("peso_removido_original", "mean"),
            retorno_medio_removido=("retorno_periodo", "mean"),
            contribuicao_total_removida=("contribuicao_removida_original", "sum"),
            vezes_bateu_ibov=("bateu_ibov_ativo", "sum"),
        ).sort_values(["vezes_removida", "contribuicao_total_removida"], ascending=[False, True])
    else:
        removed_summary = pd.DataFrame()

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo", index=False)
        yearly.to_excel(writer, sheet_name="Resumo Ano", index=False)
        top_months.to_excel(writer, sheet_name="Auditoria Mes a Mes", index=False)
        removed_out.to_excel(writer, sheet_name="Ativos Removidos", index=False)
        removed_summary.to_excel(writer, sheet_name="Resumo Removidos", index=False)
        portfolios_out.to_excel(writer, sheet_name="Carteiras", index=False)

    lines = ["Teste 47C - Top 15 Operacional com Auditoria Mes a Mes", f"Entrada: {INPUT_FILE.name}", ""]
    for _, row in summary.sort_values("retorno_modelo", ascending=False).iterrows():
        lines.append(
            f"{row['cenario']}: retorno={pct(row['retorno_modelo'])}; IBOV={pct(row['retorno_ibov'])}; "
            f"alfa={pct(row['alfa_vs_ibov'])}; acerto={pct(row['taxa_acerto'])}; drawdown={pct(row['drawdown'])}; "
            f"qtd_media={row['qtd_acoes_media']:.1f}; qtd_min={row['qtd_acoes_min']:.0f}; qtd_max={row['qtd_acoes_max']:.0f}"
        )
    helped = int((top_months["delta_retorno_vs_atual"] > 0).sum())
    hurt = int((top_months["delta_retorno_vs_atual"] < 0).sum())
    neutral = int((top_months["delta_retorno_vs_atual"].abs() < 1e-12).sum())
    lines.extend(["", f"Meses em que Top15 ajudou: {helped}; prejudicou: {hurt}; neutro: {neutral}"])
    worst = top_months.sort_values("delta_retorno_vs_atual").head(5)
    best = top_months.sort_values("delta_retorno_vs_atual", ascending=False).head(5)
    lines.append("Piores deltas Top15 vs atual: " + "; ".join(f"{r['mes']} {pct(r['delta_retorno_vs_atual'])}" for _, r in worst.iterrows()))
    lines.append("Melhores deltas Top15 vs atual: " + "; ".join(f"{r['mes']} {pct(r['delta_retorno_vs_atual'])}" for _, r in best.iterrows()))
    if not removed_summary.empty:
        lines.append("Ativos mais removidos: " + "; ".join(f"{r['ticker']} ({int(r['vezes_removida'])}x)" for _, r in removed_summary.head(8).iterrows()))
    lines.extend(["", f"Arquivo gerado: {OUTPUT_FILE}", f"Log gerado: {LOG_FILE}"])
    LOG_FILE.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
