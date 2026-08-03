from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_29 = EXCEL_DIR / "shadow_teste29_vencedoras_barradas_funil.xlsx"
INPUT_28D = EXCEL_DIR / "shadow_teste28d_queda_confirmada.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste31_evidencia_candidatas_restricao.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste31_evidencia_candidatas_restricao.log"


NUMERIC_FEATURES = [
    "nota_final",
    "forca_relativa_score",
    "rsi",
    "retorno_acumulado_1m",
    "retorno_acumulado_4m",
    "retorno_medio",
    "desvio_padrao",
    "cv",
    "beta",
    "correlacao_ibov",
]

CATEGORICAL_FEATURES = [
    "tipo_regime_expost",
    "bucket_regime_previsto",
    "tipo_timing",
    "tendencia_mensal",
    "contexto_estrutural",
    "classificacao_forca_relativa",
    "qualidade_fundamentalista",
    "setor_expost",
]


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def safe_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def load_data() -> pd.DataFrame:
    if not INPUT_29.exists():
        raise FileNotFoundError(INPUT_29)
    detail = pd.read_excel(INPUT_29, sheet_name="detalhe_universo")
    detail["mes"] = detail["mes"].astype(str)
    detail["ticker"] = detail["ticker"].astype(str)
    if INPUT_28D.exists():
        month_regime = pd.read_excel(INPUT_28D, sheet_name="mes_a_mes_bruto")
        month_regime = month_regime[month_regime["cenario_teste28d"].eq("modulacao_queda_confirmada_28d")].copy()
        cols = ["mes", "bucket_regime_previsto", "tipo_regime_expost", "queda_confirmada_28d"]
        detail = detail.merge(month_regime[[c for c in cols if c in month_regime.columns]], on="mes", how="left")
    else:
        detail["bucket_regime_previsto"] = ""
        detail["tipo_regime_expost"] = ""
        detail["queda_confirmada_28d"] = False
    for col in ["retorno_realizado_periodo", "retorno_ibov_periodo", "retorno_relativo_vs_ibov"]:
        detail[col] = pd.to_numeric(detail[col], errors="coerce")
    detail["bateu_ibov"] = detail["retorno_relativo_vs_ibov"] > 0
    restricted = detail[detail["saida_funil_inicial"].astype(str).str.lower().eq("candidata_com_restricao")].copy()
    return restricted


def summarize_group(group: pd.DataFrame, feature: str = "", value: Any = "") -> dict[str, Any]:
    ret = pd.to_numeric(group["retorno_realizado_periodo"], errors="coerce")
    rel = pd.to_numeric(group["retorno_relativo_vs_ibov"], errors="coerce")
    out = {
        "feature": feature,
        "valor": value,
        "n": int(len(group)),
        "retorno_medio": float(ret.mean()) if ret.notna().any() else np.nan,
        "retorno_mediano": float(ret.median()) if ret.notna().any() else np.nan,
        "retorno_relativo_medio_vs_ibov": float(rel.mean()) if rel.notna().any() else np.nan,
        "retorno_relativo_mediano_vs_ibov": float(rel.median()) if rel.notna().any() else np.nan,
        "n_bateu_ibov": int(group["bateu_ibov"].sum()),
        "pct_bateu_ibov": float(group["bateu_ibov"].mean()) if len(group) else np.nan,
        "soma_alfa_expost": float(rel.sum()) if rel.notna().any() else np.nan,
        "maior_vencedora": "",
        "maior_alfa": np.nan,
        "pior_perdedora": "",
        "pior_alfa": np.nan,
    }
    if not group.empty and rel.notna().any():
        best = group.loc[rel.idxmax()]
        worst = group.loc[rel.idxmin()]
        out["maior_vencedora"] = safe_text(best.get("ticker"))
        out["maior_alfa"] = float(best.get("retorno_relativo_vs_ibov"))
        out["pior_perdedora"] = safe_text(worst.get("ticker"))
        out["pior_alfa"] = float(worst.get("retorno_relativo_vs_ibov"))
    return out


