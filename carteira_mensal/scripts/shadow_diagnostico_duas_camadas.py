from __future__ import annotations

import re
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
OUTPUT_FILE = OUTPUT_DIR / "shadow_diagnostico_duas_camadas.xlsx"
LOG_FILE = OUTPUT_DIR / "shadow_diagnostico_duas_camadas.log"

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

BASELINE_EXPOSURE = {"alta": 1.00, "oportunidade": 1.00, "queda_leve": 0.60, "queda_forte": 0.30, "indefinido": 1.00}


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
    p2025 = OUTPUT_DIR / "shadow_backtest_2025.xlsx"
    df25 = pd.read_excel(p2025, sheet_name="mes_a_mes")
    for _, row in df25.iterrows():
        rows.append({
            "mes": str(row["mes"]),
            "retorno_carteira_100pct": row.get("retorno_expost_sombra_100pct", np.nan),
            "retorno_ibov": row.get("retorno_expost_ibov", np.nan),
            "carteira_base": row.get("tickers_pesos_sombra", ""),
        })
    p2026 = OUTPUT_DIR / "shadow_exposicao_por_regime.xlsx"
    df26 = pd.read_excel(p2026, sheet_name="detalhe_mes_cenario")
    df26 = df26[df26["cenario"].eq("BASELINE_100")]
    for _, row in df26.iterrows():
        rows.append({
            "mes": str(row["mes"]),
            "retorno_carteira_100pct": row.get("retorno_carteira_100pct", np.nan),
            "retorno_ibov": row.get("retorno_ibov", np.nan),
            "carteira_base": row.get("tickers_pesos_100pct", ""),
        })
    return pd.DataFrame(rows).drop_duplicates("mes").sort_values("mes")


def indicators_for_month(mes: str, prev_pct_fav: float | None) -> dict[str, Any]:
    path = workbook_for_month(mes)
    prelim = read_sheet(path, "Analise Preliminar")
    if prelim.empty:
        return {"mes": mes, "bucket_regime": "indefinido", "motivo_regime": "Analise Preliminar ausente"}

    rows, total, favorable, pct_fav = _market_breadth_rows(prelim)
    market_class, pct_calc = _market_classification(favorable, total)
    rowmap = {str(r.get("indicador", "")): r for r in rows}
    pct_positive_month = float(rowmap.get("ativos com retorno positivo no mes", {}).get("percentual", np.nan))
    pct_mm9_gt_mm21 = float(rowmap.get("ativos com MM9 > MM21", {}).get("percentual", np.nan))
    pct_price_above_mm50 = float(rowmap.get("ativos com preco acima da MM50", {}).get("percentual", np.nan))
    rsi = pd.to_numeric(prelim.get("rsi", pd.Series(np.nan, index=prelim.index)), errors="coerce")
    rsi_median = float(rsi.median()) if rsi.notna().any() else np.nan
    ret1 = pd.to_numeric(prelim.get("retorno_acumulado_1m", pd.Series(np.nan, index=prelim.index)), errors="coerce")
    ret4 = pd.to_numeric(prelim.get("retorno_acumulado_4m", pd.Series(np.nan, index=prelim.index)), errors="coerce")
    boll = prelim.get("bollinger_status", pd.Series("", index=prelim.index)).astype(str).str.lower()
    timing = prelim.get("tipo_timing", pd.Series("", index=prelim.index)).astype(str).str.lower()
    reversal_mask = (rsi <= 40) | boll.str.contains("oportunidade", na=False) | timing.str.contains("reversao_oportunidade", na=False)
    pct_reversal = float(reversal_mask.fillna(False).mean()) if total else np.nan
    improving = pd.notna(pct_fav) and prev_pct_fav is not None and pd.notna(prev_pct_fav) and (pct_fav - prev_pct_fav >= 0.05)
    near_boundary = pd.notna(pct_fav) and pct_fav >= 0.15
    positive_breadth = pd.notna(pct_positive_month) and pct_positive_month >= 0.40
    mm_turn = pd.notna(pct_mm9_gt_mm21) and pct_mm9_gt_mm21 >= 0.35
    rsi_recovery = pd.notna(rsi_median) and 35 <= rsi_median <= 55
    reversal_enough = pd.notna(pct_reversal) and pct_reversal >= 0.15
    ret1_recovery = pd.notna(ret1.median()) and ret1.median() > -0.02
    ret4_not_destroyed = pd.notna(ret4.median()) and ret4.median() > -0.10
    signals = {
        "amplitude_perto_limite_15pct": near_boundary,
        "amplitude_melhorando_5pp": improving,
        "ativos_positivos_1m_40pct": positive_breadth,
        "mm9_gt_mm21_35pct": mm_turn,
        "rsi_mediano_recuperacao_35_55": rsi_recovery,
        "reversao_ou_bollinger_15pct": reversal_enough,
        "retorno_1m_mediano_maior_menos2pct": ret1_recovery,
        "retorno_4m_mediano_maior_menos10pct": ret4_not_destroyed,
    }
    virada_score = int(sum(bool(v) for v in signals.values()))
    if market_class == "mercado favoravel":
        bucket = "alta"
    elif market_class == "mercado seletivo":
        bucket = "queda_leve"
    else:
        bucket = "queda_forte"
    return {
        "mes": mes,
        "arquivo": path.name,
        "market_class_producao": market_class,
        "bucket_regime": bucket,
        "motivo_regime": f"{market_class}; tendencia favoravel {pct_calc:.1%}",
        "total_ativos": total,
        "ativos_tendencia_favoravel": favorable,
        "pct_tendencia_favoravel": pct_calc,
        "pct_ativos_positivos_1m": pct_positive_month,
        "pct_mm9_maior_mm21": pct_mm9_gt_mm21,
        "pct_preco_acima_mm50": pct_price_above_mm50,
        "rsi_mediano_ativos": rsi_median,
        "retorno_1m_mediano_ativos": float(ret1.median()) if ret1.notna().any() else np.nan,
        "retorno_4m_mediano_ativos": float(ret4.median()) if ret4.notna().any() else np.nan,
        "pct_reversao_bollinger_rsi": pct_reversal,
        "prev_pct_tendencia_favoravel": prev_pct_fav,
        "virada_score": virada_score,
        "sinais_virada": "; ".join(k for k, v in signals.items() if v),
    }


