from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_33 = EXCEL_DIR / "shadow_teste33_recuperacao_restritas_cap_efetivo.xlsx"
INPUT_EXPOST = EXCEL_DIR / "shadow_regime_16_risk_on_off.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste34_cdi_residual.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste34_cdi_residual.log"

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


def cdi_fallback_from_settings(start: pd.Timestamp, end: pd.Timestamp, annual_rate: float = 0.15) -> tuple[float, pd.DataFrame, str]:
    days = max(int((end.normalize() - start.normalize()).days), 1)
    gross = (1.0 + float(annual_rate)) ** (days / 365.0) - 1.0
    return gross, pd.DataFrame(), f"fallback_taxa_anual_configurada_{annual_rate:.4f}"


def load_month_dates() -> pd.DataFrame:
    expost = pd.read_excel(INPUT_EXPOST, sheet_name="expost_universo")
    cols = ["mes", "data_inicio_performance", "data_avaliacao", "retorno_ibov_periodo"]
    dates = expost[cols].drop_duplicates("mes").copy()
    dates["data_inicio_performance"] = pd.to_datetime(dates["data_inicio_performance"], errors="coerce")
    dates["data_avaliacao"] = pd.to_datetime(dates["data_avaliacao"], errors="coerce")
    dates["retorno_ibov_periodo"] = pd.to_numeric(dates["retorno_ibov_periodo"], errors="coerce")
    return dates


