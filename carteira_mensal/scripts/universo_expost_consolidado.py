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

from data_loader import fetch_index_prices, fetch_yfinance_prices  # noqa: E402
from main import _period_return, _price_at_or_before  # noqa: E402
from utils import load_settings  # noqa: E402

MONTHS = [(2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6)]
EXCEL_DIR = ROOT / "output" / "excel"
UNIVERSE_DIR = ROOT / "data" / "processed"
OUTPUT_FILE = EXCEL_DIR / "universo_expost_consolidado.xlsx"


def norm_col(name: Any) -> str:
    text = str(name).strip().lower()
    for old, new in [("ã", "a"), ("á", "a"), ("à", "a"), ("â", "a"), ("é", "e"), ("ê", "e"), ("í", "i"), ("ó", "o"), ("ô", "o"), ("ú", "u"), ("ç", "c")]:
        text = text.replace(old, new)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    seen: dict[str, int] = {}
    cols = []
    for col in out.columns:
        base = norm_col(col)
        count = seen.get(base, 0)
        seen[base] = count + 1
        cols.append(base if count == 0 else f"{base}_{count}")
    out.columns = cols
    return out


def read_sheet(path: Path, sheet: str) -> pd.DataFrame:
    try:
        return normalize_columns(pd.read_excel(path, sheet_name=sheet))
    except Exception:
        return pd.DataFrame()


def latest_workbook(year: int, month: int) -> Path | None:
    files = list(EXCEL_DIR.glob(f"carteira_recomendada_{year}_{month:02d}_v*.xlsx"))
    if not files:
        files = list(EXCEL_DIR.glob(f"carteira_recomendada_{year}_{month:02d}.xlsx"))
    if not files:
        return None

    def version(path: Path) -> int:
        match = re.search(r"_v(\d+)\.xlsx$", path.name)
        return int(match.group(1)) if match else 0

    return max(files, key=version)


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return str(value).strip() == ""


def first_value(sources: list[dict[str, Any]], keys: list[str], default: Any = np.nan) -> Any:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if not is_missing(value):
                return value
    return default


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if is_missing(value):
        return False
    return str(value).strip().lower() in {"true", "verdadeiro", "1", "sim", "yes", "y"}


def to_float(value: Any, default: float = 0.0) -> float:
    if is_missing(value):
        return default
    if isinstance(value, str):
        text = value.strip().replace("%", "").replace(",", ".")
        try:
            number = float(text)
            return number / 100.0 if "%" in value else number
        except Exception:
            return default
    try:
        return float(value)
    except Exception:
        return default


def ticker_value(value: Any) -> str | None:
    if is_missing(value):
        return None
    ticker = str(value).strip().upper()
    if re.fullmatch(r"[A-Z0-9]{4,8}\.SA", ticker):
        return ticker
    return None


def ticker_column(df: pd.DataFrame) -> str | None:
    for col in ["ticker_yfinance", "ticker", "ativo", "codigo"]:
        if col in df.columns:
            return col
    return None


