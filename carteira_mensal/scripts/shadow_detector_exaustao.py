from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from main import _market_breadth_rows, _market_classification  # noqa: E402

OUTPUT_DIR = ROOT / "output" / "excel"
OUTPUT_FILE = OUTPUT_DIR / "shadow_detector_exaustao.xlsx"
LOG_FILE = OUTPUT_DIR / "shadow_detector_exaustao.log"

MONTHS_2025 = [f"2025-{m:02d}" for m in range(1, 13)]
MONTHS_2026 = [f"2026-{m:02d}" for m in range(1, 7)]
ALL_MONTHS = MONTHS_2025 + MONTHS_2026
FILES_2026 = {
    "2026-01": "carteira_recomendada_2026_01_v1.xlsx",
    "2026-02": "carteira_recomendada_2026_02_v4.xlsx",
    "2026-03": "carteira_recomendada_2026_03_v4.xlsx",
    "2026-04": "carteira_recomendada_2026_04_v2.xlsx",
    "2026-05": "carteira_recomendada_2026_05_v3.xlsx",
    "2026-06": "carteira_recomendada_2026_06_v4.xlsx",
}
BASELINE_EXPOSURE = {"alta": 1.00, "queda_leve": 0.60, "queda_forte": 0.30, "indefinido": 1.00}


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def workbook_for_month(mes: str) -> Path:
    if mes.startswith("2025-"):
        path = OUTPUT_DIR / f"carteira_historica_{mes.replace('-', '_')}.xlsx"
    else:
        path = OUTPUT_DIR / FILES_2026[mes]
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def read_sheet(path: Path, sheet: str) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet)
    except Exception:
        return pd.DataFrame()


def load_base_returns() -> pd.DataFrame:
    rows = []
    df25 = pd.read_excel(OUTPUT_DIR / "shadow_backtest_2025.xlsx", sheet_name="mes_a_mes")
    for _, row in df25.iterrows():
        rows.append({
            "mes": str(row["mes"]),
            "retorno_carteira_100pct": row.get("retorno_expost_sombra_100pct", np.nan),
            "retorno_ibov": row.get("retorno_expost_ibov", np.nan),
            "carteira_base": row.get("tickers_pesos_sombra", ""),
        })
    df26 = pd.read_excel(OUTPUT_DIR / "shadow_exposicao_por_regime.xlsx", sheet_name="detalhe_mes_cenario")
    df26 = df26[df26["cenario"].eq("BASELINE_100")]
    for _, row in df26.iterrows():
        rows.append({
            "mes": str(row["mes"]),
            "retorno_carteira_100pct": row.get("retorno_carteira_100pct", np.nan),
            "retorno_ibov": row.get("retorno_ibov", np.nan),
            "carteira_base": row.get("tickers_pesos_100pct", ""),
        })
    return pd.DataFrame(rows).drop_duplicates("mes").sort_values("mes")


