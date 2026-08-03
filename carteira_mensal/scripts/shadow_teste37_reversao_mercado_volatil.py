from __future__ import annotations

import glob
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "excel"
BASE_XLSX = OUT / "shadow_teste36_exposicao_regime_2.xlsx"
OUTPUT_XLSX = OUT / "shadow_teste37_reversao_mercado_volatil.xlsx"
BASE_SCENARIO = "T36C_QUALIDADE"


VARIANTS = {
    "BASE_T36C": {"mode": "baseline", "max_sleeve": 0.0, "trim": False},
    "T37A_CDI_ONLY": {"mode": "reversal", "max_sleeve": 0.10, "trim": False},
    "T37B_REVERSAO_10": {"mode": "reversal", "max_sleeve": 0.10, "trim": True},
    "T37C_REVERSAO_15": {"mode": "reversal", "max_sleeve": 0.15, "trim": True},
}


def pct_to_float(value) -> float:
    if pd.isna(value):
        return np.nan
    if isinstance(value, str):
        value = value.replace("%", "").replace(",", ".").strip()
    try:
        val = float(value)
    except Exception:
        return np.nan
    if abs(val) > 2:
        return val / 100.0
    return val


def norm_text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().lower()


def compound(series: pd.Series) -> float:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float((1.0 + vals).prod() - 1.0)


def max_drawdown(returns: pd.Series) -> float:
    vals = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    wealth = (1.0 + vals).cumprod()
    peak = wealth.cummax()
    dd = wealth / peak - 1.0
    return float(dd.min()) if len(dd) else np.nan


def load_base():
    monthly = pd.read_excel(BASE_XLSX, sheet_name="Mes a Mes")
    portfolios = pd.read_excel(BASE_XLSX, sheet_name="Carteiras Por Cenario")
    monthly = monthly[monthly["cenario_teste36"].eq(BASE_SCENARIO)].copy()
    portfolios = portfolios[portfolios["cenario_teste36"].eq(BASE_SCENARIO)].copy()
    return monthly, portfolios


def load_expost():
    frames = []
    for year in [2022, 2023, 2024, 2025]:
        path = OUT / f"shadow_backtest_{year}.xlsx"
        if path.exists():
            frames.append(pd.read_excel(path, sheet_name="expost_universo"))
    path = OUT / "universo_expost_consolidado.xlsx"
    if path.exists():
        df26 = pd.read_excel(path, sheet_name="Universo Expost")
        frames.append(df26)
    expost = pd.concat(frames, ignore_index=True)
    expost["ticker"] = expost["ticker"].astype(str)
    expost["mes"] = expost["mes"].astype(str)
    return expost


def latest_recommended_2026(month: str) -> Path | None:
    files = sorted(glob.glob(str(OUT / f"carteira_recomendada_2026_{month}_v*.xlsx")))
    if not files:
        return None

    def version(path: str) -> int:
        m = re.search(r"_v(\d+)\.xlsx$", path)
        return int(m.group(1)) if m else -1

    return Path(max(files, key=version))


def formation_file_for_month(mes: str) -> Path | None:
    year, month = mes.split("-")
    if year == "2026":
        return latest_recommended_2026(month)
    path = OUT / f"carteira_historica_{year}_{month}.xlsx"
    return path if path.exists() else None


def load_formation_month(mes: str) -> pd.DataFrame:
    path = formation_file_for_month(mes)
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_excel(path, sheet_name="Analise Preliminar")
    except Exception:
        return pd.DataFrame()
    df["mes"] = mes
    df["arquivo_formacao"] = path.name
    df["ticker"] = df["ticker"].astype(str)
    return df


def has_real_deterioration(row: pd.Series) -> bool:
    if bool(row.get("fundamento_bloqueante", False)) is True:
        return True
    roe = pct_to_float(row.get("roe"))
    margem = pct_to_float(row.get("margem_liquida"))
    pl = pct_to_float(row.get("pl_atual"))
    bads = []
    if not pd.isna(roe) and roe < 0:
        bads.append("roe_negativo")
    if not pd.isna(margem) and margem < 0:
        bads.append("margem_liquida_negativa")
    if not pd.isna(pl) and pl < 0:
        bads.append("pl_negativo")
    return bool(bads)


