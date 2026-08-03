from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_2023 = EXCEL_DIR / "shadow_backtest_2022.xlsx"
INPUT_35 = EXCEL_DIR / "shadow_teste35_modelo_consolidado_operacional_2023_2026.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste35_modelo_consolidado_operacional_2022_2026.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste35_modelo_consolidado_operacional_2022_2026.log"

import sys
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import forward_partial as fp  # noqa: E402


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


def cdi_fallback(start: pd.Timestamp, end: pd.Timestamp, annual_rate: float = 0.15) -> tuple[float, str]:
    days = max(int((end.normalize() - start.normalize()).days), 1)
    gross = (1.0 + float(annual_rate)) ** (days / 365.0) - 1.0
    return gross, f"fallback_taxa_anual_configurada_{annual_rate:.4f}"


def cdi_period(start: pd.Timestamp, end: pd.Timestamp) -> tuple[float, float, int, str]:
    try:
        gross, _daily, source = fp.fetch_cdi_gross_return(start + pd.Timedelta(days=1), end)
    except Exception as exc:  # noqa: BLE001
        gross, source = cdi_fallback(start + pd.Timedelta(days=1), end)
        source = f"{source}; erro_bcb_sgs_12={exc}"
    net, ir_rate, days = fp.cdi_net_return(gross, start, end)
    return float(net), float(ir_rate), int(days), str(source)


def load_2023_monthly() -> pd.DataFrame:
    base = pd.read_excel(INPUT_2023, sheet_name="mes_a_mes")
    expost = pd.read_excel(INPUT_2023, sheet_name="expost_universo")
    dates = expost[["mes", "data_inicio_performance", "data_avaliacao", "retorno_ibov_periodo"]].drop_duplicates("mes")
    out = base.merge(dates, on="mes", how="left", suffixes=("", "_date"))
    rows = []
    for _, row in out.iterrows():
        start = pd.Timestamp(row["data_inicio_performance"]).normalize()
        end = pd.Timestamp(row["data_avaliacao"]).normalize()
        cdi_net, ir_rate, days, source = cdi_period(start, end)
        exposure = float(row.get("exposicao_defensiva", row.get("exposicao_modelo", 1.0)) or 0.0)
        cdi_weight = float(row.get("peso_caixa", 1.0 - exposure) or 0.0)
        ret100 = float(row.get("retorno_expost_sombra_100pct", row.get("retorno_expost_sombra", np.nan)))
        ret_zero = ret100 * exposure if pd.notna(ret100) else np.nan
        cdi_contrib = cdi_weight * cdi_net
        total = ret_zero + cdi_contrib if pd.notna(ret_zero) else np.nan
        ibov = float(row.get("retorno_expost_ibov", row.get("retorno_ibov_periodo", np.nan)))
        rows.append(
            {
                "mes": str(row["mes"]),
                "bucket_regime_previsto": row.get("bucket_regime", ""),
                "motivo_regime": row.get("motivo_subtipo_queda", ""),
                "queda_confirmada_28d": str(row.get("bucket_regime", "")).startswith("queda"),
                "tipo_regime_expost": "alta" if ibov >= 0 else ("queda_forte" if ibov <= -0.03 else "queda_leve"),
                "exposicao_modelo": exposure,
                "soma_pesos_acoes_bruta": 1.0,
                "peso_acoes_efetivo": exposure,
                "peso_cdi": cdi_weight,
                "retorno_100_acoes": ret100,
                "retorno_modelo_zero": ret_zero,
                "retorno_cdi_liquido_periodo": cdi_net,
                "contribuicao_cdi_liquido": cdi_contrib,
                "retorno_total_operacional": total,
                "retorno_expost_ibov": ibov,
                "alfa_operacional_vs_ibov": total - ibov if pd.notna(total) and pd.notna(ibov) else np.nan,
                "alfa_zero": ret_zero - ibov if pd.notna(ret_zero) and pd.notna(ibov) else np.nan,
                "bateu_ibov_operacional": (total - ibov) > 0 if pd.notna(total) and pd.notna(ibov) else False,
                "bateu_ibov_sem_cdi": (ret_zero - ibov) > 0 if pd.notna(ret_zero) and pd.notna(ibov) else False,
                "maior_peso_efetivo": np.nan,
                "n_ativos": int(row.get("numero_acoes_formadas", 0) or 0) if "numero_acoes_formadas" in row else np.nan,
                "beta_carteira": row.get("beta_carteira_sombra", np.nan),
                "fonte_cdi": source,
                "aliquota_ir_cdi": ir_rate,
                "dias_corridos_cdi": days,
                "data_inicio_performance": start.date().isoformat(),
                "data_avaliacao": end.date().isoformat(),
            }
        )
    monthly = pd.DataFrame(rows).sort_values("mes")
    port = pd.read_excel(INPUT_2023, sheet_name="carteiras_por_mes")
    if not port.empty and {"mes", "peso_recomendado"}.issubset(port.columns):
        maxw = port.groupby("mes")["peso_recomendado"].max().rename("maior_peso_acoes")
        monthly = monthly.merge(maxw, on="mes", how="left")
        monthly["maior_peso_efetivo"] = monthly["maior_peso_acoes"] * monthly["exposicao_modelo"]
    return monthly