def month_indicators(mes: str) -> dict[str, Any]:
    path = workbook_for_month(mes)
    prelim = read_sheet(path, "Analise Preliminar")
    rows, total, favorable, pct_fav = _market_breadth_rows(prelim)
    market_class, pct_calc = _market_classification(favorable, total)
    rowmap = {str(r.get("indicador", "")): r for r in rows}
    pct_pos = float(rowmap.get("ativos com retorno positivo no mes", {}).get("percentual", np.nan))
    pct_mm9 = float(rowmap.get("ativos com MM9 > MM21", {}).get("percentual", np.nan))
    pct_mm50 = float(rowmap.get("ativos com preco acima da MM50", {}).get("percentual", np.nan))
    bucket = "alta" if market_class == "mercado favoravel" else "queda_leve" if market_class == "mercado seletivo" else "queda_forte"
    rsi = pd.to_numeric(prelim.get("rsi", pd.Series(np.nan, index=prelim.index)), errors="coerce")
    ret1 = pd.to_numeric(prelim.get("retorno_acumulado_1m", pd.Series(np.nan, index=prelim.index)), errors="coerce")
    ret4 = pd.to_numeric(prelim.get("retorno_acumulado_4m", pd.Series(np.nan, index=prelim.index)), errors="coerce")
    boll = prelim.get("bollinger_status", pd.Series("", index=prelim.index)).astype(str).str.lower()
    return {
        "mes": mes,
        "arquivo": path.name,
        "market_class_producao": market_class,
        "bucket_regime": bucket,
        "total_ativos": total,
        "ativos_tendencia_favoravel": favorable,
        "pct_tendencia_favoravel": pct_calc,
        "pct_ativos_positivos_1m": pct_pos,
        "pct_mm9_maior_mm21": pct_mm9,
        "pct_preco_acima_mm50": pct_mm50,
        "rsi_mediano": float(rsi.median()) if rsi.notna().any() else np.nan,
        "pct_rsi_acima_70": float((rsi > 70).mean()) if len(rsi) else np.nan,
        "pct_bollinger_sobrecompra": float(boll.str.contains("sobrecompra", na=False).mean()) if len(boll) else np.nan,
        "retorno_1m_mediano": float(ret1.median()) if ret1.notna().any() else np.nan,
        "retorno_4m_mediano": float(ret4.median()) if ret4.notna().any() else np.nan,
    }


def add_exhaustion_signals(ind: pd.DataFrame) -> pd.DataFrame:
    out = ind.copy().sort_values("mes")
    for col in ["pct_tendencia_favoravel", "pct_mm9_maior_mm21", "pct_preco_acima_mm50", "pct_ativos_positivos_1m"]:
        out[f"prev_{col}"] = out[col].shift(1)
        out[f"delta_{col}"] = out[col] - out[f"prev_{col}"]
    favorable = out["bucket_regime"].eq("alta")
    signals = {
        "queda_amplitude_tendencia_10pp": favorable & (out["delta_pct_tendencia_favoravel"] <= -0.10),
        "queda_mm9_mm21_10pp": favorable & (out["delta_pct_mm9_maior_mm21"] <= -0.10),
        "queda_preco_acima_mm50_10pp": favorable & (out["delta_pct_preco_acima_mm50"] <= -0.10),
        "participacao_positiva_1m_fraca": favorable & (out["pct_ativos_positivos_1m"] < 0.45),
        "divergencia_tendencia_alta_mes_fraco": favorable & (out["pct_tendencia_favoravel"] > 0.55) & (out["pct_ativos_positivos_1m"] < 0.50),
        "rsi_mediano_estendido": favorable & (out["rsi_mediano"] >= 60),
        "muitos_rsi_sobrecomprados": favorable & (out["pct_rsi_acima_70"] >= 0.20),
        "bollinger_sobrecompra_disseminada": favorable & (out["pct_bollinger_sobrecompra"] >= 0.20),
        "retorno_1m_mediano_negativo": favorable & (out["retorno_1m_mediano"] < 0),
        "ret4_alto_ret1_fraco": favorable & (out["retorno_4m_mediano"] > 0.10) & (out["retorno_1m_mediano"] < 0.02),
    }
    for name, mask in signals.items():
        out[name] = mask.fillna(False)
    signal_cols = list(signals)
    out["exaustao_score"] = out[signal_cols].sum(axis=1).astype(int)
    out["sinais_exaustao"] = out.apply(lambda r: "; ".join(c for c in signal_cols if bool(r[c])), axis=1)
    return out