def frame_by_ticker(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    col = ticker_column(df)
    if not col:
        return {}
    data: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        ticker = ticker_value(row.get(col))
        if ticker and ticker not in data:
            record = row.to_dict()
            record["ticker"] = ticker
            data[ticker] = record
    return data


def fields_dict(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {}
    if {"campo", "valor"}.issubset(df.columns):
        return {str(row["campo"]): row["valor"] for _, row in df.iterrows() if not is_missing(row.get("campo"))}
    if {"metrica", "valor"}.issubset(df.columns):
        return {str(row["metrica"]): row["valor"] for _, row in df.iterrows() if not is_missing(row.get("metrica"))}
    return {}


def as_date(value: Any) -> pd.Timestamp | None:
    if is_missing(value):
        return None
    try:
        return pd.Timestamp(value).normalize()
    except Exception:
        return None


def universe_csv_path(year: int, month: int) -> Path:
    return UNIVERSE_DIR / f"universo_ibovespa_{year}_{month:02d}.csv"


def read_month_universe(year: int, month: int) -> pd.DataFrame:
    path = universe_csv_path(year, month)
    if not path.exists():
        return pd.DataFrame()
    df = normalize_columns(pd.read_csv(path))
    col = ticker_column(df)
    if not col:
        return pd.DataFrame()
    df = df.copy()
    df["ticker"] = df[col].map(ticker_value)
    df = df[df["ticker"].notna()].drop_duplicates("ticker")
    return df


def month_metadata(path: Path) -> dict[str, Any]:
    fields = fields_dict(read_sheet(path, "Data Base Carteira"))
    return {
        "data_formacao": as_date(first_value([fields], ["data_formacao_carteira", "data_formacao"])),
        "data_inicio_performance": as_date(first_value([fields], ["data_inicio_performance", "data_limite_dados_selecao"])),
        "data_avaliacao": as_date(first_value([fields], ["data_avaliacao_carteira", "data_avaliacao"])),
    }


def regime_metadata(path: Path) -> tuple[Any, Any]:
    fields = fields_dict(read_sheet(path, "Regime Mercado"))
    regime = first_value([fields], ["mercado_classificacao", "regime_mercado", "classificacao_mercado"], "")
    subtipo = first_value([fields], ["subtipo_mercado_favoravel", "subtipo_mercado", "subtipo"], "")
    return regime, subtipo


def validation_status(path: Path) -> str:
    fields = fields_dict(read_sheet(path, "Validacao Final"))
    return str(first_value([fields], ["status da carteira", "status_carteira", "status"], "")).strip()


def selection_maps(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    analysis = frame_by_ticker(read_sheet(path, "Analise Preliminar"))
    risk = frame_by_ticker(read_sheet(path, "Candidatas Risco"))
    opt = frame_by_ticker(read_sheet(path, "Otimizacao"))
    portfolio = frame_by_ticker(read_sheet(path, "Resumo da Carteira"))
    return analysis, risk, opt, portfolio


def build_selection_status(path: Path) -> dict[str, dict[str, Any]]:
    analysis, risk, opt, portfolio = selection_maps(path)
    tickers = set(analysis) | set(risk) | set(opt) | set(portfolio)
    result: dict[str, dict[str, Any]] = {}

    for ticker in tickers:
        a = analysis.get(ticker, {})
        r = risk.get(ticker, {})
        o = opt.get(ticker, {})
        p = portfolio.get(ticker, {})
        sources = [p, o, r, a]
        peso_final = to_float(first_value(sources, ["peso_final", "peso_recomendado", "peso", "peso_final_carteira"], 0.0), 0.0)
        selected = peso_final > 1e-9
        status_para_risco = str(first_value(sources, ["status_para_risco"], "")).strip().lower()
        decisao = str(first_value(sources, ["decisao_preliminar_ajustada", "decisao_preliminar", "decisao_preliminar_ajustada_1"], "")).strip().lower()
        categoria = str(first_value(sources, ["categoria_elegibilidade"], "")).strip().lower()
        liberado = to_bool(first_value(sources, ["liberado_para_otimizacao"], False))
        bloqueado = to_bool(first_value(sources, ["bloqueado_otimizacao"], False))
        candidate_like = (
            liberado
            or status_para_risco in {"aprovada_para_risco", "moderada_para_risco"}
            or decisao in {"candidata_para_risco", "candidata_com_restricao"}
            or categoria in {"elegivel_forte", "elegivel_moderado"}
        )
        blocked_like = bloqueado or status_para_risco == "bloqueada_para_risco" or decisao.startswith("descartar")

        if selected:
            status = "selecionada"
            default_motive = "selecionada na carteira"
        elif candidate_like:
            status = "aprovada_nao_selecionada"
            default_motive = "aprovada/candidata, mas nao selecionada pela otimizacao"
        elif blocked_like:
            status = "bloqueada"
            default_motive = "bloqueada antes da otimizacao"
        else:
            status = "fora_do_funil"
            default_motive = "nao entrou no funil de candidatas"

        result[ticker] = {
            "status_na_selecao": status,
            "motivo_bloqueio_ou_status": first_value(
                sources,
                [
                    "motivo_bloqueio_otimizacao",
                    "motivo_bloqueio_aderencia_regime",
                    "motivo_status_para_risco",
                    "motivo_decisao_preliminar",
                    "motivo_exclusao",
                    "decisao_de_entrada_na_carteira",
                    "decisao_entrada_na_carteira",
                ],
                default_motive,
            ),
            "peso_final": peso_final if selected else 0.0,
            "nota_final": first_value(sources, ["nota_final", "nota_preliminar_ajustada", "nota_preliminar"], np.nan),
            "classificacao_forca_relativa": first_value(sources, ["classificacao_forca_relativa"], ""),
            "retorno_acumulado_1m": first_value(sources, ["retorno_acumulado_1m", "retorno_1m"], np.nan),
            "nome": first_value(sources, ["nome", "empresa", "name"], ""),
            "setor": first_value(sources, ["setor", "setor_fundamentus", "sector"], ""),
        }
    return result


def build_diagnostico_alertas(path: Path, mes: str) -> dict[str, Any]:
    risk_df = read_sheet(path, "Candidatas Risco")
    if risk_df.empty:
        risk_df = read_sheet(path, "Otimizacao")
    if risk_df.empty:
        risk_df = read_sheet(path, "Analise Preliminar")
        if "levada_para_risco" in risk_df.columns:
            risk_df = risk_df[risk_df["levada_para_risco"].map(to_bool)]

    n = int(len(risk_df))
    alerta_realizacao = risk_df["alerta_realizacao_pos_rali"].map(to_bool) if "alerta_realizacao_pos_rali" in risk_df.columns else pd.Series([], dtype=bool)
    qualidade = risk_df["qualidade_do_timing"].astype(str).str.lower() if "qualidade_do_timing" in risk_df.columns else pd.Series([], dtype=str)
    tipo_watchlist = risk_df["tipo_watchlist"].astype(str).str.lower() if "tipo_watchlist" in risk_df.columns else pd.Series([], dtype=str)

    n_alerta = int(alerta_realizacao.sum()) if len(alerta_realizacao) else 0
    return {
        "mes": mes,
        "n_candidatos_funil": n,
        "n_com_alerta_realizacao_pos_rali": n_alerta,
        "pct_com_alerta_realizacao_pos_rali": n_alerta / n if n else np.nan,
        "n_timing_com_alerta": int((qualidade == "timing_com_alerta").sum()) if len(qualidade) else 0,
        "n_timing_saudavel": int((qualidade == "timing_saudavel").sum()) if len(qualidade) else 0,
        "n_watchlist_flexivel": int(tipo_watchlist.str.contains("flexivel", na=False).sum()) if len(tipo_watchlist) else 0,
        "n_watchlist_monitoramento": int(tipo_watchlist.str.contains("monitoramento", na=False).sum()) if len(tipo_watchlist) else 0,
        "n_watchlist_bloqueante": int(tipo_watchlist.str.contains("bloqueante", na=False).sum()) if len(tipo_watchlist) else 0,
    }


def build_diagnostico_sem_carteira(path: Path, mes: str, settings: dict[str, Any]) -> dict[str, Any] | None:
    status = validation_status(path).lower()
    opt = read_sheet(path, "Otimizacao")
    if opt.empty:
        opt = read_sheet(path, "Analise Preliminar")
    has_selected = False
    if "peso_final" in opt.columns:
        has_selected = opt["peso_final"].map(lambda x: to_float(x, 0.0)).sum() > 0.999
    no_portfolio = ("sem_carteira" in status) or ("invalida" in status) or not has_selected
    if not no_portfolio:
        return None

    if "liberado_para_otimizacao" in opt.columns:
        liberated = opt[opt["liberado_para_otimizacao"].map(to_bool)].copy()
    else:
        liberated = opt.iloc[0:0].copy()
    if "peso_maximo_permitido_ativo" in liberated.columns:
        cap = liberated["peso_maximo_permitido_ativo"].map(lambda x: to_float(x, 0.0))
    else:
        cap = pd.Series([float(settings.get("portfolio", {}).get("max_weight", 0.20))] * len(liberated))
    soma_caps = float(cap.sum()) if len(cap) else 0.0

    max_sector_cap = np.nan
    if len(liberated) and "setor" in liberated.columns:
        tmp = liberated.copy()
        tmp["_cap"] = cap.values
        sector_caps = tmp.groupby("setor", dropna=False)["_cap"].sum()
        if not sector_caps.empty:
            max_sector_cap = float(sector_caps.max())
    text_cols = [c for c in ["motivo_concentracao_setorial", "motivo_alerta_bloco_risco", "motivo_bloqueio_otimizacao", "motivo_exclusao"] if c in opt.columns]
    motives = " ".join(opt[text_cols].astype(str).fillna("").agg(" ".join, axis=1).tolist()).lower() if text_cols else ""
    hard_sector = float(settings.get("portfolio", {}).get("hard_max_sector_weight", 0.40))
    flag_cap = soma_caps < 1.0
    flag_sector_block = (not flag_cap) and ((pd.notna(max_sector_cap) and max_sector_cap > hard_sector) or ("setor" in motives or "bloco" in motives))

    return {
        "mes": mes,
        "soma_pesos_maximos_liberados": soma_caps,
        "n_ativos_liberados_otimizacao": int(len(liberated)),
        "maior_concentracao_setorial_entre_liberados": max_sector_cap,
        "flag_inviavel_por_cap": bool(flag_cap),
        "flag_inviavel_por_setor_ou_bloco": bool(flag_sector_block),
        "status_carteira": status,
    }


def download_prices(tickers: list[str], settings: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    data_cfg = settings.get("data", {})
    months = int(data_cfg.get("history_months", 72))
    adjusted = bool(data_cfg.get("use_adjusted_prices", True))
    prices, _ = fetch_yfinance_prices(
        tickers,
        months,
        adjusted=adjusted,
        fallback_map=data_cfg.get("ticker_fallbacks", {}) or {},
        retries=int(data_cfg.get("yfinance_retries", 3)),
        min_rows=int(data_cfg.get("min_price_rows", 1)),
    )
    index_prices, _ = fetch_index_prices(data_cfg.get("indexes", {}) or {"IBOV": "^BVSP"}, months, adjusted=adjusted, settings=settings)
    return prices, index_prices


def main() -> None:
    settings = load_settings()
    log_rows: list[dict[str, Any]] = []
    month_packages: dict[str, dict[str, Any]] = {}
    all_tickers: set[str] = set()

    print("Arquivos mensais usados:")
    for year, month in MONTHS:
        mes = f"{year}-{month:02d}"
        workbook = latest_workbook(year, month)
        if workbook is None:
            log_rows.append({"mes": mes, "ticker": "", "motivo": "excel_mensal_ausente"})
            print(f"- {mes}: Excel ausente")
            continue
        print(f"- {mes}: {workbook.name}")

        universe_path = universe_csv_path(year, month)
        universe = read_month_universe(year, month)
        if universe.empty:
            log_rows.append({"mes": mes, "ticker": "", "motivo": f"universo_csv_ausente: {universe_path}"})
            meta = month_metadata(workbook)
            print(
                f"  datas: formacao={meta.get('data_formacao')}, inicio_perf={meta.get('data_inicio_performance')}, avaliacao={meta.get('data_avaliacao')} | universo CSV ausente, mes pulado"
            )
            continue

        meta = month_metadata(workbook)
        print(f"  datas: formacao={meta.get('data_formacao')}, inicio_perf={meta.get('data_inicio_performance')}, avaliacao={meta.get('data_avaliacao')}")
        selection = build_selection_status(workbook)
        regime, subtipo = regime_metadata(workbook)
        month_packages[mes] = {
            "year": year,
            "month": month,
            "workbook": workbook,
            "universe": universe,
            "selection": selection,
            "meta": meta,
            "regime_mercado": regime,
            "subtipo_mercado": subtipo,
        }
        all_tickers.update(universe["ticker"].dropna().astype(str).tolist())

    if all_tickers:
        prices, index_prices = download_prices(sorted(all_tickers), settings)
    else:
        prices, index_prices = pd.DataFrame(), pd.DataFrame()
    ibov_ticker = "IBOV" if "IBOV" in index_prices.columns else (index_prices.columns[0] if len(index_prices.columns) else "IBOV")

    rows: list[dict[str, Any]] = []
    alert_diag: list[dict[str, Any]] = []
    no_port_diag: list[dict[str, Any]] = []
    origin_rows: list[dict[str, Any]] = []

    for mes, package in month_packages.items():
        workbook = package["workbook"]
        origin_rows.append({"mes": mes, "arquivo_origem": str(workbook), "universo_origem": str(universe_csv_path(package["year"], package["month"]))})
        alert_diag.append(build_diagnostico_alertas(workbook, mes))
        no_port = build_diagnostico_sem_carteira(workbook, mes, settings)
        if no_port:
            no_port_diag.append(no_port)

        meta = package["meta"]
        start = meta.get("data_inicio_performance")
        end = meta.get("data_avaliacao")
        formation = meta.get("data_formacao")
        ibov_return = _period_return(index_prices, ibov_ticker, start, end) if start is not None and end is not None and not index_prices.empty else np.nan
        if pd.isna(ibov_return):
            log_rows.append({"mes": mes, "ticker": ibov_ticker, "motivo": "retorno_ibov_periodo_ausente"})

        for _, uni in package["universe"].iterrows():
            ticker = str(uni["ticker"])
            selection = package["selection"].get(ticker, {})
            start_price = _price_at_or_before(prices, ticker, start) if start is not None and not prices.empty else np.nan
            end_price = _price_at_or_before(prices, ticker, end) if end is not None and not prices.empty else np.nan
            if pd.isna(start_price):
                log_rows.append({"mes": mes, "ticker": ticker, "motivo": "preco_data_inicio_performance_ausente"})
            if pd.isna(end_price):
                log_rows.append({"mes": mes, "ticker": ticker, "motivo": "preco_data_avaliacao_ausente"})
            realized = (end_price / start_price) - 1.0 if pd.notna(start_price) and pd.notna(end_price) and start_price != 0 else np.nan
            relative = realized - ibov_return if pd.notna(realized) and pd.notna(ibov_return) else np.nan
            rows.append(
                {
                    "mes": mes,
                    "data_formacao": formation,
                    "data_inicio_performance": start,
                    "data_avaliacao": end,
                    "ticker": ticker,
                    "nome": selection.get("nome") or uni.get("nome", ""),
                    "setor": selection.get("setor") or uni.get("setor", ""),
                    "retorno_realizado_periodo": realized,
                    "retorno_ibov_periodo": ibov_return,
                    "retorno_relativo_vs_ibov": relative,
                    "bateu_ibov": bool(relative > 0) if pd.notna(relative) else np.nan,
                    "status_na_selecao": selection.get("status_na_selecao", "fora_do_funil"),
                    "motivo_bloqueio_ou_status": selection.get("motivo_bloqueio_ou_status", "ticker do universo fora das abas de selecao"),
                    "peso_final": selection.get("peso_final", 0.0),
                    "nota_final": selection.get("nota_final", np.nan),
                    "classificacao_forca_relativa": selection.get("classificacao_forca_relativa", ""),
                    "retorno_acumulado_1m": selection.get("retorno_acumulado_1m", np.nan),
                    "regime_mercado": package.get("regime_mercado", ""),
                    "subtipo_mercado": package.get("subtipo_mercado", ""),
                }
            )

    panel = pd.DataFrame(rows)
    log = pd.DataFrame(log_rows, columns=["mes", "ticker", "motivo"]).drop_duplicates()
    resumo_rows: list[dict[str, Any]] = []
    if not panel.empty:
        for mes, group in panel.groupby("mes", sort=True):
            beat = group[group["bateu_ibov"] == True]
            missed = beat[beat["status_na_selecao"] == "aprovada_nao_selecionada"].sort_values("retorno_relativo_vs_ibov", ascending=False)
            top5 = ", ".join(missed.head(5)["ticker"].astype(str).tolist())
            resumo_rows.append(
                {
                    "mes": mes,
                    "ativos_universo": int(group["ticker"].nunique()),
                    "ativos_com_retorno_valido": int(group["retorno_realizado_periodo"].notna().sum()),
                    "retorno_ibov_expost": group["retorno_ibov_periodo"].dropna().iloc[0] if group["retorno_ibov_periodo"].notna().any() else np.nan,
                    "n_ativos_universo_que_bateram_ibov": int(len(beat)),
                    "n_aprovados_nao_selecionados_que_bateram_ibov": int(len(missed)),
                    "top5_custo_oportunidade": top5,
                }
            )
    resumo = pd.DataFrame(resumo_rows)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        panel.to_excel(writer, sheet_name="Universo Expost", index=False)
        resumo.to_excel(writer, sheet_name="Resumo Mensal", index=False)
        log.to_excel(writer, sheet_name="Log Faltantes", index=False)
        pd.DataFrame(alert_diag).to_excel(writer, sheet_name="Diagnostico Alertas", index=False)
        pd.DataFrame(no_port_diag).to_excel(writer, sheet_name="Diagnostico Sem Carteira", index=False)
        pd.DataFrame(origin_rows).to_excel(writer, sheet_name="Arquivos Origem", index=False)

    print(f"Arquivo gerado: {OUTPUT_FILE}")
    print("Resumo ex-post:")
    if resumo.empty:
        print("- Nenhum mes processado; verifique Log Faltantes.")
    else:
        for _, row in resumo.iterrows():
            ibov_pct = row["retorno_ibov_expost"] * 100 if pd.notna(row["retorno_ibov_expost"]) else np.nan
            print(
                f"- {row['mes']}: IBOV={ibov_pct:.2f}%; "
                f"{row['n_ativos_universo_que_bateram_ibov']} ativos bateram o IBOV; "
                f"{row['n_aprovados_nao_selecionados_que_bateram_ibov']} eram candidatos/aprovados e nao selecionados; "
                f"top5 fora: {row['top5_custo_oportunidade'] or '-'}"
            )


if __name__ == "__main__":
    main()

