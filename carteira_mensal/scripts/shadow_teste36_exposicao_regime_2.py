from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_FILE = EXCEL_DIR / "shadow_teste35_modelo_consolidado_operacional_2022_2026.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste36_exposicao_regime_2.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste36_exposicao_regime_2.log"


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
    if vals.empty:
        return np.nan
    equity = (1.0 + vals).cumprod()
    return float((equity / equity.cummax() - 1.0).min())


def normalize_regime(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("queda_forte"):
        return "queda_forte"
    if text.startswith("queda"):
        return "queda_leve"
    return "alta"


def stock_month_stats(portfolio: pd.DataFrame) -> pd.DataFrame:
    stocks = portfolio[portfolio["tipo_alocacao"].astype(str).eq("acao")].copy()
    stocks["peso_bruto_acao"] = pd.to_numeric(stocks["peso_dentro_da_parte_acoes"], errors="coerce").fillna(0.0)
    stocks["retorno_periodo_num"] = pd.to_numeric(stocks["retorno_periodo"], errors="coerce").fillna(0.0)
    stocks["contribuicao_bruta_acao"] = stocks["peso_bruto_acao"] * stocks["retorno_periodo_num"]
    return stocks.groupby("mes", as_index=False).agg(
        soma_pesos_acoes_bruta_calc=("peso_bruto_acao", "sum"),
        retorno_bruto_acoes_calc=("contribuicao_bruta_acao", "sum"),
        n_ativos_acoes=("ticker", "count"),
        nota_media=("nota_final", "mean"),
        nota_mediana=("nota_final", "median"),
        beta_medio=("beta", "mean"),
        maior_peso_acao_bruto=("peso_bruto_acao", "max"),
    )


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(INPUT_FILE)
    monthly = pd.read_excel(INPUT_FILE, sheet_name="Mes a Mes")
    portfolio = pd.read_excel(INPUT_FILE, sheet_name="Carteira Operacional")
    monthly["mes"] = monthly["mes"].astype(str)
    monthly["regime_previsto_norm"] = monthly["bucket_regime_previsto"].map(normalize_regime)
    stats = stock_month_stats(portfolio)
    monthly = monthly.merge(stats, on="mes", how="left")
    monthly["soma_pesos_acoes_bruta_calc"] = pd.to_numeric(monthly["soma_pesos_acoes_bruta_calc"], errors="coerce").fillna(0.0)
    monthly["retorno_bruto_acoes_calc"] = pd.to_numeric(monthly["retorno_bruto_acoes_calc"], errors="coerce").fillna(0.0)
    monthly["n_ativos_acoes"] = pd.to_numeric(monthly["n_ativos_acoes"], errors="coerce").fillna(0).astype(int)
    return monthly, portfolio


def exposure_current(row: pd.Series) -> float:
    return float(row.get("exposicao_modelo", 1.0) or 0.0)


def exposure_max_cap_atual(row: pd.Series) -> float:
    return 1.0


def exposure_36a(row: pd.Series) -> float:
    regime = row["regime_previsto_norm"]
    if regime == "queda_leve":
        return 0.80
    if regime == "queda_forte":
        return 0.30
    return 1.0


def exposure_36b(row: pd.Series) -> float:
    regime = row["regime_previsto_norm"]
    if regime == "queda_leve":
        return 0.60
    if regime == "queda_forte":
        return 0.50
    return 1.0


def exposure_36ab(row: pd.Series) -> float:
    regime = row["regime_previsto_norm"]
    if regime == "queda_leve":
        return 0.80
    if regime == "queda_forte":
        return 0.50
    return 1.0


def exposure_36c_quality(row: pd.Series) -> float:
    regime = row["regime_previsto_norm"]
    if regime == "alta":
        return 1.0
    nota = float(row.get("nota_media", np.nan)) if pd.notna(row.get("nota_media", np.nan)) else 0.0
    n = int(row.get("n_ativos_acoes", 0) or 0)
    beta = float(row.get("beta_carteira", row.get("beta_medio", np.nan))) if pd.notna(row.get("beta_carteira", np.nan)) else float(row.get("beta_medio", np.nan) or 0.0)
    qualidade_forte = nota >= 55 and n >= 8 and beta <= 1.15
    qualidade_media = nota >= 48 and n >= 6 and beta <= 1.30
    if regime == "queda_leve":
        return 1.0 if qualidade_forte else (0.80 if qualidade_media else 0.60)
    if regime == "queda_forte":
        return 0.70 if qualidade_forte else (0.50 if qualidade_media else 0.30)
    return 1.0


def exposure_36d_confirmation(row: pd.Series) -> float:
    regime = row["regime_previsto_norm"]
    if regime == "alta":
        return 1.0
    nota = float(row.get("nota_media", np.nan)) if pd.notna(row.get("nota_media", np.nan)) else 0.0
    n = int(row.get("n_ativos_acoes", 0) or 0)
    beta = float(row.get("beta_carteira", np.nan)) if pd.notna(row.get("beta_carteira", np.nan)) else np.nan
    queda_confirmada = bool(row.get("queda_confirmada_28d", False))
    risco_carteira_alto = (pd.notna(beta) and beta > 1.05) or nota < 50 or n < 8
    if not queda_confirmada:
        return 1.0
    if regime == "queda_leve":
        return 0.70 if risco_carteira_alto else 0.90
    if regime == "queda_forte":
        return 0.50 if risco_carteira_alto else 0.75
    return 1.0


SCENARIOS: list[tuple[str, str, Callable[[pd.Series], float]]] = [
    ("ATUAL_T35", "Regra atual do Teste 35", exposure_current),
    ("DIAG_MAX_CAP_ATUAL", "Diagnostico: usa 100% do cap bruto de acoes ja permitido", exposure_max_cap_atual),
    ("T36A_QUEDA_LEVE_80", "Queda leve passa de 60% para 80%; queda forte fica atual", exposure_36a),
    ("T36B_QUEDA_FORTE_50", "Queda forte passa de 30% para 50%; queda leve fica atual", exposure_36b),
    ("T36AB_80_50", "Queda leve 80% e queda forte 50%", exposure_36ab),
    ("T36C_QUALIDADE", "Corta menos quando nota/n_ativos/beta da carteira parecem saudaveis", exposure_36c_quality),
    ("T36D_CONFIRMACAO", "Corte depende de queda confirmada + risco/qualidade da carteira", exposure_36d_confirmation),
]


def build_monthly_scenarios(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for scenario, description, func in SCENARIOS:
        for _, row in monthly.iterrows():
            exposure_multiplier = float(np.clip(func(row), 0.0, 1.0))
            stock_sum = float(row["soma_pesos_acoes_bruta_calc"])
            stock_weight_effective = float(np.clip(stock_sum * exposure_multiplier, 0.0, 1.0))
            cdi_weight = 1.0 - stock_weight_effective
            stock_return = float(row["retorno_bruto_acoes_calc"])
            cdi = float(row["retorno_cdi_liquido_periodo"])
            ibov = float(row["retorno_expost_ibov"])
            total = exposure_multiplier * stock_return + cdi_weight * cdi
            rows.append(
                {
                    "cenario_teste36": scenario,
                    "descricao_cenario": description,
                    "mes": str(row["mes"]),
                    "bucket_regime_previsto": row.get("bucket_regime_previsto", ""),
                    "regime_previsto_norm": row.get("regime_previsto_norm", ""),
                    "queda_confirmada_28d": row.get("queda_confirmada_28d", False),
                    "tipo_regime_expost": row.get("tipo_regime_expost", ""),
                    "multiplicador_exposicao_regime": exposure_multiplier,
                    "soma_pesos_acoes_bruta": stock_sum,
                    "peso_acoes_efetivo": stock_weight_effective,
                    "peso_cdi": cdi_weight,
                    "multiplicador_atual_t35": float(row.get("exposicao_modelo", np.nan)),
                    "delta_multiplicador_vs_atual": exposure_multiplier - float(row.get("exposicao_modelo", 0.0)),
                    "retorno_bruto_acoes": stock_return,
                    "retorno_cdi_liquido_periodo": cdi,
                    "retorno_total": total,
                    "retorno_total_atual_t35": row.get("retorno_total_operacional", np.nan),
                    "retorno_expost_ibov": ibov,
                    "alfa_vs_ibov": total - ibov,
                    "bateu_ibov": total > ibov,
                    "n_ativos_acoes": int(row.get("n_ativos_acoes", 0) or 0),
                    "nota_media": row.get("nota_media", np.nan),
                    "nota_mediana": row.get("nota_mediana", np.nan),
                    "beta_carteira": row.get("beta_carteira", np.nan),
                    "data_inicio_performance": row.get("data_inicio_performance", ""),
                    "data_avaliacao": row.get("data_avaliacao", ""),
                }
            )
    return pd.DataFrame(rows)


def summarize(group: pd.DataFrame) -> dict[str, Any]:
    ret = pd.to_numeric(group["retorno_total"], errors="coerce")
    ibov = pd.to_numeric(group["retorno_expost_ibov"], errors="coerce")
    alpha = pd.to_numeric(group["alfa_vs_ibov"], errors="coerce")
    return {
        "cenario_teste36": str(group["cenario_teste36"].iloc[0]),
        "descricao_cenario": str(group["descricao_cenario"].iloc[0]),
        "meses": int(len(group)),
        "retorno_modelo": compound(ret),
        "retorno_ibov": compound(ibov),
        "alfa_vs_ibov": compound(ret) - compound(ibov),
        "meses_bateu_ibov": int((alpha > 0).sum()),
        "taxa_acerto": float((alpha > 0).mean()) if len(alpha) else np.nan,
        "drawdown": max_drawdown(ret),
        "peso_acoes_medio": float(pd.to_numeric(group["peso_acoes_efetivo"], errors="coerce").mean()),
        "peso_medio_cdi": float(pd.to_numeric(group["peso_cdi"], errors="coerce").mean()),
        "multiplicador_exposicao_medio": float(pd.to_numeric(group["multiplicador_exposicao_regime"], errors="coerce").mean()),
    }


def build_portfolio_scenarios(monthly_scenarios: pd.DataFrame, portfolio: pd.DataFrame) -> pd.DataFrame:
    stocks = portfolio[portfolio["tipo_alocacao"].astype(str).eq("acao")].copy()
    rows: list[dict[str, Any]] = []
    idx = monthly_scenarios.set_index(["cenario_teste36", "mes"])
    for scenario in monthly_scenarios["cenario_teste36"].drop_duplicates():
        for _, stock in stocks.iterrows():
            mes = str(stock["mes"])
            if (scenario, mes) not in idx.index:
                continue
            m = idx.loc[(scenario, mes)]
            w_stock = float(stock.get("peso_dentro_da_parte_acoes", 0.0) or 0.0)
            exposure = float(m["multiplicador_exposicao_regime"])
            ret = float(stock.get("retorno_periodo", np.nan)) if pd.notna(stock.get("retorno_periodo", np.nan)) else np.nan
            rows.append(
                {
                    "cenario_teste36": scenario,
                    "mes": mes,
                    "ticker": stock.get("ticker", ""),
                    "nome": stock.get("nome", ""),
                    "setor": stock.get("setor", ""),
                    "tipo_alocacao": "acao",
                    "peso_bruto_acao": w_stock,
                    "multiplicador_exposicao_regime": exposure,
                    "peso_efetivo_carteira_total": w_stock * exposure,
                    "retorno_periodo": ret,
                    "contribuicao_retorno_total": w_stock * exposure * ret if pd.notna(ret) else np.nan,
                    "nota_final": stock.get("nota_final", np.nan),
                    "beta": stock.get("beta", np.nan),
                    "regime_previsto_norm": m.get("regime_previsto_norm", ""),
                    "tipo_regime_expost": m.get("tipo_regime_expost", ""),
                }
            )
        for _, m in monthly_scenarios[monthly_scenarios["cenario_teste36"].eq(scenario)].iterrows():
            rows.append(
                {
                    "cenario_teste36": scenario,
                    "mes": str(m["mes"]),
                    "ticker": "CDI",
                    "nome": "CDI liquido de IR no residual de exposicao",
                    "setor": "Caixa/CDI",
                    "tipo_alocacao": "cdi_residual",
                    "peso_bruto_acao": np.nan,
                    "multiplicador_exposicao_regime": float(m["multiplicador_exposicao_regime"]),
                    "peso_efetivo_carteira_total": float(m["peso_cdi"]),
                    "retorno_periodo": float(m["retorno_cdi_liquido_periodo"]),
                    "contribuicao_retorno_total": float(m["peso_cdi"]) * float(m["retorno_cdi_liquido_periodo"]),
                    "nota_final": np.nan,
                    "beta": 0.0,
                    "regime_previsto_norm": m.get("regime_previsto_norm", ""),
                    "tipo_regime_expost": m.get("tipo_regime_expost", ""),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    monthly, portfolio = load_inputs()
    monthly_scenarios = build_monthly_scenarios(monthly)
    summary = pd.DataFrame([summarize(g) for _, g in monthly_scenarios.groupby("cenario_teste36", sort=False)])
    current = summary[summary["cenario_teste36"].eq("ATUAL_T35")].iloc[0]
    summary["delta_retorno_vs_atual"] = summary["retorno_modelo"] - float(current["retorno_modelo"])
    summary["delta_alfa_vs_atual"] = summary["alfa_vs_ibov"] - float(current["alfa_vs_ibov"])
    summary["delta_taxa_acerto_vs_atual"] = summary["taxa_acerto"] - float(current["taxa_acerto"])

    by_regime = pd.DataFrame(
        [summarize(g) | {"tipo_regime_expost": keys[1]} for keys, g in monthly_scenarios.groupby(["cenario_teste36", "tipo_regime_expost"], sort=False)]
    )
    by_predicted = pd.DataFrame(
        [summarize(g) | {"regime_previsto_norm": keys[1]} for keys, g in monthly_scenarios.groupby(["cenario_teste36", "regime_previsto_norm"], sort=False)]
    )
    monthly_2022 = monthly_scenarios[monthly_scenarios["mes"].astype(str).str.startswith("2022")].copy()
    summary_2022 = pd.DataFrame([summarize(g) for _, g in monthly_2022.groupby("cenario_teste36", sort=False)])
    current22 = summary_2022[summary_2022["cenario_teste36"].eq("ATUAL_T35")].iloc[0]
    summary_2022["delta_retorno_vs_atual_2022"] = summary_2022["retorno_modelo"] - float(current22["retorno_modelo"])
    summary_2022["delta_alfa_vs_atual_2022"] = summary_2022["alfa_vs_ibov"] - float(current22["alfa_vs_ibov"])

    portfolio_scenarios = build_portfolio_scenarios(monthly_scenarios, portfolio)
    validation = portfolio_scenarios.groupby(["cenario_teste36", "mes"], as_index=False).agg(
        soma_pesos=("peso_efetivo_carteira_total", "sum"),
        contribuicao_total=("contribuicao_retorno_total", "sum"),
        maior_peso=("peso_efetivo_carteira_total", "max"),
        n_linhas=("ticker", "count"),
    )
    validation = validation.merge(monthly_scenarios[["cenario_teste36", "mes", "retorno_total", "retorno_expost_ibov", "alfa_vs_ibov"]], on=["cenario_teste36", "mes"], how="left")
    validation["diferenca_contribuicao_vs_retorno"] = validation["contribuicao_total"] - validation["retorno_total"]
    validation["pesos_fecham_100"] = validation["soma_pesos"].sub(1.0).abs().lt(1e-7)
    validation["retorno_bate_contribuicao"] = validation["diferenca_contribuicao_vs_retorno"].abs().lt(1e-7)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo 2022-2026", index=False)
        summary_2022.to_excel(writer, sheet_name="Resumo 2022", index=False)
        monthly_scenarios.to_excel(writer, sheet_name="Mes a Mes", index=False)
        by_regime.to_excel(writer, sheet_name="Por Regime Real", index=False)
        by_predicted.to_excel(writer, sheet_name="Por Regime Previsto", index=False)
        portfolio_scenarios.to_excel(writer, sheet_name="Carteiras Por Cenario", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)

    log("Teste 36 - Exposicao por Regime 2.0")
    log("Escopo: muda apenas multiplicador de exposicao acoes/CDI sobre carteiras ja formadas; producao intacta.")
    log("Resumo 2022-2026:")
    for _, row in summary.iterrows():
        log(
            f"  {row['cenario_teste36']}: ret={pct(row['retorno_modelo'])}; IBOV={pct(row['retorno_ibov'])}; "
            f"alfa={pct(row['alfa_vs_ibov'])}; acerto={int(row['meses_bateu_ibov'])}/{int(row['meses'])} "
            f"({float(row['taxa_acerto']):.2%}); dd={pct(row['drawdown'])}; peso_acoes_medio={pct(row['peso_acoes_medio'])}; "
            f"delta_alfa_vs_atual={pct(row['delta_alfa_vs_atual'])}"
        )
    log("Resumo 2022 isolado:")
    for _, row in summary_2022.iterrows():
        log(
            f"  {row['cenario_teste36']}: ret={pct(row['retorno_modelo'])}; alfa={pct(row['alfa_vs_ibov'])}; "
            f"acerto={int(row['meses_bateu_ibov'])}/{int(row['meses'])}; delta_alfa_2022={pct(row['delta_alfa_vs_atual_2022'])}"
        )
    invalid_weights = int((~validation["pesos_fecham_100"]).sum())
    invalid_returns = int((~validation["retorno_bate_contribuicao"]).sum())
    log(f"Validacao: pesos != 100%: {invalid_weights}; retorno != soma contribuicoes: {invalid_returns}")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
