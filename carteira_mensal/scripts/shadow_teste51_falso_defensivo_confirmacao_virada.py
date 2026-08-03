from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
OUT = EXCEL_DIR / "shadow_teste51_falso_defensivo_confirmacao_virada.xlsx"
BASE_T49 = EXCEL_DIR / "shadow_teste49_top15_regime_capital.xlsx"
BASE_T43 = EXCEL_DIR / "shadow_teste43_auditoria_erros_diagnostico.xlsx"
IBOV = ROOT / "data" / "processed" / "ibov_mensal_oficial.csv"
CDI = ROOT / "data" / "processed" / "cdi_mensal_ipeadata.csv"

CAPITAL = 10000
IR_CDI_CURTO_PRAZO = 0.225


SCENARIOS = {
    "BASELINE_T49": {
        "descricao": "modelo operacional atual, sem ajuste",
        "target": None,
        "require_prev_ibov_positive": False,
        "require_no_28d_downtrend": False,
        "min_note": None,
        "min_assets": None,
        "max_prior_ibov_loss": None,
    },
    "T51A_VIRADA_CONFIRMADA_MIN50": {
        "descricao": "defensivo apos IBOV anterior positivo, sem queda 28d confirmada, nota media >= 64 e minimo 12 ativos; exposicao minima 50%",
        "target": 0.50,
        "require_prev_ibov_positive": True,
        "require_no_28d_downtrend": True,
        "min_note": 64.0,
        "min_assets": 12,
        "max_prior_ibov_loss": None,
    },
    "T51B_VIRADA_QUALIDADE_MIN50": {
        "descricao": "defensivo apos IBOV anterior positivo, nota media >= 64 e minimo 12 ativos; exposicao minima 50%",
        "target": 0.50,
        "require_prev_ibov_positive": True,
        "require_no_28d_downtrend": False,
        "min_note": 64.0,
        "min_assets": 12,
        "max_prior_ibov_loss": None,
    },
    "T51C_REPIQUE_APOS_QUEDA_LEVE_MIN50": {
        "descricao": "defensivo apos mes anterior nao muito negativo (> -2%), nota media >= 64 e minimo 12 ativos; exposicao minima 50%",
        "target": 0.50,
        "require_prev_ibov_positive": False,
        "require_no_28d_downtrend": False,
        "min_note": 64.0,
        "min_assets": 12,
        "max_prior_ibov_loss": -0.02,
    },
    "T51D_VIRADA_CONFIRMADA_MIN70": {
        "descricao": "mesmo gatilho do T51A, mas exposicao minima 70%",
        "target": 0.70,
        "require_prev_ibov_positive": True,
        "require_no_28d_downtrend": True,
        "min_note": 64.0,
        "min_assets": 12,
        "max_prior_ibov_loss": None,
    },
    "T51E_REPIQUE_COM_AMPLITUDE_MIN50": {
        "descricao": "defensivo com mes anterior nao muito negativo, mais de 50% do universo positivo em 1m e mais de 50% com forca relativa 1m positiva; exposicao minima 50%",
        "target": 0.50,
        "require_prev_ibov_positive": False,
        "require_no_28d_downtrend": False,
        "min_note": None,
        "min_assets": None,
        "max_prior_ibov_loss": -0.02,
        "min_pct_pos_1m": 0.50,
        "min_pct_rel1_pos": 0.50,
        "min_universe_note": 50.0,
    },
    "T51F_REPIQUE_COM_AMPLITUDE_MIN70": {
        "descricao": "mesmo gatilho do T51E, mas exposicao minima 70%",
        "target": 0.70,
        "require_prev_ibov_positive": False,
        "require_no_28d_downtrend": False,
        "min_note": None,
        "min_assets": None,
        "max_prior_ibov_loss": -0.02,
        "min_pct_pos_1m": 0.50,
        "min_pct_rel1_pos": 0.50,
        "min_universe_note": 50.0,
    },
}


