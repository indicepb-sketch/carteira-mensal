from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_36 = EXCEL_DIR / "shadow_teste36_exposicao_regime_2.xlsx"
INPUT_38 = EXCEL_DIR / "shadow_teste38_sensibilidade_janela_retorno.xlsx"
INPUT_39 = EXCEL_DIR / "shadow_teste39_4m_mercado_fraco_rotacional.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste42_falso_alerta_queda_4m.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste42_falso_alerta_queda_4m.log"


SCENARIOS = {
    "BASE_T36C": {
        "descricao": "Controle: modelo 36C sem troca condicional para janela 4M.",
        "kind": "base",
    },
    "T39_4M_TODA_QUEDA_PREVISTA": {
        "descricao": "Controle forte: usa 4M em toda queda prevista, como Teste 39.",
        "kind": "t39",
    },
    "T42A_QUALIDADE_FORTE": {
        "descricao": "4M so em queda prevista com qualidade forte: nota_media>=60, n_ativos>=10, beta<=1.10.",
        "kind": "detector",
    },
    "T42B_QUALIDADE_MEDIA": {
        "descricao": "4M em queda prevista com qualidade media: nota_media>=55, n_ativos>=8, beta<=1.30.",
        "kind": "detector",
    },
    "T42C_QUEDA_LEVE_QUALIDADE": {
        "descricao": "4M so em queda_leve com nota_media>=55 e n_ativos>=8.",
        "kind": "detector",
    },
    "T42D_FALSO_ALERTA_BALANCEADO": {
        "descricao": "4M em queda prevista se ha 3 de 4 sinais: queda leve, nota>=55, n_ativos>=8, beta<=1.20.",
        "kind": "detector",
    },
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


def summarize(monthly: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, group in monthly.groupby(group_cols, dropna=False, sort=False):
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
                "meses_bateu_ibov": int(pd.to_numeric(group["alfa_vs_ibov"], errors="coerce").gt(0).sum()),
                "taxa_acerto": float(pd.to_numeric(group["alfa_vs_ibov"], errors="coerce").gt(0).mean()),
                "drawdown": max_drawdown(group["retorno_total"]),
                "peso_acoes_medio": float(pd.to_numeric(group["peso_acoes"], errors="coerce").mean()),
                "maior_peso": float(pd.to_numeric(group["maior_peso"], errors="coerce").max()),
                "meses_usou_4m": int(group["usa_4m"].fillna(False).sum()) if "usa_4m" in group.columns else 0,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def norm_mes(value: Any) -> str:
    return str(value)[:7]


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    for path in [INPUT_36, INPUT_38, INPUT_39]:
        if not path.exists():
            raise FileNotFoundError(path)
    monthly36 = pd.read_excel(INPUT_36, sheet_name="Mes a Mes")
    monthly38 = pd.read_excel(INPUT_38, sheet_name="Mes a Mes")
    portfolio38 = pd.read_excel(INPUT_38, sheet_name="Carteiras")
    monthly39 = pd.read_excel(INPUT_39, sheet_name="Mes a Mes")
    monthly36 = monthly36[monthly36["cenario_teste36"].astype(str).eq("T36C_QUALIDADE")].copy()
    monthly36["mes"] = monthly36["mes"].map(norm_mes)
    monthly38["mes"] = monthly38["mes"].map(norm_mes)
    monthly39["mes"] = monthly39["mes"].map(norm_mes)
    portfolio38["mes"] = portfolio38["mes"].map(norm_mes)
    return monthly36, monthly38, portfolio38, monthly39


def decide_4m(scenario: str, row: pd.Series) -> tuple[bool, str]:
    regime = str(row.get("regime_previsto_norm", "")).strip().lower()
    is_down = regime in {"queda_leve", "queda_forte"}
    if scenario == "BASE_T36C":
        return False, "baseline_36c"
    if scenario == "T39_4M_TODA_QUEDA_PREVISTA":
        return is_down, "t39_queda_prevista" if is_down else "mantem_base_sem_queda_prevista"
    if not is_down:
        return False, "sem_queda_prevista"

    nota = float(pd.to_numeric(row.get("nota_media"), errors="coerce") or 0.0)
    n_assets = float(pd.to_numeric(row.get("n_ativos_acoes"), errors="coerce") or 0.0)
    beta = float(pd.to_numeric(row.get("beta_carteira"), errors="coerce") or 999.0)
    queda_leve = regime == "queda_leve"

    if scenario == "T42A_QUALIDADE_FORTE":
        ok = nota >= 60 and n_assets >= 10 and beta <= 1.10
        return ok, f"qualidade_forte={'sim' if ok else 'nao'}; nota={nota:.1f}; n={n_assets:.0f}; beta={beta:.2f}"
    if scenario == "T42B_QUALIDADE_MEDIA":
        ok = nota >= 55 and n_assets >= 8 and beta <= 1.30
        return ok, f"qualidade_media={'sim' if ok else 'nao'}; nota={nota:.1f}; n={n_assets:.0f}; beta={beta:.2f}"
    if scenario == "T42C_QUEDA_LEVE_QUALIDADE":
        ok = queda_leve and nota >= 55 and n_assets >= 8
        return ok, f"queda_leve_qualidade={'sim' if ok else 'nao'}; regime={regime}; nota={nota:.1f}; n={n_assets:.0f}"
    if scenario == "T42D_FALSO_ALERTA_BALANCEADO":
        sinais = {
            "queda_leve": queda_leve,
            "nota_media_55": nota >= 55,
            "n_ativos_8": n_assets >= 8,
            "beta_ate_1_20": beta <= 1.20,
        }
        count = int(sum(bool(v) for v in sinais.values()))
        ok = count >= 3
        return ok, f"sinais={count}/4; " + "; ".join(f"{k}={v}" for k, v in sinais.items())
    raise ValueError(f"Cenario desconhecido: {scenario}")


def build_scenario(
    scenario: str,
    monthly36: pd.DataFrame,
    monthly38: pd.DataFrame,
    portfolio38: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = monthly38[monthly38["cenario_t38"].astype(str).eq("BASE_T36C")].copy()
    j4 = monthly38[monthly38["cenario_t38"].astype(str).eq("JANELA_4M")].copy()
    base_idx = base.set_index("mes")
    j4_idx = j4.set_index("mes")
    signal_idx = monthly36.set_index("mes")

    rows = []
    for mes, base_row in base_idx.iterrows():
        signal = signal_idx.loc[mes] if mes in signal_idx.index else base_row
        use_4m, reason = decide_4m(scenario, signal)
        source = "JANELA_4M" if use_4m and mes in j4_idx.index else "BASE_T36C"
        src = (j4_idx.loc[mes] if source == "JANELA_4M" else base_row).copy()
        src["mes"] = mes
        src["cenario_t42"] = scenario
        src["cenario_origem_t42"] = source
        src["usa_4m"] = source == "JANELA_4M"
        src["motivo_acionamento_4m"] = reason
        src["nota_media_formacao"] = signal.get("nota_media", np.nan)
        src["nota_mediana_formacao"] = signal.get("nota_mediana", np.nan)
        src["n_ativos_acoes_formacao"] = signal.get("n_ativos_acoes", np.nan)
        src["beta_carteira_formacao"] = signal.get("beta_carteira", np.nan)
        src["queda_confirmada_28d"] = signal.get("queda_confirmada_28d", np.nan)
        rows.append(src)

    monthly = pd.DataFrame(rows)
    monthly["alfa_vs_ibov"] = monthly["retorno_total"] - monthly["retorno_expost_ibov"]
    monthly["bateu_ibov"] = monthly["alfa_vs_ibov"] > 0

    parts = []
    for _, row in monthly.iterrows():
        mes = str(row["mes"])
        source = str(row["cenario_origem_t42"])
        chunk = portfolio38[
            portfolio38["cenario_t38"].astype(str).eq(source)
            & portfolio38["mes"].astype(str).eq(mes)
        ].copy()
        chunk["cenario_t42"] = scenario
        chunk["cenario_origem_t42"] = source
        chunk["usa_4m"] = bool(row["usa_4m"])
        chunk["motivo_acionamento_4m"] = row["motivo_acionamento_4m"]
        parts.append(chunk)
    portfolio = pd.concat(parts, ignore_index=True, sort=False)
    return monthly, portfolio


def build_comparison(monthly: pd.DataFrame) -> pd.DataFrame:
    pivot = monthly.pivot_table(index="mes", columns="cenario_t42", values=["retorno_total", "alfa_vs_ibov"], aggfunc="first")
    pivot.columns = [f"{a}_{b}" for a, b in pivot.columns]
    pivot = pivot.reset_index()
    for scenario in SCENARIOS:
        col = f"alfa_vs_ibov_{scenario}"
        if col in pivot.columns and "alfa_vs_ibov_BASE_T36C" in pivot.columns:
            pivot[f"delta_alfa_{scenario}_vs_base"] = pivot[col] - pivot["alfa_vs_ibov_BASE_T36C"]
    return pivot


def build_validation(monthly: pd.DataFrame, portfolio: pd.DataFrame) -> pd.DataFrame:
    validation = portfolio.groupby(["cenario_t42", "mes"], as_index=False).agg(
        soma_pesos=("peso_efetivo_carteira_total", "sum"),
        contribuicao_total=("contribuicao_retorno_total", "sum"),
        maior_peso=("peso_efetivo_carteira_total", "max"),
        n_linhas=("ticker", "count"),
    )
    validation = validation.merge(
        monthly[["cenario_t42", "mes", "retorno_total", "retorno_expost_ibov", "alfa_vs_ibov"]],
        on=["cenario_t42", "mes"],
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

    monthly36, monthly38, portfolio38, monthly39 = load_inputs()
    monthly_parts = []
    portfolio_parts = []
    for scenario in SCENARIOS:
        monthly_s, portfolio_s = build_scenario(scenario, monthly36, monthly38, portfolio38)
        monthly_parts.append(monthly_s)
        portfolio_parts.append(portfolio_s)

    monthly = pd.concat(monthly_parts, ignore_index=True, sort=False)
    portfolio = pd.concat(portfolio_parts, ignore_index=True, sort=False)
    monthly["ano"] = monthly["mes"].astype(str).str[:4]
    summary = summarize(monthly, ["cenario_t42"])
    summary_year = summarize(monthly, ["cenario_t42", "ano"])
    summary_pred_regime = summarize(monthly, ["cenario_t42", "regime_previsto_norm"])
    summary_real_regime = summarize(monthly, ["cenario_t42", "tipo_regime_expost"])
    comparison = build_comparison(monthly)
    validation = build_validation(monthly, portfolio)
    months_4m = monthly[monthly["usa_4m"]].copy()

    base_alpha = float(summary.loc[summary["cenario_t42"].eq("BASE_T36C"), "alfa_vs_ibov"].iloc[0])
    summary["delta_alfa_vs_base"] = summary["alfa_vs_ibov"] - base_alpha
    base_year = summary_year[summary_year["cenario_t42"].eq("BASE_T36C")][["ano", "alfa_vs_ibov"]].rename(columns={"alfa_vs_ibov": "alfa_base_ano"})
    summary_year = summary_year.merge(base_year, on="ano", how="left")
    summary_year["delta_alfa_vs_base"] = summary_year["alfa_vs_ibov"] - summary_year["alfa_base_ano"]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        pd.DataFrame([{"cenario_t42": k, **v} for k, v in SCENARIOS.items()]).to_excel(writer, sheet_name="Descricao Cenarios", index=False)
        summary.to_excel(writer, sheet_name="Resumo Geral", index=False)
        summary_year.to_excel(writer, sheet_name="Resumo Ano", index=False)
        summary_pred_regime.to_excel(writer, sheet_name="Resumo Regime Previsto", index=False)
        summary_real_regime.to_excel(writer, sheet_name="Resumo Regime Real", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        comparison.to_excel(writer, sheet_name="Comparativo Mensal", index=False)
        months_4m.to_excel(writer, sheet_name="Meses Usando 4M", index=False)
        portfolio.to_excel(writer, sheet_name="Carteiras", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)

    log("Teste 42 - Falso Alerta de Queda com Janela 4M")
    log("Regra: comparar detectores que usam 4M apenas quando a queda prevista parece falso alerta/rotacao de qualidade.")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log("")
    log("Resumo geral:")
    log(summary[["cenario_t42", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "drawdown", "meses_usou_4m", "delta_alfa_vs_base"]].to_string(index=False))
    log("")
    log("Resumo por ano:")
    log(summary_year[["cenario_t42", "ano", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "meses_usou_4m", "delta_alfa_vs_base"]].to_string(index=False))
    log("")
    log("Meses em que cada detector acionou 4M:")
    cols = ["cenario_t42", "mes", "regime_previsto_norm", "tipo_regime_expost", "retorno_total", "retorno_expost_ibov", "alfa_vs_ibov", "motivo_acionamento_4m"]
    log(months_4m[cols].to_string(index=False) if not months_4m.empty else "Nenhum acionamento.")
    invalid = validation[(~validation["pesos_fecham_100"]) | (~validation["retorno_consistente"])]
    log("")
    log(f"Validacao: {'OK' if invalid.empty else 'FALHAS=' + str(len(invalid))}")
    if not invalid.empty:
        log(invalid.to_string(index=False))
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
