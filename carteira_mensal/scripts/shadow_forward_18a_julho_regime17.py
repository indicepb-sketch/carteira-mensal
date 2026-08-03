from __future__ import annotations

import argparse
import copy
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for item in (SRC, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

import forward_partial as fp  # noqa: E402
import forward_test as ft  # noqa: E402
import shadow_backtest_2025 as bt  # noqa: E402
import shadow_consolidada_6meses as cons  # noqa: E402
import shadow_consolidado_14_13b as t14  # noqa: E402
import shadow_regime_16_risk_on_off as r16  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
from utils import load_settings  # noqa: E402

EXCEL_DIR = ROOT / "output" / "excel"
LOG_DIR = ROOT / "output" / "logs"
OUTPUT_FILE = EXCEL_DIR / "shadow_forward_18a_julho_regime17.xlsx"
LOG_FILE = LOG_DIR / "shadow_forward_18a_julho_regime17.log"

MONTH_KEY = "2026-07"
YEAR = 2026
MONTH = 7
SCENARIOS: dict[str, Callable[[dict[str, Any]], tuple[str, str]]] = {
    "oficial_13b": r16.baseline_13b,
    "regime17_mm50": r16.mm50_only,
    "regime17_voto": r16.majority_vote,
}




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Teste 18A: carteira sombra julho com Regime 17.")
    parser.add_argument("--allow-network", action="store_true", help="Autoriza yfinance e Banco Central para parcial atualizada.")
    return parser.parse_args()

def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def latest_official_forward() -> Path:
    files = sorted(
        [p for p in EXCEL_DIR.glob("carteira_forward_2026_07*.xlsx") if not p.name.startswith("parcial_")],
        key=lambda p: p.stat().st_mtime,
    )
    if not files:
        raise FileNotFoundError("carteira_forward_2026_07*.xlsx nao encontrado")
    return files[-1]




def latest_partial_file() -> Path | None:
    files = sorted(EXCEL_DIR.glob("parcial_carteira_forward_2026_07*.xlsx"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def load_partial_cache_frames(path: Path) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    try:
        assets = pd.read_excel(path, sheet_name="Ativos")
    except Exception:
        assets = pd.DataFrame()
    try:
        summary = pd.read_excel(path, sheet_name="Resumo Parcial")
        fields = dict(zip(summary["metrica"].astype(str), summary["valor"])) if {"metrica", "valor"}.issubset(summary.columns) else {}
    except Exception:
        fields = {}
    try:
        cdi_daily = pd.read_excel(path, sheet_name="CDI Diario")
    except Exception:
        cdi_daily = pd.DataFrame()
    fields["arquivo_cache_parcial"] = path.name
    return assets, fields, cdi_daily


def cache_prices_and_cdi_from_latest_partial() -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    files = sorted(EXCEL_DIR.glob("parcial_carteira_forward_2026_07*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return pd.DataFrame(), {}, pd.DataFrame()
    chosen = None
    cached = (pd.DataFrame(), {}, pd.DataFrame())
    for candidate in files:
        assets, fields, cdi_daily = load_partial_cache_frames(candidate)
        ibov = pd.to_numeric(fields.get("retorno_ibov_parcial", np.nan), errors="coerce")
        if pd.notna(ibov) and not assets.empty:
            chosen = candidate
            cached = (assets, fields, cdi_daily)
            break
    if chosen is None:
        chosen = files[0]
        cached = load_partial_cache_frames(chosen)
    assets, fields, cdi_daily = cached
    if assets.empty or not {"ticker", "preco_atual", "data_avaliacao"}.issubset(assets.columns):
        return pd.DataFrame(), fields, cdi_daily
    dates = pd.to_datetime(assets["data_avaliacao"], errors="coerce").dropna()
    eval_date = dates.max().normalize() if not dates.empty else pd.Timestamp(datetime.today()).normalize()
    data: dict[str, pd.Series] = {}
    for _, row in assets.iterrows():
        ticker = str(row.get("ticker", "")).upper()
        if ticker in {"", "CDI", "CAIXA"}:
            continue
        price = pd.to_numeric(row.get("preco_atual"), errors="coerce")
        if pd.notna(price):
            data[ticker] = pd.Series([float(price)], index=[eval_date])
    return pd.DataFrame(data), fields, cdi_daily


def ibov_cache_from_summary(fields: dict[str, Any], entry_date: pd.Timestamp, eval_date: pd.Timestamp) -> pd.DataFrame:
    ret = pd.to_numeric(fields.get("retorno_ibov_parcial", np.nan), errors="coerce")
    if pd.isna(ret):
        return pd.DataFrame()
    return pd.DataFrame({"^BVSP": pd.Series([1.0, 1.0 + float(ret)], index=[entry_date, eval_date])})


def cdi_from_local_daily_cache(start: pd.Timestamp, end: pd.Timestamp) -> tuple[float, float, pd.DataFrame, str]:
    for path in sorted(EXCEL_DIR.glob("parcial_carteira_forward_2026_07*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            daily = pd.read_excel(path, sheet_name="CDI Diario")
        except Exception:
            continue
        if daily.empty or not {"data", "cdi_diario_decimal"}.issubset(daily.columns):
            continue
        frame = daily.copy()
        frame["data"] = pd.to_datetime(frame["data"], errors="coerce")
        frame["cdi_diario_decimal"] = pd.to_numeric(frame["cdi_diario_decimal"], errors="coerce")
        frame = frame.dropna(subset=["data", "cdi_diario_decimal"]).sort_values("data")
        frame = frame[(frame["data"] >= start.normalize()) & (frame["data"] <= end.normalize())]
        if frame.empty:
            continue
        gross = float((1.0 + frame["cdi_diario_decimal"]).prod() - 1.0)
        net, _ir, _days = fp.cdi_net_return(gross, start - pd.Timedelta(days=1), end)
        return gross, net, frame, f"cache_cdi_diario:{path.name}"
    return np.nan, np.nan, pd.DataFrame(), "cache_cdi_diario_indisponivel"

def setup_month_mapping(base_workbook: Path) -> None:
    mapping = {MONTH_KEY: base_workbook.name}
    sh.MONTHS = mapping
    t14.MONTHS = mapping
    r16.MONTHS = mapping


def audit_inputs_for_july(base_workbook: Path) -> dict[str, Any]:
    setup_month_mapping(base_workbook)
    inputs = r16.month_audit_inputs(MONTH_KEY, pd.DataFrame(columns=["mes", "ticker", "retorno_realizado_periodo"]))
    inputs["ibov_expost"] = np.nan
    inputs["label_expost"] = "em_aberto"
    return inputs


def make_profiles(bucket: str, reason: str, original_beta_profile, original_downturn_profile):
    def beta_target_profile(path: Path, settings: dict) -> dict[str, Any]:
        base = dict(original_beta_profile(path, settings))
        base.update(bt.beta_profile_for_bucket(bucket))
        base["beta_target_reason"] = reason
        base["forward_regime17_bucket"] = bucket
        return base

    def downturn_profile(path: Path, settings: dict) -> dict[str, Any]:
        base = dict(original_downturn_profile(path, settings))
        subtype = "alta" if bucket in {"alta", "oportunidade"} else ("queda_forte" if bucket == "queda_forte" else "queda_leve_lateral")
        base["subtipo_queda"] = subtype
        base["motivo_subtipo_queda"] = reason
        base["forward_regime17_bucket"] = bucket
        return base

    return beta_target_profile, downturn_profile


def run_shadow_portfolio(base_workbook: Path, base_settings: dict, bucket: str, reason: str) -> dict[str, Any]:
    setup_month_mapping(base_workbook)
    original_build = sh.build_free_size_portfolio
    original_d3 = sh.technical_veto_to_penalty_in_opportunity
    original_beta_profile = sh.beta_target_profile
    original_downturn_profile = sh.downturn_regime_profile
    try:
        beta_profile, downturn_profile = make_profiles(bucket, reason, original_beta_profile, original_downturn_profile)
        sh.build_free_size_portfolio = cons.consolidated_build_free_size_portfolio
        sh.technical_veto_to_penalty_in_opportunity = ft.make_forward_d3(original_d3)
        sh.beta_target_profile = beta_profile
        sh.downturn_regime_profile = downturn_profile
        settings = copy.deepcopy(base_settings)
        settings.setdefault("shadow", {})["forward_test"] = True
        result = sh.run_free_size_for_month(
            MONTH_KEY,
            base_workbook,
            settings,
            lambda_beta=ft.LAMBDA_BETA_FORWARD,
            downturn_signal="SINAL_A_DEFENSIVO",
        )
        result.setdefault("metrics", {})["forward_regime17_bucket"] = bucket
        result.setdefault("metrics", {})["forward_regime17_motivo"] = reason
        return result
    finally:
        sh.build_free_size_portfolio = original_build
        sh.technical_veto_to_penalty_in_opportunity = original_d3
        sh.beta_target_profile = original_beta_profile
        sh.downturn_regime_profile = original_downturn_profile


def entry_prices(base_workbook: Path, portfolio: pd.DataFrame, selection_cutoff: pd.Timestamp) -> pd.DataFrame:
    out = portfolio.copy()
    prices = ft.entry_prices(base_workbook, out["ticker"].astype(str).tolist() if not out.empty and "ticker" in out else [])
    if not out.empty:
        out["preco_entrada_fechamento_mes_anterior"] = out["ticker"].map(prices)
        out["data_preco_entrada"] = selection_cutoff.date().isoformat()
        out["data_formacao_forward"] = ft.first_business_day(YEAR, MONTH).date().isoformat()
    return out


def apply_exposure(portfolio: pd.DataFrame, exposure: float) -> pd.DataFrame:
    applied = portfolio.copy()
    if applied.empty:
        return applied
    applied["peso_modelo_100pct"] = pd.to_numeric(applied["peso_recomendado"], errors="coerce")
    applied["exposicao_investida"] = exposure
    applied["peso_recomendado"] = applied["peso_modelo_100pct"] * exposure
    applied["peso_defensivo_cdi"] = 1.0 - exposure
    if 1.0 - exposure > 1e-9:
        row = {col: np.nan for col in applied.columns}
        row.update(
            {
                "ticker": "CDI",
                "nome": "CDI / Tesouro Selic",
                "setor": "Renda Fixa",
                "peso_recomendado": 1.0 - exposure,
                "exposicao_investida": exposure,
                "peso_defensivo_cdi": 1.0 - exposure,
            }
        )
        applied = pd.concat([applied, pd.DataFrame([row])], ignore_index=True, sort=False)
    return applied


def last_price(series: pd.Series, date: pd.Timestamp) -> tuple[float, pd.Timestamp | None]:
    return fp.last_price(series, date)


def scenario_partial(
    scenario: str,
    applied: pd.DataFrame,
    prices: pd.DataFrame,
    entry_date: pd.Timestamp,
    eval_date: pd.Timestamp,
    cdi_net: float,
    cdi_gross: float,
    cdi_source: str,
    cdi_ir: float,
    fonte_precos: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = []
    defensive = {"CAIXA", "CDI"}
    for _, row in applied.iterrows():
        ticker = str(row.get("ticker", "")).upper()
        weight = float(row.get("peso_recomendado", 0.0) or 0.0)
        if ticker in defensive:
            ret = cdi_net if pd.notna(cdi_net) else 0.0
            rows.append(
                {
                    "cenario": scenario,
                    "ticker": "CDI",
                    "peso_recomendado": weight,
                    "preco_entrada": np.nan,
                    "preco_atual": np.nan,
                    "data_avaliacao": eval_date.date().isoformat(),
                    "retorno_periodo": ret,
                    "contribuicao": ret * weight,
                    "fonte_preco": cdi_source,
                    "retorno_cdi_bruto_periodo": cdi_gross,
                    "aliquota_ir_cdi": cdi_ir,
                    "retorno_cdi_liquido_periodo": cdi_net,
                }
            )
            continue
        entry = row.get("preco_entrada_fechamento_mes_anterior", np.nan)
        if pd.isna(entry) and ticker in prices:
            entry, _ = last_price(prices[ticker], entry_date)
        current, price_date = last_price(prices[ticker], eval_date) if ticker in prices else (np.nan, None)
        ret = (current / float(entry) - 1.0) if pd.notna(entry) and pd.notna(current) and float(entry) else np.nan
        rows.append(
            {
                "cenario": scenario,
                "ticker": ticker,
                "peso_recomendado": weight,
                "preco_entrada": entry,
                "preco_atual": current,
                "data_avaliacao": price_date.date().isoformat() if price_date is not None else "",
                "retorno_periodo": ret,
                "contribuicao": ret * weight if pd.notna(ret) else np.nan,
                "fonte_preco": "yfinance",
                "retorno_cdi_bruto_periodo": np.nan,
                "aliquota_ir_cdi": np.nan,
                "retorno_cdi_liquido_periodo": np.nan,
            }
        )
    assets = pd.DataFrame(rows)
    ibov_entry, _ = last_price(prices["^BVSP"], entry_date) if "^BVSP" in prices else (np.nan, None)
    ibov_current, ibov_date = last_price(prices["^BVSP"], eval_date) if "^BVSP" in prices else (np.nan, None)
    ibov_ret = (ibov_current / ibov_entry - 1.0) if pd.notna(ibov_entry) and pd.notna(ibov_current) and ibov_entry else np.nan
    portfolio_ret = pd.to_numeric(assets["contribuicao"], errors="coerce").fillna(0.0).sum()
    summary = {
        "cenario": scenario,
        "data_entrada": entry_date.date().isoformat(),
        "data_avaliacao_parcial": ibov_date.date().isoformat() if ibov_date is not None else eval_date.date().isoformat(),
        "retorno_carteira_parcial_aplicada": portfolio_ret,
        "retorno_ibov_parcial": ibov_ret,
        "alfa_parcial_vs_ibov": portfolio_ret - ibov_ret if pd.notna(ibov_ret) else np.nan,
        "retorno_cdi_bruto_periodo": cdi_gross,
        "retorno_cdi_liquido_periodo": cdi_net,
        "fonte_cdi": cdi_source,
                "fonte_precos_parcial": fonte_precos,
    }
    return assets, summary


def compact_portfolio(portfolio: pd.DataFrame, scenario: str) -> pd.DataFrame:
    cols = [
        "ticker",
        "nome",
        "setor",
        "peso_recomendado",
        "peso_modelo_100pct",
        "exposicao_investida",
        "preco_entrada_fechamento_mes_anterior",
        "beta",
        "nota_final",
        "forca_relativa_score",
        "tipo_timing",
        "sinal_v3_original_tamanho_livre",
        "sinal_v3_ajustado_beta_tamanho_livre",
    ]
    out = portfolio[[c for c in cols if c in portfolio.columns]].copy() if not portfolio.empty else pd.DataFrame()
    out.insert(0, "cenario", scenario)
    return out


def main() -> None:
    args = parse_args()
    logs: list[str] = []

    def log(message: str) -> None:
        print(message, flush=True)
        logs.append(message)

    base_settings = load_settings()
    settings_check, production_ok = ft.production_intact_check(base_settings)
    log(f"Verificacao producao/flags shadow: {'OK' if production_ok else 'VERIFICAR'}")
    if not production_ok:
        raise SystemExit("Producao/flags shadow nao passaram na verificacao.")

    anchor, anchor_ok = ft.anchor_june(base_settings, log)
    if not anchor_ok:
        raise SystemExit("Ancora de junho falhou.")

    formation_date = ft.first_business_day(YEAR, MONTH)
    selection_cutoff = ft.previous_month_end(YEAR, MONTH)
    base_workbook = ft.ensure_base_workbook(YEAR, MONTH, selection_cutoff, formation_date, False, log)
    official_forward = latest_official_forward()
    inputs = audit_inputs_for_july(base_workbook)
    setup_month_mapping(base_workbook)

    scenario_results: dict[str, dict[str, Any]] = {}
    regime_rows = []
    portfolio_frames = []
    applied_frames = []
    all_tickers: set[str] = {"^BVSP"}

    for name, classifier in SCENARIOS.items():
        bucket, reason = classifier(inputs)
        result = run_shadow_portfolio(base_workbook, base_settings, bucket, reason)
        portfolio = entry_prices(base_workbook, sh.normalize_portfolio_weights(result.get("portfolio", pd.DataFrame())), selection_cutoff)
        metrics = result.get("metrics", {})
        exposure, exposure_reason = ft.exposure_for_metrics(metrics)
        applied = apply_exposure(portfolio, exposure)
        scenario_results[name] = {
            "bucket": bucket,
            "reason": reason,
            "result": result,
            "portfolio": portfolio,
            "applied": applied,
            "exposure": exposure,
            "exposure_reason": exposure_reason,
        }
        all_tickers.update([t for t in applied.get("ticker", pd.Series(dtype=str)).astype(str).str.upper().tolist() if t not in {"CDI", "CAIXA"}])
        regime_rows.append(
            {
                "cenario": name,
                "bucket_regime17": bucket,
                "motivo_regime17": reason,
                "sinal_beta_risk": inputs.get("sinal_beta_risk", ""),
                "sinal_mm50_risk": inputs.get("sinal_mm50_risk", ""),
                "med_beta": inputs.get("med_beta", np.nan),
                "pct_mm50_gt_mm100": inputs.get("pct_mm50_gt_mm100", np.nan),
                "subtipo_queda": metrics.get("subtipo_queda", ""),
                "sinal_usado": metrics.get("sinal_quedas_aplicado", ""),
                "exposicao_acoes": exposure,
                "peso_cdi": 1.0 - exposure,
                "beta_alvo": metrics.get("beta_target", np.nan),
                "beta_realizado_carteira": metrics.get("beta_carteira", np.nan),
                "motivo_exposicao": exposure_reason,
            }
        )
        portfolio_frames.append(compact_portfolio(portfolio, name))
        applied_frames.append(compact_portfolio(applied, name))
        log(f"{name}: bucket={bucket}; exposicao={exposure:.0%}; pesos={sh.format_weights(sh.weights_map(portfolio))}")

    eval_date = pd.Timestamp(datetime.today()).normalize()
    tickers = sorted(t for t in all_tickers if t)
    if args.allow_network:
        prices = fp.download_prices(tickers, selection_cutoff, eval_date)
        cdi_gross, cdi_daily, cdi_source = fp.fetch_cdi_gross_return(selection_cutoff + pd.Timedelta(days=1), eval_date)
        cdi_net, cdi_ir, _days = fp.cdi_net_return(cdi_gross, selection_cutoff, eval_date)
        fonte_precos = "yfinance"
    else:
        prices, cache_fields, cdi_daily = cache_prices_and_cdi_from_latest_partial()
        cached_dates = pd.to_datetime(prices.index, errors="coerce").dropna() if not prices.empty else pd.DatetimeIndex([])
        if len(cached_dates):
            eval_date = pd.Timestamp(cached_dates.max()).normalize()
        ibov_cache = ibov_cache_from_summary(cache_fields, selection_cutoff, eval_date)
        if not ibov_cache.empty:
            prices = prices.join(ibov_cache, how="outer") if not prices.empty else ibov_cache
        cdi_gross = pd.to_numeric(cache_fields.get("retorno_cdi_bruto_periodo", np.nan), errors="coerce")
        cdi_net = pd.to_numeric(cache_fields.get("retorno_cdi_liquido_periodo", np.nan), errors="coerce")
        cdi_ir = pd.to_numeric(cache_fields.get("aliquota_ir_cdi", 0.225), errors="coerce")
        cdi_source = "cache_parcial_local"
        if pd.isna(cdi_net):
            cdi_gross2, cdi_net2, cdi_daily2, cdi_source2 = cdi_from_local_daily_cache(selection_cutoff + pd.Timedelta(days=1), eval_date)
            if pd.notna(cdi_net2):
                cdi_gross, cdi_net, cdi_daily, cdi_source = cdi_gross2, cdi_net2, cdi_daily2, cdi_source2
        fonte_precos = "cache_parcial_local"
        missing = [t for t in tickers if t != "^BVSP" and t not in prices.columns]
        if missing:
            log("AVISO: sem preco em cache para: " + ", ".join(missing))

    partial_frames = []
    partial_rows = []
    for name, data in scenario_results.items():
        assets, row = scenario_partial(name, data["applied"], prices, selection_cutoff, eval_date, cdi_net, cdi_gross, cdi_source, cdi_ir, fonte_precos)
        row.update(
            {
                "bucket_regime17": data["bucket"],
                "exposicao_acoes": data["exposure"],
                "peso_cdi": 1.0 - data["exposure"],
                "tickers_pesos_modelo_100pct": sh.format_weights(sh.weights_map(data["portfolio"])),
                "arquivo_base": base_workbook.name,
                "arquivo_forward_oficial_referencia": official_forward.name,
                "arquivo_cache_parcial": cache_fields.get("arquivo_cache_parcial", "") if not args.allow_network else "consulta_rede",
            }
        )
        partial_rows.append(row)
        partial_frames.append(assets)

    partial_summary = pd.DataFrame(partial_rows)
    baseline_ret = partial_summary.loc[partial_summary["cenario"].eq("oficial_13b"), "retorno_carteira_parcial_aplicada"]
    baseline_alpha = partial_summary.loc[partial_summary["cenario"].eq("oficial_13b"), "alfa_parcial_vs_ibov"]
    base_ret = float(baseline_ret.iloc[0]) if not baseline_ret.empty else np.nan
    base_alpha = float(baseline_alpha.iloc[0]) if not baseline_alpha.empty else np.nan
    partial_summary["delta_retorno_vs_oficial_13b"] = partial_summary["retorno_carteira_parcial_aplicada"] - base_ret
    partial_summary["delta_alfa_vs_oficial_13b"] = partial_summary["alfa_parcial_vs_ibov"] - base_alpha

    validation_rows = []
    for name, data in scenario_results.items():
        portfolio = data["portfolio"]
        applied = data["applied"]
        validation_rows.extend(
            [
                {
                    "cenario": name,
                    "restricao": "soma_modelo_100pct",
                    "valor": float(portfolio["peso_recomendado"].sum()) if not portfolio.empty else np.nan,
                    "ok": bool(not portfolio.empty and abs(float(portfolio["peso_recomendado"].sum()) - 1.0) <= 1e-6),
                },
                {
                    "cenario": name,
                    "restricao": "soma_aplicada_ativos_cdi",
                    "valor": float(applied["peso_recomendado"].sum()) if not applied.empty else np.nan,
                    "ok": bool(not applied.empty and abs(float(applied["peso_recomendado"].sum()) - 1.0) <= 1e-6),
                },
                {
                    "cenario": name,
                    "restricao": "max_2_por_setor_modelo",
                    "valor": int(portfolio.groupby("setor")["ticker"].count().max()) if not portfolio.empty and "setor" in portfolio else np.nan,
                    "ok": bool(not portfolio.empty and ("setor" not in portfolio or int(portfolio.groupby("setor")["ticker"].count().max()) <= 2)),
                },
                {
                    "cenario": name,
                    "restricao": "sem_deterioracao_fundamental",
                    "valor": "",
                    "ok": bool(not sh.has_fundamental_deterioration_in_portfolio(portfolio)),
                },
            ]
        )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        partial_summary.to_excel(writer, sheet_name="Resumo Parcial", index=False)
        pd.DataFrame(regime_rows).to_excel(writer, sheet_name="Regime 17 Julho", index=False)
        pd.concat(portfolio_frames, ignore_index=True, sort=False).to_excel(writer, sheet_name="Carteiras Modelo", index=False)
        pd.concat(applied_frames, ignore_index=True, sort=False).to_excel(writer, sheet_name="Carteiras Aplicadas", index=False)
        pd.concat(partial_frames, ignore_index=True, sort=False).to_excel(writer, sheet_name="Ativos Parcial", index=False)
        pd.DataFrame(validation_rows).to_excel(writer, sheet_name="Validacao", index=False)
        pd.DataFrame([inputs]).to_excel(writer, sheet_name="Inputs Regime17", index=False)
        anchor.to_excel(writer, sheet_name="Ancora Junho", index=False)
        settings_check.to_excel(writer, sheet_name="Verificacao Producao", index=False)
        cdi_daily.to_excel(writer, sheet_name="CDI Diario", index=False)

    log("Resumo parcial:")
    for _, row in partial_summary.iterrows():
        log(
            f"  {row['cenario']}: retorno={pct(row['retorno_carteira_parcial_aplicada'])}; "
            f"IBOV={pct(row['retorno_ibov_parcial'])}; alfa={pct(row['alfa_parcial_vs_ibov'])}; "
            f"delta_ret_vs_13B={pct(row['delta_retorno_vs_oficial_13b'])}"
        )
    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