def build_cdi_periods(months: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    daily_frames: list[pd.DataFrame] = []
    for _, row in months.iterrows():
        mes = str(row["mes"])
        start = pd.Timestamp(row["data_inicio_performance"]).normalize()
        end = pd.Timestamp(row["data_avaliacao"]).normalize()
        gross = np.nan
        source = ""
        daily = pd.DataFrame()
        try:
            gross, daily, source = fp.fetch_cdi_gross_return(start + pd.Timedelta(days=1), end)
        except Exception as exc:  # noqa: BLE001 - fallback is reported in the output
            gross, daily, source = cdi_fallback_from_settings(start + pd.Timedelta(days=1), end)
            source = f"{source}; erro_bcb_sgs_12={exc}"
        net, ir_rate, days = fp.cdi_net_return(gross, start, end)
        rows.append(
            {
                "mes": mes,
                "data_inicio_performance": start.date().isoformat(),
                "data_avaliacao": end.date().isoformat(),
                "retorno_cdi_bruto_periodo": gross,
                "retorno_cdi_liquido_periodo": net,
                "aliquota_ir_cdi": ir_rate,
                "dias_corridos_cdi": days,
                "fonte_cdi": source,
            }
        )
        if not daily.empty:
            d = daily.copy()
            d.insert(0, "mes", mes)
            daily_frames.append(d)
    daily_all = pd.concat(daily_frames, ignore_index=True, sort=False) if daily_frames else pd.DataFrame()
    return pd.DataFrame(rows), daily_all


def summarize(group: pd.DataFrame, scenario_col: str = "cenario_teste34") -> dict[str, Any]:
    ret_zero = pd.to_numeric(group["retorno_modelo_zero"], errors="coerce")
    ret_cdi = pd.to_numeric(group["retorno_modelo_cdi_liquido"], errors="coerce")
    ibov = pd.to_numeric(group["retorno_expost_ibov"], errors="coerce")
    alpha_zero = pd.to_numeric(group["alfa_zero"], errors="coerce")
    alpha_cdi = pd.to_numeric(group["alfa_cdi_liquido"], errors="coerce")
    return {
        scenario_col: str(group[scenario_col].iloc[0]),
        "meses": int(len(group)),
        "retorno_zero": compound(ret_zero),
        "retorno_cdi_liquido": compound(ret_cdi),
        "retorno_ibov": compound(ibov),
        "alfa_zero": compound(ret_zero) - compound(ibov),
        "alfa_cdi_liquido": compound(ret_cdi) - compound(ibov),
        "delta_retorno_cdi_vs_zero": compound(ret_cdi) - compound(ret_zero),
        "meses_bateu_ibov_zero": int((alpha_zero > 0).sum()),
        "meses_bateu_ibov_cdi": int((alpha_cdi > 0).sum()),
        "taxa_acerto_zero": float((alpha_zero > 0).mean()) if len(alpha_zero) else np.nan,
        "taxa_acerto_cdi": float((alpha_cdi > 0).mean()) if len(alpha_cdi) else np.nan,
        "drawdown_zero": max_drawdown(ret_zero),
        "drawdown_cdi": max_drawdown(ret_cdi),
        "exposicao_media_acoes_efetiva": float(pd.to_numeric(group["exposicao_acoes_efetiva"], errors="coerce").mean()),
        "residual_medio_cdi": float(pd.to_numeric(group["peso_residual_cdi"], errors="coerce").mean()),
    }


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    if not INPUT_33.exists():
        raise FileNotFoundError(INPUT_33)
    if not INPUT_EXPOST.exists():
        raise FileNotFoundError(INPUT_EXPOST)

    monthly = pd.read_excel(INPUT_33, sheet_name="mes_a_mes_bruto")
    validation = pd.read_excel(INPUT_33, sheet_name="validacao")
    portfolios = pd.read_excel(INPUT_33, sheet_name="carteiras")
    dates = load_month_dates()
    cdi_periods, cdi_daily = build_cdi_periods(dates)

    base_cols = ["cenario_teste33", "mes", "soma_pesos", "maior_peso_acoes", "maior_peso_efetivo", "pesos_ok"]
    weight_info = validation[[c for c in base_cols if c in validation.columns]].copy()
    out = monthly.merge(weight_info, on=["cenario_teste33", "mes"], how="left")
    out = out.merge(cdi_periods, on="mes", how="left")
    out = out.rename(columns={"cenario_teste33": "cenario_teste34"})

    out["soma_pesos_acoes_bruta"] = pd.to_numeric(out.get("soma_pesos", 1.0), errors="coerce").fillna(1.0)
    out["exposicao_modelo"] = pd.to_numeric(out["exposicao_modelo"], errors="coerce").fillna(1.0)
    out["exposicao_acoes_efetiva"] = (out["soma_pesos_acoes_bruta"] * out["exposicao_modelo"]).clip(lower=0, upper=1)
    out["peso_residual_cdi"] = (1.0 - out["exposicao_acoes_efetiva"]).clip(lower=0, upper=1)
    out["retorno_modelo_zero"] = pd.to_numeric(out["retorno_modelo"], errors="coerce")
    out["retorno_cdi_liquido_periodo"] = pd.to_numeric(out["retorno_cdi_liquido_periodo"], errors="coerce").fillna(0.0)
    out["contribuicao_cdi_liquido"] = out["peso_residual_cdi"] * out["retorno_cdi_liquido_periodo"]
    out["retorno_modelo_cdi_liquido"] = out["retorno_modelo_zero"] + out["contribuicao_cdi_liquido"]
    out["retorno_expost_ibov"] = pd.to_numeric(out["retorno_expost_ibov"], errors="coerce")
    out["alfa_zero"] = out["retorno_modelo_zero"] - out["retorno_expost_ibov"]
    out["alfa_cdi_liquido"] = out["retorno_modelo_cdi_liquido"] - out["retorno_expost_ibov"]
    out["delta_alfa_cdi_vs_zero"] = out["alfa_cdi_liquido"] - out["alfa_zero"]

    summary = pd.DataFrame([summarize(g) for _, g in out.groupby("cenario_teste34", sort=False)])
    by_regime = pd.DataFrame(
        [summarize(g) | {"tipo_regime_expost": keys[1]} for keys, g in out.groupby(["cenario_teste34", "tipo_regime_expost"], sort=False)]
    )
    residual_months = out[out["peso_residual_cdi"].gt(1e-9)].copy()
    validation_out = out[
        [
            "cenario_teste34", "mes", "soma_pesos_acoes_bruta", "exposicao_modelo", "exposicao_acoes_efetiva",
            "peso_residual_cdi", "maior_peso_acoes", "maior_peso_efetivo", "retorno_modelo_zero",
            "contribuicao_cdi_liquido", "retorno_modelo_cdi_liquido", "retorno_expost_ibov", "alfa_cdi_liquido",
            "fonte_cdi",
        ]
    ].copy()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        by_regime.to_excel(writer, sheet_name="por_regime_real", index=False)
        out.to_excel(writer, sheet_name="mes_a_mes_cdi", index=False)
        residual_months.to_excel(writer, sheet_name="meses_com_residual", index=False)
        cdi_periods.to_excel(writer, sheet_name="cdi_periodos", index=False)
        cdi_daily.to_excel(writer, sheet_name="cdi_diario_bcb", index=False)
        validation_out.to_excel(writer, sheet_name="validacao_residual", index=False)
        portfolios.to_excel(writer, sheet_name="carteiras_base_t33", index=False)

    log("Teste 34 - Retorno com Caixa/CDI no Residual de Exposicao")
    log("Fonte primaria CDI: BCB/SGS serie 12; liquido de IR pela tabela regressiva, todos os meses <=180 dias => 22,5%.")
    for _, row in summary.iterrows():
        log(
            f"  {row['cenario_teste34']}: zero={pct(row['retorno_zero'])}; CDI={pct(row['retorno_cdi_liquido'])}; "
            f"IBOV={pct(row['retorno_ibov'])}; alfa_zero={pct(row['alfa_zero'])}; alfa_cdi={pct(row['alfa_cdi_liquido'])}; "
            f"delta_CDI={pct(row['delta_retorno_cdi_vs_zero'])}; acerto_zero={int(row['meses_bateu_ibov_zero'])}/{int(row['meses'])}; "
            f"acerto_cdi={int(row['meses_bateu_ibov_cdi'])}/{int(row['meses'])}; drawdown_cdi={pct(row['drawdown_cdi'])}; "
            f"residual_medio={pct(row['residual_medio_cdi'])}"
        )
    max_residual = float(out["peso_residual_cdi"].max()) if not out.empty else np.nan
    log(f"Maior peso residual em CDI: {pct(max_residual)}")
    log(f"Meses-cenario com residual > 0: {int(out['peso_residual_cdi'].gt(1e-9).sum())}/{len(out)}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
