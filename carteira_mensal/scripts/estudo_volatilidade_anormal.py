from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
OUTPUT = EXCEL_DIR / "estudo_volatilidade_historica.xlsx"

VOL_BUCKETS = [-np.inf, 0.8, 1.2, 1.5, 2.0, np.inf]
VOL_LABELS = ["abaixo_do_normal", "normal", "atencao", "elevada", "anormal"]


def latest_recommended(year: int, month: int) -> Path | None:
    files = sorted(EXCEL_DIR.glob(f"carteira_recomendada_{year}_{month:02d}_v*.xlsx"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def monthly_files() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for year in [2024, 2025]:
        for month in range(1, 13):
            path = EXCEL_DIR / f"carteira_historica_{year}_{month:02d}.xlsx"
            if path.exists():
                out.append((f"{year}-{month:02d}", path))
    for month in range(1, 8):
        path = latest_recommended(2026, month)
        if path:
            out.append((f"2026-{month:02d}", path))
    return out


def fields_from_sheet(path: Path, sheet: str) -> dict[str, Any]:
    try:
        df = pd.read_excel(path, sheet_name=sheet)
    except Exception:
        return {}
    if {"campo", "valor"}.issubset(df.columns):
        return dict(zip(df["campo"].astype(str), df["valor"]))
    if {"metrica", "valor"}.issubset(df.columns):
        return dict(zip(df["metrica"].astype(str), df["valor"]))
    return {}


def latest_partial(mes: str) -> Path | None:
    year, month = mes.split("-")
    files = sorted(EXCEL_DIR.glob(f"parcial_carteira_forward_{year}_{month}_v*.xlsx"), key=lambda p: p.stat().st_mtime)
    if files:
        return files[-1]
    fallback = EXCEL_DIR / f"parcial_carteira_forward_{year}_{month}.xlsx"
    return fallback if fallback.exists() else None


def read_month_dates(path: Path, mes: str) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    partial = latest_partial(mes)
    if partial is not None:
        partial_fields = fields_from_sheet(partial, "Resumo Parcial")
        form = partial_fields.get("data_entrada")
        end = partial_fields.get("data_avaliacao_parcial")
        if form is not None and end is not None and not pd.isna(form) and not pd.isna(end):
            return pd.to_datetime(form), pd.to_datetime(form), pd.to_datetime(end)

    fields: dict[str, Any] = {}
    for sheet in ["Data Base Carteira", "Validacao Final"]:
        fields.update(fields_from_sheet(path, sheet))
    perf = pd.DataFrame()
    try:
        perf = pd.read_excel(path, sheet_name="Performance Realizada")
    except Exception:
        pass
    if not perf.empty:
        row = perf.iloc[0]
        form = row.get("data_formacao_carteira") or fields.get("data_formacao_carteira")
        start = row.get("data_inicio_performance") or form
        end = row.get("data_avaliacao_carteira") or fields.get("data_avaliacao_carteira")
    else:
        form = fields.get("data_formacao_carteira") or fields.get("data_limite_dados_selecao")
        start = fields.get("data_inicio_performance") or form
        end = fields.get("data_avaliacao_carteira")
    if pd.isna(form) or form is None:
        form = pd.Timestamp(f"{mes}-01")
    if pd.isna(start) or start is None:
        start = form
    if pd.isna(end) or end is None:
        end = pd.Timestamp(f"{mes}-01") + pd.offsets.MonthEnd(0)
    return pd.to_datetime(form), pd.to_datetime(start), pd.to_datetime(end)


def read_regime_map() -> dict[str, dict[str, Any]]:
    regimes: dict[str, dict[str, Any]] = {}
    for f in [EXCEL_DIR / "shadow_backtest_2024.xlsx", EXCEL_DIR / "shadow_backtest_2025.xlsx"]:
        if f.exists():
            try:
                df = pd.read_excel(f, sheet_name="mes_a_mes")
                for _, row in df.iterrows():
                    mes = str(row.get("mes"))[:7]
                    regimes[mes] = {
                        "regime_mercado": row.get("regime_mercado"),
                        "subtipo_mercado": row.get("subtipo_queda") or row.get("beta_target_subtipo"),
                        "bucket_regime": row.get("sinal_quedas_aplicado"),
                    }
            except Exception:
                pass
    for month in range(1, 8):
        p = latest_recommended(2026, month)
        if not p:
            continue
        mes = f"2026-{month:02d}"
        reg = fields_from_sheet(p, "Regime Mercado")
        val = fields_from_sheet(p, "Validacao Final")
        regimes[mes] = {
            "regime_mercado": reg.get("classificacao_geral_mercado") or reg.get("regime_mercado") or val.get("regime_mercado"),
            "subtipo_mercado": reg.get("subtipo_queda") or reg.get("subtipo_mercado") or val.get("subtipo_queda"),
            "bucket_regime": reg.get("sinal_quedas_aplicado") or val.get("sinal_quedas_aplicado"),
        }
    valid = EXCEL_DIR / "shadow_validacao_oos_2024_2026.xlsx"
    if valid.exists():
        try:
            df = pd.read_excel(valid, sheet_name="mes_a_mes")
            for _, row in df.iterrows():
                mes = str(row.get("mes"))[:7]
                regimes.setdefault(mes, {})["bucket_regime"] = row.get("bucket")
                regimes.setdefault(mes, {})["sinal"] = row.get("sinal")
        except Exception:
            pass
    return regimes


def price_at_or_before(close: pd.Series, dt: pd.Timestamp) -> float:
    s = close.loc[close.index <= dt]
    if s.empty:
        return np.nan
    return float(s.iloc[-1])


def realized_return(close: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    p0 = price_at_or_before(close, start)
    p1 = price_at_or_before(close, end)
    if not np.isfinite(p0) or not np.isfinite(p1) or p0 <= 0:
        return np.nan
    return float(p1 / p0 - 1.0)


def vol_stats(close: pd.Series, form: pd.Timestamp) -> dict[str, float]:
    close = close.loc[close.index <= form].dropna()
    ret = np.log(close / close.shift(1)).dropna()
    out: dict[str, float] = {}
    hist = ret.tail(756)
    for n in [21, 63, 126]:
        recent = ret.tail(n)
        rolling = hist.rolling(n).std().dropna()
        vol = float(recent.std()) if len(recent) >= max(10, n // 2) else np.nan
        hist_median = float(rolling.median()) if not rolling.empty else np.nan
        ratio = float(vol / hist_median) if np.isfinite(vol) and np.isfinite(hist_median) and hist_median > 0 else np.nan
        mean = float(recent.mean()) if len(recent) else np.nan
        cv = float(vol / mean) if np.isfinite(vol) and np.isfinite(mean) and mean != 0 else np.nan
        out[f"vol_{n}"] = vol
        out[f"vol_{n}_anualizada"] = vol * math.sqrt(252) if np.isfinite(vol) else np.nan
        out[f"ret_medio_{n}"] = mean
        out[f"cv_{n}"] = cv
        out[f"vol_hist_mediana_{n}"] = hist_median
        out[f"vol_ratio_{n}"] = ratio
    return out


def load_month_universe(mes: str, path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="Analise Preliminar")
    if "ticker" not in df.columns:
        return pd.DataFrame()
    keep = [c for c in [
        "ticker", "nome", "setor", "decisao_preliminar_ajustada", "status_para_risco",
        "categoria_elegibilidade", "tipo_timing", "nota_final", "nota_preliminar",
        "classificacao_forca_relativa", "retorno_acumulado_1m", "retorno_acumulado_4m",
        "rsi", "bollinger_status", "fundamento_bloqueante", "qualidade_fundamentalista"
    ] if c in df.columns]
    out = df[keep].copy()
    out["ticker"] = out["ticker"].astype(str).str.upper()
    out = out[out["ticker"].str.endswith(".SA")]
    out["mes"] = mes
    return out.drop_duplicates(["mes", "ticker"])


def classify_bucket(series: pd.Series) -> pd.Series:
    return pd.cut(series, bins=VOL_BUCKETS, labels=VOL_LABELS)


def summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.groupby(group_cols, dropna=False)
    return g.agg(
        linhas=("ticker", "count"),
        retorno_medio=("retorno_realizado_periodo", "mean"),
        retorno_mediano=("retorno_realizado_periodo", "median"),
        alfa_medio=("retorno_relativo_vs_ibov", "mean"),
        pct_bateu_ibov=("bateu_ibov", "mean"),
        media_vol_ratio_21=("vol_ratio_21", "mean"),
        media_vol_ratio_63=("vol_ratio_63", "mean"),
        media_vol_ratio_126=("vol_ratio_126", "mean"),
    ).reset_index()


def main() -> None:
    files = monthly_files()
    regimes = read_regime_map()
    month_frames = []
    month_meta = []
    for mes, path in files:
        try:
            form, start, end = read_month_dates(path, mes)
            uni = load_month_universe(mes, path)
            if uni.empty:
                continue
            reg = regimes.get(mes, {})
            uni["data_formacao"] = form
            uni["data_inicio_performance"] = start
            uni["data_avaliacao"] = end
            uni["arquivo_origem"] = path.name
            uni["regime_mercado"] = reg.get("regime_mercado")
            uni["subtipo_mercado"] = reg.get("subtipo_mercado")
            uni["bucket_regime"] = reg.get("bucket_regime") or reg.get("sinal")
            month_frames.append(uni)
            month_meta.append({"mes": mes, "arquivo": path.name, "data_formacao": form, "data_inicio_performance": start, "data_avaliacao": end, "tickers": len(uni)})
        except Exception as exc:
            month_meta.append({"mes": mes, "arquivo": path.name, "erro": str(exc)})
    base = pd.concat(month_frames, ignore_index=True) if month_frames else pd.DataFrame()
    if base.empty:
        raise SystemExit("Nenhum universo mensal encontrado")

    tickers = sorted(base["ticker"].dropna().unique().tolist())
    all_tickers = tickers + ["^BVSP"]
    earliest = pd.to_datetime(base["data_formacao"]).min() - pd.Timedelta(days=1200)
    latest = pd.to_datetime(base["data_avaliacao"]).max() + pd.Timedelta(days=5)
    print(f"Baixando precos: {len(tickers)} ativos + IBOV | {earliest.date()} a {latest.date()}")
    raw = yf.download(all_tickers, start=earliest.date().isoformat(), end=latest.date().isoformat(), auto_adjust=False, progress=False, threads=True)
    if raw.empty:
        raise SystemExit("yfinance nao retornou dados")
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Adj Close"] if "Adj Close" in raw.columns.get_level_values(0) else raw["Close"]
    else:
        close = raw[["Adj Close"]] if "Adj Close" in raw.columns else raw[["Close"]]
        close.columns = all_tickers[:1]
    close.index = pd.to_datetime(close.index).tz_localize(None)

    ibov = close["^BVSP"].dropna() if "^BVSP" in close.columns else pd.Series(dtype=float)
    rows = []
    missing = []
    for _, row in base.iterrows():
        ticker = row["ticker"]
        if ticker not in close.columns:
            missing.append({"mes": row["mes"], "ticker": ticker, "motivo": "sem_coluna_yfinance"})
            continue
        s = close[ticker].dropna()
        if s.empty:
            missing.append({"mes": row["mes"], "ticker": ticker, "motivo": "sem_precos"})
            continue
        form = pd.to_datetime(row["data_formacao"])
        start = pd.to_datetime(row["data_inicio_performance"])
        end = pd.to_datetime(row["data_avaliacao"])
        stats = vol_stats(s, form)
        ret_fwd = realized_return(s, start, end)
        ret_ibov = realized_return(ibov, start, end) if not ibov.empty else np.nan
        out = row.to_dict()
        out.update(stats)
        out["preco_formacao"] = price_at_or_before(s, start)
        out["preco_avaliacao"] = price_at_or_before(s, end)
        out["retorno_realizado_periodo"] = ret_fwd
        out["retorno_ibov_periodo"] = ret_ibov
        out["retorno_relativo_vs_ibov"] = ret_fwd - ret_ibov if np.isfinite(ret_fwd) and np.isfinite(ret_ibov) else np.nan
        out["bateu_ibov"] = bool(out["retorno_relativo_vs_ibov"] > 0) if np.isfinite(out["retorno_relativo_vs_ibov"]) else np.nan
        rows.append(out)
    detail = pd.DataFrame(rows)
    for n in [21, 63, 126]:
        detail[f"bucket_vol_ratio_{n}"] = classify_bucket(detail[f"vol_ratio_{n}"])

    resumo_faixa = pd.concat([
        summarize(detail.assign(janela="21d", bucket=detail["bucket_vol_ratio_21"]), ["janela", "bucket"]),
        summarize(detail.assign(janela="63d", bucket=detail["bucket_vol_ratio_63"]), ["janela", "bucket"]),
        summarize(detail.assign(janela="126d", bucket=detail["bucket_vol_ratio_126"]), ["janela", "bucket"]),
    ], ignore_index=True)
    resumo_regime = summarize(detail, ["bucket_regime", "bucket_vol_ratio_21"])
    resumo_setor = summarize(detail, ["setor", "bucket_vol_ratio_21"])
    top_quedas_vol_alta = detail[detail["vol_ratio_21"].ge(1.5)].sort_values("retorno_relativo_vs_ibov").head(50)
    top_altas_vol_alta = detail[detail["vol_ratio_21"].ge(1.5)].sort_values("retorno_relativo_vs_ibov", ascending=False).head(50)
    egie = detail[detail["ticker"].eq("EGIE3.SA")].sort_values("mes")
    meta = pd.DataFrame(month_meta)
    miss = pd.DataFrame(missing)

    # Pandas writer is used here because this is a data-heavy analytical workbook generated by a script.
    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        detail.to_excel(writer, sheet_name="Detalhe Ativo Mes", index=False)
        resumo_faixa.to_excel(writer, sheet_name="Resumo por Faixa", index=False)
        resumo_regime.to_excel(writer, sheet_name="Resumo por Regime", index=False)
        resumo_setor.to_excel(writer, sheet_name="Resumo por Setor", index=False)
        top_quedas_vol_alta.to_excel(writer, sheet_name="Vol Alta Piores", index=False)
        top_altas_vol_alta.to_excel(writer, sheet_name="Vol Alta Melhores", index=False)
        egie.to_excel(writer, sheet_name="Diagnostico EGIE3", index=False)
        meta.to_excel(writer, sheet_name="Meses Usados", index=False)
        miss.to_excel(writer, sheet_name="Log Faltantes", index=False)
        wb = writer.book
        for ws in wb.worksheets:
            ws.freeze_panes = "A2"
            for cell in ws[1]:
                cell.style = "Headline 4"
            for col in ws.columns:
                max_len = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 10), 34)
            for row_cells in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=ws.max_column):
                for cell in row_cells:
                    if isinstance(cell.value, float):
                        cell.number_format = "0.00%" if abs(cell.value) < 5 else "0.00"
    print(f"Arquivo gerado: {OUTPUT}")
    print(f"Linhas analisadas: {len(detail)} | meses: {detail['mes'].nunique()} | tickers unicos: {detail['ticker'].nunique()}")
    print("Resumo vol_ratio_21:")
    print(summarize(detail, ["bucket_vol_ratio_21"]).to_string(index=False))
    if not egie.empty:
        print("EGIE3 ultimos registros:")
        print(egie[["mes", "vol_ratio_21", "vol_ratio_63", "retorno_realizado_periodo", "retorno_relativo_vs_ibov", "bucket_vol_ratio_21"]].tail(8).to_string(index=False))


if __name__ == "__main__":
    main()