def compound(s: pd.Series) -> float:
    vals = pd.to_numeric(s, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    return float((1.0 + vals).prod() - 1.0)


def max_drawdown(s: pd.Series) -> float:
    vals = pd.to_numeric(s, errors="coerce").fillna(0.0)
    curve = (1.0 + vals).cumprod()
    dd = curve / curve.cummax() - 1.0
    return float(dd.min())


def normalize_month(s: pd.Series) -> pd.Series:
    return s.astype(str).str[:7]


def cdi_net_map() -> dict[str, float]:
    if not CDI.exists():
        return {}
    cdi = pd.read_csv(CDI)
    cdi["mes"] = normalize_month(cdi["mes"])
    cdi["retorno_cdi_liquido_periodo"] = pd.to_numeric(cdi["cdi_bruto_mensal"], errors="coerce") * (1.0 - IR_CDI_CURTO_PRAZO)
    return dict(zip(cdi["mes"], cdi["retorno_cdi_liquido_periodo"]))


def load_july_partial(cdi_by_month: dict[str, float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    partials = sorted(EXCEL_DIR.glob("parcial_carteira_forward_2026_07*.xlsx"), key=lambda p: p.stat().st_mtime)
    if not partials:
        return pd.DataFrame(), pd.DataFrame()
    pt = partials[-1]
    psdf = pd.read_excel(pt, sheet_name="Resumo Parcial")
    summary = dict(zip(psdf.iloc[:, 0].astype(str), psdf.iloc[:, 1]))
    adf = pd.read_excel(pt, sheet_name="Ativos")
    rows = []
    for _, r in adf.iterrows():
        ticker = str(r.get("ticker", ""))
        is_cdi = ticker.upper() == "CDI"
        weight = float(r.get("peso_recomendado", 0.0))
        ret = float(r.get("retorno_periodo", cdi_by_month.get("2026-07", 0.0)))
        if is_cdi:
            ret = float(cdi_by_month.get("2026-07", ret))
        rows.append(
            {
                "mes": "2026-07",
                "cenario": "TOP15",
                "capital": CAPITAL,
                "ticker": ticker,
                "nome": "Reserva/CDI liquido" if is_cdi else str(r.get("nome", ticker)),
                "setor": "Protecao" if is_cdi else str(r.get("setor", "")),
                "tipo_linha": "cdi" if is_cdi else "acao",
                "peso_final": weight,
                "retorno_periodo": ret,
                "contribuicao": weight * ret,
            }
        )
    cart = pd.DataFrame(rows)
    month = pd.DataFrame(
        [
            {
                "mes": "2026-07",
                "cenario": "TOP15",
                "capital": CAPITAL,
                "tipo_regime_expost": "alta",
                "regime_previsto": "queda_forte",
                "qtd_acoes": int((cart["tipo_linha"] == "acao").sum()),
                "peso_acoes": float(cart.loc[cart["tipo_linha"] == "acao", "peso_final"].sum()),
                "peso_cdi": float(cart.loc[cart["tipo_linha"] == "cdi", "peso_final"].sum()),
                "retorno": float(cart["contribuicao"].sum()),
                "retorno_ibov": float(summary.get("retorno_ibov_parcial", np.nan)),
                "alfa_vs_ibov": float(cart["contribuicao"].sum()) - float(summary.get("retorno_ibov_parcial", np.nan)),
                "bateu_ibov": float(cart["contribuicao"].sum()) > float(summary.get("retorno_ibov_parcial", np.nan)),
                "ano": 2026,
            }
        ]
    )
    return month, cart


def load_monthly_breadth() -> pd.DataFrame:
    files = []
    for year in range(2022, 2026):
        files.extend(EXCEL_DIR.glob(f"carteira_historica_{year}_*.xlsx"))
    files.extend(EXCEL_DIR.glob("carteira_recomendada_2026_*_v*.xlsx"))

    latest: dict[str, tuple[int, Path]] = {}
    for path in files:
        match = re.search(r"(20\d{2})_(\d{2})(?:_v(\d+))?", path.name)
        if not match:
            continue
        key = f"{match.group(1)}-{match.group(2)}"
        version = int(match.group(3) or 0)
        if key not in latest or version > latest[key][0]:
            latest[key] = (version, path)

    rows = []
    for mes, (_, path) in sorted(latest.items()):
        try:
            df = pd.read_excel(path, sheet_name="Analise Preliminar")
        except Exception as exc:
            rows.append({"mes": mes, "arquivo_amplitude": path.name, "erro_amplitude": str(exc)})
            continue
        ret1 = pd.to_numeric(df.get("retorno_acumulado_1m"), errors="coerce")
        rel1 = pd.to_numeric(df.get("retorno_1m_relativo_ibov"), errors="coerce")
        tend = df.get("tendencia_mensal", pd.Series("", index=df.index)).astype(str).str.lower()
        timing = df.get("tipo_timing", pd.Series("", index=df.index)).astype(str).str.lower()
        note = pd.to_numeric(df.get("nota_final"), errors="coerce")
        rows.append({
            "mes": mes,
            "arquivo_amplitude": path.name,
            "pct_pos_1m_universo": float((ret1 > 0).mean()),
            "pct_rel1_pos_universo": float((rel1 > 0).mean()),
            "pct_tendencia_alta_universo": float(tend.str.contains("alta").mean()),
            "pct_timing_favoravel_universo": float(timing.str.contains("favoravel").mean()),
            "nota_media_universo": float(note.mean()),
            "nota_mediana_universo": float(note.median()),
        })
    return pd.DataFrame(rows)


def load_base() -> tuple[pd.DataFrame, pd.DataFrame]:
    cdi_by_month = cdi_net_map()
    mes = pd.read_excel(BASE_T49, sheet_name="Mes a Mes")
    cart = pd.read_excel(BASE_T49, sheet_name="Carteiras")
    diag = pd.read_excel(BASE_T43, sheet_name="Mes a Mes Base")
    ib = pd.read_csv(IBOV)

    mes = mes[(mes["cenario"].eq("TOP15")) & (mes["capital"].eq(CAPITAL))].copy()
    cart = cart[(cart["cenario"].eq("TOP15")) & (cart["capital"].eq(CAPITAL))].copy()
    mes["mes"] = normalize_month(mes["mes"])
    cart["mes"] = normalize_month(cart["mes"])
    diag["mes"] = normalize_month(diag["mes"])
    ib["mes"] = normalize_month(ib["mes"])

    mes = mes.merge(ib[["mes", "retorno_ibov_oficial"]], on="mes", how="left")
    mes["retorno_ibov_base"] = mes["retorno_ibov_oficial"].combine_first(mes["retorno_ibov"])
    mes["retorno_cdi_liquido_periodo"] = mes["mes"].map(cdi_by_month)

    diag_cols = [
        "mes",
        "regime_previsto_norm",
        "nota_media_formacao",
        "nota_mediana_formacao",
        "n_ativos_acoes_formacao",
        "beta_carteira_formacao",
        "queda_confirmada_28d",
        "motivo_acionamento_4m",
        "tipo_erro_diagnostico",
        "diagnostico_acertou_direcao",
    ]
    mes = mes.merge(diag[[c for c in diag_cols if c in diag.columns]], on="mes", how="left")
    if "regime_previsto_norm" in mes.columns:
        mes["regime_previsto"] = mes["regime_previsto_norm"].combine_first(mes["regime_previsto"])
    july_m, july_c = load_july_partial(cdi_by_month)
    if not july_m.empty and not mes["mes"].eq("2026-07").any():
        last_ibov = float(mes.sort_values("mes").iloc[-1]["retorno_ibov_base"])
        july_m["retorno_ibov_base"] = july_m["retorno_ibov"]
        july_m["ibov_anterior"] = last_ibov
        # Julho nao esta no T43; usa os sinais de formacao do forward original.
        forward = EXCEL_DIR / "carteira_forward_2026_07.xlsx"
        if forward.exists():
            reg = pd.read_excel(forward, sheet_name="Regime Mercado Base")
            reg_map = dict(zip(reg["campo"].astype(str), reg["valor"]))
            cand = pd.read_excel(forward, sheet_name="Carteira Forward")
            july_m["nota_media_formacao"] = pd.to_numeric(cand.get("nota_final"), errors="coerce").mean()
            july_m["nota_mediana_formacao"] = pd.to_numeric(cand.get("nota_final"), errors="coerce").median()
            july_m["n_ativos_acoes_formacao"] = int(cand["ticker"].astype(str).str.upper().ne("CDI").sum())
            july_m["beta_carteira_formacao"] = pd.to_numeric(cand.get("beta"), errors="coerce").mean()
            july_m["queda_confirmada_28d"] = True
            july_m["motivo_acionamento_4m"] = str(reg_map.get("motivo_subtipo_mercado_favoravel", "forward_julho"))
            july_m["tipo_erro_diagnostico"] = "falso_alerta_queda"
            july_m["diagnostico_acertou_direcao"] = False
        mes = pd.concat([mes, july_m], ignore_index=True, sort=False)
        cart = pd.concat([cart, july_c], ignore_index=True, sort=False)

    breadth = load_monthly_breadth()
    if not breadth.empty:
        mes = mes.merge(breadth, on="mes", how="left")

    mes = mes.sort_values("mes").reset_index(drop=True)
    if "ibov_anterior" not in mes.columns:
        mes["ibov_anterior"] = np.nan
    mes["ibov_anterior"] = mes["ibov_anterior"].combine_first(mes["retorno_ibov_base"].shift(1))
    return mes, cart


def is_defensive(row: pd.Series) -> bool:
    reg = str(row.get("regime_previsto", "")).lower()
    return "queda" in reg or "fraco" in reg or "defens" in reg


def trigger_reason(row: pd.Series, cfg: dict) -> tuple[bool, str]:
    if cfg["target"] is None:
        return False, "baseline"
    if not is_defensive(row):
        return False, "sem_gatilho: diagnostico_nao_defensivo"
    prev = row.get("ibov_anterior")
    if cfg["require_prev_ibov_positive"] and (pd.isna(prev) or float(prev) <= 0):
        return False, "sem_gatilho: ibov_anterior_nao_positivo"
    if cfg["max_prior_ibov_loss"] is not None and (pd.isna(prev) or float(prev) <= float(cfg["max_prior_ibov_loss"])):
        return False, "sem_gatilho: queda_anterior_maior_que_limite"
    q28 = row.get("queda_confirmada_28d")
    q28_bool = bool(q28) if not pd.isna(q28) else False
    if cfg["require_no_28d_downtrend"] and q28_bool:
        return False, "sem_gatilho: queda_28d_ainda_confirmada"
    note = row.get("nota_media_formacao")
    if cfg["min_note"] is not None and (pd.isna(note) or float(note) < float(cfg["min_note"])):
        return False, "sem_gatilho: nota_media_baixa"
    n_assets = row.get("n_ativos_acoes_formacao")
    if cfg["min_assets"] is not None and (pd.isna(n_assets) or int(n_assets) < int(cfg["min_assets"])):
        return False, "sem_gatilho: poucos_ativos_de_qualidade"
    pct_pos = row.get("pct_pos_1m_universo")
    if cfg.get("min_pct_pos_1m") is not None and (pd.isna(pct_pos) or float(pct_pos) < float(cfg["min_pct_pos_1m"])):
        return False, "sem_gatilho: amplitude_1m_insuficiente"
    pct_rel = row.get("pct_rel1_pos_universo")
    if cfg.get("min_pct_rel1_pos") is not None and (pd.isna(pct_rel) or float(pct_rel) < float(cfg["min_pct_rel1_pos"])):
        return False, "sem_gatilho: forca_relativa_1m_insuficiente"
    note_universe = row.get("nota_media_universo")
    if cfg.get("min_universe_note") is not None and (pd.isna(note_universe) or float(note_universe) < float(cfg["min_universe_note"])):
        return False, "sem_gatilho: nota_media_universo_baixa"
    return True, "acionado: defensivo_com_confirmacao_de_virada"


def run_scenario(mes: pd.DataFrame, cart: pd.DataFrame, name: str, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    monthly_rows = []
    adjusted_rows = []
    for _, m in mes.sort_values("mes").iterrows():
        month = str(m["mes"])
        part = cart[cart["mes"].eq(month)].copy()
        if part.empty:
            continue
        part["peso_final"] = pd.to_numeric(part["peso_final"], errors="coerce").fillna(0.0)
        part["retorno_periodo"] = pd.to_numeric(part["retorno_periodo"], errors="coerce").fillna(0.0)
        is_cdi = part["tipo_linha"].astype(str).str.lower().eq("cdi") | part["ticker"].astype(str).str.upper().eq("CDI")
        stock_weight = float(part.loc[~is_cdi, "peso_final"].sum())
        should_trigger, reason = trigger_reason(m, cfg)
        target = cfg.get("target")
        new_stock_weight = stock_weight
        action = "sem_alteracao"
        if should_trigger and target is not None and stock_weight > 0 and stock_weight < float(target):
            new_stock_weight = float(target)
            action = f"exposicao_acoes_{stock_weight:.2%}_para_{new_stock_weight:.2%}"
        elif should_trigger and target is not None:
            reason = "gatilho_presente_mas_exposicao_ja_maior_ou_igual_ao_minimo"

        scale = new_stock_weight / stock_weight if stock_weight > 0 else 0.0
        part["cenario_t51"] = name
        part["peso_original"] = part["peso_final"]
        part.loc[~is_cdi, "peso_final"] = part.loc[~is_cdi, "peso_final"] * scale
        part.loc[is_cdi, "peso_final"] = max(0.0, 1.0 - float(part.loc[~is_cdi, "peso_final"].sum()))
        part["contribuicao_t51"] = part["peso_final"] * part["retorno_periodo"]
        part["gatilho_t51"] = bool(action != "sem_alteracao")
        part["motivo_t51"] = reason
        part["acao_t51"] = action
        adjusted_rows.append(part)

        ret = float(part["contribuicao_t51"].sum())
        ibov = float(m.get("retorno_ibov_base", m.get("retorno_ibov", np.nan)))
        monthly_rows.append(
            {
                "mes": month,
                "cenario_t51": name,
                "regime_previsto": m.get("regime_previsto"),
                "tipo_regime_expost": m.get("tipo_regime_expost"),
                "ibov_anterior": m.get("ibov_anterior"),
                "retorno_ibov": ibov,
                "retorno_modelo": ret,
                "alfa_vs_ibov": ret - ibov,
                "bateu_ibov": ret > ibov,
                "peso_acoes_original": stock_weight,
                "peso_acoes_t51": float(part.loc[~is_cdi, "peso_final"].sum()),
                "peso_cdi_t51": float(part.loc[is_cdi, "peso_final"].sum()) if is_cdi.any() else 0.0,
                "nota_media_formacao": m.get("nota_media_formacao"),
                "n_ativos_acoes_formacao": m.get("n_ativos_acoes_formacao"),
                "beta_carteira_formacao": m.get("beta_carteira_formacao"),
                "queda_confirmada_28d": m.get("queda_confirmada_28d"),
                "pct_pos_1m_universo": m.get("pct_pos_1m_universo"),
                "pct_rel1_pos_universo": m.get("pct_rel1_pos_universo"),
                "pct_tendencia_alta_universo": m.get("pct_tendencia_alta_universo"),
                "pct_timing_favoravel_universo": m.get("pct_timing_favoravel_universo"),
                "nota_media_universo": m.get("nota_media_universo"),
                "gatilho_acionado": bool(action != "sem_alteracao"),
                "motivo_t51": reason,
                "acao_t51": action,
                "ano": int(month[:4]),
            }
        )
    return pd.DataFrame(monthly_rows), pd.concat(adjusted_rows, ignore_index=True, sort=False)


def summarize(monthly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows = []
    for name, g in monthly.groupby("cenario_t51"):
        model = compound(g["retorno_modelo"])
        ibov = compound(g["retorno_ibov"])
        rows.append(
            {
                "cenario_t51": name,
                "meses": len(g),
                "retorno_modelo": model,
                "retorno_ibov": ibov,
                "alfa_vs_ibov": model - ibov,
                "taxa_acerto": float(g["bateu_ibov"].mean()),
                "drawdown": max_drawdown(g["retorno_modelo"]),
                "meses_acionados": int(g["gatilho_acionado"].sum()),
                "alfa_medio": float(g["alfa_vs_ibov"].mean()),
                "peso_acoes_medio": float(g["peso_acoes_t51"].mean()),
            }
        )
    geral = pd.DataFrame(rows)
    by_year = monthly.groupby(["cenario_t51", "ano"], as_index=False).agg(
        meses=("mes", "count"),
        retorno_modelo=("retorno_modelo", compound),
        retorno_ibov=("retorno_ibov", compound),
        taxa_acerto=("bateu_ibov", "mean"),
        meses_acionados=("gatilho_acionado", "sum"),
        peso_acoes_medio=("peso_acoes_t51", "mean"),
    )
    by_year["alfa_vs_ibov"] = by_year["retorno_modelo"] - by_year["retorno_ibov"]
    by_regime = monthly.groupby(["cenario_t51", "regime_previsto"], as_index=False).agg(
        meses=("mes", "count"),
        retorno_modelo=("retorno_modelo", compound),
        retorno_ibov=("retorno_ibov", compound),
        taxa_acerto=("bateu_ibov", "mean"),
        meses_acionados=("gatilho_acionado", "sum"),
    )
    by_regime["alfa_vs_ibov"] = by_regime["retorno_modelo"] - by_regime["retorno_ibov"]
    return geral, by_year, by_regime


def main() -> None:
    mes, cart = load_base()
    desc = []
    all_months = []
    all_carts = []
    for name, cfg in SCENARIOS.items():
        monthly, carts = run_scenario(mes, cart, name, cfg)
        all_months.append(monthly)
        all_carts.append(carts)
        desc.append({"cenario_t51": name, **cfg})
    monthly = pd.concat(all_months, ignore_index=True, sort=False)
    carts = pd.concat(all_carts, ignore_index=True, sort=False)
    base = monthly[monthly["cenario_t51"].eq("BASELINE_T49")][
        ["mes", "retorno_modelo", "alfa_vs_ibov", "peso_acoes_t51", "peso_cdi_t51"]
    ].rename(
        columns={
            "retorno_modelo": "retorno_base",
            "alfa_vs_ibov": "alfa_base",
            "peso_acoes_t51": "peso_acoes_base",
            "peso_cdi_t51": "peso_cdi_base",
        }
    )
    monthly = monthly.merge(base, on="mes", how="left")
    monthly["delta_alfa_vs_base"] = monthly["alfa_vs_ibov"] - monthly["alfa_base"]
    resumo, ano, regime = summarize(monthly)
    cases = monthly[monthly["gatilho_acionado"]].copy()

    with pd.ExcelWriter(OUT, engine="xlsxwriter") as writer:
        pd.DataFrame(desc).to_excel(writer, sheet_name="Descricao", index=False)
        resumo.to_excel(writer, sheet_name="Resumo Geral", index=False)
        ano.to_excel(writer, sheet_name="Resumo Ano", index=False)
        regime.to_excel(writer, sheet_name="Resumo Regime", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        cases.to_excel(writer, sheet_name="Casos Acionados", index=False)
        carts.to_excel(writer, sheet_name="Carteiras Ajustadas", index=False)

    print(f"Arquivo gerado: {OUT}")
    print("\nResumo Geral")
    print(resumo.to_string(index=False))
    print("\nCasos acionados")
    cols = [
        "mes",
        "cenario_t51",
        "regime_previsto",
        "ibov_anterior",
        "retorno_ibov",
        "retorno_modelo",
        "alfa_vs_ibov",
        "delta_alfa_vs_base",
        "peso_acoes_original",
        "peso_acoes_t51",
        "nota_media_formacao",
        "n_ativos_acoes_formacao",
        "queda_confirmada_28d",
        "pct_pos_1m_universo",
        "pct_rel1_pos_universo",
        "pct_tendencia_alta_universo",
        "nota_media_universo",
        "motivo_t51",
    ]
    if cases.empty:
        print("Nenhum acionamento.")
    else:
        print(cases[cols].to_string(index=False))
    print("\nComparativo 2026-07")
    print(monthly[monthly["mes"].eq("2026-07")][cols[:10] + ["motivo_t51"]].to_string(index=False))


if __name__ == "__main__":
    main()