def bollinger_reversal(row: pd.Series) -> tuple[bool, str, float]:
    status = norm_text(row.get("bollinger_status"))
    pos = pd.to_numeric(row.get("bollinger_position"), errors="coerce")
    status_ok = any(x in status for x in ["oportunidade", "sobrevenda", "inferior"])
    pos_ok = not pd.isna(pos) and pos <= 0.30
    score = 0.0
    if status_ok:
        score += 0.55
    if pos_ok:
        score += 0.45 * max(0.0, min(1.0, (0.30 - float(pos)) / 0.30))
    reason = []
    if status_ok:
        reason.append(f"bollinger_status={status}")
    if pos_ok:
        reason.append(f"bollinger_position={float(pos):.3f}")
    return status_ok or pos_ok, "; ".join(reason), min(1.0, score)


def reversal_candidates(mes: str, expost: pd.DataFrame, current_tickers: set[str]) -> pd.DataFrame:
    form = load_formation_month(mes)
    if form.empty:
        return pd.DataFrame()
    ex = expost[expost["mes"].eq(mes)].copy()
    if ex.empty:
        return pd.DataFrame()
    cols = [
        "mes",
        "ticker",
        "retorno_realizado_periodo",
        "retorno_ibov_periodo",
        "retorno_relativo_vs_ibov",
        "status_na_selecao",
        "motivo_bloqueio_ou_status",
    ]
    df = form.merge(ex[cols], on=["mes", "ticker"], how="left", suffixes=("", "_expost"))
    rows = []
    for _, row in df.iterrows():
        ticker = str(row.get("ticker"))
        if ticker in current_tickers:
            continue
        rsi = pd.to_numeric(row.get("rsi"), errors="coerce")
        ret4 = pct_to_float(row.get("retorno_acumulado_4m"))
        ret1 = pct_to_float(row.get("retorno_acumulado_1m"))
        ret_medio = pct_to_float(row.get("retorno_medio"))
        note = pd.to_numeric(row.get("nota_final"), errors="coerce")
        motive = norm_text(row.get("motivo_bloqueio_ou_status")) + " " + norm_text(row.get("motivo_decisao_preliminar"))
        status = norm_text(row.get("status_na_selecao"))
        blocked_by_mean = ("retorno_medio_negativo" in motive) or (not pd.isna(ret_medio) and ret_medio < 0)
        if not blocked_by_mean:
            continue
        if has_real_deterioration(row):
            continue
        if pd.isna(rsi) or rsi > 40:
            continue
        if pd.isna(ret4) or ret4 >= 0:
            continue
        boll_ok, boll_reason, boll_score = bollinger_reversal(row)
        if not boll_ok:
            continue
        if pd.isna(row.get("retorno_realizado_periodo")):
            continue
        rsi_score = max(0.0, min(1.0, (40.0 - float(rsi)) / 15.0))
        ret4_score = max(0.0, min(1.0, -float(ret4) / 0.20))
        note_score = 0.35 if pd.isna(note) else max(0.0, min(1.0, float(note) / 100.0))
        score = 0.35 * rsi_score + 0.25 * ret4_score + 0.25 * boll_score + 0.15 * note_score
        rows.append(
            {
                "mes": mes,
                "ticker": ticker,
                "nome": row.get("nome"),
                "setor": row.get("setor"),
                "rsi_formacao": rsi,
                "retorno_1m_formacao": ret1,
                "retorno_4m_formacao": ret4,
                "bollinger_status": row.get("bollinger_status"),
                "bollinger_position": row.get("bollinger_position"),
                "roe": row.get("roe"),
                "margem_liquida": row.get("margem_liquida"),
                "pl_atual": row.get("pl_atual"),
                "nota_final": note,
                "retorno_medio": ret_medio,
                "status_na_selecao": row.get("status_na_selecao", status),
                "motivo_bloqueio_ou_status": row.get("motivo_bloqueio_ou_status"),
                "motivo_decisao_preliminar": row.get("motivo_decisao_preliminar"),
                "reversal_score": score,
                "motivo_reversao": f"RSI<=40; retorno_4m<0; {boll_reason}; sem deterioracao fundamental real",
                "retorno_realizado_periodo": row.get("retorno_realizado_periodo"),
                "retorno_ibov_periodo": row.get("retorno_ibov_periodo"),
                "retorno_relativo_vs_ibov": row.get("retorno_relativo_vs_ibov"),
                "arquivo_formacao": row.get("arquivo_formacao"),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["reversal_score", "nota_final"], ascending=False)


def allocate_capped(scores: pd.Series, total: float, cap: float = 0.05) -> dict[str, float]:
    weights = {str(k): 0.0 for k in scores.index}
    remaining = float(total)
    active = {str(k): max(0.0, float(v)) for k, v in scores.items()}
    while remaining > 1e-12 and active:
        denom = sum(active.values())
        if denom <= 0:
            equal = remaining / len(active)
            for k in list(active):
                add = min(cap - weights[k], equal)
                weights[k] += add
                remaining -= add
                if weights[k] >= cap - 1e-12:
                    active.pop(k, None)
            break
        changed = False
        for k in list(active):
            add = remaining * active[k] / denom
            room = cap - weights[k]
            if add >= room:
                weights[k] += room
                remaining -= room
                active.pop(k, None)
                changed = True
        if not changed:
            for k in list(active):
                add = remaining * active[k] / denom
                weights[k] += add
            remaining = 0.0
    return weights


def apply_reversal_overlay(mes: str, base_rows: pd.DataFrame, monthly_row: pd.Series, expost: pd.DataFrame, variant: dict):
    rows = base_rows.copy()
    rows["peso_final"] = pd.to_numeric(rows["peso_efetivo_carteira_total"], errors="coerce").fillna(0.0)
    rows["retorno_final"] = pd.to_numeric(rows["retorno_periodo"], errors="coerce").fillna(0.0)
    rows["origem_t37"] = "base_t36c"
    rows["peso_antes_t37"] = rows["peso_final"]
    rows["peso_reversao_adicionado"] = 0.0

    if variant["mode"] == "baseline" or norm_text(monthly_row.get("regime_previsto_norm")) not in {"queda_leve", "queda_forte"}:
        return rows, pd.DataFrame(), "nao_aplicavel"

    cdi_mask = rows["tipo_alocacao"].astype(str).str.contains("cdi", case=False, na=False) | rows["ticker"].astype(str).eq("CDI")
    cdi_available = float(rows.loc[cdi_mask, "peso_final"].sum())
    current_tickers = set(rows.loc[~cdi_mask, "ticker"].astype(str))
    cands = reversal_candidates(mes, expost, current_tickers)
    if cands.empty:
        return rows, cands, "sem_candidatas_reversao"

    sector_counts = rows.loc[(~cdi_mask) & (rows["peso_final"] > 0.0001), "setor"].astype(str).value_counts().to_dict()
    selected = []
    for _, cand in cands.iterrows():
        sector = str(cand.get("setor"))
        if sector_counts.get(sector, 0) >= 2:
            continue
        selected.append(cand)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected) >= 3:
            break
    if not selected:
        return rows, cands.assign(aprovada_overlay=False, motivo_overlay="bloqueada_por_limite_setorial_2_por_setor"), "limite_setorial"

    sel = pd.DataFrame(selected)
    max_sleeve = float(variant["max_sleeve"])
    if variant["trim"]:
        sleeve = max_sleeve
    else:
        sleeve = min(max_sleeve, cdi_available)
    sleeve = min(sleeve, 0.05 * len(sel))
    if sleeve <= 1e-9:
        return rows, sel, "sem_espaco_cdi_para_overlay"

    weights = allocate_capped(sel.set_index("ticker")["reversal_score"], sleeve, cap=0.05)

    if cdi_available >= sleeve:
        if cdi_mask.any():
            idx = rows.index[cdi_mask][0]
            rows.loc[idx, "peso_final"] = float(rows.loc[idx, "peso_final"]) - sleeve
    else:
        if cdi_mask.any():
            rows.loc[cdi_mask, "peso_final"] = 0.0
        need_trim = sleeve - cdi_available
        action_mask = ~cdi_mask
        action_sum = float(rows.loc[action_mask, "peso_final"].sum())
        if action_sum > 0:
            rows.loc[action_mask, "peso_final"] *= (action_sum - need_trim) / action_sum

    extra_rows = []
    for _, cand in sel.iterrows():
        ticker = str(cand["ticker"])
        w = float(weights.get(ticker, 0.0))
        extra_rows.append(
            {
                "cenario_teste36": BASE_SCENARIO,
                "mes": mes,
                "ticker": ticker,
                "nome": cand.get("nome"),
                "setor": cand.get("setor"),
                "tipo_alocacao": "acao_reversao_t37",
                "peso_bruto_acao": w,
                "multiplicador_exposicao_regime": 1.0,
                "peso_efetivo_carteira_total": 0.0,
                "retorno_periodo": cand.get("retorno_realizado_periodo"),
                "contribuicao_retorno_total": 0.0,
                "nota_final": cand.get("nota_final"),
                "beta": np.nan,
                "regime_previsto_norm": monthly_row.get("regime_previsto_norm"),
                "tipo_regime_expost": monthly_row.get("tipo_regime_expost"),
                "peso_final": w,
                "retorno_final": cand.get("retorno_realizado_periodo"),
                "origem_t37": "overlay_reversao",
                "peso_antes_t37": 0.0,
                "peso_reversao_adicionado": w,
            }
        )
    rows = pd.concat([rows, pd.DataFrame(extra_rows)], ignore_index=True)
    rows["contribuicao_t37"] = rows["peso_final"] * rows["retorno_final"]

    sel = sel.copy()
    sel["aprovada_overlay"] = sel["ticker"].map(lambda t: t in weights and weights[t] > 0)
    sel["peso_overlay"] = sel["ticker"].map(lambda t: weights.get(str(t), 0.0))
    sel["motivo_overlay"] = "aprovada_reversao_mercado_volatil"
    return rows, sel, "overlay_aplicado"


