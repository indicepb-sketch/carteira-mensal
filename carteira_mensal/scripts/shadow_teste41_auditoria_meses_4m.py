from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
INPUT_39 = EXCEL_DIR / "shadow_teste39_4m_mercado_fraco_rotacional.xlsx"
INPUT_38 = EXCEL_DIR / "shadow_teste38_sensibilidade_janela_retorno.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste41_auditoria_meses_4m.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste41_auditoria_meses_4m.log"


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def compound(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float((1.0 + vals).prod() - 1.0)


def summarize(group: pd.DataFrame, label_cols: list[str]) -> pd.DataFrame:
    rows = []
    for keys, g in group.groupby(label_cols, dropna=False, sort=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(label_cols, keys))
        row.update(
            {
                "meses": len(g),
                "delta_alfa_medio": float(pd.to_numeric(g["delta_alfa_t39_vs_base"], errors="coerce").mean()),
                "delta_alfa_total_simples": float(pd.to_numeric(g["delta_alfa_t39_vs_base"], errors="coerce").sum()),
                "meses_4m_ajudou": int(pd.to_numeric(g["delta_alfa_t39_vs_base"], errors="coerce").gt(0).sum()),
                "taxa_ajuda": float(pd.to_numeric(g["delta_alfa_t39_vs_base"], errors="coerce").gt(0).mean()),
                "retorno_t39_composto": compound(g["retorno_total_t39"]),
                "retorno_base_composto": compound(g["retorno_total_base"]),
                "retorno_ibov_composto": compound(g["retorno_expost_ibov"]),
            }
        )
        row["delta_retorno_composto_t39_vs_base"] = row["retorno_t39_composto"] - row["retorno_base_composto"]
        rows.append(row)
    return pd.DataFrame(rows)


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not INPUT_39.exists():
        raise FileNotFoundError(INPUT_39)
    if not INPUT_38.exists():
        raise FileNotFoundError(INPUT_38)
    monthly39 = pd.read_excel(INPUT_39, sheet_name="Mes a Mes")
    portfolio39 = pd.read_excel(INPUT_39, sheet_name="Carteiras")
    audit4m = pd.read_excel(INPUT_39, sheet_name="Auditoria Sinal 4M")
    return monthly39, portfolio39, audit4m


def build_month_audit(monthly39: pd.DataFrame) -> pd.DataFrame:
    base = monthly39[monthly39["cenario_t39"].astype(str).eq("BASE_T36C")].copy()
    t39 = monthly39[monthly39["cenario_t39"].astype(str).eq("T39_4M_MERCADO_FRACO_ROTACIONAL")].copy()
    cols_base = [
        "mes",
        "retorno_total",
        "retorno_expost_ibov",
        "alfa_vs_ibov",
        "regime_previsto_norm",
        "tipo_regime_expost",
        "peso_acoes",
        "peso_cdi",
    ]
    cols_t39 = [
        "mes",
        "retorno_total",
        "alfa_vs_ibov",
        "cenario_origem_t39",
        "regra_t39",
        "regime_previsto_norm",
        "tipo_regime_expost",
        "peso_acoes",
        "peso_cdi",
    ]
    out = t39[cols_t39].merge(
        base[cols_base],
        on="mes",
        how="left",
        suffixes=("_t39", "_base"),
    )
    out["retorno_expost_ibov"] = out["retorno_expost_ibov"]
    out["delta_retorno_t39_vs_base"] = out["retorno_total_t39"] - out["retorno_total_base"]
    out["delta_alfa_t39_vs_base"] = out["alfa_vs_ibov_t39"] - out["alfa_vs_ibov_base"]
    out["4m_foi_usado"] = out["cenario_origem_t39"].astype(str).eq("JANELA_4M")
    out["4m_ajudou"] = out["delta_alfa_t39_vs_base"] > 0
    out["ano"] = out["mes"].astype(str).str[:4]
    out["bucket_resultado_4m"] = np.where(out["delta_alfa_t39_vs_base"].gt(0), "4m_ajudou", np.where(out["delta_alfa_t39_vs_base"].lt(0), "4m_atrapalhou", "neutro"))
    return out.sort_values("mes")


def build_asset_audit(portfolio39: pd.DataFrame, months_using_4m: list[str]) -> pd.DataFrame:
    base = portfolio39[portfolio39["cenario_t39"].astype(str).eq("BASE_T36C")].copy()
    t39 = portfolio39[portfolio39["cenario_t39"].astype(str).eq("T39_4M_MERCADO_FRACO_ROTACIONAL")].copy()
    base = base[base["mes"].astype(str).isin(months_using_4m)]
    t39 = t39[t39["mes"].astype(str).isin(months_using_4m)]
    key = ["mes", "ticker"]
    keep = [
        "mes",
        "ticker",
        "nome",
        "setor",
        "tipo_alocacao",
        "peso_efetivo_carteira_total",
        "peso_dentro_da_parte_acoes",
        "retorno_periodo",
        "contribuicao_retorno_total",
        "nota_final",
        "beta",
        "cv",
        "regime_previsto_norm",
        "tipo_regime_expost",
    ]
    base = base[[c for c in keep if c in base.columns]].rename(
        columns={
            "peso_efetivo_carteira_total": "peso_base",
            "peso_dentro_da_parte_acoes": "peso_acoes_base",
            "contribuicao_retorno_total": "contribuicao_base",
        }
    )
    t39 = t39[[c for c in keep if c in t39.columns]].rename(
        columns={
            "peso_efetivo_carteira_total": "peso_t39",
            "peso_dentro_da_parte_acoes": "peso_acoes_t39",
            "contribuicao_retorno_total": "contribuicao_t39",
        }
    )
    merged = t39.merge(
        base[[c for c in base.columns if c not in {"nome", "setor", "tipo_alocacao", "retorno_periodo", "nota_final", "beta", "cv", "regime_previsto_norm", "tipo_regime_expost"}]],
        on=key,
        how="outer",
    )
    for col in ["peso_base", "peso_t39", "peso_acoes_base", "peso_acoes_t39", "contribuicao_base", "contribuicao_t39"]:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0.0)
    merged["delta_peso"] = merged["peso_t39"] - merged["peso_base"]
    merged["delta_contribuicao"] = merged["contribuicao_t39"] - merged["contribuicao_base"]
    merged["abs_delta_peso"] = merged["delta_peso"].abs()
    merged["acao_ou_cdi"] = np.where(merged["ticker"].astype(str).eq("CDI"), "CDI", "acao")
    return merged.sort_values(["mes", "abs_delta_peso"], ascending=[True, False])