def exposure_for(scenario: str, bucket: str, score: int) -> tuple[float, str]:
    base = BASELINE_EXPOSURE.get(bucket, 1.0)
    if scenario == "BASELINE_ATUAL":
        return base, "exposicao atual por regime"
    if bucket != "alta":
        return base, "nao favoravel: mantem regra defensiva atual"
    if scenario == "EXAUSTAO_CONSERVADOR":
        if score >= 5:
            return 0.60, "exaustao forte em mercado favoravel: exposicao 60%"
        return base, "sem exaustao suficiente"
    if scenario == "EXAUSTAO_MODERADO":
        if score >= 5:
            return 0.50, "exaustao forte: exposicao 50%"
        if score >= 3:
            return 0.70, "exaustao moderada: exposicao 70%"
        return base, "sem exaustao suficiente"
    if scenario == "EXAUSTAO_AGRESSIVO":
        if score >= 5:
            return 0.40, "exaustao forte: exposicao 40%"
        if score >= 3:
            return 0.60, "exaustao moderada: exposicao 60%"
        if score >= 2:
            return 0.80, "exaustao inicial: exposicao 80%"
        return base, "sem exaustao suficiente"
    return base, "cenario desconhecido"


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    base_returns = load_base_returns()
    ind = add_exhaustion_signals(pd.DataFrame([month_indicators(m) for m in ALL_MONTHS]))
    data = ind.merge(base_returns, on="mes", how="left")
    scenarios = ["BASELINE_ATUAL", "EXAUSTAO_CONSERVADOR", "EXAUSTAO_MODERADO", "EXAUSTAO_AGRESSIVO"]
    rows = []
    for _, item in data.iterrows():
        for scenario in scenarios:
            exposure, reason = exposure_for(scenario, str(item["bucket_regime"]), int(item["exaustao_score"]))
            ret100 = item.get("retorno_carteira_100pct", np.nan)
            ibov = item.get("retorno_ibov", np.nan)
            ret = ret100 * exposure if pd.notna(ret100) else np.nan
            rows.append({**item.to_dict(), "cenario": scenario, "exposicao": exposure, "peso_caixa": 1.0 - exposure, "motivo_exposicao": reason, "retorno_carteira": ret, "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan})
    detail = pd.DataFrame(rows)
    summary_rows = []
    for scenario, group in detail.groupby("cenario"):
        for label, months in {
            "TOTAL_18_MESES": ALL_MONTHS,
            "2025": MONTHS_2025,
            "2026_JAN_JUN": MONTHS_2026,
            "MESES_FAVORAVEIS": group.loc[group["bucket_regime"].eq("alta"), "mes"].tolist(),
            "MESES_COM_EXAUSTAO_SCORE_GE3": group.loc[(group["bucket_regime"].eq("alta")) & (group["exaustao_score"] >= 3), "mes"].tolist(),
        }.items():
            subset = group[group["mes"].isin(months)]
            ret = compound(subset["retorno_carteira"])
            ibov = compound(subset["retorno_ibov"])
            summary_rows.append({"cenario": scenario, "grupo": label, "meses": ", ".join(subset["mes"].astype(str)), "retorno_carteira": ret, "retorno_ibov": ibov, "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan})
    summary = pd.DataFrame(summary_rows)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        detail.to_excel(writer, sheet_name="detalhe_mes_cenario", index=False)
        ind.to_excel(writer, sheet_name="diagnostico_exaustao", index=False)
    log("Teste 7 - Detector de Exaustao / Virada Para Baixo")
    for _, row in summary[summary["grupo"].eq("TOTAL_18_MESES")].iterrows():
        log(f"{row['cenario']}: retorno={pct(row['retorno_carteira'])} | IBOV={pct(row['retorno_ibov'])} | alfa={pct(row['alfa'])}")
    log("Meses favoraveis com exaustao_score >= 3:")
    for _, row in ind[(ind["bucket_regime"].eq("alta")) & (ind["exaustao_score"] >= 3)].iterrows():
        log(f"  {row['mes']}: score={int(row['exaustao_score'])} | carteira100={pct(data.loc[data['mes'].eq(row['mes']), 'retorno_carteira_100pct'].iloc[0])} | IBOV={pct(data.loc[data['mes'].eq(row['mes']), 'retorno_ibov'].iloc[0])} | sinais={row['sinais_exaustao']}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
