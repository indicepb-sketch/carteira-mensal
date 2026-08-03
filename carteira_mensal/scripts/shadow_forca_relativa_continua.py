from __future__ import annotations

import copy
import re
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for p in (str(SRC), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from utils import load_settings  # noqa: E402
import shadow_simulacao as sh  # noqa: E402
import shadow_consolidada_6meses as sc  # noqa: E402

OUTPUT_FILE = ROOT / "output" / "excel" / "shadow_forca_relativa_continua.xlsx"
LOG_FILE = ROOT / "output" / "excel" / "shadow_forca_relativa_continua.log"

VARIANTS = {
    "ATUAL_BOOLEAN": {"label": "forca atual 0-5 booleana", "discount": False, "continuous": False},
    "CONTINUA_PURA": {"label": "forca relativa continua pura", "discount": False, "continuous": True},
    "CONTINUA_DESCONTO": {"label": "forca relativa continua com desconto suave", "discount": True, "continuous": True},
}

FORCA_WEIGHTS = {"rel_1m": 3.0, "rel_4m": 2.0, "rel_ytd": 1.0}
DISCOUNT_FACTOR = 0.75
CURRENT_VARIANT = "ATUAL_BOOLEAN"


def zscore(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() == 0:
        return pd.Series(0.0, index=series.index)
    values = values.fillna(values.median())
    std = float(values.std(ddof=0))
    if std < 1e-12:
        return pd.Series(0.0, index=series.index)
    return (values - float(values.mean())) / std


def bool_series(frame: pd.DataFrame, col: str, default: bool = False) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(default, index=frame.index)
    return frame[col].map(sh.to_bool).fillna(default)


def numeric_col(frame: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(default, index=frame.index)
    return pd.to_numeric(frame[col], errors="coerce")


def apply_continuous_relative_strength(scored: pd.DataFrame, variant: str) -> pd.DataFrame:
    out = scored.copy()
    if "forca_relativa_score_original_boolean" not in out.columns:
        out["forca_relativa_score_original_boolean"] = pd.to_numeric(
            out.get("forca_relativa_score", pd.Series(np.nan, index=out.index)), errors="coerce"
        )
    meta = VARIANTS[variant]
    out["forca_relativa_continua_variant"] = variant
    out["forca_relativa_continua_pesos"] = "rel_1m=3;rel_4m=2;rel_ytd=1"
    if not meta["continuous"]:
        out["forca_relativa_continua"] = np.nan
        out["forca_relativa_continua_fator_desconto"] = 1.0
        out["forca_relativa_continua_descontada"] = np.nan
        return out

    rel_1m = numeric_col(out, "retorno_1m_relativo_ibov", 0.0)
    rel_4m = numeric_col(out, "retorno_4m_relativo_ibov", 0.0)
    rel_ytd = numeric_col(out, "retorno_ytd_relativo_ibov", 0.0)
    raw = (
        FORCA_WEIGHTS["rel_1m"] * zscore(rel_1m)
        + FORCA_WEIGHTS["rel_4m"] * zscore(rel_4m)
        + FORCA_WEIGHTS["rel_ytd"] * zscore(rel_ytd)
    )
    out["forca_relativa_continua"] = raw
    factor = pd.Series(1.0, index=out.index)
    if meta["discount"]:
        rsi = numeric_col(out, "rsi", np.nan)
        boll = out.get("bollinger_status", pd.Series("", index=out.index)).astype(str).str.lower()
        extreme = rsi.gt(75.0) | boll.str.contains("sobrecompra", na=False)
        factor = factor.mask(extreme, DISCOUNT_FACTOR)
    out["forca_relativa_continua_fator_desconto"] = factor
    adjusted = raw * factor
    out["forca_relativa_continua_descontada"] = adjusted
    out["forca_relativa_score"] = adjusted
    return out


def make_continuous_signal_adder(original_add):
    def add_signals(scored: pd.DataFrame, settings: dict) -> pd.DataFrame:
        if scored.empty:
            return original_add(scored, settings)
        adjusted = apply_continuous_relative_strength(scored, CURRENT_VARIANT)
        return original_add(adjusted, settings)

    return add_signals


def technical_d3_prelim_mask(frame: pd.DataFrame) -> pd.Series:
    text_cols = [
        "motivo_bloqueio_otimizacao",
        "tipo_bloqueio_otimizacao",
        "motivo_decisao_preliminar",
        "motivo_watchlist_qualificada",
        "alertas_nao_bloqueantes",
        "alerta_timing",
        "sinal_timing",
        "justificativa_timing",
    ]
    combined = pd.Series("", index=frame.index, dtype="object")
    for col in text_cols:
        if col in frame.columns:
            combined = combined + " " + frame[col].fillna("").astype(str).str.lower()
    timing = frame.get("tipo_timing", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    prelim = frame.get("decisao_preliminar_ajustada", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    category = frame.get("categoria_elegibilidade", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    status = frame.get("status_para_risco", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    tech_text = combined.str.contains(
        "sobrecompra|esticad|tendencia tecnica|tendencia_mensal|watchlist|retorno_medio_negativo|timing",
        regex=True,
        na=False,
    )
    tech_state = (
        timing.str.contains("esticado|sobrecompra|neutro|fraco", regex=True, na=False)
        | prelim.str.contains("watchlist|restricao|tecnico", regex=True, na=False)
        | category.str.contains("moderad|restricao", regex=True, na=False)
        | status.str.contains("moderad|aprovad", regex=True, na=False)
    )
    return tech_text | tech_state


def deterioration_mask(frame: pd.DataFrame) -> pd.Series:
    roe = numeric_col(frame, "roe", np.nan)
    margem = numeric_col(frame, "margem_liquida", np.nan)
    pl = numeric_col(frame, "pl_atual", numeric_col(frame, "p_l_atual", np.nan))
    block = bool_series(frame, "fundamento_bloqueante", False)
    return block | roe.lt(0) | margem.lt(0) | pl.lt(0)


def make_extended_load_candidate_input(original_loader):
    def extended_loader(path: Path, settings: dict | None = None) -> pd.DataFrame:
        base = original_loader(path, settings)
        if settings is None:
            return base
        profile = settings.get("_runtime_downturn_profile", {}) or {}
        subtype = str(profile.get("subtipo_queda", "")).lower()
        beta_subtype = str(settings.get("_runtime_beta_target_subtipo", "")).lower()
        momentum_subtypes = {"favoravel_oportunidade", "favoravel_cansado", "favoravel_esticado", "favoravel_amplo"}
        if subtype != "alta" or beta_subtype not in momentum_subtypes:
            return base
        prelim = sh.read_sheet(path, "Analise Preliminar")
        if prelim.empty or "ticker" not in prelim.columns:
            return base
        prelim = sh.enrich_candidate_input(prelim.drop_duplicates("ticker").copy(), path, include_downturn_cols=False)
        if prelim.empty:
            return base
        base_tickers = set(base.get("ticker", pd.Series(dtype=str)).astype(str)) if not base.empty else set()
        mask = ~prelim["ticker"].astype(str).isin(base_tickers)
        mask &= technical_d3_prelim_mask(prelim)
        mask &= ~deterioration_mask(prelim)
        if "retorno_medio" in prelim.columns:
            mask &= numeric_col(prelim, "retorno_medio", np.nan).notna()
        if "beta" in prelim.columns:
            mask &= numeric_col(prelim, "beta", np.nan).notna()
        extra = prelim[mask].copy()
        if extra.empty:
            return base
        extra["shadow_d3_extendida_adicionada_do_preliminar"] = True
        extra["motivo_bloqueio_original_d3"] = extra.get("motivo_bloqueio_otimizacao", pd.Series("veto_tecnico_preliminar", index=extra.index)).fillna("veto_tecnico_preliminar")
        extra["bloqueado_otimizacao"] = False
        extra["liberado_para_otimizacao"] = True
        extra["tipo_bloqueio_otimizacao"] = ""
        extra["motivo_bloqueio_otimizacao"] = "relaxado_d3_extendida_veto_tecnico_para_penalizacao"
        extra["liberado_por_d3"] = True
        extra["d3_extendida_subtipo_original"] = beta_subtype
        extra["d3_extendida_sinal_ativo"] = "V3_MOMENTUM"
        combined = pd.concat([base, extra], ignore_index=True, sort=False) if not base.empty else extra
        return combined.drop_duplicates("ticker", keep="first").reset_index(drop=True)

    return extended_loader


def consolidated_beta_target_profile_factory(original_profile):
    def profile(path: Path, settings: dict) -> dict:
        base = dict(original_profile(path, settings))
        match = re.search(r"2026_(\d{2})", path.name)
        mes_key = f"2026-{match.group(1)}" if match else ""
        base.update(sc.CONSOLIDATED_BETA_PROFILE.get(mes_key, {}))
        return base

    return profile


def consolidated_downturn_profile_factory(original_profile):
    def profile(path: Path, settings: dict) -> dict:
        base = dict(original_profile(path, settings))
        match = re.search(r"2026_(\d{2})", path.name)
        mes_key = f"2026-{match.group(1)}" if match else ""
        subtype, reason = sc.CONSOLIDATED_SIGNAL_PROFILE.get(
            mes_key,
            (base.get("subtipo_queda", ""), base.get("motivo_subtipo_queda", "")),
        )
        base["subtipo_queda"] = subtype
        base["motivo_subtipo_queda"] = reason
        return base

    return profile


def classify_month_group(mes: str) -> str:
    if mes in {"2026-01", "2026-02"}:
        return "alta"
    if mes in {"2026-03", "2026-04", "2026-05"}:
        return "baixa"
    return "jun_oportunidade"


def compounded(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float(np.prod(1.0 + vals) - 1.0)


def add_expost_metrics(row: dict[str, Any], result: dict[str, Any], expost: pd.DataFrame, mes: str) -> dict[str, Any]:
    ret = sh.portfolio_expost_return(result.get("portfolio", pd.DataFrame()), expost, mes)
    ibov = sh.ibov_return(expost, mes)
    row["retorno_expost_carteira"] = ret
    row["retorno_expost_ibov"] = ibov
    row["alfa_expost"] = ret - ibov if pd.notna(ret) and pd.notna(ibov) else np.nan
    return row


def d3_liberated_rows_by_variant(results_by_variant: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for variant, results in results_by_variant.items():
        for mes, result in results.items():
            cand = result.get("candidates", pd.DataFrame())
            if cand.empty or "liberado_por_d3" not in cand.columns:
                continue
            mask = cand["liberado_por_d3"].map(sh.to_bool).fillna(False)
            for _, row in cand[mask].iterrows():
                rows.append({
                    "variante_forca": variant,
                    "mes": mes,
                    "ticker": row.get("ticker", ""),
                    "setor": row.get("setor", ""),
                    "motivo_bloqueio_original_d3": row.get("motivo_bloqueio_original_d3", row.get("motivo_bloqueio_otimizacao", "")),
                    "d3_extendida_subtipo_original": row.get("d3_extendida_subtipo_original", ""),
                    "d3_adicionada_do_preliminar": row.get("shadow_d3_extendida_adicionada_do_preliminar", False),
                    "retorno_1m_relativo_ibov": row.get("retorno_1m_relativo_ibov", np.nan),
                    "retorno_realizado_periodo": row.get("retorno_realizado_periodo", np.nan),
                })
    return pd.DataFrame(rows)


def portfolio_rows_by_variant(results_by_variant: dict[str, dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for variant, results in results_by_variant.items():
        for mes, result in results.items():
            portfolio = result.get("portfolio", pd.DataFrame())
            if portfolio.empty:
                continue
            panel = expost[expost["mes"].astype(str).eq(mes)].set_index("ticker")
            for _, r in portfolio.iterrows():
                ticker = str(r.get("ticker", ""))
                ret_asset = panel.loc[ticker, "retorno_realizado_periodo"] if ticker in panel.index and "retorno_realizado_periodo" in panel else np.nan
                peso = float(pd.to_numeric(pd.Series([r.get("peso_recomendado", r.get("peso_final", 0.0))]), errors="coerce").fillna(0).iloc[0])
                rows.append({
                    "variante_forca": variant,
                    "mes": mes,
                    "ticker": ticker,
                    "peso": peso,
                    "retorno_expost_ativo": ret_asset,
                    "contribuicao_retorno": peso * ret_asset if pd.notna(ret_asset) else np.nan,
                    "setor": r.get("setor", ""),
                    "beta": r.get("beta", np.nan),
                    "nota_final": r.get("nota_final", np.nan),
                    "forca_relativa_score_original_boolean": r.get("forca_relativa_score_original_boolean", np.nan),
                    "forca_relativa_score_usada": r.get("forca_relativa_score", np.nan),
                    "forca_relativa_continua": r.get("forca_relativa_continua", np.nan),
                    "fator_desconto": r.get("forca_relativa_continua_fator_desconto", np.nan),
                    "retorno_1m_relativo_ibov": r.get("retorno_1m_relativo_ibov", np.nan),
                    "retorno_4m_relativo_ibov": r.get("retorno_4m_relativo_ibov", np.nan),
                    "retorno_ytd_relativo_ibov": r.get("retorno_ytd_relativo_ibov", np.nan),
                    "sinal_v3_norm": r.get("_shadow_signal_v3_norm", np.nan),
                    "sinal_v3_ajustado_beta": r.get("sinal_v3_ajustado_beta_tamanho_livre", np.nan),
                    "peso_antes_teto": r.get("peso_antes_teto_tamanho_livre", np.nan),
                    "teto_aplicado": r.get("teto_tamanho_livre_aplicado", False),
                })
    return pd.DataFrame(rows)


def validation_rows_by_variant(results_by_variant: dict[str, dict[str, Any]], expost: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for variant, results in results_by_variant.items():
        rows = sh.free_size_validation_rows(results, expost)
        df = pd.DataFrame(rows)
        if not df.empty:
            df.insert(0, "variante_forca", variant)
            frames.append(df)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def build_variant_summary(shadow_rows: list[dict[str, Any]]) -> pd.DataFrame:
    base = pd.DataFrame(shadow_rows)
    if base.empty:
        return base
    rows = []
    for variant, group in base.groupby("variante_forca", sort=False):
        for _, r in group.sort_values("mes").iterrows():
            rows.append(r.to_dict())
        rows.append({
            "variante_forca": variant,
            "mes": "ACUMULADO_6_MESES",
            "grupo_regime": "total",
            "retorno_expost_carteira": compounded(group["retorno_expost_carteira"]),
            "retorno_expost_ibov": compounded(group["retorno_expost_ibov"]),
            "alfa_expost": compounded(group["retorno_expost_carteira"]) - compounded(group["retorno_expost_ibov"]),
        })
        for label in ["alta", "baixa", "jun_oportunidade"]:
            sub = group[group["grupo_regime"].eq(label)]
            if sub.empty:
                continue
            rows.append({
                "variante_forca": variant,
                "mes": f"ACUMULADO_{label.upper()}",
                "grupo_regime": label,
                "retorno_expost_carteira": compounded(sub["retorno_expost_carteira"]),
                "retorno_expost_ibov": compounded(sub["retorno_expost_ibov"]),
                "alfa_expost": compounded(sub["retorno_expost_carteira"]) - compounded(sub["retorno_expost_ibov"]),
            })
    return pd.DataFrame(rows)


def write_workbook(
    anchor_rows: list[dict[str, Any]],
    shadow_rows: list[dict[str, Any]],
    anchor_results: dict[str, Any],
    results_by_variant: dict[str, dict[str, Any]],
    validation_df: pd.DataFrame,
    expost: pd.DataFrame,
    unit_df: pd.DataFrame,
) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        pd.DataFrame(anchor_rows).to_excel(writer, sheet_name="ancora_vs_real", index=False)
        build_variant_summary(shadow_rows).to_excel(writer, sheet_name="resumo_por_variante", index=False)
        portfolio_rows_by_variant(results_by_variant, expost).to_excel(writer, sheet_name="carteiras_por_mes_por_variante", index=False)
        validation_df.to_excel(writer, sheet_name="validacao_retorno", index=False)
        d3_liberated_rows_by_variant(results_by_variant).to_excel(writer, sheet_name="d3_extendida_liberadas", index=False)
        unit_df.to_excel(writer, sheet_name="teste_unitario_pesos", index=False)
        for mes, result in anchor_results.items():
            result.get("portfolio", pd.DataFrame()).to_excel(writer, sheet_name=f"ancora_{mes[-2:]}", index=False)
        for variant, results in results_by_variant.items():
            for mes, result in results.items():
                sheet = f"{variant[:10]}_{mes[-2:]}"
                result.get("portfolio", pd.DataFrame()).to_excel(writer, sheet_name=sheet[:31], index=False)


def main() -> None:
    global CURRENT_VARIANT
    sh.MONTHS = sc.MONTHS_6
    logs: list[str] = []

    def log(message: str) -> None:
        print(message)
        logs.append(message)

    base_settings = load_settings()
    expost = sc.load_expost_6(sc.MONTHS_6)
    log("Datas/retornos ex-post carregados:")
    for mes in sc.MONTHS_6:
        month = expost[expost["mes"].astype(str).eq(mes)]
        ibov = sh.ibov_return(expost, mes)
        if not month.empty:
            log(f"  {mes}: linhas={len(month)} IBOV={ibov:.2%}")
        else:
            log(f"  {mes}: sem ex-post")

    anchor_rows: list[dict[str, Any]] = []
    anchor_results: dict[str, Any] = {}
    all_pass = True
    log("TESTE-ANCORA (flags sombra desligadas):")
    for mes in sc.MONTHS_6:
        path = sh.workbook_path(mes)
        result = sh.run_optimizer_for_month(
            mes,
            path,
            base_settings,
            shadow_fixes=False,
            enable_partial_portfolio=False,
            enable_beta_target=False,
            enable_objetivo_retorno=False,
            enable_composicao_ampliada=False,
        )
        passed, detail = sh.anchor_passed_for_month(mes, path, result.get("portfolio", pd.DataFrame()), result.get("metrics", {}))
        row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=False)
        row["anchor_passou"] = passed
        row["anchor_detalhe"] = detail
        anchor_rows.append(row)
        anchor_results[mes] = result
        all_pass = all_pass and passed
        log(f"  {mes}: {'PASSOU' if passed else 'FALHOU'} - {detail}")

    if not all_pass:
        write_workbook(anchor_rows, [], anchor_results, {}, pd.DataFrame(), expost, pd.DataFrame())
        LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
        raise SystemExit("Ancora falhou; teste de forca relativa continua abortado.")

    original_build = sh.build_free_size_portfolio
    original_d3 = sh.technical_veto_to_penalty_in_opportunity
    original_beta_profile = sh.beta_target_profile
    original_downturn_profile = sh.downturn_regime_profile
    original_add = sh.add_objetivo_retorno_signals
    original_loader = sh.load_candidate_input

    sh.build_free_size_portfolio = sc.consolidated_build_free_size_portfolio
    sh.technical_veto_to_penalty_in_opportunity = sc.make_extended_d3(original_d3)
    sh.beta_target_profile = consolidated_beta_target_profile_factory(original_beta_profile)
    sh.downturn_regime_profile = consolidated_downturn_profile_factory(original_downturn_profile)
    sh.add_objetivo_retorno_signals = make_continuous_signal_adder(original_add)
    sh.load_candidate_input = make_extended_load_candidate_input(original_loader)

    unit_df = sc.run_weight_unit_tests()
    for _, r in unit_df.iterrows():
        log(f"PESOS_TESTE {r.get('caso')}: {r.get('pesos')}")

    shadow_rows: list[dict[str, Any]] = []
    results_by_variant: dict[str, dict[str, Any]] = {}
    log("Rodando variantes de forca relativa continua:")
    try:
        for variant, meta in VARIANTS.items():
            CURRENT_VARIANT = variant
            results_by_variant[variant] = {}
            log(f"VARIANTE {variant}: {meta['label']}")
            for mes in sc.MONTHS_6:
                path = sh.workbook_path(mes)
                result = sh.run_free_size_for_month(
                    mes,
                    path,
                    base_settings,
                    lambda_beta=sc.LAMBDA_BETA_CONSOLIDADO,
                    downturn_signal="SINAL_A_DEFENSIVO",
                )
                results_by_variant[variant][mes] = result
                row = sh.build_summary_row(mes, path, result, expost, shadow_fixes=True)
                row = add_expost_metrics(row, result, expost, mes)
                row["variante_forca"] = variant
                row["descricao_variante_forca"] = meta["label"]
                row["grupo_regime"] = classify_month_group(mes)
                row["forca_relativa_continua_w1_w4_wytd"] = "3/2/1"
                row["desconto_sobrecompra_extrema"] = DISCOUNT_FACTOR if meta["discount"] else 1.0
                cand = result.get("candidates", pd.DataFrame())
                d3_count = 0
                if not cand.empty and "liberado_por_d3" in cand.columns:
                    d3_count = int(cand["liberado_por_d3"].map(sh.to_bool).fillna(False).sum())
                row["d3_extendida_qtd_liberada"] = d3_count
                shadow_rows.append(row)
                port = result.get("portfolio", pd.DataFrame())
                tickers = ",".join(port.get("ticker", pd.Series(dtype=str)).astype(str).tolist()) if not port.empty else ""
                weights = ",".join([f"{x:.1%}" for x in pd.to_numeric(port.get("peso_recomendado", pd.Series(dtype=float)), errors="coerce").fillna(0)]) if not port.empty else ""
                log(
                    f"  {mes}: status={row.get('status_carteira')} alfa={row.get('alfa_expost', np.nan):.2%} "
                    f"ret={row.get('retorno_expost_carteira', np.nan):.2%} ibov={row.get('retorno_expost_ibov', np.nan):.2%} "
                    f"tickers={tickers} pesos={weights}"
                )
    finally:
        sh.build_free_size_portfolio = original_build
        sh.technical_veto_to_penalty_in_opportunity = original_d3
        sh.beta_target_profile = original_beta_profile
        sh.downturn_regime_profile = original_downturn_profile
        sh.add_objetivo_retorno_signals = original_add
        sh.load_candidate_input = original_loader

    validation_df = validation_rows_by_variant(results_by_variant, expost)
    write_workbook(anchor_rows, shadow_rows, anchor_results, results_by_variant, validation_df, expost, unit_df)

    summary = build_variant_summary(shadow_rows)
    log("RESUMO ACUMULADO:")
    if not summary.empty:
        for _, r in summary[summary["mes"].astype(str).str.startswith("ACUMULADO")].iterrows():
            log(
                f"  {r.get('variante_forca')} {r.get('mes')}: "
                f"ret={r.get('retorno_expost_carteira', np.nan):.2%} "
                f"ibov={r.get('retorno_expost_ibov', np.nan):.2%} "
                f"alfa={r.get('alfa_expost', np.nan):.2%}"
            )

    LOG_FILE.write_text("\n".join(logs), encoding="utf-8")
    log(f"Arquivo gerado: {OUTPUT_FILE}")
    log(f"Log gerado: {LOG_FILE}")


if __name__ == "__main__":
    main()
