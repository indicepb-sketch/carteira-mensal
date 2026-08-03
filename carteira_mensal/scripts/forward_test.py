from __future__ import annotations

import argparse
import copy
import importlib
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for item in (str(SRC), str(SCRIPTS)):
    if item not in sys.path:
        sys.path.insert(0, item)

from utils import load_settings  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
import shadow_consolidada_6meses as sc  # noqa: E402
import shadow_backtest_2025 as bt  # noqa: E402
import shadow_consolidado_14_13b as rg14  # noqa: E402


EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
LAMBDA_BETA_FORWARD = 1.5
FORWARD_MODE_NAME = "shadow.forward_test"
EXPOSURE_BY_REGIME = {
    "alta": 1.00,
    "oportunidade": 1.00,
    "queda_leve": 0.60,
    "queda_forte": 0.30,
    "indefinido": 1.00,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Forma a carteira forward-test em modo sombra consolidado.")
    parser.add_argument("--mes", default=None, help="Mes de referencia no formato YYYY-MM. Default: mes atual.")
    parser.add_argument("--force-base", action="store_true", help="Gera nova base mensal mesmo se ja existir.")
    return parser.parse_args()


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def month_parts(month: str | None) -> tuple[int, int, str]:
    if month is None:
        today = pd.Timestamp(datetime.today()).normalize()
        return int(today.year), int(today.month), f"{today.year:04d}-{today.month:02d}"
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(month).strip())
    if not match:
        raise ValueError("--mes deve estar no formato YYYY-MM")
    year = int(match.group(1))
    mon = int(match.group(2))
    if mon < 1 or mon > 12:
        raise ValueError("mes invalido")
    return year, mon, f"{year:04d}-{mon:02d}"


def first_business_day(year: int, month: int) -> pd.Timestamp:
    return pd.bdate_range(f"{year:04d}-{month:02d}-01", periods=1)[0].normalize()


def previous_month_end(year: int, month: int) -> pd.Timestamp:
    first = pd.Timestamp(f"{year:04d}-{month:02d}-01")
    return (first - pd.Timedelta(days=1)).normalize()