def summarize(monthly: pd.DataFrame) -> pd.DataFrame:
    ret = pd.to_numeric(monthly["retorno_total_operacional"], errors="coerce")
    ret_zero = pd.to_numeric(monthly["retorno_modelo_zero"], errors="coerce")
    ibov = pd.to_numeric(monthly["retorno_expost_ibov"], errors="coerce")
    alpha = pd.to_numeric(monthly["alfa_operacional_vs_ibov"], errors="coerce")
    alpha_zero = pd.to_numeric(monthly["alfa_zero"], errors="coerce")
    rows = [
        {"metrica": "cenario", "valor": "base_28d_cap_efetivo_residual_cdi_liquido_2022_2026"},
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
        {"metrica": "maior_peso_acao_efetivo", "valor": float(pd.to_numeric(monthly["maior_peso_efetivo"], errors="coerce").max())},
    ]
    return pd.DataFrame(rows)


def build_2023_portfolio(monthly_2023: pd.DataFrame) -> pd.DataFrame:
    port = pd.read_excel(INPUT_2023, sheet_name="carteiras_por_mes")
    expost = pd.read_excel(INPUT_2023, sheet_name="expost_universo")
    exp_idx = expost.set_index(["mes", "ticker"])
    m_idx = monthly_2023.set_index("mes")
    rows = []
    for _, row in port.iterrows():
        mes = str(row.get("mes"))
        if mes not in m_idx.index:
            continue
        ticker = str(row.get("ticker", ""))
        if not ticker or ticker.lower() == "nan":
            continue
        m = m_idx.loc[mes]
        w_stock = float(row.get("peso_recomendado", 0.0) or 0.0)
        w_eff = w_stock * float(m["exposicao_modelo"])
        ret = np.nan
        if (mes, ticker) in exp_idx.index:
            ret = pd.to_numeric(exp_idx.loc[(mes, ticker), "retorno_realizado_periodo"], errors="coerce")
        rows.append({
            "mes": mes,
            "ticker": ticker,
            "nome": row.get("nome", ""),
            "setor": row.get("setor", ""),
            "tipo_alocacao": "acao",
            "peso_dentro_da_parte_acoes": w_stock,
            "exposicao_modelo": float(m["exposicao_modelo"]),
            "peso_efetivo_carteira_total": w_eff,
            "retorno_periodo": ret,
            "contribuicao_retorno_total": w_eff * float(ret) if pd.notna(ret) else np.nan,
            "nota_final": row.get("nota_final", np.nan),
            "beta": row.get("beta", np.nan),
            "cv": row.get("cv", np.nan),
            "bucket_regime_previsto": m.get("bucket_regime_previsto", ""),
            "tipo_regime_expost": m.get("tipo_regime_expost", ""),
        })
    for _, m in monthly_2023.iterrows():
        rows.append({
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
        })
    return pd.DataFrame(rows)


