from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_42 = EXCEL_DIR / "shadow_teste42_falso_alerta_queda_4m.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste44_diagnostico_grau_confianca.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste44_diagnostico_grau_confianca.log"


SCENARIOS = {
    "BASE_T36C": "Controle original 36C.",
    "T42A_QUALIDADE_FORTE": "Queda prevista com qualidade forte usa janela 4M.",
    "T44A_QUEDA_CONFIANCA": "Mesmo gatilho do 42A, reportado como queda de baixa confianca.",
    "T44B_QUEDA_CONF_ALTA_FRAGIL_80": "T44A + alta fragil reduz exposicao em acoes para 80%.",
    "T44C_QUEDA_CONF_ALTA_MUITO_FRAGIL_70": "T44A + alta muito fragil reduz exposicao em acoes para 70%.",
    "T44D_QUEDA_CONF_ALTA_RISCO_85": "T44A + alta com beta alto/nota baixa reduz exposicao em acoes para 85%.",
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


def summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, group in df.groupby(group_cols, dropna=False, sort=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        ret_model = compound(group["retorno_total"])
        ret_ibov = compound(group["retorno_expost_ibov"])
        row.update(
            {
                "meses": len(group),
                "retorno_modelo": ret_model,
                "retorno_ibov": ret_ibov,
                "alfa_vs_ibov": ret_model - ret_ibov,
                "taxa_acerto": float(pd.to_numeric(group["alfa_vs_ibov"], errors="coerce").gt(0).mean()),
                "drawdown": max_drawdown(group["retorno_total"]),
                "peso_acoes_medio": float(pd.to_numeric(group["peso_acoes"], errors="coerce").mean()),
                "peso_cdi_medio": float(pd.to_numeric(group["peso_cdi"], errors="coerce").mean()),
                "meses_4m": int(group["usa_4m"].fillna(False).sum()),
                "meses_exposicao_reduzida": int(group["exposicao_reduzida"].fillna(False).sum()) if "exposicao_reduzida" in group.columns else 0,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def direction(regime: Any) -> str:
    return "alta" if str(regime).strip().lower() == "alta" else "queda"


def real_direction(ret_ibov: Any) -> str:
    value = pd.to_numeric(ret_ibov, errors="coerce")
    if pd.isna(value):
        return "indefinido"
    return "alta" if float(value) >= 0 else "queda"


def error_type(prev: str, real: str) -> str:
    if prev == "alta" and real == "alta":
        return "acerto_ofensivo"
    if prev == "alta" and real == "queda":
        return "falso_positivo_alta"
    if prev == "queda" and real == "queda":
        return "acerto_defensivo"
    if prev == "queda" and real == "alta":
        return "falso_alerta_queda"
    return "indefinido"


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not INPUT_42.exists():
        raise FileNotFoundError(INPUT_42)
    monthly = pd.read_excel(INPUT_42, sheet_name="Mes a Mes")
    portfolio = pd.read_excel(INPUT_42, sheet_name="Carteiras")
    monthly["mes"] = monthly["mes"].astype(str).str[:7]
    portfolio["mes"] = portfolio["mes"].astype(str).str[:7]
    return monthly, portfolio


def is_queda_baixa_confianca(row: pd.Series) -> bool:
    regime = str(row.get("regime_previsto_norm", "")).strip().lower()
    if regime not in {"queda_leve", "queda_forte"}:
        return False
    nota = float(pd.to_numeric(row.get("nota_media_formacao"), errors="coerce") or 0.0)
    n_assets = float(pd.to_numeric(row.get("n_ativos_acoes_formacao"), errors="coerce") or 0.0)
    beta = float(pd.to_numeric(row.get("beta_carteira_formacao"), errors="coerce") or 999.0)
    return nota >= 60 and n_assets >= 10 and beta <= 1.10


def high_confidence_label(row: pd.Series) -> tuple[str, str]:
    prev = direction(row.get("regime_previsto_norm"))
    nota = float(pd.to_numeric(row.get("nota_media_formacao"), errors="coerce") or 0.0)
    n_assets = float(pd.to_numeric(row.get("n_ativos_acoes_formacao"), errors="coerce") or 0.0)
    beta = float(pd.to_numeric(row.get("beta_carteira_formacao"), errors="coerce") or 999.0)
    queda_confirmada = bool(row.get("queda_confirmada_28d"))
    if prev == "queda":
        if is_queda_baixa_confianca(row):
            return "queda_baixa_confianca", f"qualidade_forte: nota={nota:.1f}, n={n_assets:.0f}, beta={beta:.2f}"
        return "queda_alta_confianca", f"sem gatilho 4M: nota={nota:.1f}, n={n_assets:.0f}, beta={beta:.2f}, queda_confirmada={queda_confirmada}"
    # Alta: testamos fragilidade separadamente; o baseline continua 100% em acoes.
    if nota < 55 or n_assets < 10:
        return "alta_muito_fragil", f"nota={nota:.1f}, n={n_assets:.0f}, beta={beta:.2f}"
    if nota < 58 or n_assets < 12:
        return "alta_fragil", f"nota={nota:.1f}, n={n_assets:.0f}, beta={beta:.2f}"
    if beta > 1.15 and nota < 60:
        return "alta_risco", f"nota={nota:.1f}, n={n_assets:.0f}, beta={beta:.2f}"
    return "alta_normal", f"nota={nota:.1f}, n={n_assets:.0f}, beta={beta:.2f}"


def source_for_scenario(scenario: str, base_row: pd.Series) -> str:
    if scenario == "BASE_T36C":
        return "BASE_T36C"
    if scenario == "T42A_QUALIDADE_FORTE":
        return "JANELA_4M" if is_queda_baixa_confianca(base_row) else "BASE_T36C"
    # All T44 variants inherit the 42A rule for low-confidence predicted drops.
    return "JANELA_4M" if is_queda_baixa_confianca(base_row) else "BASE_T36C"


def target_equity_for_scenario(scenario: str, row: pd.Series) -> tuple[float | None, str]:
    label, reason = high_confidence_label(row)
    if scenario == "T44B_QUEDA_CONF_ALTA_FRAGIL_80" and label in {"alta_fragil", "alta_muito_fragil"}:
        return 0.80, f"{label}; {reason}; reduz_para_80"
    if scenario == "T44C_QUEDA_CONF_ALTA_MUITO_FRAGIL_70" and label == "alta_muito_fragil":
        return 0.70, f"{label}; {reason}; reduz_para_70"
    if scenario == "T44D_QUEDA_CONF_ALTA_RISCO_85" and label == "alta_risco":
        return 0.85, f"{label}; {reason}; reduz_para_85"
    return None, f"{label}; {reason}; sem_reducao"


def adjust_portfolio_exposure(
    monthly_row: pd.Series,
    portfolio_rows: pd.DataFrame,
    target_equity: float | None,
) -> tuple[pd.Series, pd.DataFrame]:
    row = monthly_row.copy()
    portfolio = portfolio_rows.copy()
    current_equity = float(pd.to_numeric(row.get("peso_acoes"), errors="coerce") or 0.0)
    current_cdi = float(pd.to_numeric(row.get("peso_cdi"), errors="coerce") or 0.0)
    if target_equity is None or current_equity <= 0 or target_equity >= current_equity:
        row["exposicao_reduzida"] = False
        row["peso_acoes_antes_reducao"] = current_equity
        row["peso_acoes_depois_reducao"] = current_equity
        return row, portfolio

    factor = target_equity / current_equity
    is_cdi = portfolio["ticker"].astype(str).str.upper().eq("CDI")
    for col in ["peso_efetivo_carteira_total", "contribuicao_retorno_total"]:
        portfolio.loc[~is_cdi, col] = pd.to_numeric(portfolio.loc[~is_cdi, col], errors="coerce").fillna(0.0) * factor
    if "peso_dentro_da_parte_acoes" in portfolio.columns:
        portfolio.loc[~is_cdi, "peso_dentro_da_parte_acoes"] = pd.to_numeric(
            portfolio.loc[~is_cdi, "peso_dentro_da_parte_acoes"], errors="coerce"
        ).fillna(0.0)

    target_cdi = 1.0 - target_equity
    cdi_return = float(pd.to_numeric(row.get("retorno_cdi_liquido_periodo"), errors="coerce") or 0.0)
    if is_cdi.any():
        cdi_idx = portfolio[is_cdi].index
        portfolio.loc[cdi_idx, "peso_efetivo_carteira_total"] = target_cdi
        portfolio.loc[cdi_idx, "retorno_periodo"] = cdi_return
        portfolio.loc[cdi_idx, "contribuicao_retorno_total"] = target_cdi * cdi_return
    else:
        template = {col: np.nan for col in portfolio.columns}
        template.update(
            {
                "ticker": "CDI",
                "nome": "Reserva/CDI liquido",
                "setor": "Posicao defensiva",
                "tipo_alocacao": "cdi",
                "peso_dentro_da_parte_acoes": 0.0,
                "peso_efetivo_carteira_total": target_cdi,
                "retorno_periodo": cdi_return,
                "contribuicao_retorno_total": target_cdi * cdi_return,
            }
        )
        portfolio = pd.concat([portfolio, pd.DataFrame([template])], ignore_index=True, sort=False)

    total_return = float(pd.to_numeric(portfolio["contribuicao_retorno_total"], errors="coerce").sum())
    row["peso_acoes_antes_reducao"] = current_equity
    row["peso_acoes_depois_reducao"] = target_equity
    row["peso_acoes"] = target_equity
    row["peso_cdi"] = target_cdi
    row["retorno_total"] = total_return
    row["alfa_vs_ibov"] = total_return - float(pd.to_numeric(row.get("retorno_expost_ibov"), errors="coerce") or 0.0)
    row["bateu_ibov"] = row["alfa_vs_ibov"] > 0
    row["soma_pesos"] = float(pd.to_numeric(portfolio["peso_efetivo_carteira_total"], errors="coerce").sum())
    row["maior_peso"] = float(pd.to_numeric(portfolio["peso_efetivo_carteira_total"], errors="coerce").max())
    row["n_linhas"] = int(len(portfolio))
    row["exposicao_reduzida"] = True
    return row, portfolio


def build_scenario(
    scenario: str,
    monthly42: pd.DataFrame,
    portfolio42: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    base_months = monthly42[monthly42["cenario_t42"].eq("BASE_T36C")].copy().set_index("mes")
    monthly_by_source = {
        source: monthly42[monthly42["cenario_t42"].eq(source)].copy().set_index("mes")
        for source in ["BASE_T36C", "T42A_QUALIDADE_FORTE"]
        if source in monthly42["cenario_t42"].unique()
    }
    # JANELA_4M rows are stored under the T42A scenario when the trigger fires.
    t42a_idx = monthly42[monthly42["cenario_t42"].eq("T42A_QUALIDADE_FORTE")].copy().set_index("mes")

    rows = []
    portfolios = []
    for mes, base_row in base_months.iterrows():
        source = source_for_scenario(scenario, base_row)
        if scenario == "BASE_T36C":
            src_row = base_row.copy()
            src_scenario = "BASE_T36C"
        elif source == "JANELA_4M":
            src_row = t42a_idx.loc[mes].copy()
            src_scenario = "T42A_QUALIDADE_FORTE"
        elif scenario == "T42A_QUALIDADE_FORTE":
            src_row = t42a_idx.loc[mes].copy()
            src_scenario = "T42A_QUALIDADE_FORTE"
        else:
            src_row = base_row.copy()
            src_scenario = "BASE_T36C"

        portfolio_rows = portfolio42[
            portfolio42["cenario_t42"].astype(str).eq(src_scenario)
            & portfolio42["mes"].astype(str).eq(mes)
        ].copy()
        target_equity, exposure_reason = target_equity_for_scenario(scenario, base_row)
        src_row, portfolio_rows = adjust_portfolio_exposure(src_row, portfolio_rows, target_equity)
        confidence_label, confidence_reason = high_confidence_label(base_row)

        src_row["mes"] = mes
        src_row["cenario_t44"] = scenario
        src_row["cenario_origem_t44"] = src_scenario
        src_row["usa_4m"] = source == "JANELA_4M"
        src_row["grau_confianca_diagnostico"] = confidence_label
        src_row["motivo_confianca"] = confidence_reason
        src_row["motivo_exposicao"] = exposure_reason
        src_row["direcao_prevista"] = direction(src_row.get("regime_previsto_norm"))
        src_row["direcao_real_ibov"] = real_direction(src_row.get("retorno_expost_ibov"))
        src_row["tipo_erro_diagnostico"] = error_type(src_row["direcao_prevista"], src_row["direcao_real_ibov"])
        rows.append(src_row)

        portfolio_rows["cenario_t44"] = scenario
        portfolio_rows["cenario_origem_t44"] = src_scenario
        portfolio_rows["usa_4m"] = source == "JANELA_4M"
        portfolio_rows["grau_confianca_diagnostico"] = confidence_label
        portfolio_rows["motivo_exposicao"] = exposure_reason
        portfolios.append(portfolio_rows)

    return pd.DataFrame(rows), pd.concat(portfolios, ignore_index=True, sort=False)


def build_validation(monthly: pd.DataFrame, portfolio: pd.DataFrame) -> pd.DataFrame:
    validation = portfolio.groupby(["cenario_t44", "mes"], as_index=False).agg(
        soma_pesos=("peso_efetivo_carteira_total", "sum"),
        contribuicao_total=("contribuicao_retorno_total", "sum"),
        maior_peso=("peso_efetivo_carteira_total", "max"),
        n_linhas=("ticker", "count"),
    )
    validation = validation.merge(
        monthly[["cenario_t44", "mes", "retorno_total", "retorno_expost_ibov", "alfa_vs_ibov"]],
        on=["cenario_t44", "mes"],
        how="left",
    )
    validation["diferenca_contribuicao_vs_retorno"] = validation["contribuicao_total"] - validation["retorno_total"]
    validation["pesos_fecham_100"] = validation["soma_pesos"].sub(1.0).abs().lt(1e-8)
    validation["retorno_consistente"] = validation["diferenca_contribuicao_vs_retorno"].abs().lt(1e-8)
    return validation


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    monthly42, portfolio42 = load_inputs()
    monthly_parts = []
    portfolio_parts = []
    for scenario in SCENARIOS:
        monthly_s, portfolio_s = build_scenario(scenario, monthly42, portfolio42)
        monthly_parts.append(monthly_s)
        portfolio_parts.append(portfolio_s)
    monthly = pd.concat(monthly_parts, ignore_index=True, sort=False)
    portfolio = pd.concat(portfolio_parts, ignore_index=True, sort=False)
    monthly["ano"] = monthly["mes"].astype(str).str[:4]

    summary = summarize(monthly, ["cenario_t44"])
    summary_year = summarize(monthly, ["cenario_t44", "ano"])
    summary_error = summarize(monthly, ["cenario_t44", "tipo_erro_diagnostico"])
    summary_conf = summarize(monthly, ["cenario_t44", "grau_confianca_diagnostico"])
    validation = build_validation(monthly, portfolio)

    base_alpha = float(summary.loc[summary["cenario_t44"].eq("BASE_T36C"), "alfa_vs_ibov"].iloc[0])
    summary["delta_alfa_vs_base"] = summary["alfa_vs_ibov"] - base_alpha
    base_year = summary_year[summary_year["cenario_t44"].eq("BASE_T36C")][["ano", "alfa_vs_ibov"]].rename(columns={"alfa_vs_ibov": "alfa_base_ano"})
    summary_year = summary_year.merge(base_year, on="ano", how="left")
    summary_year["delta_alfa_vs_base"] = summary_year["alfa_vs_ibov"] - summary_year["alfa_base_ano"]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        pd.DataFrame([{"cenario_t44": key, "descricao": val} for key, val in SCENARIOS.items()]).to_excel(writer, sheet_name="Descricao Cenarios", index=False)
        summary.to_excel(writer, sheet_name="Resumo Geral", index=False)
        summary_year.to_excel(writer, sheet_name="Resumo Ano", index=False)
        summary_error.to_excel(writer, sheet_name="Resumo Tipo Erro", index=False)
        summary_conf.to_excel(writer, sheet_name="Resumo Confianca", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        portfolio.to_excel(writer, sheet_name="Carteiras", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)

    log("Teste 44 - Diagnostico com Grau de Confianca")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log("")
    log("Resumo geral:")
    log(summary[["cenario_t44", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "drawdown", "peso_acoes_medio", "meses_4m", "meses_exposicao_reduzida", "delta_alfa_vs_base"]].to_string(index=False))
    log("")
    log("Resumo por ano:")
    log(summary_year[["cenario_t44", "ano", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "delta_alfa_vs_base"]].to_string(index=False))
    log("")
    log("Resumo por tipo de erro:")
    log(summary_error[["cenario_t44", "tipo_erro_diagnostico", "meses", "alfa_vs_ibov", "taxa_acerto", "meses_4m", "meses_exposicao_reduzida"]].to_string(index=False))
    invalid = validation[(~validation["pesos_fecham_100"]) | (~validation["retorno_consistente"])]
    log("")
    log(f"Validacao: {'OK' if invalid.empty else 'FALHAS=' + str(len(invalid))}")
    if not invalid.empty:
        log(invalid.to_string(index=False))
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
