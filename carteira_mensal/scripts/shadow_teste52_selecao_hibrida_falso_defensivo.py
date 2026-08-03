
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
OUT = EXCEL_DIR / "shadow_teste52_selecao_hibrida_falso_defensivo.xlsx"
BASE_T49 = EXCEL_DIR / "shadow_teste49_top15_regime_capital.xlsx"
IBOV = ROOT / "data" / "processed" / "ibov_mensal_oficial.csv"
CDI = ROOT / "data" / "processed" / "cdi_mensal_ipeadata.csv"
CAPITAL = 10000
IR_CDI = 0.225

SCENARIOS = {
    "BASELINE_T49": {"kind": "baseline", "stock_target": None, "repique_share_stock": 0.0},
    "T51E_EXPOSICAO50_SEM_TROCA": {"kind": "exposure", "stock_target": 0.50, "repique_share_stock": 0.0},
    "T52A_HIBRIDO_EXP30_REPIQUE30": {"kind": "hybrid", "stock_target": None, "repique_share_stock": 0.30},
    "T52B_HIBRIDO_EXP50_REPIQUE40": {"kind": "hybrid", "stock_target": 0.50, "repique_share_stock": 0.40},
    "T52C_HIBRIDO_EXP70_REPIQUE50": {"kind": "hybrid", "stock_target": 0.70, "repique_share_stock": 0.50},
}


def norm_month(s: pd.Series) -> pd.Series:
    return s.astype(str).str[:7]


def compound(series: pd.Series) -> float:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float((1.0 + vals).prod() - 1.0)


def max_drawdown(series: pd.Series) -> float:
    vals = pd.to_numeric(series, errors="coerce").fillna(0.0)
    curve = (1.0 + vals).cumprod()
    return float((curve / curve.cummax() - 1.0).min())


def cdi_net_by_month() -> dict[str, float]:
    if not CDI.exists():
        return {}
    df = pd.read_csv(CDI)
    df["mes"] = norm_month(df["mes"])
    df["retorno_cdi_liquido_periodo"] = pd.to_numeric(df["cdi_bruto_mensal"], errors="coerce") * (1.0 - IR_CDI)
    return dict(zip(df["mes"], df["retorno_cdi_liquido_periodo"]))


