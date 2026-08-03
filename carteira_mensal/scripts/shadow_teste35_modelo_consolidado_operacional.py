from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_34 = EXCEL_DIR / "shadow_teste34_cdi_residual.xlsx"
INPUT_EXPOST = EXCEL_DIR / "shadow_regime_16_risk_on_off.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste35_modelo_consolidado_operacional.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste35_modelo_consolidado_operacional.log"
BASE_SCENARIO = "base_28d"


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


def summarize(monthly: pd.DataFrame) -> pd.DataFrame:
    ret = pd.to_numeric(monthly["retorno_total_operacional"], errors="coerce")
    ret_zero = pd.to_numeric(monthly["retorno_modelo_zero"], errors="coerce")
    ibov = pd.to_numeric(monthly["retorno_expost_ibov"], errors="coerce")
    alpha = pd.to_numeric(monthly["alfa_operacional_vs_ibov"], errors="coerce")
    alpha_zero = pd.to_numeric(monthly["alfa_zero"], errors="coerce")
    rows = [
        {"metrica": "cenario", "valor": "base_28d_cap_efetivo_residual_cdi_liquido"},
        {"metrica": "meses", "valor": int(len(monthly))},
        {"metrica": "retorno_operacional_cdi", "valor": compound(ret)},
        {"metrica": "retorno_sem_cdi_residual_zero", "valor": compound(ret_zero)},
        {"metrica": "retorno_ibov", "valor": compound(ibov)},
        {"metrica": "alfa_operacional_vs_ibov", "valor": compound(ret) - compound(ibov)},
        {"metrica": "alfa_sem_cdi_vs_ibov", "valor": compound(ret_zero) - compound(ibov)},
        {"metrica": "ganho_do_cdi_no_residual", "valor": compound(ret) - compound(ret_zero)},
        {"metrica": "meses_bateu_ibov_operacional", "valor": int((alpha > 0).sum())},
        {"metrica": "meses_bateu_ibov_sem_cdi", "valor": int((alpha_zero > 0).sum())},
        {"metrica": "taxa_acerto_operacional", "valor": float((alpha > 0).mean())},
        {"metrica": "taxa_acerto_sem_cdi", "valor": float((alpha_zero > 0).mean())},
        {"metrica": "drawdown_operacional", "valor": max_drawdown(ret)},
        {"metrica": "drawdown_sem_cdi", "valor": max_drawdown(ret_zero)},
        {"metrica": "exposicao_media_acoes", "valor": float(monthly["peso_acoes_efetivo"].mean())},
        {"metrica": "peso_medio_cdi", "valor": float(monthly["peso_cdi"].mean())},
        {"metrica": "maior_peso_cdi", "valor": float(monthly["peso_cdi"].max())},
        {"metrica": "maior_peso_acao_efetivo", "valor": float(monthly["maior_peso_efetivo"].max())},
    ]
    return pd.DataFrame(rows)


