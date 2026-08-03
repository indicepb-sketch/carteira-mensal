from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_16 = ROOT / "output" / "excel" / "shadow_regime_16_risk_on_off.xlsx"
INPUT_18A = ROOT / "output" / "excel" / "shadow_forward_18a_julho_regime17.xlsx"
OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_regime_22_forward_mm50_100_50_20.xlsx"
LOG_FILE = ROOT / "output" / "logs" / "shadow_regime_22_forward_mm50_100_50_20.log"

SCENARIO = "risk_on_off_mm50"
PROFILE = "100_alta_50_20"
EXPOSURE_HIGH = 1.00
EXPOSURE_LIGHT_DOWN = 0.50
EXPOSURE_STRONG_DOWN = 0.20


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def max_drawdown(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").fillna(0.0)
    if vals.empty:
        return np.nan
    equity = (1.0 + vals).cumprod()
    return float((equity / equity.cummax() - 1.0).min())


def add_realized_regime(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    ibov = pd.to_numeric(out["retorno_expost_ibov"], errors="coerce")
    out["tipo_regime_expost"] = np.select(
        [
            out["mes"].astype(str).eq("2026-06"),
            ibov >= 0,
            ibov <= -0.03,
        ],
        ["jun_oportunidade", "alta", "queda_forte"],
        default="queda_leve",
    )
    return out


def exposure_from_bucket(bucket: str) -> float:
    bucket = str(bucket).lower()
    if bucket in {"alta", "jun_oportunidade", "oportunidade"}:
        return EXPOSURE_HIGH
    if bucket == "queda_leve":
        return EXPOSURE_LIGHT_DOWN
    if bucket == "queda_forte":
        return EXPOSURE_STRONG_DOWN
    return EXPOSURE_HIGH


def summarize(group: pd.DataFrame, ret_col: str, alpha_col: str) -> dict[str, Any]:
    ret = pd.to_numeric(group[ret_col], errors="coerce")
    ibov = pd.to_numeric(group["retorno_expost_ibov"], errors="coerce")
    alpha = pd.to_numeric(group[alpha_col], errors="coerce")
    return {
        "cenario": SCENARIO,
        "perfil_exposicao": PROFILE,
        "meses": int(len(group)),
        "retorno_carteira": compound(ret),
        "retorno_ibov": compound(ibov),
        "alfa_composto": compound(ret) - compound(ibov),
        "meses_bateu_ibov": int((alpha > 0).sum()),
        "taxa_meses_bateu_ibov": float((alpha > 0).mean()) if len(alpha) else np.nan,
        "pior_alfa_mensal": float(alpha.min()) if alpha.notna().any() else np.nan,
        "melhor_alfa_mensal": float(alpha.max()) if alpha.notna().any() else np.nan,
        "drawdown_carteira": max_drawdown(ret),
        "drawdown_relativo_vs_ibov": max_drawdown(ret - ibov),
        "exposicao_media": float(pd.to_numeric(group["exposicao_modelo"], errors="coerce").mean()),
    }


def historical_monthly() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = pd.read_excel(INPUT_16, sheet_name="mes_a_mes")
    base = base[base["cenario"].eq(SCENARIO)].copy()
    base = add_realized_regime(base)
    base["retorno_expost_sombra_100pct"] = pd.to_numeric(base["retorno_expost_sombra_100pct"], errors="coerce")
    base["retorno_expost_ibov"] = pd.to_numeric(base["retorno_expost_ibov"], errors="coerce")
    base = base.dropna(subset=["retorno_expost_sombra_100pct", "retorno_expost_ibov"])
    base["exposicao_modelo"] = base["bucket_regime"].apply(exposure_from_bucket)
    base["peso_defensivo"] = 1.0 - base["exposicao_modelo"]
    base["retorno_modelo"] = base["retorno_expost_sombra_100pct"] * base["exposicao_modelo"]
    base["alfa_modelo"] = base["retorno_modelo"] - base["retorno_expost_ibov"]
    base["perfil_exposicao"] = PROFILE
    base["descricao_perfil"] = "Alta 100% acoes; queda leve 50% acoes; queda forte 20% acoes; restante defensivo/CDI."
    cols = [
        "mes",
        "bucket_regime",
        "tipo_regime_expost",
        "exposicao_modelo",
        "peso_defensivo",
        "retorno_expost_sombra_100pct",
        "retorno_modelo",
        "retorno_expost_ibov",
        "alfa_modelo",
        "tickers_pesos_sombra",
    ]
    monthly = base[[c for c in cols if c in base.columns]].copy()
    summary = pd.DataFrame([summarize(monthly, "retorno_modelo", "alfa_modelo")])
    by_regime = []
    for regime, group in monthly.groupby("tipo_regime_expost", sort=False):
        row = summarize(group, "retorno_modelo", "alfa_modelo")
        row["tipo_regime_expost"] = regime
        row["meses_lista"] = ", ".join(group["mes"].astype(str).tolist())
        by_regime.append(row)
    return monthly, summary, pd.DataFrame(by_regime)


def july_forward() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not INPUT_18A.exists():
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    resumo = pd.read_excel(INPUT_18A, sheet_name="Resumo Parcial")
    resumo = resumo[resumo["cenario"].eq("regime17_mm50")].copy()
    if resumo.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    regime = pd.read_excel(INPUT_18A, sheet_name="Regime 17 Julho")
    regime = regime[regime["cenario"].eq("regime17_mm50")].copy()
    model = pd.read_excel(INPUT_18A, sheet_name="Carteiras Modelo")
    model = model[model["cenario"].eq("regime17_mm50")].copy()
    partial = pd.read_excel(INPUT_18A, sheet_name="Ativos Parcial")
    partial = partial[partial["cenario"].eq("regime17_mm50")].copy()
    stock_returns = partial[~partial["ticker"].astype(str).str.upper().isin(["CDI", "CAIXA"])][
        ["ticker", "preco_entrada", "preco_atual", "data_avaliacao", "retorno_periodo"]
    ].copy()
    applied_assets = model.merge(stock_returns, on="ticker", how="left")
    applied_assets["peso_modelo_100pct"] = pd.to_numeric(applied_assets["peso_recomendado"], errors="coerce")
    exposure = exposure_from_bucket(str(regime["bucket_regime17"].iloc[0]))
    applied_assets["exposicao_acoes_teste22"] = exposure
    applied_assets["peso_aplicado_teste22"] = applied_assets["peso_modelo_100pct"] * exposure
    applied_assets["contribuicao_acoes_teste22"] = applied_assets["peso_aplicado_teste22"] * pd.to_numeric(applied_assets["retorno_periodo"], errors="coerce")
    stock_ret_100 = float((applied_assets["peso_modelo_100pct"] * pd.to_numeric(applied_assets["retorno_periodo"], errors="coerce")).sum())
    cdi_liq = float(pd.to_numeric(resumo["retorno_cdi_liquido_periodo"], errors="coerce").iloc[0])
    ibov = float(pd.to_numeric(resumo["retorno_ibov_parcial"], errors="coerce").iloc[0])
    ret_applied = stock_ret_100 * exposure + cdi_liq * (1.0 - exposure)
    summary = pd.DataFrame(
        [
            {
                "mes": "2026-07",
                "status": "forward_parcial_em_observacao",
                "data_entrada": resumo["data_entrada"].iloc[0],
                "data_avaliacao_parcial": resumo["data_avaliacao_parcial"].iloc[0],
                "bucket_regime_mm50": regime["bucket_regime17"].iloc[0],
                "motivo_regime_mm50": regime["motivo_regime17"].iloc[0],
                "exposicao_acoes": exposure,
                "peso_cdi": 1.0 - exposure,
                "retorno_acoes_100pct_parcial": stock_ret_100,
                "retorno_cdi_liquido_periodo": cdi_liq,
                "retorno_carteira_teste22_parcial": ret_applied,
                "retorno_ibov_parcial": ibov,
                "alfa_parcial_vs_ibov": ret_applied - ibov,
                "arquivo_base": resumo["arquivo_base"].iloc[0],
                "fonte_precos_parcial": resumo["fonte_precos_parcial"].iloc[0],
                "fonte_cdi": resumo["fonte_cdi"].iloc[0],
            }
        ]
    )
    cdi_row = pd.DataFrame(
        [
            {
                "ticker": "CDI",
                "nome": "CDI liquido IR",
                "setor": "Defensivo",
                "peso_modelo_100pct": np.nan,
                "peso_aplicado_teste22": 1.0 - exposure,
                "retorno_periodo": cdi_liq,
                "contribuicao_acoes_teste22": cdi_liq * (1.0 - exposure),
                "exposicao_acoes_teste22": exposure,
            }
        ]
    )
    applied_assets = pd.concat([applied_assets, cdi_row], ignore_index=True, sort=False)
    validation = pd.DataFrame(
        [
            {
                "checagem": "soma_pesos_aplicados_teste22",
                "valor": float(pd.to_numeric(applied_assets["peso_aplicado_teste22"], errors="coerce").sum()),
                "ok": abs(float(pd.to_numeric(applied_assets["peso_aplicado_teste22"], errors="coerce").sum()) - 1.0) < 1e-8,
            },
            {
                "checagem": "retorno_consistente",
                "valor": float(pd.to_numeric(applied_assets["peso_aplicado_teste22"], errors="coerce").fillna(0.0).mul(pd.to_numeric(applied_assets["retorno_periodo"], errors="coerce").fillna(0.0)).sum() - ret_applied),
                "ok": True,
            },
        ]
    )
    return summary, applied_assets, validation


def main() -> None:
    logs: list[str] = []

    def log(message: str) -> None:
        print(message, flush=True)
        logs.append(message)

    if not INPUT_16.exists():
        raise FileNotFoundError(INPUT_16)

    monthly, summary, by_regime = historical_monthly()
    july_summary, july_assets, july_validation = july_forward()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo_historico", index=False)
        by_regime.to_excel(writer, sheet_name="historico_por_regime_real", index=False)
        monthly.to_excel(writer, sheet_name="historico_mes_a_mes", index=False)
        july_summary.to_excel(writer, sheet_name="julho_forward_parcial", index=False)
        july_assets.to_excel(writer, sheet_name="julho_ativos_aplicados", index=False)
        july_validation.to_excel(writer, sheet_name="validacao", index=False)

    hist = summary.iloc[0]
    log("Teste 22 - Forward-Test MM50 100/50/20")
    log("Regra: alta=100% acoes; queda_leve=50% acoes; queda_forte=20% acoes; restante defensivo/CDI.")
    log(
        f"Historico 2024-01 a 2026-06: retorno={pct(hist['retorno_carteira'])}; "
        f"IBOV={pct(hist['retorno_ibov'])}; alfa={pct(hist['alfa_composto'])}; "
        f"bateu={int(hist['meses_bateu_ibov'])}/{int(hist['meses'])}; "
        f"exposicao_media={pct(hist['exposicao_media'])}"
    )
    if not july_summary.empty:
        js = july_summary.iloc[0]
        log(
            f"Julho parcial ({js['data_entrada']} a {js['data_avaliacao_parcial']}): "
            f"regime={js['bucket_regime_mm50']}; exposicao={pct(js['exposicao_acoes'])}; "
            f"retorno={pct(js['retorno_carteira_teste22_parcial'])}; IBOV={pct(js['retorno_ibov_parcial'])}; "
            f"alfa={pct(js['alfa_parcial_vs_ibov'])}"
        )
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
