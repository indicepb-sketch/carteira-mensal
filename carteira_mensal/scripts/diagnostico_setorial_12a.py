from __future__ import annotations

import glob
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
VOL_FILE = EXCEL_DIR / "estudo_volatilidade_historica.xlsx"
SHADOW_FILE = EXCEL_DIR / "shadow_volatilidade_11c.xlsx"
OUTPUT_FILE = EXCEL_DIR / "diagnostico_setorial_12a.xlsx"
LOG_FILE = LOG_DIR / "diagnostico_setorial_12a.log"
HIST_END = "2026-06"


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def norm_ticker(value: Any) -> str:
    ticker = str(value).strip().upper()
    if not ticker or ticker == "NAN":
        return ""
    return ticker if ticker.endswith(".SA") else f"{ticker}.SA"


def read_sheet(path: Path, sheet: str) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet)
    except Exception:
        return pd.DataFrame()


def load_universe_detail() -> pd.DataFrame:
    xls = pd.ExcelFile(VOL_FILE)
    sheet = "Detalhe Ativo Mes" if "Detalhe Ativo Mes" in xls.sheet_names else "detalhe_por_ativo_mes"
    df = pd.read_excel(VOL_FILE, sheet_name=sheet)
    df["ticker"] = df["ticker"].map(norm_ticker)
    df["mes"] = df["mes"].astype(str)
    for col in [
        "retorno_realizado_periodo",
        "retorno_ibov_periodo",
        "retorno_relativo_vs_ibov",
        "vol_ratio_21",
        "vol_ratio_63",
        "retorno_acumulado_1m",
        "retorno_acumulado_4m",
        "nota_final",
        "rsi",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["setor"] = df.get("setor", "").fillna("Nao mapeado").astype(str).str.strip()
    df.loc[df["setor"].isin(["", "nan", "None"]), "setor"] = "Nao mapeado"
    df["periodo_tipo"] = np.where(df["mes"].le(HIST_END), "historico_calibracao", "parcial_ou_forward")
    return df


def sector_month_stats(detail: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (mes, setor), group in detail.groupby(["mes", "setor"], dropna=False):
        ret = group["retorno_realizado_periodo"].dropna()
        ibov = pd.to_numeric(group["retorno_ibov_periodo"], errors="coerce").dropna()
        ibov_ret = float(ibov.iloc[0]) if not ibov.empty else np.nan
        if ret.empty:
            continue
        best = group.loc[group["retorno_realizado_periodo"].idxmax()]
        worst = group.loc[group["retorno_realizado_periodo"].idxmin()]
        mean_ret = float(ret.mean())
        median_ret = float(ret.median())
        alpha_mean = mean_ret - ibov_ret if pd.notna(ibov_ret) else np.nan
        alpha_median = median_ret - ibov_ret if pd.notna(ibov_ret) else np.nan
        pct_bateu = float((group["retorno_realizado_periodo"] > group["retorno_ibov_periodo"]).mean())
        pct_pos = float((group["retorno_realizado_periodo"] > 0).mean())
        rows.append(
            {
                "mes": mes,
                "setor": setor,
                "periodo_tipo": "historico_calibracao" if str(mes) <= HIST_END else "parcial_ou_forward",
                "n_ativos_setor": int(group["ticker"].nunique()),
                "retorno_medio_setor": mean_ret,
                "retorno_mediano_setor": median_ret,
                "retorno_std_setor": float(ret.std(ddof=1)) if len(ret) > 1 else np.nan,
                "retorno_min_setor": float(ret.min()),
                "retorno_max_setor": float(ret.max()),
                "amplitude_setor": float(ret.max() - ret.min()),
                "retorno_ibov_periodo": ibov_ret,
                "alfa_medio_setor_vs_ibov": alpha_mean,
                "alfa_mediano_setor_vs_ibov": alpha_median,
                "pct_ativos_positivos": pct_pos,
                "pct_ativos_bateram_ibov": pct_bateu,
                "vol_ratio_21_medio": float(pd.to_numeric(group.get("vol_ratio_21"), errors="coerce").mean()),
                "vol_ratio_21_mediano": float(pd.to_numeric(group.get("vol_ratio_21"), errors="coerce").median()),
                "pct_ativos_vol_ratio_21_acima_1_5": float((pd.to_numeric(group.get("vol_ratio_21"), errors="coerce") > 1.5).mean()),
                "melhor_ativo_setor": best.get("ticker", ""),
                "retorno_melhor_ativo_setor": best.get("retorno_realizado_periodo", np.nan),
                "pior_ativo_setor": worst.get("ticker", ""),
                "retorno_pior_ativo_setor": worst.get("retorno_realizado_periodo", np.nan),
                "classificacao_setorial_expost": classify_sector(alpha_median, pct_bateu),
            }
        )
    return pd.DataFrame(rows)


def classify_sector(alpha_median: float, pct_bateu: float) -> str:
    if pd.isna(alpha_median):
        return "dados_insuficientes"
    if alpha_median >= 0.02 and pct_bateu >= 0.55:
        return "setor_forte"
    if alpha_median >= 0 and pct_bateu >= 0.45:
        return "setor_positivo"
    if alpha_median <= -0.02 and pct_bateu <= 0.45:
        return "setor_fraco"
    if alpha_median < 0:
        return "setor_negativo"
    return "setor_neutro"


def sector_period_summary(sector_month: pd.DataFrame) -> pd.DataFrame:
    hist = sector_month[sector_month["periodo_tipo"].eq("historico_calibracao")].copy()
    rows = []
    for setor, group in hist.groupby("setor"):
        alpha = group["alfa_mediano_setor_vs_ibov"].dropna()
        ret = group["retorno_mediano_setor"].dropna()
        rows.append(
            {
                "setor": setor,
                "meses_observados": int(group["mes"].nunique()),
                "retorno_mediano_medio_mensal": float(ret.mean()) if not ret.empty else np.nan,
                "alfa_mediano_medio_mensal": float(alpha.mean()) if not alpha.empty else np.nan,
                "alfa_mediano_acumulado_simples": float(alpha.sum()) if not alpha.empty else np.nan,
                "pct_meses_setor_bateu_ibov": float((group["alfa_mediano_setor_vs_ibov"] > 0).mean()),
                "pct_meses_retorno_positivo": float((group["retorno_mediano_setor"] > 0).mean()),
                "pior_mes": group.loc[group["alfa_mediano_setor_vs_ibov"].idxmin(), "mes"] if not group.empty else "",
                "pior_alfa_mediano": float(group["alfa_mediano_setor_vs_ibov"].min()) if not group.empty else np.nan,
                "melhor_mes": group.loc[group["alfa_mediano_setor_vs_ibov"].idxmax(), "mes"] if not group.empty else "",
                "melhor_alfa_mediano": float(group["alfa_mediano_setor_vs_ibov"].max()) if not group.empty else np.nan,
                "vol_ratio_21_mediano_medio": float(group["vol_ratio_21_mediano"].mean()),
                "pct_meses_vol_setorial_elevada": float((group["vol_ratio_21_mediano"] > 1.5).mean()),
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["alfa_mediano_medio_mensal", "pct_meses_setor_bateu_ibov"], ascending=[False, False])
    return out


def load_selected_portfolios() -> pd.DataFrame:
    if not SHADOW_FILE.exists():
        return pd.DataFrame()
    port = pd.read_excel(SHADOW_FILE, sheet_name="carteiras")
    port = port[port["cenario"].astype(str).eq("baseline_consolidado")].copy()
    if port.empty:
        return port
    port["ticker"] = port["ticker"].map(norm_ticker)
    port["mes"] = port["mes"].astype(str)
    port["fonte_carteira"] = "shadow_consolidado_baseline"
    return port


def load_july_partial_portfolio() -> pd.DataFrame:
    files = sorted(glob.glob(str(EXCEL_DIR / "parcial_carteira_forward_2026_07*.xlsx")))
    if not files:
        return pd.DataFrame()
    path = Path(files[-1])
    assets = read_sheet(path, "Ativos")
    if assets.empty or "ticker" not in assets.columns:
        return pd.DataFrame()
    assets["ticker"] = assets["ticker"].map(norm_ticker)
    assets["mes"] = "2026-07"
    assets["fonte_carteira"] = path.name
    return assets


def portfolio_vs_sector(detail: pd.DataFrame, sector_month: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    cols = ["mes", "ticker", "setor", "retorno_realizado_periodo", "retorno_ibov_periodo", "retorno_relativo_vs_ibov", "vol_ratio_21", "bucket_vol_ratio_21"]
    base = detail[[c for c in cols if c in detail.columns]].drop_duplicates(["mes", "ticker"]).copy()
    merged = selected.merge(base, on=["mes", "ticker"], how="left", suffixes=("", "_universo"))
    if "setor_universo" in merged.columns:
        merged["setor"] = merged.get("setor", pd.Series("", index=merged.index)).fillna("")
        merged.loc[merged["setor"].astype(str).isin(["", "nan", "None"]), "setor"] = merged["setor_universo"]
    sm_cols = [
        "mes",
        "setor",
        "retorno_medio_setor",
        "retorno_mediano_setor",
        "alfa_medio_setor_vs_ibov",
        "alfa_mediano_setor_vs_ibov",
        "pct_ativos_bateram_ibov",
        "classificacao_setorial_expost",
    ]
    merged = merged.merge(sector_month[sm_cols], on=["mes", "setor"], how="left")
    asset_ret = pd.to_numeric(merged["retorno_realizado_periodo"], errors="coerce")
    sector_med = pd.to_numeric(merged["retorno_mediano_setor"], errors="coerce")
    ibov = pd.to_numeric(merged["retorno_ibov_periodo"], errors="coerce")
    merged["ativo_vs_setor_mediano"] = asset_ret - sector_med
    merged["setor_vs_ibov_mediano"] = sector_med - ibov
    merged["diagnostico_queda"] = [
        diagnose_drop(a, s, i) for a, s, i in zip(asset_ret, sector_med, ibov)
    ]
    return merged


def diagnose_drop(asset_ret: float, sector_ret: float, ibov_ret: float) -> str:
    if pd.isna(asset_ret):
        return "dados_insuficientes"
    if asset_ret >= 0:
        return "ativo_positivo"
    if pd.notna(sector_ret) and sector_ret < 0 and abs(asset_ret - sector_ret) <= 0.03:
        return "queda_explicada_pelo_setor"
    if pd.notna(sector_ret) and asset_ret < sector_ret - 0.05:
        return "queda_especifica_do_ativo"
    if pd.notna(ibov_ret) and asset_ret < ibov_ret - 0.05:
        return "queda_maior_que_ibov"
    if pd.notna(sector_ret) and sector_ret < 0:
        return "queda_parcialmente_setorial"
    return "queda_sem_explicacao_setorial_clara"


def concentration_by_month(selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    weight_col = "peso_recomendado" if "peso_recomendado" in selected.columns else "peso_final"
    if weight_col not in selected.columns:
        return pd.DataFrame()
    df = selected.copy()
    df[weight_col] = pd.to_numeric(df[weight_col], errors="coerce").fillna(0)
    out = df.groupby(["mes", "setor"], dropna=False).agg(
        peso_setor=(weight_col, "sum"),
        n_ativos=("ticker", "nunique"),
        tickers=("ticker", lambda s: ", ".join(s.astype(str).tolist())),
    ).reset_index()
    return out.sort_values(["mes", "peso_setor"], ascending=[True, False])


def main() -> None:
    logs: list[str] = []

    def log(message: str) -> None:
        print(message, flush=True)
        logs.append(message)

    detail = load_universe_detail()
    sector_month = sector_month_stats(detail)
    sector_summary = sector_period_summary(sector_month)
    sector_rank = sector_month.sort_values(["mes", "alfa_mediano_setor_vs_ibov"], ascending=[True, False]).copy()

    selected_hist = load_selected_portfolios()
    selected_july = load_july_partial_portfolio()
    selected_all = pd.concat([selected_hist, selected_july], ignore_index=True, sort=False)
    port_vs_sector = portfolio_vs_sector(detail, sector_month, selected_all)
    concentration = concentration_by_month(selected_all)

    july_diag = port_vs_sector[port_vs_sector["mes"].astype(str).eq("2026-07")].copy()
    sector_july = sector_month[sector_month["mes"].astype(str).eq("2026-07")].copy()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        sector_month.to_excel(writer, sheet_name="Setor Mes", index=False)
        sector_summary.to_excel(writer, sheet_name="Resumo Setorial", index=False)
        sector_rank.to_excel(writer, sheet_name="Ranking Setor Mes", index=False)
        port_vs_sector.to_excel(writer, sheet_name="Carteira vs Setor", index=False)
        concentration.to_excel(writer, sheet_name="Concentracao Carteira", index=False)
        sector_july.to_excel(writer, sheet_name="Julho Parcial Setores", index=False)
        july_diag.to_excel(writer, sheet_name="Julho Parcial Carteira", index=False)
        detail.to_excel(writer, sheet_name="Base Universo", index=False)

    hist = sector_month[sector_month["periodo_tipo"].eq("historico_calibracao")]
    log("Teste 12A - Diagnostico Historico Setorial")
    log(f"Meses historicos analisados: {hist['mes'].nunique()} ({hist['mes'].min()} a {hist['mes'].max()})")
    log(f"Setores analisados: {hist['setor'].nunique()}")
    if not sector_summary.empty:
        log("Top 5 setores por alfa mediano medio mensal:")
        for _, row in sector_summary.head(5).iterrows():
            log(f"  {row['setor']}: alfa_medio={pct(row['alfa_mediano_medio_mensal'])} | meses_bateu={pct(row['pct_meses_setor_bateu_ibov'])}")
        log("Piores 5 setores por alfa mediano medio mensal:")
        for _, row in sector_summary.tail(5).iterrows():
            log(f"  {row['setor']}: alfa_medio={pct(row['alfa_mediano_medio_mensal'])} | meses_bateu={pct(row['pct_meses_setor_bateu_ibov'])}")
    if not july_diag.empty:
        log("Julho parcial - carteira vs setor:")
        for _, row in july_diag.iterrows():
            log(
                f"  {row.get('ticker')}: ativo={pct(row.get('retorno_realizado_periodo'))} | "
                f"setor_mediano={pct(row.get('retorno_mediano_setor'))} | "
                f"ativo_vs_setor={pct(row.get('ativo_vs_setor_mediano'))} | "
                f"diagnostico={row.get('diagnostico_queda')}"
            )
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