def exposure_for(scenario: str, bucket: str, virada_score: int) -> tuple[float, str]:
    base = BASELINE_EXPOSURE.get(bucket, 1.0)
    if scenario == "BASELINE_ATUAL":
        return base, "exposicao atual por regime"
    if bucket not in {"queda_leve", "queda_forte"}:
        return base, "mercado favoravel: sem ajuste de virada"
    if scenario == "DUAS_CAMADAS_CONSERVADOR":
        if virada_score >= 5:
            return min(1.0, base + 0.30), "virada forte: +30pp de exposicao"
        return base, "sem sinais suficientes de virada"
    if scenario == "DUAS_CAMADAS_MODERADO":
        if virada_score >= 4:
            return 1.00 if bucket == "queda_leve" else 0.70, "virada moderada/forte: sobe exposicao"
        return base, "sem sinais suficientes de virada"
    if scenario == "DUAS_CAMADAS_AGRESSIVO":
        if virada_score >= 3:
            return 1.00 if bucket == "queda_leve" else 0.80, "virada inicial: sobe exposicao agressivamente"
        return base, "sem sinais suficientes de virada"
    return base, "cenario desconhecido"


def main() -> None:
    logs: list[str] = []
    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    base_returns = load_base_returns()
    indicator_rows = []
    prev_by_year: dict[str, float | None] = {}
    for mes in ALL_MONTHS:
        year = mes[:4]
        item = indicators_for_month(mes, prev_by_year.get(year))
        prev_by_year[year] = item.get("pct_tendencia_favoravel", np.nan)
        indicator_rows.append(item)
    ind = pd.DataFrame(indicator_rows)
    data = ind.merge(base_returns, on="mes", how="left")

    scenarios = ["BASELINE_ATUAL", "DUAS_CAMADAS_CONSERVADOR", "DUAS_CAMADAS_MODERADO", "DUAS_CAMADAS_AGRESSIVO"]
    rows = []
    for _, item in data.iterrows():
        for scenario in scenarios:
            exposure, reason = exposure_for(scenario, str(item["bucket_regime"]), int(item.get("virada_score", 0)))
            ret100 = item.get("retorno_carteira_100pct", np.nan)
            ibov = item.get("retorno_ibov", np.nan)
            ret = ret100 * exposure if pd.notna(ret100) else np.nan
            rows.append({
                **item.to_dict(),
                "cenario": scenario,
                "exposicao": exposure,
                "peso_caixa": 1.0 - exposure,
                "motivo_exposicao": reason,
                "retorno_carteira": ret,
                "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan,
            })
    detail = pd.DataFrame(rows)

    summary_rows = []
    for scenario, group in detail.groupby("cenario"):
        all_ret = compound(group["retorno_carteira"])
        all_ibov = compound(group["retorno_ibov"])
        summary_rows.append({"cenario": scenario, "grupo": "TOTAL_18_MESES", "retorno_carteira": all_ret, "retorno_ibov": all_ibov, "alfa": all_ret - all_ibov})
        for label, months in {
            "2025": MONTHS_2025,
            "2026_JAN_JUN": MONTHS_2026,
            "MERCADO_FRACO_SELETIVO": group.loc[group["bucket_regime"].isin(["queda_leve", "queda_forte"]), "mes"].tolist(),
            "MERCADO_FAVORAVEL": group.loc[group["bucket_regime"].eq("alta"), "mes"].tolist(),
        }.items():
            subset = group[group["mes"].isin(months)]
            ret = compound(subset["retorno_carteira"])
            ibov = compound(subset["retorno_ibov"])
            summary_rows.append({"cenario": scenario, "grupo": label, "retorno_carteira": ret, "retorno_ibov": ibov, "alfa": ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan})
    summary = pd.DataFrame(summary_rows)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="resumo", index=False)
        detail.to_excel(writer, sheet_name="detalhe_mes_cenario", index=False)
        ind.to_excel(writer, sheet_name="diagnostico_duas_camadas", index=False)

    log("Teste 6 - Diagnostico de Mercado em Duas Camadas")
    for _, row in summary[summary["grupo"].eq("TOTAL_18_MESES")].iterrows():
        log(f"{row['cenario']}: retorno={pct(row['retorno_carteira'])} | IBOV={pct(row['retorno_ibov'])} | alfa={pct(row['alfa'])}")
    log("Meses com sinal de virada relevante:")
    for _, row in ind[ind["virada_score"].fillna(0).astype(int) >= 4].iterrows():
        log(f"  {row['mes']}: bucket={row['bucket_regime']} | score={int(row['virada_score'])} | sinais={row['sinais_virada']}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