def summarize(monthly: pd.DataFrame, scenario_col: str = "cenario_t37") -> pd.DataFrame:
    out = []
    for scenario, df in monthly.groupby(scenario_col):
        ret = compound(df["retorno_total_t37"])
        ibov = compound(df["retorno_expost_ibov"])
        out.append(
            {
                scenario_col: scenario,
                "meses": len(df),
                "retorno_modelo": ret,
                "retorno_ibov": ibov,
                "alfa_vs_ibov": ret - ibov,
                "meses_bateu_ibov": int(df["bateu_ibov_t37"].sum()),
                "taxa_acerto": float(df["bateu_ibov_t37"].mean()),
                "drawdown": max_drawdown(df["retorno_total_t37"]),
                "peso_acoes_medio": float(df["peso_acoes_t37"].mean()),
                "peso_cdi_medio": float(df["peso_cdi_t37"].mean()),
                "meses_com_overlay": int((df["n_reversoes_overlay"] > 0).sum()),
            }
        )
    return pd.DataFrame(out)


def summarize_year(monthly: pd.DataFrame, year: str) -> pd.DataFrame:
    df = monthly[monthly["mes"].astype(str).str.startswith(year)].copy()
    return summarize(df) if not df.empty else pd.DataFrame()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    base_monthly, base_port = load_base()
    expost = load_expost()
    months = sorted(base_monthly["mes"].astype(str).unique())

    all_months = []
    all_portfolios = []
    all_candidates = []
    validations = []
    logs = []

    for scenario, cfg in VARIANTS.items():
        for mes in months:
            mrow = base_monthly[base_monthly["mes"].astype(str).eq(mes)].iloc[0]
            brows = base_port[base_port["mes"].astype(str).eq(mes)].copy()
            rows, cands, status = apply_reversal_overlay(mes, brows, mrow, expost, cfg)
            rows["cenario_t37"] = scenario
            rows["contribuicao_t37"] = rows["peso_final"] * rows["retorno_final"]
            total_return = float(rows["contribuicao_t37"].sum())
            ibov_return = float(mrow.get("retorno_expost_ibov"))
            action_mask = ~rows["tipo_alocacao"].astype(str).str.contains("cdi", case=False, na=False)
            cdi_mask = ~action_mask
            weight_sum = float(rows["peso_final"].sum())
            all_portfolios.append(rows)
            all_months.append(
                {
                    "cenario_t37": scenario,
                    "mes": mes,
                    "regime_previsto_norm": mrow.get("regime_previsto_norm"),
                    "tipo_regime_expost": mrow.get("tipo_regime_expost"),
                    "status_overlay": status,
                    "retorno_total_t37": total_return,
                    "retorno_t36c": mrow.get("retorno_total"),
                    "retorno_expost_ibov": ibov_return,
                    "alfa_vs_ibov_t37": total_return - ibov_return,
                    "delta_retorno_vs_t36c": total_return - float(mrow.get("retorno_total")),
                    "bateu_ibov_t37": total_return > ibov_return,
                    "peso_acoes_t37": float(rows.loc[action_mask, "peso_final"].sum()),
                    "peso_cdi_t37": float(rows.loc[cdi_mask, "peso_final"].sum()),
                    "n_ativos_acoes": int((rows.loc[action_mask, "peso_final"] > 0.0001).sum()),
                    "n_reversoes_overlay": int((rows["origem_t37"].eq("overlay_reversao") & (rows["peso_final"] > 0)).sum()),
                    "peso_reversoes_overlay": float(rows.loc[rows["origem_t37"].eq("overlay_reversao"), "peso_final"].sum()),
                    "data_inicio_performance": mrow.get("data_inicio_performance"),
                    "data_avaliacao": mrow.get("data_avaliacao"),
                }
            )
            if not cands.empty:
                cands = cands.copy()
                cands["cenario_t37"] = scenario
                all_candidates.append(cands)
            validations.append(
                {
                    "cenario_t37": scenario,
                    "mes": mes,
                    "soma_pesos": weight_sum,
                    "retorno_calculado_por_contribuicao": total_return,
                    "retorno_mes": total_return,
                    "diferenca": 0.0,
                    "pesos_fecham_100": abs(weight_sum - 1.0) < 0.0001,
                    "retorno_consistente": True,
                    "maior_peso": float(rows["peso_final"].max()),
                    "n_reversoes": int((rows["origem_t37"].eq("overlay_reversao") & (rows["peso_final"] > 0)).sum()),
                }
            )
            if status in {"sem_candidatas_reversao", "sem_espaco_cdi_para_overlay", "limite_setorial"}:
                logs.append({"cenario_t37": scenario, "mes": mes, "status": status})

    monthly = pd.DataFrame(all_months)
    portfolios = pd.concat(all_portfolios, ignore_index=True)
    candidates = pd.concat(all_candidates, ignore_index=True) if all_candidates else pd.DataFrame()
    validation = pd.DataFrame(validations)
    logs_df = pd.DataFrame(logs)

    summary_all = summarize(monthly)
    summary_2022 = summarize_year(monthly, "2022")
    baseline_all = summary_all[summary_all["cenario_t37"].eq("BASE_T36C")].iloc[0]
    summary_all["delta_alfa_vs_base"] = summary_all["alfa_vs_ibov"] - float(baseline_all["alfa_vs_ibov"])
    if not summary_2022.empty:
        base_2022 = summary_2022[summary_2022["cenario_t37"].eq("BASE_T36C")].iloc[0]
        summary_2022["delta_alfa_vs_base_2022"] = summary_2022["alfa_vs_ibov"] - float(base_2022["alfa_vs_ibov"])

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        summary_all.to_excel(writer, sheet_name="Resumo 2022-2026", index=False)
        summary_2022.to_excel(writer, sheet_name="Resumo 2022", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        portfolios.to_excel(writer, sheet_name="Carteiras", index=False)
        candidates.to_excel(writer, sheet_name="Candidatas Reversao", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)
        logs_df.to_excel(writer, sheet_name="Log", index=False)

    print("Teste 37 - Reversao em Mercado Volatil")
    print(f"Arquivo gerado: {OUTPUT_XLSX}")
    print("\nResumo 2022-2026:")
    print(summary_all[["cenario_t37", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "drawdown", "meses_com_overlay", "delta_alfa_vs_base"]].to_string(index=False))
    print("\nResumo 2022:")
    print(summary_2022[["cenario_t37", "retorno_modelo", "retorno_ibov", "alfa_vs_ibov", "taxa_acerto", "drawdown", "meses_com_overlay", "delta_alfa_vs_base_2022"]].to_string(index=False))
    print("\nMeses com overlay aplicado:")
    applied = monthly[monthly["n_reversoes_overlay"] > 0][["cenario_t37", "mes", "regime_previsto_norm", "n_reversoes_overlay", "peso_reversoes_overlay", "delta_retorno_vs_t36c", "alfa_vs_ibov_t37"]]
    print(applied.to_string(index=False) if not applied.empty else "Nenhum overlay aplicado.")
    bad = validation[(~validation["pesos_fecham_100"]) | (~validation["retorno_consistente"])]
    print("\nValidacao:", "OK" if bad.empty else "FALHAS")


if __name__ == "__main__":
    main()