def load_july_partial(cdi_map: dict[str, float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    partials = sorted(EXCEL_DIR.glob("parcial_carteira_forward_2026_07*.xlsx"), key=lambda p: p.stat().st_mtime)
    if not partials:
        return pd.DataFrame(), pd.DataFrame()
    pt = partials[-1]
    summary_raw = pd.read_excel(pt, sheet_name="Resumo Parcial")
    summary = dict(zip(summary_raw.iloc[:, 0].astype(str), summary_raw.iloc[:, 1]))
    assets = pd.read_excel(pt, sheet_name="Ativos")
    rows = []
    for _, r in assets.iterrows():
        ticker = str(r.get("ticker", ""))
        is_cdi = ticker.upper() == "CDI"
        weight = float(r.get("peso_recomendado", 0.0))
        ret = float(r.get("retorno_periodo", 0.0))
        if is_cdi:
            ret = float(cdi_map.get("2026-07", ret))
        rows.append({
            "mes": "2026-07",
            "cenario": "TOP15",
            "capital": CAPITAL,
            "ticker": ticker,
            "nome": str(r.get("nome", "Reserva/CDI liquido" if is_cdi else ticker)),
            "setor": str(r.get("setor", "Protecao" if is_cdi else "")),
            "tipo_linha": "cdi" if is_cdi else "acao",
            "peso_final": weight,
            "retorno_periodo": ret,
            "contribuicao": weight * ret,
        })
    cart = pd.DataFrame(rows)
    month = pd.DataFrame([{
        "mes": "2026-07",
        "cenario": "TOP15",
        "capital": CAPITAL,
        "tipo_regime_expost": "alta",
        "regime_previsto": "queda_forte",
        "qtd_acoes": int((cart["tipo_linha"] == "acao").sum()),
        "peso_acoes": float(cart.loc[cart["tipo_linha"] == "acao", "peso_final"].sum()),
        "peso_cdi": float(cart.loc[cart["tipo_linha"] == "cdi", "peso_final"].sum()),
        "retorno": float(cart["contribuicao"].sum()),
        "retorno_ibov": float(summary.get("retorno_ibov_parcial", 0.034734)),
        "alfa_vs_ibov": float(cart["contribuicao"].sum()) - float(summary.get("retorno_ibov_parcial", 0.034734)),
        "bateu_ibov": float(cart["contribuicao"].sum()) > float(summary.get("retorno_ibov_parcial", 0.034734)),
        "ano": 2026,
    }])
    return month, cart


def load_base() -> tuple[pd.DataFrame, pd.DataFrame]:
    cdi_map = cdi_net_by_month()
    mes = pd.read_excel(BASE_T49, sheet_name="Mes a Mes")
    cart = pd.read_excel(BASE_T49, sheet_name="Carteiras")
    mes = mes[(mes["cenario"].eq("TOP15")) & (mes["capital"].eq(CAPITAL))].copy()
    cart = cart[(cart["cenario"].eq("TOP15")) & (cart["capital"].eq(CAPITAL))].copy()
    mes["mes"] = norm_month(mes["mes"])
    cart["mes"] = norm_month(cart["mes"])
    ib = pd.read_csv(IBOV)
    ib["mes"] = norm_month(ib["mes"])
    mes = mes.merge(ib[["mes", "retorno_ibov_oficial"]], on="mes", how="left")
    mes["retorno_ibov_base"] = mes["retorno_ibov_oficial"].combine_first(mes["retorno_ibov"])
    mes = mes.sort_values("mes").reset_index(drop=True)
    mes["ibov_anterior"] = mes["retorno_ibov_base"].shift(1)
    july_m, july_c = load_july_partial(cdi_map)
    if not july_m.empty and not mes["mes"].eq("2026-07").any():
        july_m["retorno_ibov_base"] = july_m["retorno_ibov"]
        july_m["ibov_anterior"] = float(mes.iloc[-1]["retorno_ibov_base"])
        mes = pd.concat([mes, july_m], ignore_index=True, sort=False)
        cart = pd.concat([cart, july_c], ignore_index=True, sort=False)
    return mes.sort_values("mes").reset_index(drop=True), cart


def latest_monthly_file(month: str) -> Path | None:
    year, mm = month.split("-")
    if year == "2026":
        files = list(EXCEL_DIR.glob(f"carteira_recomendada_{year}_{mm}_v*.xlsx"))
    else:
        files = list(EXCEL_DIR.glob(f"carteira_historica_{year}_{mm}.xlsx"))
    if not files:
        return None
    def version(path: Path) -> int:
        m = re.search(r"_v(\d+)", path.name)
        return int(m.group(1)) if m else 0
    return sorted(files, key=version)[-1]


def load_prelim(month: str) -> pd.DataFrame:
    path = latest_monthly_file(month)
    if path is None:
        return pd.DataFrame()
    df = pd.read_excel(path, sheet_name="Analise Preliminar")
    df["_arquivo_origem"] = path.name
    return df


def pct(x) -> float:
    v = pd.to_numeric(x, errors="coerce")
    if pd.isna(v):
        return np.nan
    return float(v)


def falso_defensivo_repiquing(month_row: pd.Series) -> bool:
    month = str(month_row["mes"])
    reg = str(month_row.get("regime_previsto", "")).lower()
    if "queda" not in reg and "fraco" not in reg:
        return False
    prev = pct(month_row.get("ibov_anterior"))
    if pd.isna(prev) or prev <= -0.02:
        return False
    pre = load_prelim(month)
    if pre.empty:
        return False
    ret1 = pd.to_numeric(pre.get("retorno_acumulado_1m"), errors="coerce")
    rel1 = pd.to_numeric(pre.get("retorno_1m_relativo_ibov"), errors="coerce")
    nota_final = pd.to_numeric(pre.get("nota_final"), errors="coerce")
    nota_preliminar = pd.to_numeric(pre.get("nota preliminar"), errors="coerce")
    nota_ref = nota_final.mean()
    if pd.isna(nota_ref):
        nota_ref = nota_preliminar.mean()
    return bool((ret1 > 0).mean() >= 0.50 and (rel1 > 0).mean() >= 0.50 and nota_ref >= 50)


def get_yfinance_returns(tickers: list[str], start: str, end: str) -> tuple[dict[str, float], pd.DataFrame]:
    import yfinance as yf
    out: dict[str, float] = {}
    logs = []
    for ticker in tickers:
        try:
            data = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False, threads=False)
            if data.empty:
                logs.append({"ticker": ticker, "status": "sem_dados", "motivo": "download vazio"})
                continue
            close = data["Close"] if "Close" in data.columns else data.iloc[:, 0]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = pd.to_numeric(close, errors="coerce").dropna()
            if len(close) < 2:
                logs.append({"ticker": ticker, "status": "sem_dados", "motivo": "menos de 2 fechamentos"})
                continue
            ret = float(close.iloc[-1] / close.iloc[0] - 1.0)
            out[ticker] = ret
            logs.append({"ticker": ticker, "status": "ok", "data_inicio": str(close.index[0].date()), "data_fim": str(close.index[-1].date()), "preco_inicio": float(close.iloc[0]), "preco_fim": float(close.iloc[-1]), "retorno": ret})
        except Exception as exc:
            logs.append({"ticker": ticker, "status": "erro", "motivo": str(exc)})
    return out, pd.DataFrame(logs)


def select_repique_candidates(month: str, current_tickers: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    pre = load_prelim(month)
    if pre.empty:
        return pd.DataFrame(), pd.DataFrame()
    df = pre.copy()
    for col in ["retorno_acumulado_1m", "retorno_1m_relativo_ibov", "forca_relativa_score", "nota preliminar", "nota_final", "beta", "correlacao_ibov"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    fund_block = df.get("fundamento_bloqueante", False)
    if not isinstance(fund_block, pd.Series):
        fund_block = pd.Series(False, index=df.index)
    fund_block = fund_block.astype(str).str.lower().isin(["true", "1", "sim"])
    status = df.get("status_para_risco", pd.Series("", index=df.index)).astype(str).str.lower()
    decisao = df.get("decisao_preliminar_ajustada", pd.Series("", index=df.index)).astype(str).str.lower()
    mask = (
        df["ticker"].astype(str).str.endswith(".SA")
        & ~df["ticker"].astype(str).isin(current_tickers)
        & ~fund_block
        & (df["retorno_acumulado_1m"] > 0)
        & (df["retorno_1m_relativo_ibov"] > 0)
        & ~status.str.contains("descartar_fundamental|dados_insuficientes", na=False)
        & ~decisao.str.contains("descartar_fundamental|dados_insuficientes", na=False)
    )
    cand = df.loc[mask].copy()
    if cand.empty:
        return cand, pd.DataFrame()
    note = cand["nota_final"].combine_first(cand["nota preliminar"]).fillna(0.0)
    ret1 = cand["retorno_acumulado_1m"].fillna(0.0)
    rel1 = cand["retorno_1m_relativo_ibov"].fillna(0.0)
    fr = cand["forca_relativa_score"].fillna(0.0)
    def minmax(s: pd.Series) -> pd.Series:
        if float(s.max() - s.min()) == 0.0:
            return pd.Series(0.5, index=s.index)
        return (s - s.min()) / (s.max() - s.min())
    cand["score_repique"] = 0.35 * minmax(rel1) + 0.25 * minmax(ret1) + 0.25 * (note / 100.0) + 0.15 * (fr / 5.0)
    cand = cand.sort_values(["score_repique", "retorno_1m_relativo_ibov"], ascending=False)
    selected = []
    sector_counts: dict[str, int] = {}
    # Conta setores ja presentes na carteira defensiva, para preservar maximo 2/setor.
    pre_current = pre[pre["ticker"].astype(str).isin(current_tickers)]
    for setor, n in pre_current.get("setor", pd.Series(dtype=str)).astype(str).value_counts().items():
        sector_counts[setor] = int(n)
    for _, row in cand.iterrows():
        setor = str(row.get("setor", ""))
        if sector_counts.get(setor, 0) >= 2:
            continue
        selected.append(row)
        sector_counts[setor] = sector_counts.get(setor, 0) + 1
        if len(selected) >= 6:
            break
    selected_df = pd.DataFrame(selected)
    return selected_df, cand


def apply_hybrid(month: str, base_part: pd.DataFrame, scenario: str, cfg: dict, trigger: bool, returns_override: dict[str, float]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    part = base_part.copy()
    part["peso_final"] = pd.to_numeric(part["peso_final"], errors="coerce").fillna(0.0)
    part["retorno_periodo"] = pd.to_numeric(part["retorno_periodo"], errors="coerce").fillna(0.0)
    is_cdi = part["tipo_linha"].astype(str).str.lower().eq("cdi") | part["ticker"].astype(str).str.upper().eq("CDI")
    original_stock_weight = float(part.loc[~is_cdi, "peso_final"].sum())
    target = cfg.get("stock_target")
    stock_weight = original_stock_weight if target is None else max(original_stock_weight, float(target))
    cdi_weight = max(0.0, 1.0 - stock_weight)
    if not trigger or cfg["kind"] == "baseline":
        part["cenario_t52"] = scenario
        part["origem_t52"] = "baseline"
        part["peso_original"] = part["peso_final"]
        part["contribuicao_t52"] = part["peso_final"] * part["retorno_periodo"]
        return part, pd.DataFrame(), pd.DataFrame()
    if cfg["kind"] == "exposure":
        scale = stock_weight / original_stock_weight if original_stock_weight > 0 else 0.0
        part["cenario_t52"] = scenario
        part["origem_t52"] = np.where(is_cdi, "cdi", "defensivo_original")
        part["peso_original"] = part["peso_final"]
        part.loc[~is_cdi, "peso_final"] = part.loc[~is_cdi, "peso_final"] * scale
        part.loc[is_cdi, "peso_final"] = cdi_weight
        part["contribuicao_t52"] = part["peso_final"] * part["retorno_periodo"]
        return part, pd.DataFrame(), pd.DataFrame()

    current_tickers = set(part.loc[~is_cdi, "ticker"].astype(str))
    selected, pool = select_repique_candidates(month, current_tickers)
    if selected.empty:
        part["cenario_t52"] = scenario
        part["origem_t52"] = np.where(is_cdi, "cdi", "sem_candidatas_repique")
        part["peso_original"] = part["peso_final"]
        part["contribuicao_t52"] = part["peso_final"] * part["retorno_periodo"]
        return part, selected, pool

    repique_share = float(cfg["repique_share_stock"])
    defensive_weight = stock_weight * (1.0 - repique_share)
    repique_weight = stock_weight * repique_share
    old_stocks = part.loc[~is_cdi].copy()
    old_scale = defensive_weight / original_stock_weight if original_stock_weight > 0 else 0.0
    old_stocks["peso_original"] = old_stocks["peso_final"]
    old_stocks["peso_final"] = old_stocks["peso_final"] * old_scale
    old_stocks["cenario_t52"] = scenario
    old_stocks["origem_t52"] = "nucleo_defensivo"

    scores = pd.to_numeric(selected["score_repique"], errors="coerce").fillna(0.0)
    if scores.sum() <= 0:
        scores = pd.Series(1.0, index=selected.index)
    selected = selected.copy()
    selected["peso_final"] = repique_weight * scores / scores.sum()
    selected["peso_original"] = 0.0
    selected["cenario_t52"] = scenario
    selected["tipo_linha"] = "acao"
    selected["origem_t52"] = "repique_confirmado"
    selected["retorno_periodo"] = selected["ticker"].astype(str).map(returns_override)
    selected = selected.dropna(subset=["retorno_periodo"])

    cdi = part.loc[is_cdi].copy()
    if cdi.empty:
        cdi = pd.DataFrame([{"mes": month, "ticker": "CDI", "nome": "Reserva/CDI liquido", "setor": "Protecao", "tipo_linha": "cdi", "retorno_periodo": 0.0}])
    cdi["peso_original"] = cdi.get("peso_final", 0.0)
    cdi["peso_final"] = cdi_weight
    cdi["cenario_t52"] = scenario
    cdi["origem_t52"] = "cdi"

    cols = sorted(set(old_stocks.columns) | set(selected.columns) | set(cdi.columns))
    combined = pd.concat([old_stocks.reindex(columns=cols), selected.reindex(columns=cols), cdi.reindex(columns=cols)], ignore_index=True, sort=False)
    combined["contribuicao_t52"] = pd.to_numeric(combined["peso_final"], errors="coerce").fillna(0.0) * pd.to_numeric(combined["retorno_periodo"], errors="coerce").fillna(0.0)
    return combined, selected, pool


def main() -> None:
    mes, cart = load_base()
    # Retornos de julho para todos os possiveis candidatos do hibrido.
    pre_july = load_prelim("2026-07")
    possible = pre_july.loc[
        (pd.to_numeric(pre_july.get("retorno_acumulado_1m"), errors="coerce") > 0)
        & (pd.to_numeric(pre_july.get("retorno_1m_relativo_ibov"), errors="coerce") > 0),
        "ticker",
    ].astype(str).unique().tolist()
    yf_returns, yf_log = get_yfinance_returns(possible, "2026-06-30", "2026-08-01") if possible else ({}, pd.DataFrame())
    # Para ativos ja na carteira de julho, prefere o retorno do fechamento do arquivo parcial.
    july_cart = cart[cart["mes"].astype(str).str[:7].eq("2026-07")]
    for _, r in july_cart.iterrows():
        if str(r.get("ticker", "")).upper() != "CDI":
            yf_returns[str(r["ticker"])] = float(r.get("retorno_periodo", np.nan))

    monthly_rows = []
    all_carts = []
    all_selected = []
    all_pool = []
    trigger_rows = []

    for _, m in mes.iterrows():
        month = str(m["mes"])
        base_part = cart[cart["mes"].astype(str).str[:7].eq(month)].copy()
        if base_part.empty:
            continue
        trigger = falso_defensivo_repiquing(m)
        trigger_rows.append({"mes": month, "gatilho_falso_defensivo_repiquing": trigger, "regime_previsto": m.get("regime_previsto"), "ibov_anterior": m.get("ibov_anterior")})
        for scenario, cfg in SCENARIOS.items():
            adjusted, selected, pool = apply_hybrid(month, base_part, scenario, cfg, trigger, yf_returns)
            ret = float(pd.to_numeric(adjusted["contribuicao_t52"], errors="coerce").fillna(0.0).sum())
            ibov = float(m.get("retorno_ibov_base", m.get("retorno_ibov", np.nan)))
            stock_weight = float(adjusted.loc[adjusted["tipo_linha"].astype(str).str.lower().ne("cdi"), "peso_final"].sum())
            monthly_rows.append({
                "mes": month,
                "cenario_t52": scenario,
                "gatilho_acionado": bool(trigger and scenario != "BASELINE_T49"),
                "regime_previsto": m.get("regime_previsto"),
                "tipo_regime_expost": m.get("tipo_regime_expost"),
                "retorno_modelo": ret,
                "retorno_ibov": ibov,
                "alfa_vs_ibov": ret - ibov,
                "bateu_ibov": ret > ibov,
                "peso_acoes": stock_weight,
                "peso_cdi": 1.0 - stock_weight,
                "qtd_acoes": int(adjusted[adjusted["tipo_linha"].astype(str).str.lower().ne("cdi")]["ticker"].nunique()),
                "retorno_base_mes": float(m.get("retorno", np.nan)),
                "alfa_base_mes": float(m.get("alfa_vs_ibov", np.nan)),
                "ano": int(month[:4]),
            })
            adjusted["mes"] = month
            all_carts.append(adjusted)
            if not selected.empty:
                selected = selected.copy(); selected["mes"] = month; selected["cenario_t52"] = scenario; all_selected.append(selected)
            if not pool.empty:
                pool = pool.copy(); pool["mes"] = month; pool["cenario_t52"] = scenario; all_pool.append(pool)

    monthly = pd.DataFrame(monthly_rows)
    carts = pd.concat(all_carts, ignore_index=True, sort=False) if all_carts else pd.DataFrame()
    selected = pd.concat(all_selected, ignore_index=True, sort=False) if all_selected else pd.DataFrame()
    pool = pd.concat(all_pool, ignore_index=True, sort=False) if all_pool else pd.DataFrame()
    triggers = pd.DataFrame(trigger_rows)

    resumo_rows = []
    for scenario, g in monthly.groupby("cenario_t52"):
        model = compound(g["retorno_modelo"])
        ibov = compound(g["retorno_ibov"])
        resumo_rows.append({
            "cenario_t52": scenario,
            "meses": len(g),
            "retorno_modelo": model,
            "retorno_ibov": ibov,
            "alfa_vs_ibov": model - ibov,
            "taxa_acerto": float(g["bateu_ibov"].mean()),
            "drawdown": max_drawdown(g["retorno_modelo"]),
            "meses_acionados": int(g["gatilho_acionado"].sum()),
            "peso_acoes_medio": float(g["peso_acoes"].mean()),
        })
    resumo = pd.DataFrame(resumo_rows)
    by_year = monthly.groupby(["cenario_t52", "ano"], as_index=False).agg(
        meses=("mes", "count"),
        retorno_modelo=("retorno_modelo", compound),
        retorno_ibov=("retorno_ibov", compound),
        taxa_acerto=("bateu_ibov", "mean"),
        meses_acionados=("gatilho_acionado", "sum"),
    )
    by_year["alfa_vs_ibov"] = by_year["retorno_modelo"] - by_year["retorno_ibov"]
    july = monthly[monthly["mes"].eq("2026-07")].copy()

    with pd.ExcelWriter(OUT, engine="xlsxwriter") as writer:
        pd.DataFrame([{ "cenario_t52": k, **v } for k, v in SCENARIOS.items()]).to_excel(writer, sheet_name="Descricao", index=False)
        resumo.to_excel(writer, sheet_name="Resumo Geral", index=False)
        by_year.to_excel(writer, sheet_name="Resumo Ano", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        july.to_excel(writer, sheet_name="Comparativo Julho", index=False)
        triggers.to_excel(writer, sheet_name="Gatilhos", index=False)
        selected.to_excel(writer, sheet_name="Repique Selecionado", index=False)
        pool.to_excel(writer, sheet_name="Pool Repique", index=False)
        yf_log.to_excel(writer, sheet_name="Log Retornos", index=False)
        carts.to_excel(writer, sheet_name="Carteiras Ajustadas", index=False)

    print(f"Arquivo gerado: {OUT}")
    print("\nResumo Geral")
    print(resumo.to_string(index=False))
    print("\nComparativo Julho")
    print(july[["mes","cenario_t52","retorno_modelo","retorno_ibov","alfa_vs_ibov","peso_acoes","peso_cdi","qtd_acoes","gatilho_acionado"]].to_string(index=False))
    if not selected.empty:
        print("\nAcoes de repique selecionadas")
        cols = ["mes","cenario_t52","ticker","nome","setor","peso_final","retorno_periodo","retorno_acumulado_1m","retorno_1m_relativo_ibov","nota preliminar","score_repique"]
        print(selected[[c for c in cols if c in selected.columns]].to_string(index=False))
    print("\nLog retornos yfinance")
    if yf_log.empty:
        print("Sem downloads.")
    else:
        print(yf_log.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