def build_operational_portfolio(monthly: pd.DataFrame, portfolios: pd.DataFrame, expost: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    expost_idx = expost.set_index(["mes", "ticker"])
    monthly_idx = monthly.set_index("mes")
    base_port = portfolios[portfolios["cenario_teste33"].astype(str).eq(BASE_SCENARIO)].copy()
    for _, row in base_port.iterrows():
        mes = str(row["mes"])
        if mes not in monthly_idx.index:
            continue
        m = monthly_idx.loc[mes]
        ticker = str(row["ticker"])
        stock_weight = float(row.get("peso_recomendado", 0.0) or 0.0)
        effective_weight = stock_weight * float(m["exposicao_modelo"])
        ret = np.nan
        if (mes, ticker) in expost_idx.index:
            ret = pd.to_numeric(expost_idx.loc[(mes, ticker), "retorno_realizado_periodo"], errors="coerce")
        rows.append(
            {
                "mes": mes,
                "ticker": ticker,
                "nome": row.get("nome", ""),
                "setor": row.get("setor", ""),
                "tipo_alocacao": "acao",
                "peso_dentro_da_parte_acoes": stock_weight,
                "exposicao_modelo": float(m["exposicao_modelo"]),
                "peso_efetivo_carteira_total": effective_weight,
                "retorno_periodo": ret,
                "contribuicao_retorno_total": effective_weight * float(ret) if pd.notna(ret) else np.nan,
                "nota_final": row.get("nota_final", np.nan),
                "beta": row.get("beta", np.nan),
                "cv": row.get("cv", np.nan),
                "bucket_regime_previsto": m.get("bucket_regime_previsto", ""),
                "tipo_regime_expost": m.get("tipo_regime_expost", ""),
            }
        )
    for _, m in monthly.iterrows():
        rows.append(
            {
                "mes": str(m["mes"]),
                "ticker": "CDI",
                "nome": "CDI liquido de IR no residual de exposicao",
                "setor": "Caixa/CDI",
                "tipo_alocacao": "cdi_residual",
                "peso_dentro_da_parte_acoes": np.nan,
                "exposicao_modelo": float(m["exposicao_modelo"]),
                "peso_efetivo_carteira_total": float(m["peso_cdi"]),
                "retorno_periodo": float(m["retorno_cdi_liquido_periodo"]),
                "contribuicao_retorno_total": float(m["contribuicao_cdi_liquido"]),
                "nota_final": np.nan,
                "beta": 0.0,
                "cv": np.nan,
                "bucket_regime_previsto": m.get("bucket_regime_previsto", ""),
                "tipo_regime_expost": m.get("tipo_regime_expost", ""),
            }
        )
    out = pd.DataFrame(rows)
    return out.sort_values(["mes", "tipo_alocacao", "peso_efetivo_carteira_total"], ascending=[True, True, False])


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    if not INPUT_34.exists():
        raise FileNotFoundError(INPUT_34)
    if not INPUT_EXPOST.exists():
        raise FileNotFoundError(INPUT_EXPOST)

    raw = pd.read_excel(INPUT_34, sheet_name="mes_a_mes_cdi")
    base = raw[raw["cenario_teste34"].astype(str).eq(BASE_SCENARIO)].copy()
    base = base.sort_values("mes")
    base["peso_acoes_efetivo"] = pd.to_numeric(base["exposicao_acoes_efetiva"], errors="coerce").fillna(0.0)
    base["peso_cdi"] = pd.to_numeric(base["peso_residual_cdi"], errors="coerce").fillna(0.0)
    base["retorno_total_operacional"] = pd.to_numeric(base["retorno_modelo_cdi_liquido"], errors="coerce")
    base["alfa_operacional_vs_ibov"] = pd.to_numeric(base["alfa_cdi_liquido"], errors="coerce")
    base["bateu_ibov_operacional"] = base["alfa_operacional_vs_ibov"].gt(0)
    base["bateu_ibov_sem_cdi"] = pd.to_numeric(base["alfa_zero"], errors="coerce").gt(0)

    cols_monthly = [
        "mes", "bucket_regime_previsto", "motivo_regime", "queda_confirmada_28d", "tipo_regime_expost",
        "exposicao_modelo", "soma_pesos_acoes_bruta", "peso_acoes_efetivo", "peso_cdi",
        "retorno_100_acoes", "retorno_modelo_zero", "retorno_cdi_liquido_periodo", "contribuicao_cdi_liquido",
        "retorno_total_operacional", "retorno_expost_ibov", "alfa_operacional_vs_ibov", "alfa_zero",
        "bateu_ibov_operacional", "bateu_ibov_sem_cdi", "maior_peso_efetivo", "n_ativos", "beta_carteira", "fonte_cdi",
    ]
    monthly = base[[c for c in cols_monthly if c in base.columns]].copy()

    summary = summarize(monthly)
    regime_rows = []
    for regime, group in monthly.groupby("tipo_regime_expost", sort=False):
        s = summarize(group)
        row = dict(zip(s["metrica"], s["valor"]))
        row["tipo_regime_expost"] = regime
        regime_rows.append(row)
    by_regime = pd.DataFrame(regime_rows)

    portfolios = pd.read_excel(INPUT_34, sheet_name="carteiras_base_t33")
    expost = pd.read_excel(INPUT_EXPOST, sheet_name="expost_universo")
    operational_portfolio = build_operational_portfolio(monthly, portfolios, expost)

    validation = operational_portfolio.groupby("mes", as_index=False).agg(
        soma_pesos=("peso_efetivo_carteira_total", "sum"),
        maior_peso=("peso_efetivo_carteira_total", "max"),
        contribuicao_total=("contribuicao_retorno_total", "sum"),
        n_linhas=("ticker", "count"),
    )
    validation = validation.merge(monthly[["mes", "retorno_total_operacional", "retorno_expost_ibov", "alfa_operacional_vs_ibov"]], on="mes", how="left")
    validation["diferenca_contribuicao_vs_retorno"] = validation["contribuicao_total"] - validation["retorno_total_operacional"]
    validation["pesos_fecham_100"] = validation["soma_pesos"].sub(1.0).abs().lt(1e-9)
    validation["retorno_bate_contribuicao"] = validation["diferenca_contribuicao_vs_retorno"].abs().lt(1e-9)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo Operacional", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        by_regime.to_excel(writer, sheet_name="Por Regime Real", index=False)
        operational_portfolio.to_excel(writer, sheet_name="Carteira Operacional", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)
        base.to_excel(writer, sheet_name="Base Completa T34", index=False)

    summary_map = dict(zip(summary["metrica"], summary["valor"]))
    log("Teste 35 - Modelo Consolidado Operacional")
    log("Configuracao: Base 28D + cap efetivo rigido + residual em CDI liquido de IR. Sem recuperacao de restritas.")
    log(f"Retorno operacional: {pct(summary_map['retorno_operacional_cdi'])}")
    log(f"Retorno IBOV: {pct(summary_map['retorno_ibov'])}")
    log(f"Alfa operacional: {pct(summary_map['alfa_operacional_vs_ibov'])}")
    log(f"Acerto operacional: {int(summary_map['meses_bateu_ibov_operacional'])}/{int(summary_map['meses'])} ({float(summary_map['taxa_acerto_operacional']):.2%})")
    log(f"Ganho do CDI no residual vs zero: {pct(summary_map['ganho_do_cdi_no_residual'])}")
    log(f"Drawdown operacional: {pct(summary_map['drawdown_operacional'])}")
    log(f"Exposicao media em acoes: {pct(summary_map['exposicao_media_acoes'])}; peso medio em CDI: {pct(summary_map['peso_medio_cdi'])}")
    invalid_weights = int((~validation["pesos_fecham_100"]).sum())
    invalid_returns = int((~validation["retorno_bate_contribuicao"]).sum())
    log(f"Validacao: meses com pesos != 100%: {invalid_weights}; meses com retorno != soma contribuicoes: {invalid_returns}")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