def numeric_bins(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature in NUMERIC_FEATURES:
        if feature not in frame.columns:
            continue
        values = pd.to_numeric(frame[feature], errors="coerce")
        valid = frame[values.notna()].copy()
        if valid.empty:
            continue
        values = pd.to_numeric(valid[feature], errors="coerce")
        try:
            valid["_faixa"] = pd.qcut(values, q=4, duplicates="drop")
        except ValueError:
            valid["_faixa"] = pd.cut(values, bins=4, duplicates="drop")
        for faixa, group in valid.groupby("_faixa", observed=True):
            row = summarize_group(group, feature, str(faixa))
            row["min_valor"] = float(pd.to_numeric(group[feature], errors="coerce").min())
            row["max_valor"] = float(pd.to_numeric(group[feature], errors="coerce").max())
            row["media_valor"] = float(pd.to_numeric(group[feature], errors="coerce").mean())
            rows.append(row)
    return pd.DataFrame(rows)


def categorical_segments(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature in CATEGORICAL_FEATURES:
        if feature not in frame.columns:
            continue
        temp = frame.copy()
        temp[feature] = temp[feature].fillna("sem_dado").astype(str).replace("", "sem_dado")
        for value, group in temp.groupby(feature, dropna=False):
            rows.append(summarize_group(group, feature, value))
    return pd.DataFrame(rows)


def combo_segments(frame: pd.DataFrame) -> pd.DataFrame:
    combos = [
        ("tipo_regime_expost", "tipo_timing"),
        ("tipo_regime_expost", "classificacao_forca_relativa"),
        ("bucket_regime_previsto", "tipo_timing"),
        ("tipo_timing", "classificacao_forca_relativa"),
        ("tendencia_mensal", "classificacao_forca_relativa"),
        ("tipo_regime_expost", "setor_expost"),
    ]
    rows: list[dict[str, Any]] = []
    for a, b in combos:
        if a not in frame.columns or b not in frame.columns:
            continue
        temp = frame.copy()
        temp[a] = temp[a].fillna("sem_dado").astype(str).replace("", "sem_dado")
        temp[b] = temp[b].fillna("sem_dado").astype(str).replace("", "sem_dado")
        for keys, group in temp.groupby([a, b], dropna=False):
            row = summarize_group(group, f"{a} x {b}", f"{keys[0]} | {keys[1]}")
            row["feature_1"] = a
            row["valor_1"] = keys[0]
            row["feature_2"] = b
            row["valor_2"] = keys[1]
            rows.append(row)
    return pd.DataFrame(rows)


def candidate_rules(frame: pd.DataFrame) -> pd.DataFrame:
    idx = frame.index
    nota = pd.to_numeric(frame.get("nota_final", pd.Series(np.nan, index=idx)), errors="coerce")
    forca = pd.to_numeric(frame.get("forca_relativa_score", pd.Series(np.nan, index=idx)), errors="coerce")
    rsi = pd.to_numeric(frame.get("rsi", pd.Series(np.nan, index=idx)), errors="coerce")
    beta = pd.to_numeric(frame.get("beta", pd.Series(np.nan, index=idx)), errors="coerce")
    cv = pd.to_numeric(frame.get("cv", pd.Series(np.nan, index=idx)), errors="coerce")
    ret_medio = pd.to_numeric(frame.get("retorno_medio", pd.Series(np.nan, index=idx)), errors="coerce")
    timing = frame.get("tipo_timing", pd.Series("", index=idx)).fillna("").astype(str).str.lower()
    force_class = frame.get("classificacao_forca_relativa", pd.Series("", index=idx)).fillna("").astype(str).str.lower()
    regime_real = frame.get("tipo_regime_expost", pd.Series("", index=idx)).fillna("").astype(str).str.lower()
    trend = frame.get("tendencia_mensal", pd.Series("", index=idx)).fillna("").astype(str).str.lower()

    rules = {
        "nota>=50_forca>=3_rsi<75": nota.ge(50) & forca.ge(3) & rsi.lt(75),
        "nota>=50_forca>=3_rsi_45_70": nota.ge(50) & forca.ge(3) & rsi.between(45, 70, inclusive="both"),
        "nota>=50_forca_forte_ou_moderada": nota.ge(50) & force_class.str.contains("forte|moderada|positiva", na=False),
        "timing_neutro_ou_favoravel_forca>=3": timing.str.contains("neutro|favoravel", na=False) & forca.ge(3),
        "alta_real_nota>=50_forca>=3": regime_real.eq("alta") & nota.ge(50) & forca.ge(3),
        "queda_real_beta<0.9_cv_nao_alto": regime_real.str.contains("queda", na=False) & beta.lt(0.9) & (cv.lt(50) | cv.isna()),
        "retorno_medio_pos_nota>=50": ret_medio.gt(0) & nota.ge(50),
        "virada_aceitavel_forca>=3": trend.str.contains("virada|alta_aceitavel", na=False) & forca.ge(3),
    }
    rows: list[dict[str, Any]] = []
    for name, mask in rules.items():
        group = frame[mask.fillna(False)].copy()
        row = summarize_group(group, "regra_candidata", name)
        row["cobertura_pct_restritas"] = float(len(group) / len(frame)) if len(frame) else np.nan
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["retorno_relativo_medio_vs_ibov", "pct_bateu_ibov"], ascending=False)


def top_examples(frame: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "mes",
        "ticker",
        "nome_expost",
        "setor_expost",
        "tipo_regime_expost",
        "bucket_regime_previsto",
        "retorno_realizado_periodo",
        "retorno_ibov_periodo",
        "retorno_relativo_vs_ibov",
        "bateu_ibov",
        "nota_final",
        "forca_relativa_score",
        "classificacao_forca_relativa",
        "tipo_timing",
        "tendencia_mensal",
        "rsi",
        "retorno_acumulado_1m",
        "retorno_acumulado_4m",
        "retorno_medio",
        "beta",
        "correlacao_ibov",
        "desvio_padrao",
        "cv",
        "motivo_principal_barreira",
    ]
    winners = frame.sort_values("retorno_relativo_vs_ibov", ascending=False).head(100)
    losers = frame.sort_values("retorno_relativo_vs_ibov", ascending=True).head(100)
    winners.insert(0, "amostra", "top_vencedoras_restricao")
    losers.insert(0, "amostra", "top_perdedoras_restricao")
    out = pd.concat([winners, losers], ignore_index=True, sort=False)
    return out[[c for c in ["amostra", *cols] if c in out.columns]].copy()


def conclusion_table(seg: pd.DataFrame, min_n: int = 20) -> pd.DataFrame:
    if seg.empty:
        return pd.DataFrame()
    temp = seg[pd.to_numeric(seg["n"], errors="coerce").ge(min_n)].copy()
    temp["sinal"] = np.where(
        (temp["retorno_relativo_medio_vs_ibov"] > 0) & (temp["pct_bateu_ibov"] >= 0.52),
        "favoravel",
        np.where((temp["retorno_relativo_medio_vs_ibov"] < 0) & (temp["pct_bateu_ibov"] < 0.48), "desfavoravel", "neutro"),
    )
    return temp.sort_values(["sinal", "retorno_relativo_medio_vs_ibov"], ascending=[True, False])


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    log("Teste 31 - Selecao por Evidencia Ex-Post das Candidatas com Restricao")
    log("Somente leitura/medicao: avalia quais perfis de candidata_com_restricao bateram o IBOV.")
    frame = load_data()
    overall = pd.DataFrame([summarize_group(frame, "universo", "candidata_com_restricao")])
    num = numeric_bins(frame)
    cat = categorical_segments(frame)
    combo = combo_segments(frame)
    rules = candidate_rules(frame)
    examples = top_examples(frame)
    conclusions = pd.concat(
        [
            conclusion_table(num.assign(origem="faixas_numericas")),
            conclusion_table(cat.assign(origem="segmentos_categoricos")),
            conclusion_table(combo.assign(origem="combinacoes"), min_n=15),
            conclusion_table(rules.assign(origem="regras_candidatas"), min_n=15),
        ],
        ignore_index=True,
        sort=False,
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        overall.to_excel(writer, sheet_name="resumo_geral", index=False)
        num.to_excel(writer, sheet_name="faixas_numericas", index=False)
        cat.to_excel(writer, sheet_name="segmentos_categoricos", index=False)
        combo.to_excel(writer, sheet_name="combinacoes", index=False)
        rules.to_excel(writer, sheet_name="regras_candidatas", index=False)
        conclusions.to_excel(writer, sheet_name="sinais_fortes", index=False)
        examples.to_excel(writer, sheet_name="exemplos_top100", index=False)
        frame.to_excel(writer, sheet_name="base_restritas", index=False)

    total = overall.iloc[0]
    log(
        f"Restritas analisadas: {int(total['n'])}; retorno medio={pct(total['retorno_medio'])}; "
        f"alfa medio={pct(total['retorno_relativo_medio_vs_ibov'])}; bateu={pct(total['pct_bateu_ibov'])}"
    )
    log("Melhores regras candidatas:")
    for _, row in rules.head(8).iterrows():
        log(
            f"  {row['valor']}: n={int(row['n'])}; alfa_medio={pct(row['retorno_relativo_medio_vs_ibov'])}; "
            f"bateu={pct(row['pct_bateu_ibov'])}; cobertura={pct(row['cobertura_pct_restritas'])}"
        )
    fav = conclusions[conclusions["sinal"].eq("favoravel")].head(10)
    if not fav.empty:
        log("Sinais favoraveis mais fortes:")
        for _, row in fav.iterrows():
            log(
                f"  {row.get('origem','')}: {row['feature']}={row['valor']} | n={int(row['n'])}; "
                f"alfa_medio={pct(row['retorno_relativo_medio_vs_ibov'])}; bateu={pct(row['pct_bateu_ibov'])}"
            )
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