def main() -> None:
    logs: list[str] = []
    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    if not INPUT_2023.exists():
        raise FileNotFoundError(INPUT_2023)
    if not INPUT_35.exists():
        raise FileNotFoundError(INPUT_35)

    monthly_old = pd.read_excel(INPUT_35, sheet_name="Mes a Mes")
    port_old = pd.read_excel(INPUT_35, sheet_name="Carteira Operacional")
    base_old = pd.read_excel(INPUT_35, sheet_name="Base Completa T34")
    monthly_2023 = load_2023_monthly()
    monthly = pd.concat([monthly_2023, monthly_old], ignore_index=True, sort=False).sort_values("mes")
    summary = summarize(monthly)

    regime_rows = []
    for regime, group in monthly.groupby("tipo_regime_expost", sort=False):
        s = summarize(group)
        row = dict(zip(s["metrica"], s["valor"]))
        row["tipo_regime_expost"] = regime
        regime_rows.append(row)
    by_regime = pd.DataFrame(regime_rows)

    port_2023 = build_2023_portfolio(monthly_2023)
    operational_portfolio = pd.concat([port_2023, port_old], ignore_index=True, sort=False).sort_values(["mes", "tipo_alocacao", "peso_efetivo_carteira_total"], ascending=[True, True, False])

    validation = operational_portfolio.groupby("mes", as_index=False).agg(
        soma_pesos=("peso_efetivo_carteira_total", "sum"),
        maior_peso=("peso_efetivo_carteira_total", "max"),
        contribuicao_total=("contribuicao_retorno_total", "sum"),
        n_linhas=("ticker", "count"),
    )
    validation = validation.merge(monthly[["mes", "retorno_total_operacional", "retorno_expost_ibov", "alfa_operacional_vs_ibov"]], on="mes", how="left")
    validation["diferenca_contribuicao_vs_retorno"] = validation["contribuicao_total"] - validation["retorno_total_operacional"]
    validation["pesos_fecham_100"] = validation["soma_pesos"].sub(1.0).abs().lt(1e-7)
    validation["retorno_bate_contribuicao"] = validation["diferenca_contribuicao_vs_retorno"].abs().lt(1e-7)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo Operacional", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        by_regime.to_excel(writer, sheet_name="Por Regime Real", index=False)
        operational_portfolio.to_excel(writer, sheet_name="Carteira Operacional", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)
        base_old.to_excel(writer, sheet_name="Base Completa T34", index=False)
        monthly_2023.to_excel(writer, sheet_name="Base 2022 CDI", index=False)

    summary_map = dict(zip(summary["metrica"], summary["valor"]))
    log("Modelo Consolidado Operacional estendido 2022-2026")
    log(f"Meses: {int(summary_map['meses'])}")
    log(f"Retorno operacional: {pct(summary_map['retorno_operacional_cdi'])}")
    log(f"Retorno IBOV: {pct(summary_map['retorno_ibov'])}")
    log(f"Alfa operacional: {pct(summary_map['alfa_operacional_vs_ibov'])}")
    log(f"Acerto operacional: {int(summary_map['meses_bateu_ibov_operacional'])}/{int(summary_map['meses'])} ({float(summary_map['taxa_acerto_operacional']):.2%})")
    log(f"Ganho do CDI no residual vs zero: {pct(summary_map['ganho_do_cdi_no_residual'])}")
    log(f"Drawdown operacional: {pct(summary_map['drawdown_operacional'])}")
    log(f"Validacao: pesos !=100%: {int((~validation['pesos_fecham_100']).sum())}; retorno != contribuicoes: {int((~validation['retorno_bate_contribuicao']).sum())}")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