def latest_base_workbook(year: int, month: int) -> Path | None:
    prefix = f"carteira_recomendada_{year:04d}_{month:02d}_v"
    files = sorted(EXCEL_DIR.glob(f"{prefix}*.xlsx"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def fields_from_sheet(path: Path, sheet: str) -> dict[str, Any]:
    return sh.fields_dict(sh.read_sheet(path, sheet))


def check_shadow_defaults(settings: dict) -> pd.DataFrame:
    shadow = settings.get("shadow", {}) or {}
    rows = []
    for key, value in shadow.items():
        if key.startswith("enable_") or key == "forward_test":
            rows.append({"flag": f"shadow.{key}", "valor_default": value, "default_false": bool(value) is False})
    return pd.DataFrame(rows)


def production_intact_check(settings: dict) -> tuple[pd.DataFrame, bool]:
    checks = []
    for rel in ["src/main.py", "src/optimizer.py", "src/scoring.py"]:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        checks.append(
            {
                "arquivo": rel,
                "contem_forward_test": "forward_test" in text,
                "contem_enable_carteira_tamanho_livre": "enable_carteira_tamanho_livre" in text,
                "contem_enable_objetivo_retorno": "enable_objetivo_retorno" in text,
                "status": "ok_sem_modo_forward" if "forward_test" not in text else "verificar",
            }
        )
    flags = check_shadow_defaults(settings)
    all_false = bool(flags["default_false"].all()) if not flags.empty else True
    return pd.concat([pd.DataFrame(checks), flags], ignore_index=True, sort=False), all_false and all(
        not row["contem_forward_test"] for row in checks
    )


def generate_base_workbook(year: int, month: int, selection_cutoff: pd.Timestamp, formation_date: pd.Timestamp, log) -> Path:
    prod_main = importlib.import_module("main")
    original_load_settings = prod_main.load_settings
    original_fetch_prices = prod_main.fetch_yfinance_prices
    original_fetch_indexes = prod_main.fetch_index_prices

    def configured_settings() -> dict[str, Any]:
        settings = load_settings()
        settings = copy.deepcopy(settings)
        settings.setdefault("strategy", {})
        settings["strategy"]["ano_referencia"] = int(year)
        settings["strategy"]["mes_referencia"] = int(month)
        settings["strategy"]["usar_primeiro_dia_util_mes"] = True
        settings["strategy"]["data_formacao_carteira"] = formation_date.date().isoformat()
        settings["strategy"]["data_avaliacao_carteira"] = formation_date.date().isoformat()
        return settings

    def trim_frame(frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty:
            return frame
        idx = pd.to_datetime(frame.index, errors="coerce")
        out = frame.copy()
        out.index = idx
        return out.loc[out.index <= selection_cutoff].copy()

    def fetch_prices_cut(*args, **kwargs):
        prices, records = original_fetch_prices(*args, **kwargs)
        return trim_frame(prices), records

    def fetch_indexes_cut(*args, **kwargs):
        prices, records = original_fetch_indexes(*args, **kwargs)
        return trim_frame(prices), records

    before = set(EXCEL_DIR.glob(f"carteira_recomendada_{year:04d}_{month:02d}_v*.xlsx"))
    prod_main.load_settings = configured_settings
    prod_main.fetch_yfinance_prices = fetch_prices_cut
    prod_main.fetch_index_prices = fetch_indexes_cut
    try:
        log(
            f"Gerando base mensal {year:04d}-{month:02d}: formacao={formation_date.date()} "
            f"com series cortadas em {selection_cutoff.date()}"
        )
        prod_main.main()
    finally:
        prod_main.load_settings = original_load_settings
        prod_main.fetch_yfinance_prices = original_fetch_prices
        prod_main.fetch_index_prices = original_fetch_indexes

    after = set(EXCEL_DIR.glob(f"carteira_recomendada_{year:04d}_{month:02d}_v*.xlsx"))
    created = sorted(after - before, key=lambda p: p.stat().st_mtime)
    if created:
        return created[-1]
    latest = latest_base_workbook(year, month)
    if latest is None:
        raise FileNotFoundError("base mensal nao foi gerada")
    return latest


def ensure_base_workbook(year: int, month: int, selection_cutoff: pd.Timestamp, formation_date: pd.Timestamp, force: bool, log) -> Path:
    latest = latest_base_workbook(year, month)
    if latest is not None and not force:
        data_base = fields_from_sheet(latest, "Data Base Carteira")
        limit = str(data_base.get("data_limite_dados_selecao", ""))
        formation = str(data_base.get("data_formacao_carteira", ""))
        if limit.startswith(selection_cutoff.date().isoformat()) and formation.startswith(formation_date.date().isoformat()):
            log(f"Base mensal existente reutilizada: {latest.name}")
            return latest
        log(f"Base existente {latest.name} nao bate datas esperadas; gerando nova versao.")
    return generate_base_workbook(year, month, selection_cutoff, formation_date, log)


def make_forward_d3(original_d3):
    extended = sc.make_extended_d3(original_d3)

    def wrapped(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
        return extended(frame, settings)

    return wrapped


def forward_13b_inputs(mes_key: str, workbook: Path) -> dict[str, Any]:
    # Usa somente dados da planilha-base de formacao; sem ex-post/futuro.
    rg14.MONTHS = {mes_key: workbook.name}
    data = rg14.month_audit_inputs(mes_key, pd.DataFrame(columns=["mes", "ticker", "retorno_realizado_periodo"]))
    data["ibov_expost"] = np.nan
    data["label_expost"] = "em_aberto"
    return data


def classify_forward_13b(mes_key: str, workbook: Path) -> tuple[str, str, dict[str, Any]]:
    inputs = forward_13b_inputs(mes_key, workbook)
    bucket, reason = rg14.anti_false_positive_conservative(inputs)
    return bucket, reason, inputs


def make_forward_profiles(mes_key: str, workbook: Path, original_beta_profile, original_downturn_profile):
    bucket, reason, _inputs = classify_forward_13b(mes_key, workbook)

    def beta_target_profile(path: Path, settings: dict) -> dict[str, Any]:
        base = dict(original_beta_profile(path, settings))
        base.update(bt.beta_profile_for_bucket(bucket))
        base["beta_target_reason"] = reason
        base["forward_13b_bucket"] = bucket
        return base

    def downturn_profile(path: Path, settings: dict) -> dict[str, Any]:
        base = dict(original_downturn_profile(path, settings))
        subtype = "alta" if bucket in {"alta", "oportunidade"} else ("queda_forte" if bucket == "queda_forte" else "queda_leve_lateral")
        base["subtipo_queda"] = subtype
        base["motivo_subtipo_queda"] = reason
        base["forward_13b_bucket"] = bucket
        return base

    return bucket, reason, beta_target_profile, downturn_profile


def run_forward_portfolio(mes_key: str, workbook: Path, base_settings: dict) -> dict[str, Any]:
    sh.MONTHS = {mes_key: workbook.name}
    original_build = sh.build_free_size_portfolio
    original_d3 = sh.technical_veto_to_penalty_in_opportunity
    original_beta_profile = sh.beta_target_profile
    original_downturn_profile = sh.downturn_regime_profile
    try:
        bucket, reason, beta_profile, downturn_profile = make_forward_profiles(mes_key, workbook, original_beta_profile, original_downturn_profile)
        sh.build_free_size_portfolio = sc.consolidated_build_free_size_portfolio
        sh.technical_veto_to_penalty_in_opportunity = make_forward_d3(original_d3)
        sh.beta_target_profile = beta_profile
        sh.downturn_regime_profile = downturn_profile
        result = sh.run_free_size_for_month(
            mes_key,
            workbook,
            base_settings,
            lambda_beta=LAMBDA_BETA_FORWARD,
            downturn_signal="SINAL_A_DEFENSIVO",
        )
        result.setdefault("metrics", {})["forward_13b_bucket"] = bucket
        result.setdefault("metrics", {})["forward_13b_motivo"] = reason
        return result
    finally:
        sh.build_free_size_portfolio = original_build
        sh.technical_veto_to_penalty_in_opportunity = original_d3
        sh.beta_target_profile = original_beta_profile
        sh.downturn_regime_profile = original_downturn_profile


def anchor_june(base_settings: dict, log) -> tuple[pd.DataFrame, bool]:
    june_path = EXCEL_DIR / "carteira_recomendada_2026_06_v4.xlsx"
    reference_path = EXCEL_DIR / "shadow_consolidado_14_13b.xlsx"
    rows = []
    if not june_path.exists() or not reference_path.exists():
        return pd.DataFrame([{"status": "falhou", "motivo": "arquivos de referencia do Teste 14/junho ausentes"}]), False
    try:
        reference = pd.read_excel(reference_path, sheet_name="carteiras")
    except Exception as exc:
        return pd.DataFrame([{"status": "falhou", "motivo": f"erro ao ler referencia Teste 14: {exc}"}]), False
    expected_df = reference[reference.get("cenario", pd.Series(dtype=str)).astype(str).eq("13b_conservador") & reference.get("mes", pd.Series(dtype=str)).astype(str).eq("2026-06")].copy()
    if expected_df.empty:
        return pd.DataFrame([{"status": "falhou", "motivo": "carteira 13b_conservador 2026-06 ausente no Teste 14"}]), False
    expected = sh.weights_map(expected_df)
    result = run_forward_portfolio("2026-06", june_path, base_settings)
    actual = sh.weights_map(result.get("portfolio", pd.DataFrame()))
    passed = sh.same_weights(actual, expected, tol=1e-6)
    all_tickers = sorted(set(expected) | set(actual))
    for ticker in all_tickers:
        rows.append(
            {
                "ticker": ticker,
                "peso_esperado_teste14_13b": expected.get(ticker, 0.0),
                "peso_recalculado_forward": actual.get(ticker, 0.0),
                "diferenca": actual.get(ticker, 0.0) - expected.get(ticker, 0.0),
            }
        )
    log(f"Ancora junho Teste 14 / 13B conservador: {'PASSOU' if passed else 'FALHOU'}")
    return pd.DataFrame(rows), passed


def entry_prices(base_workbook: Path, tickers: list[str]) -> dict[str, Any]:
    prelim = sh.read_sheet(base_workbook, "Analise Preliminar")
    if prelim.empty or "ticker" not in prelim.columns:
        return {}
    candidates = [
        "cotacao_atual",
        "cotacao atual",
        "preco_atual",
        "preÃ§o_atual",
        "fechamento_usado",
    ]
    price_col = next((col for col in candidates if col in prelim.columns), None)
    if price_col is None:
        numeric_cols = [col for col in prelim.columns if "preco" in col or "cotacao" in col]
        price_col = numeric_cols[0] if numeric_cols else None
    if price_col is None:
        return {}
    return prelim.drop_duplicates("ticker").set_index("ticker")[price_col].to_dict()



def exposure_for_metrics(metrics: dict[str, Any]) -> tuple[float, str]:
    regime = str(metrics.get("subtipo_queda", "") or "").strip()
    exposure = float(EXPOSURE_BY_REGIME.get(regime, 1.0))
    if regime == "queda_forte":
        return exposure, "queda_forte: 30% em acoes e 70% em CDI/Tesouro Selic liquido de IR"
    if regime == "queda_leve":
        return exposure, "queda_leve: 60% em acoes e 40% em CDI/Tesouro Selic liquido de IR"
    if regime in {"alta", "oportunidade"}:
        return exposure, f"{regime}: 100% investido"
    return exposure, "regime indefinido: 100% investido por fallback"

def build_forward_tables(
    mes_key: str,
    base_workbook: Path,
    result: dict[str, Any],
    anchor: pd.DataFrame,
    settings_check: pd.DataFrame,
    formation_date: pd.Timestamp,
    selection_cutoff: pd.Timestamp,
    command: str,
) -> dict[str, pd.DataFrame]:
    portfolio = result.get("portfolio", pd.DataFrame()).copy()
    metrics = result.get("metrics", {})
    exposure, exposure_reason = exposure_for_metrics(metrics)
    candidates = result.get("candidates", pd.DataFrame()).copy()
    regime_fields = fields_from_sheet(base_workbook, "Regime Mercado")
    data_base_fields = fields_from_sheet(base_workbook, "Data Base Carteira")
    prices = entry_prices(base_workbook, portfolio["ticker"].astype(str).tolist() if not portfolio.empty and "ticker" in portfolio else [])
    if not portfolio.empty:
        portfolio["preco_entrada_fechamento_mes_anterior"] = portfolio["ticker"].map(prices)
        portfolio["data_preco_entrada"] = selection_cutoff.date().isoformat()
        portfolio["data_formacao_forward"] = formation_date.date().isoformat()
        portfolio["retorno_expost_mes"] = np.nan
        portfolio["observacao_expost"] = "em aberto ate fechamento do mes"
        first_cols = [
            "ticker",
            "nome",
            "setor",
            "peso_recomendado",
            "preco_entrada_fechamento_mes_anterior",
            "data_preco_entrada",
            "beta",
            "nota_final",
            "forca_relativa_score",
            "tipo_timing",
            "decisao_preliminar_ajustada",
            "status_para_risco",
            "categoria_elegibilidade",
            "shadow_sinal_quedas_aplicado",
            "sinal_v3_original_tamanho_livre",
            "sinal_v3_ajustado_beta_tamanho_livre",
            "peso_antes_teto_tamanho_livre",
            "teto_tamanho_livre_aplicado",
            "retorno_expost_mes",
            "observacao_expost",
        ]
        portfolio = portfolio[[col for col in first_cols if col in portfolio.columns] + [col for col in portfolio.columns if col not in first_cols]]

    portfolio_aplicada = portfolio.copy()
    if not portfolio_aplicada.empty and "peso_recomendado" in portfolio_aplicada.columns:
        portfolio_aplicada["peso_modelo_100pct"] = portfolio_aplicada["peso_recomendado"]
        portfolio_aplicada["exposicao_investida"] = exposure
        portfolio_aplicada["peso_recomendado"] = portfolio_aplicada["peso_modelo_100pct"] * exposure
        portfolio_aplicada["peso_defensivo_cdi"] = 1.0 - exposure
        if (1.0 - exposure) > 1e-9:
            cash_row = {col: np.nan for col in portfolio_aplicada.columns}
            cash_row.update({
                "ticker": "CDI",
                "nome": "CDI / Tesouro Selic",
                "setor": "Renda Fixa",
                "peso_recomendado": 1.0 - exposure,
                "peso_modelo_100pct": np.nan,
                "exposicao_investida": exposure,
                "peso_defensivo_cdi": 1.0 - exposure,
                "observacao_expost": "parcela defensiva em CDI/Tesouro Selic; retorno liquido de IR sera calculado automaticamente na parcial/fechamento via BCB SGS 12",
            })
            portfolio_aplicada = pd.concat([portfolio_aplicada, pd.DataFrame([cash_row])], ignore_index=True, sort=False)
    regime = pd.DataFrame(
        [
            {"campo": "mes_forward", "valor": mes_key},
            {"campo": "modo", "valor": FORWARD_MODE_NAME},
            {"campo": "base_mensal_usada", "valor": base_workbook.name},
            {"campo": "data_formacao_forward", "valor": formation_date.date().isoformat()},
            {"campo": "data_limite_dados_selecao", "valor": selection_cutoff.date().isoformat()},
            {"campo": "preco_entrada", "valor": f"fechamento ajustado em/ate {selection_cutoff.date().isoformat()}"},
            {"campo": "regime_mercado", "valor": regime_fields.get("mercado_classificacao", "")},
            {"campo": "subtipo_mercado_favoravel", "valor": regime_fields.get("subtipo_mercado_favoravel", "")},
            {"campo": "motivo_subtipo_mercado_favoravel", "valor": regime_fields.get("motivo_subtipo_mercado_favoravel", "")},
            {"campo": "rsi_ibov_data_base", "valor": regime_fields.get("rsi_ibov_data_base", np.nan)},
            {"campo": "bollinger_ibov_data_base", "valor": regime_fields.get("bollinger_ibov_data_base", "")},
            {"campo": "pct_ativos_positivos_1m", "valor": regime_fields.get("pct_ativos_positivos_1m", np.nan)},
            {"campo": "bucket_13b_conservador", "valor": metrics.get("forward_13b_bucket", "")},
            {"campo": "motivo_13b_conservador", "valor": metrics.get("forward_13b_motivo", "")},
            {"campo": "subtipo_queda", "valor": metrics.get("subtipo_queda", "")},
            {"campo": "motivo_subtipo_queda", "valor": metrics.get("motivo_subtipo_queda", "")},
            {"campo": "sinal_usado", "valor": metrics.get("sinal_quedas_aplicado", "")},
            {"campo": "exposicao_investida", "valor": exposure},
            {"campo": "peso_defensivo_cdi", "valor": 1.0 - exposure},
            {"campo": "ativo_defensivo", "valor": "CDI / Tesouro Selic liquido de IR"},
            {"campo": "fonte_cdi", "valor": "Banco Central SGS serie 12 - CDI diario"},
            {"campo": "regra_ir_cdi", "valor": "IR regressivo sobre rendimento: ate 180d=22,5%; 181-360d=20%; 361-720d=17,5%; acima=15%"},
            {"campo": "motivo_exposicao", "valor": exposure_reason},
            {"campo": "beta_alvo", "valor": metrics.get("beta_target", np.nan)},
            {"campo": "beta_realizado_carteira", "valor": metrics.get("beta_carteira", np.nan)},
            {"campo": "desvio_beta_alvo", "valor": metrics.get("desvio_beta_target", np.nan)},
            {"campo": "lambda_beta", "valor": LAMBDA_BETA_FORWARD},
            {"campo": "lambda_cv", "valor": 0.5},
            {"campo": "retorno_expost_status", "valor": f"em aberto - {mes_key} ainda nao fechado"},
            {"campo": "comando_mensal", "valor": command},
        ]
    )
    valid = pd.DataFrame(
        [
            {"restricao": "soma_pesos_100", "valor": float(portfolio["peso_recomendado"].sum()) if not portfolio.empty and "peso_recomendado" in portfolio else np.nan, "ok": bool(not portfolio.empty and abs(float(portfolio["peso_recomendado"].sum()) - 1.0) <= 1e-6)},
            {"restricao": "peso_maximo_25", "valor": float(portfolio["peso_recomendado"].max()) if not portfolio.empty and "peso_recomendado" in portfolio else np.nan, "ok": bool(not portfolio.empty and float(portfolio["peso_recomendado"].max()) <= 0.250001)},
            {"restricao": "max_2_por_setor", "valor": int(portfolio.groupby("setor")["ticker"].count().max()) if not portfolio.empty and "setor" in portfolio else np.nan, "ok": bool(not portfolio.empty and ("setor" not in portfolio or int(portfolio.groupby("setor")["ticker"].count().max()) <= 2))},
            {"restricao": "minimo_5_acoes", "valor": len(portfolio), "ok": bool(len(portfolio) >= 5)},
            {"restricao": "sem_deterioracao_fundamental", "valor": "", "ok": bool(not sh.has_fundamental_deterioration_in_portfolio(portfolio))},
            {"restricao": "retorno_expost", "valor": "em_aberto", "ok": True},
        ]
    )
    valid_aplicada = pd.DataFrame([
        {"restricao": "soma_ativos_mais_cdi_100", "valor": float(portfolio_aplicada["peso_recomendado"].sum()) if not portfolio_aplicada.empty and "peso_recomendado" in portfolio_aplicada else np.nan, "ok": bool(not portfolio_aplicada.empty and abs(float(portfolio_aplicada["peso_recomendado"].sum()) - 1.0) <= 1e-6)},
        {"restricao": "exposicao_ativos", "valor": float(portfolio["peso_recomendado"].sum() * exposure) if not portfolio.empty and "peso_recomendado" in portfolio else np.nan, "ok": bool(not portfolio.empty and abs(float(portfolio["peso_recomendado"].sum() * exposure) - exposure) <= 1e-6)},
        {"restricao": "peso_maximo_ativo_sobre_aporte_total", "valor": float(portfolio_aplicada[~portfolio_aplicada["ticker"].isin(["CAIXA", "CDI"])]["peso_recomendado"].max()) if not portfolio_aplicada.empty and "ticker" in portfolio_aplicada and "peso_recomendado" in portfolio_aplicada else np.nan, "ok": bool(not portfolio_aplicada.empty and float(portfolio_aplicada[~portfolio_aplicada["ticker"].isin(["CAIXA", "CDI"])]["peso_recomendado"].max()) <= 0.250001)},
        {"restricao": "cdi_conforme_regime", "valor": 1.0 - exposure, "ok": True},
    ])
    command_df = pd.DataFrame(
        [
            {"item": "comando_mensal", "valor": command},
            {"item": "observacao", "valor": "rodar no primeiro dia util; o script usa modo sombra forward_test em memoria e nao altera producao"},
            {"item": "base_temporal", "valor": "formacao no primeiro dia util do mes; entrada no fechamento do ultimo pregao anterior"},
        ]
    )
    expost = portfolio[["ticker", "peso_recomendado", "data_formacao_forward", "data_preco_entrada", "preco_entrada_fechamento_mes_anterior", "retorno_expost_mes", "observacao_expost"]].copy() if not portfolio.empty else pd.DataFrame()
    candidate_cols = [
        "ticker",
        "nome",
        "setor",
        "liberado_para_otimizacao",
        "shadow_liberado_para_otimizacao",
        "liberado_por_d3",
        "motivo_bloqueio_original_d3",
        "motivo_bloqueio_otimizacao",
        "penalizacoes_otimizacao",
        "nota_final",
        "score_prioridade_otimizacao",
        "forca_relativa_score",
        "retorno_medio",
        "beta",
        "correlacao_ibov",
        "roe",
        "margem_liquida",
        "pl_atual",
        "tipo_timing",
        "tipo_watchlist",
    ]
    return {
        "Resumo Forward": regime,
        "Carteira Forward": portfolio,
        "Carteira Aplicada": portfolio_aplicada,
        "Validacao": valid,
        "Validacao Aplicada": valid_aplicada,
        "Retorno Expost Em Aberto": expost,
        "Regime Mercado Base": pd.DataFrame([{"campo": key, "valor": value} for key, value in regime_fields.items()]),
        "Data Base Carteira": pd.DataFrame([{"campo": key, "valor": value} for key, value in data_base_fields.items()]),
        "Candidatos Sombra": candidates[[col for col in candidate_cols if col in candidates.columns]].copy() if not candidates.empty else pd.DataFrame(),
        "Ancora Junho": anchor,
        "Verificacao Producao": settings_check,
        "Comando Mensal": command_df,
    }


def write_forward_workbook(tables: dict[str, pd.DataFrame], year: int, month: int) -> Path:
    path = EXCEL_DIR / f"carteira_forward_{year:04d}_{month:02d}.xlsx"
    if path.exists():
        path = EXCEL_DIR / f"carteira_forward_{year:04d}_{month:02d}_v{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        for name, frame in tables.items():
            data = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
            data = data.loc[:, ~data.columns.duplicated()].copy()
            sheet = name[:31]
            data.to_excel(writer, sheet_name=sheet, index=False)
            worksheet = writer.sheets[sheet]
            for idx, col in enumerate(data.columns):
                width = min(max(len(str(col)) + 2, 12), 42)
                worksheet.set_column(idx, idx, width)
    return path


def main() -> None:
    args = parse_args()
    year, month, mes_key = month_parts(args.mes)
    formation_date = first_business_day(year, month)
    selection_cutoff = previous_month_end(year, month)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"forward_test_{year:04d}_{month:02d}_{datetime.now():%Y%m%d_%H%M%S}.log"
    logs: list[str] = []

    def log(message: str) -> None:
        print(message)
        logs.append(message)

    base_settings = load_settings()
    settings_check, production_ok = production_intact_check(base_settings)
    log(f"Verificacao producao/flags shadow: {'OK' if production_ok else 'VERIFICAR'}")
    if not production_ok:
        log("Falha na verificacao de producao ou flags shadow; abortando antes de formar carteira.")
        pd.DataFrame(settings_check).to_excel(EXCEL_DIR / f"carteira_forward_{year:04d}_{month:02d}_verificacao_falhou.xlsx", index=False)
        log_path.write_text("\n".join(logs), encoding="utf-8")
        raise SystemExit(1)

    anchor, anchor_ok = anchor_june(base_settings, log)
    if not anchor_ok:
        log(f"Ancora de junho falhou; abortando antes de formar {mes_key}.")
        log_path.write_text("\n".join(logs), encoding="utf-8")
        raise SystemExit(1)

    base_workbook = ensure_base_workbook(year, month, selection_cutoff, formation_date, args.force_base, log)
    data_base = fields_from_sheet(base_workbook, "Data Base Carteira")
    actual_limit = str(data_base.get("data_limite_dados_selecao", ""))
    actual_formation = str(data_base.get("data_formacao_carteira", ""))
    if not actual_limit.startswith(selection_cutoff.date().isoformat()) or not actual_formation.startswith(formation_date.date().isoformat()):
        log(f"Datas da base nao conferem: formacao={actual_formation}; limite={actual_limit}; esperado {formation_date.date()} / {selection_cutoff.date()}")
        log_path.write_text("\n".join(logs), encoding="utf-8")
        raise SystemExit(1)

    base_settings = copy.deepcopy(base_settings)
    base_settings.setdefault("shadow", {})["forward_test"] = True
    result = run_forward_portfolio(mes_key, base_workbook, base_settings)
    portfolio = result.get("portfolio", pd.DataFrame())
    command = r".\.venv\Scripts\python.exe scripts\forward_test.py --mes YYYY-MM"
    command_this_month = rf".\.venv\Scripts\python.exe scripts\forward_test.py --mes {mes_key}"
    tables = build_forward_tables(mes_key, base_workbook, result, anchor, settings_check, formation_date, selection_cutoff, command, )
    tables["Comando Mensal"].loc[len(tables["Comando Mensal"])] = {"item": f"comando_{year:04d}_{month:02d}", "valor": command_this_month}
    output = write_forward_workbook(tables, year, month)

    valid = tables["Validacao"]
    if not bool(valid["ok"].all()):
        log("REGRESSAO/VALIDACAO: alguma restricao da carteira forward falhou.")
    log(f"Base mensal usada: {base_workbook.name}")
    log(f"Arquivo forward gerado: {output}")
    log(f"Data formacao: {formation_date.date()} | limite selecao/preco entrada: {selection_cutoff.date()}")
    if portfolio.empty:
        log("Carteira forward nao foi formada.")
    else:
        weights = sh.format_weights(sh.weights_map(portfolio))
        metrics = result.get("metrics", {})
        exposure, exposure_reason = exposure_for_metrics(metrics)
        log(f"Carteira forward modelo 100%: {weights}")
        log(f"Exposicao aplicada: {exposure:.0%}; CDI/Tesouro Selic: {1.0 - exposure:.0%}; motivo={exposure_reason}")
        log(
            f"Bucket13B={metrics.get('forward_13b_bucket', '')}; regime={metrics.get('subtipo_queda', '')}; sinal={metrics.get('sinal_quedas_aplicado', '')}; "
            f"beta_alvo={metrics.get('beta_target', np.nan)}; beta_real={metrics.get('beta_carteira', np.nan)}"
        )
    log(f"Comando mensal: {command}")
    log_path.write_text("\n".join(logs), encoding="utf-8")
    print(f"Log gerado: {log_path}")


if __name__ == "__main__":
    main()




