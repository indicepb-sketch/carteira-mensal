
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
RAW_DIR = ROOT / "data" / "raw"
INPUT_45 = EXCEL_DIR / "shadow_teste45_consolidacao_final_t44a.xlsx"
INPUT_44 = EXCEL_DIR / "shadow_teste44_diagnostico_grau_confianca.xlsx"
OUTPUT_FILE = EXCEL_DIR / "shadow_teste46_carteira_executavel.xlsx"
LOG_FILE = LOG_DIR / "shadow_teste46_carteira_executavel.log"

MODEL = "T44A_QUEDA_CONFIANCA"
CAPITAL_BASE = 10_000.0
MIN_WEIGHT_INSIDE_STOCKS = 0.01
FRACTIONAL_MARKET = True
SOURCE_CDI_IR = 0.15
PLATFORM_CDI_IR = 0.225


def platform_cdi_return(source_net_return: Any) -> float:
    net = pd.to_numeric(source_net_return, errors="coerce")
    if pd.isna(net):
        return 0.0
    gross = float(net) / (1.0 - SOURCE_CDI_IR)
    return gross * (1.0 - PLATFORM_CDI_IR)


def pct(x: Any) -> str:
    if pd.isna(x):
        return "-"
    return f"{float(x):.2%}"


def compound_return(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return float("nan")
    return float((1.0 + values).prod() - 1.0)


def max_drawdown(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return float("nan")
    curve = (1.0 + values).cumprod()
    peak = curve.cummax()
    dd = curve / peak - 1.0
    return float(dd.min())


def ticker_price_files(ticker: str) -> list[Path]:
    safe = ticker.replace(".", "_").replace("^", "^")
    candidates = sorted(RAW_DIR.glob(f"prices_{safe}*.csv"))
    no_suffix = ticker.replace(".SA", "").replace(".", "_")
    candidates += sorted(RAW_DIR.glob(f"prices_{no_suffix}*.csv"))
    unique: list[Path] = []
    seen = set()
    for item in candidates:
        if item not in seen:
            unique.append(item)
            seen.add(item)
    return unique


def read_price_csv(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path, skiprows=[1, 2])
    except Exception:
        return None
    if "Price" in df.columns:
        df = df.rename(columns={"Price": "Date"})
    if "Date" not in df.columns:
        return None
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    px_col = "Close" if "Close" in df.columns else "Adj Close" if "Adj Close" in df.columns else None
    if px_col is None:
        return None
    df["_px"] = pd.to_numeric(df[px_col], errors="coerce")
    return df.dropna(subset=["Date", "_px"]).sort_values("Date")


def load_price_at_or_before(ticker: str, date_value: Any) -> tuple[float, str, str]:
    candidates = ticker_price_files(ticker)
    if not candidates:
        return float("nan"), "", "arquivo_preco_nao_encontrado"
    target = pd.to_datetime(date_value, errors="coerce")
    if pd.isna(target):
        return float("nan"), "", "data_inicio_invalida"

    best_before: tuple[pd.Timestamp, float] | None = None
    first_after: tuple[pd.Timestamp, float] | None = None
    readable = 0
    for path in candidates:
        df = read_price_csv(path)
        if df is None or df.empty:
            continue
        readable += 1
        before = df[df["Date"] <= target]
        if not before.empty:
            row = before.iloc[-1]
            candidate = (row["Date"], float(row["_px"]))
            if best_before is None or candidate[0] > best_before[0]:
                best_before = candidate
        else:
            after = df[df["Date"] >= target]
            if not after.empty:
                row = after.iloc[0]
                candidate = (row["Date"], float(row["_px"]))
                if first_after is None or candidate[0] < first_after[0]:
                    first_after = candidate

    if best_before is not None:
        return best_before[1], str(best_before[0].date()), "ok"
    if first_after is not None:
        return first_after[1], str(first_after[0].date()), "preco_primeiro_posterior"
    if readable == 0:
        return float("nan"), "", "erro_ler_preco"
    return float("nan"), "", "sem_preco_na_janela"

def build_executable(month_key: str, month_row: pd.Series, stocks: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    stocks = stocks.copy()
    stocks["peso_teorico_total"] = pd.to_numeric(stocks["peso_efetivo_carteira_total"], errors="coerce").fillna(0.0)
    stocks["peso_teorico_acoes"] = pd.to_numeric(stocks["peso_dentro_da_parte_acoes"], errors="coerce").fillna(0.0)
    stocks["retorno_periodo"] = pd.to_numeric(stocks["retorno_periodo"], errors="coerce")
    stocks = stocks[stocks["tipo_alocacao"].astype(str).eq("acao")].copy()

    removed = stocks[stocks["peso_teorico_acoes"] < MIN_WEIGHT_INSIDE_STOCKS].copy()
    if not removed.empty:
        removed["motivo_remocao"] = "peso_dentro_da_parte_acoes_menor_que_minimo"
    kept = stocks[stocks["peso_teorico_acoes"] >= MIN_WEIGHT_INSIDE_STOCKS].copy()

    exposure = float(pd.to_numeric(month_row.get("peso_acoes", 0.0), errors="coerce") or 0.0)
    cdi_weight_target = float(pd.to_numeric(month_row.get("peso_cdi", 0.0), errors="coerce") or 0.0)
    cdi_target_value = CAPITAL_BASE * cdi_weight_target
    stock_budget = CAPITAL_BASE * exposure

    if kept.empty or stock_budget <= 0:
        cdi_value = CAPITAL_BASE
        cdi_ret = platform_cdi_return(month_row.get("retorno_cdi_liquido_periodo", 0.0))
        exec_df = pd.DataFrame([{
            "mes": month_key, "ticker": "CDI", "nome": "Reserva/CDI liquido", "setor": "Protecao",
            "tipo_alocacao": "cdi_residual", "preco_entrada": np.nan, "data_preco_entrada": "",
            "peso_teorico_total": cdi_weight_target, "peso_executavel_total": 1.0,
            "quantidade": np.nan, "valor_alvo": cdi_target_value, "valor_executado": cdi_value,
            "retorno_periodo": cdi_ret, "contribuicao_executavel": cdi_ret,
            "status_preco": "sem_acoes_executaveis", "aliquota_ir_cdi": PLATFORM_CDI_IR, "aliquota_ir_cdi_origem": SOURCE_CDI_IR,
        }])
        meta = {"mes": month_key, "n_acoes_teoricas": len(stocks), "n_acoes_executaveis": 0, "n_removidas_peso_minimo": len(removed), "valor_acoes_executado": 0.0, "valor_cdi_executado": cdi_value, "peso_acoes_executavel": 0.0, "peso_cdi_executavel": 1.0}
        return exec_df, removed, meta

    kept["peso_reescalado_acoes"] = kept["peso_teorico_acoes"] / kept["peso_teorico_acoes"].sum()
    kept["valor_alvo"] = kept["peso_reescalado_acoes"] * stock_budget

    prices = kept["ticker"].apply(lambda t: load_price_at_or_before(str(t), month_row.get("data_inicio_performance")))
    kept["preco_entrada"] = [p[0] for p in prices]
    kept["data_preco_entrada"] = [p[1] for p in prices]
    kept["status_preco"] = [p[2] for p in prices]
    kept["quantidade"] = 0
    valid_price = kept["preco_entrada"].notna() & (kept["preco_entrada"] > 0)
    if FRACTIONAL_MARKET:
        kept.loc[valid_price, "quantidade"] = np.floor(kept.loc[valid_price, "valor_alvo"] / kept.loc[valid_price, "preco_entrada"]).astype(int)
        kept["forma_execucao"] = "fracionario_quantidade_inteira"
    else:
        kept.loc[valid_price, "quantidade"] = (np.floor(kept.loc[valid_price, "valor_alvo"] / (kept.loc[valid_price, "preco_entrada"] * 100)) * 100).astype(int)
        kept["forma_execucao"] = "lote_padrao_100"
    kept["valor_executado"] = kept["quantidade"] * kept["preco_entrada"]

    zero_qty = kept[kept["quantidade"] <= 0].copy()
    if not zero_qty.empty:
        removed = pd.concat([removed, zero_qty.assign(motivo_remocao="quantidade_zero_por_capital")], ignore_index=True)
    kept = kept[kept["quantidade"] > 0].copy()

    value_stocks = float(kept["valor_executado"].sum())
    cdi_value = max(0.0, CAPITAL_BASE - value_stocks)
    kept["peso_executavel_total"] = kept["valor_executado"] / CAPITAL_BASE
    kept["contribuicao_executavel"] = kept["peso_executavel_total"] * kept["retorno_periodo"]
    kept["mes"] = month_key
    kept["tipo_alocacao"] = "acao"

    cdi_ret = platform_cdi_return(month_row.get("retorno_cdi_liquido_periodo", 0.0))
    cdi_row = pd.DataFrame([{
        "mes": month_key, "ticker": "CDI", "nome": "Reserva/CDI liquido", "setor": "Protecao",
        "tipo_alocacao": "cdi_residual", "preco_entrada": np.nan, "data_preco_entrada": "",
        "peso_teorico_total": cdi_weight_target, "peso_executavel_total": cdi_value / CAPITAL_BASE,
        "quantidade": np.nan, "valor_alvo": cdi_target_value, "valor_executado": cdi_value,
        "retorno_periodo": cdi_ret, "contribuicao_executavel": (cdi_value / CAPITAL_BASE) * cdi_ret,
        "status_preco": "ok", "forma_execucao": "aplicacao_cdi_mais_sobra", "aliquota_ir_cdi": PLATFORM_CDI_IR, "aliquota_ir_cdi_origem": SOURCE_CDI_IR,
    }])

    cols = ["mes", "ticker", "nome", "setor", "tipo_alocacao", "peso_teorico_total", "peso_executavel_total", "peso_teorico_acoes", "peso_reescalado_acoes", "valor_alvo", "preco_entrada", "data_preco_entrada", "quantidade", "valor_executado", "retorno_periodo", "contribuicao_executavel", "nota_final", "beta", "cv", "status_preco", "forma_execucao", "aliquota_ir_cdi", "aliquota_ir_cdi_origem"]
    for col in cols:
        if col not in kept.columns:
            kept[col] = np.nan
    exec_df = pd.concat([kept[cols], cdi_row.reindex(columns=cols)], ignore_index=True)
    if not removed.empty:
        if "motivo_remocao" not in removed.columns:
            removed["motivo_remocao"] = "peso_dentro_da_parte_acoes_menor_que_minimo"
        removed["motivo_remocao"] = removed["motivo_remocao"].fillna("peso_dentro_da_parte_acoes_menor_que_minimo")
        removed["mes"] = month_key

    meta = {
        "mes": month_key,
        "n_acoes_teoricas": len(stocks),
        "n_acoes_executaveis": int((kept["tipo_alocacao"] == "acao").sum()),
        "n_removidas_peso_minimo": int((stocks["peso_teorico_acoes"] < MIN_WEIGHT_INSIDE_STOCKS).sum()),
        "n_removidas_quantidade_zero": int(len(zero_qty)),
        "valor_acoes_executado": value_stocks,
        "valor_cdi_executado": cdi_value,
        "peso_acoes_executavel": value_stocks / CAPITAL_BASE,
        "peso_cdi_executavel": cdi_value / CAPITAL_BASE,
        "retorno_executavel": float(exec_df["contribuicao_executavel"].sum()),
        "retorno_teorico_t44a": float(month_row.get("retorno_total", np.nan)),
        "retorno_ibov": float(month_row.get("retorno_expost_ibov", np.nan)),
        "alfa_executavel": float(exec_df["contribuicao_executavel"].sum() - float(month_row.get("retorno_expost_ibov", np.nan))),
        "alfa_teorico_t44a": float(month_row.get("alfa_vs_ibov", np.nan)),
        "delta_retorno_vs_teorico": float(exec_df["contribuicao_executavel"].sum() - float(month_row.get("retorno_total", np.nan))),
        "data_inicio_performance": month_row.get("data_inicio_performance"),
        "data_avaliacao": month_row.get("data_avaliacao"),
        "regime_previsto_norm": month_row.get("regime_previsto_norm"),
        "aliquota_ir_cdi": PLATFORM_CDI_IR,
        "aliquota_ir_cdi_origem": SOURCE_CDI_IR,
        "tipo_regime_expost": month_row.get("tipo_regime_expost"),
    }
    return exec_df, removed, meta


def summarize(group: pd.DataFrame, label: str) -> dict[str, Any]:
    return {
        "grupo": label,
        "meses": int(len(group)),
        "retorno_executavel": compound_return(group["retorno_executavel"]),
        "retorno_teorico_t44a": compound_return(group["retorno_teorico_t44a"]),
        "retorno_ibov": compound_return(group["retorno_ibov"]),
        "alfa_executavel_vs_ibov": compound_return(group["retorno_executavel"]) - compound_return(group["retorno_ibov"]),
        "alfa_teorico_vs_ibov": compound_return(group["retorno_teorico_t44a"]) - compound_return(group["retorno_ibov"]),
        "delta_exec_vs_teorico": compound_return(group["retorno_executavel"]) - compound_return(group["retorno_teorico_t44a"]),
        "taxa_acerto_executavel": float((group["retorno_executavel"] > group["retorno_ibov"]).mean()),
        "taxa_acerto_teorico": float((group["retorno_teorico_t44a"] > group["retorno_ibov"]).mean()),
        "drawdown_executavel": max_drawdown(group["retorno_executavel"]),
        "peso_acoes_medio_executavel": float(pd.to_numeric(group["peso_acoes_executavel"], errors="coerce").mean()),
        "n_acoes_executaveis_medio": float(pd.to_numeric(group["n_acoes_executaveis"], errors="coerce").mean()),
    }


def main() -> None:
    if not INPUT_45.exists():
        raise FileNotFoundError(INPUT_45)
    if not INPUT_44.exists():
        raise FileNotFoundError(INPUT_44)

    monthly = pd.read_excel(INPUT_45, sheet_name="Mes a Mes vs 36C")
    monthly["mes"] = monthly["mes"].astype(str).str[:7]
    monthly = monthly[monthly["modelo"].astype(str).eq(MODEL)].copy()
    monthly = monthly.sort_values("mes")

    portfolios = pd.read_excel(INPUT_44, sheet_name="Carteiras")
    portfolios["mes"] = portfolios["mes"].astype(str).str[:7]
    portfolios = portfolios[portfolios["cenario_t44"].astype(str).eq(MODEL)].copy()

    exec_rows: list[pd.DataFrame] = []
    removed_rows: list[pd.DataFrame] = []
    month_rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for _, mrow in monthly.iterrows():
        mes = str(mrow["mes"])
        stocks = portfolios[portfolios["mes"].eq(mes)].copy()
        exec_df, removed, meta = build_executable(mes, mrow, stocks)
        exec_rows.append(exec_df)
        if not removed.empty:
            removed_rows.append(removed)
        month_rows.append(meta)
        bad = exec_df[exec_df["status_preco"].astype(str).ne("ok") & exec_df["ticker"].astype(str).ne("CDI")]
        for _, row in bad.iterrows():
            missing.append({"mes": mes, "ticker": row.get("ticker"), "motivo": row.get("status_preco")})

    exec_all = pd.concat(exec_rows, ignore_index=True) if exec_rows else pd.DataFrame()
    removed_all = pd.concat(removed_rows, ignore_index=True) if removed_rows else pd.DataFrame()
    month_df = pd.DataFrame(month_rows)
    month_df["ano"] = month_df["mes"].astype(str).str[:4].astype(int)
    month_df["bateu_ibov_executavel"] = month_df["retorno_executavel"] > month_df["retorno_ibov"]
    month_df["bateu_ibov_teorico"] = month_df["retorno_teorico_t44a"] > month_df["retorno_ibov"]

    summary_rows = [summarize(month_df, "2022-2026")]
    subset_2023 = month_df[month_df["mes"] >= "2023-01"]
    if not subset_2023.empty:
        summary_rows.append(summarize(subset_2023, "2023-2026"))
    summary = pd.DataFrame(summary_rows)
    summary_year = pd.DataFrame([summarize(g, str(year)) for year, g in month_df.groupby("ano")])
    by_regime = pd.DataFrame([summarize(g, str(regime)) for regime, g in month_df.groupby("tipo_regime_expost")])

    validation = month_df[["mes", "retorno_executavel", "retorno_teorico_t44a", "retorno_ibov", "delta_retorno_vs_teorico", "peso_acoes_executavel", "peso_cdi_executavel", "n_acoes_executaveis"]].copy()
    validation["pesos_fecham_100"] = (validation["peso_acoes_executavel"] + validation["peso_cdi_executavel"] - 1.0).abs() < 1e-9
    validation["diferenca_retorno_recalculado"] = exec_all.groupby("mes")["contribuicao_executavel"].sum().reindex(validation["mes"]).to_numpy() - validation["retorno_executavel"].to_numpy()
    validation["retorno_bate_contribuicoes"] = validation["diferenca_retorno_recalculado"].abs() < 1e-9

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    EXCEL_DIR.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Resumo Geral", index=False)
        summary_year.to_excel(writer, sheet_name="Resumo Ano", index=False)
        by_regime.to_excel(writer, sheet_name="Resumo Regime Real", index=False)
        month_df.to_excel(writer, sheet_name="Mes a Mes", index=False)
        exec_all.to_excel(writer, sheet_name="Carteiras Executaveis", index=False)
        removed_all.to_excel(writer, sheet_name="Ativos Removidos", index=False)
        validation.to_excel(writer, sheet_name="Validacao", index=False)
        pd.DataFrame(missing).to_excel(writer, sheet_name="Log Precos", index=False)

    lines = []
    lines.append("Teste 46 - Carteira Executavel com Quantidades Inteiras")
    lines.append(f"Modelo base: {MODEL}")
    lines.append(f"Capital base: R$ {CAPITAL_BASE:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'))
    lines.append(f"Minimo por ativo dentro da parte de acoes: {MIN_WEIGHT_INSIDE_STOCKS:.2%}")
    lines.append(f"Mercado fracionario com quantidade inteira: {FRACTIONAL_MARKET}")
    lines.append(f"IR CDI origem: {SOURCE_CDI_IR:.2%}")
    lines.append(f"IR CDI plataforma: {PLATFORM_CDI_IR:.2%}")
    lines.append("")
    for _, row in summary.iterrows():
        lines.append(f"{row['grupo']}: executavel={pct(row['retorno_executavel'])}; teorico={pct(row['retorno_teorico_t44a'])}; IBOV={pct(row['retorno_ibov'])}; alfa_exec={pct(row['alfa_executavel_vs_ibov'])}; acerto_exec={pct(row['taxa_acerto_executavel'])}; delta_exec_vs_teorico={pct(row['delta_exec_vs_teorico'])}")
    lines.append("")
    lines.append("Resumo por ano:")
    for _, row in summary_year.iterrows():
        lines.append(f"{row['grupo']}: exec={pct(row['retorno_executavel'])}; teorico={pct(row['retorno_teorico_t44a'])}; IBOV={pct(row['retorno_ibov'])}; alfa_exec={pct(row['alfa_executavel_vs_ibov'])}; acerto={pct(row['taxa_acerto_executavel'])}; n_medio={row['n_acoes_executaveis_medio']:.1f}")
    lines.append("")
    lines.append(f"Validacao pesos != 100%: {int((~validation['pesos_fecham_100']).sum())}")
    lines.append(f"Validacao retorno != contribuicoes: {int((~validation['retorno_bate_contribuicoes']).sum())}")
    lines.append(f"Precos faltantes/problemas: {len(missing)}")
    lines.append(f"Arquivo gerado: {OUTPUT_FILE}")
    lines.append(f"Log gerado: {LOG_FILE}")
    text = "\n".join(lines)
    LOG_FILE.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