def build_top_changes(asset_audit: pd.DataFrame, month_audit: pd.DataFrame) -> pd.DataFrame:
    rows = []
    month_result = month_audit.set_index("mes")["bucket_resultado_4m"].to_dict()
    for mes, g in asset_audit[asset_audit["acao_ou_cdi"].eq("acao")].groupby("mes", sort=True):
        up = g.sort_values("delta_peso", ascending=False).head(5)
        down = g.sort_values("delta_peso", ascending=True).head(5)
        for kind, chunk in [("mais_peso_com_4m", up), ("menos_peso_com_4m", down)]:
            for _, row in chunk.iterrows():
                rows.append(
                    {
                        "mes": mes,
                        "bucket_resultado_4m": month_result.get(mes),
                        "tipo_mudanca": kind,
                        "ticker": row.get("ticker"),
                        "nome": row.get("nome"),
                        "setor": row.get("setor"),
                        "peso_base": row.get("peso_base"),
                        "peso_t39": row.get("peso_t39"),
                        "delta_peso": row.get("delta_peso"),
                        "retorno_periodo": row.get("retorno_periodo"),
                        "contribuicao_base": row.get("contribuicao_base"),
                        "contribuicao_t39": row.get("contribuicao_t39"),
                        "delta_contribuicao": row.get("delta_contribuicao"),
                        "nota_final": row.get("nota_final"),
                        "beta": row.get("beta"),
                        "cv": row.get("cv"),
                    }
                )
    return pd.DataFrame(rows)


def build_pattern_summary(month_audit: pd.DataFrame, asset_audit: pd.DataFrame) -> pd.DataFrame:
    used = month_audit[month_audit["4m_foi_usado"]].copy()
    summaries = [
        summarize(used, ["bucket_resultado_4m"]),
        summarize(used, ["ano"]),
        summarize(used, ["regime_previsto_norm_t39"]),
        summarize(used, ["tipo_regime_expost_t39"]),
    ]
    out = []
    labels = ["por_resultado", "por_ano", "por_regime_previsto", "por_regime_real"]
    for label, df in zip(labels, summaries):
        temp = df.copy()
        temp.insert(0, "tipo_resumo", label)
        out.append(temp)
    return pd.concat(out, ignore_index=True, sort=False)


def main() -> None:
    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        logs.append(msg)

    monthly39, portfolio39, audit4m = load_inputs()
    month_audit = build_month_audit(monthly39)
    used = month_audit[month_audit["4m_foi_usado"]].copy()
    months_using_4m = used["mes"].astype(str).tolist()
    asset_audit = build_asset_audit(portfolio39, months_using_4m)
    top_changes = build_top_changes(asset_audit, used)
    pattern_summary = build_pattern_summary(month_audit, asset_audit)
    contribution_summary = asset_audit.groupby(["mes", "acao_ou_cdi"], as_index=False).agg(
        delta_contribuicao=("delta_contribuicao", "sum"),
        delta_peso_abs=("abs_delta_peso", "sum"),
        n_linhas=("ticker", "count"),
    ).merge(used[["mes", "bucket_resultado_4m", "delta_alfa_t39_vs_base", "tipo_regime_expost_t39"]], on="mes", how="left")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        used.to_excel(writer, sheet_name="Meses 4M", index=False)
        pattern_summary.to_excel(writer, sheet_name="Resumo Padroes", index=False)
        contribution_summary.to_excel(writer, sheet_name="Resumo Contribuicao", index=False)
        top_changes.to_excel(writer, sheet_name="Top Mudancas Peso", index=False)
        asset_audit.to_excel(writer, sheet_name="Auditoria Ativos", index=False)
        audit4m.to_excel(writer, sheet_name="Auditoria Sinal 4M", index=False)

    helped = int(used["4m_ajudou"].sum())
    hurt = int((~used["4m_ajudou"]).sum())
    delta_total = float(used["delta_alfa_t39_vs_base"].sum())
    log("Teste 41 - Auditoria dos Meses 4M")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Meses auditados com 4M: {len(used)}; ajudou: {helped}; atrapalhou/neutro: {hurt}; delta alfa simples: {pct(delta_total)}")
    log("")
    log("Resumo dos meses 4M:")
    log(used[["mes", "regime_previsto_norm_t39", "tipo_regime_expost_t39", "retorno_total_base", "retorno_total_t39", "retorno_expost_ibov", "delta_alfa_t39_vs_base", "bucket_resultado_4m"]].to_string(index=False))
    log("")
    log("Padroes por regime real:")
    real = pattern_summary[pattern_summary["tipo_resumo"].eq("por_regime_real")]
    log(real[[c for c in ["tipo_regime_expost_t39", "meses", "delta_alfa_medio", "delta_alfa_total_simples", "taxa_ajuda"] if c in real.columns]].to_string(index=False))
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")


if __name__ == "__main__":
    main()
