
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
OUT = EXCEL_DIR / "shadow_teste50_falso_defensivo_repiquedealta.xlsx"
BASE = EXCEL_DIR / "shadow_teste49_top15_regime_capital.xlsx"
IBOV = ROOT / "data" / "processed" / "ibov_mensal_oficial.csv"
CAPITAL = 10000

SCENARIOS = {
    "BASELINE_T49": {"descricao": "modelo operacional atual, sem ajuste", "kind": "base", "target": None},
    "T50A_QUEDA_FORTE_MIN50": {"descricao": "se queda_forte apos IBOV positivo, exposicao minima em acoes = 50%", "kind": "queda_forte", "target": 0.50},
    "T50B_DEFENSIVO_MIN50": {"descricao": "se qualquer diagnostico defensivo apos IBOV positivo, exposicao minima em acoes = 50%", "kind": "defensivo", "target": 0.50},
    "T50C_QUEDA_FORTE_MIN70": {"descricao": "se queda_forte apos IBOV positivo, exposicao minima em acoes = 70%", "kind": "queda_forte", "target": 0.70},
    "T50D_TODA_QUEDA_FORTE_MIN50": {"descricao": "todo mes queda_forte passa a ter exposicao minima em acoes = 50%", "kind": "queda_forte_all", "target": 0.50},
    "T50E_TODA_QUEDA_FORTE_MIN70": {"descricao": "todo mes queda_forte passa a ter exposicao minima em acoes = 70%", "kind": "queda_forte_all", "target": 0.70},
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


def load_base() -> tuple[pd.DataFrame, pd.DataFrame]:
    mes = pd.read_excel(BASE, sheet_name="Mes a Mes")
    cart = pd.read_excel(BASE, sheet_name="Carteiras")
    mes = mes[(mes["cenario"].eq("TOP15")) & (mes["capital"].eq(CAPITAL))].copy()
    cart = cart[(cart["cenario"].eq("TOP15")) & (cart["capital"].eq(CAPITAL))].copy()
    mes["mes"] = mes["mes"].astype(str).str[:7]
    cart["mes"] = cart["mes"].astype(str).str[:7]
    ib = pd.read_csv(IBOV)
    ib["mes"] = ib["mes"].astype(str).str[:7]
    mes = mes.merge(ib[["mes", "retorno_ibov_oficial"]], on="mes", how="left")
    mes["retorno_ibov_base"] = mes["retorno_ibov_oficial"].combine_first(mes["retorno_ibov"])
    mes = mes.sort_values("mes")
    mes["ibov_anterior"] = mes["retorno_ibov_base"].shift(1)
    # Julho forward encerrado, fora do T49 original.
    partials = sorted(EXCEL_DIR.glob("parcial_carteira_forward_2026_07*.xlsx"), key=lambda p: p.stat().st_mtime)
    if partials and not mes["mes"].eq("2026-07").any():
        pt = partials[-1]
        psdf = pd.read_excel(pt, sheet_name="Resumo Parcial")
        ps = dict(zip(psdf.iloc[:, 0].astype(str), psdf.iloc[:, 1]))
        adf = pd.read_excel(pt, sheet_name="Ativos")
        last_ibov = float(mes.iloc[-1]["retorno_ibov_base"])
        july_rows = []
        for _, r in adf.iterrows():
            ticker = str(r.get("ticker", ""))
            tipo = "cdi" if ticker.upper() == "CDI" else "acao"
            july_rows.append({
                "mes": "2026-07",
                "cenario": "TOP15",
                "capital": CAPITAL,
                "ticker": ticker,
                "nome": "Reserva/CDI liquido" if tipo == "cdi" else ticker,
                "setor": "Protecao" if tipo == "cdi" else "",
                "tipo_linha": tipo,
                "peso_final": float(r.get("peso_recomendado", 0.0)),
                "retorno_periodo": float(r.get("retorno_periodo", 0.0)),
                "contribuicao": float(r.get("peso_recomendado", 0.0)) * float(r.get("retorno_periodo", 0.0)),
            })
        cart = pd.concat([cart, pd.DataFrame(july_rows)], ignore_index=True, sort=False)
        model = float(sum(x["contribuicao"] for x in july_rows))
        ibov = float(ps.get("retorno_ibov_parcial", 0.034734))
        mes = pd.concat([
            mes,
            pd.DataFrame([{
                "mes": "2026-07",
                "cenario": "TOP15",
                "capital": CAPITAL,
                "tipo_regime_expost": "alta",
                "regime_previsto": "queda_forte",
                "qtd_acoes": int((adf["ticker"].astype(str).str.upper() != "CDI").sum()),
                "peso_acoes": float(adf.loc[adf["ticker"].astype(str).str.upper() != "CDI", "peso_recomendado"].sum()),
                "peso_cdi": float(adf.loc[adf["ticker"].astype(str).str.upper() == "CDI", "peso_recomendado"].sum()),
                "retorno": model,
                "retorno_ibov_base": ibov,
                "ibov_anterior": last_ibov,
                "alfa_vs_ibov": model - ibov,
                "bateu_ibov": model > ibov,
                "ano": 2026,
            }])
        ], ignore_index=True, sort=False)
    return mes.sort_values("mes"), cart


def scenario_trigger(row: pd.Series, config: dict) -> bool:
    if config["kind"] == "base":
        return False
    reg = str(row.get("regime_previsto", "")).lower()
    prev_pos = pd.notna(row.get("ibov_anterior")) and float(row.get("ibov_anterior")) > 0
    if config["kind"] == "queda_forte_all":
        return reg == "queda_forte"
    if not prev_pos:
        return False
    if config["kind"] == "queda_forte":
        return reg == "queda_forte"
    if config["kind"] == "queda_forte_all":
        return reg == "queda_forte"
    if config["kind"] == "defensivo":
        return "queda" in reg or "fraco" in reg or "defens" in reg
    return False


def run_scenario(mes: pd.DataFrame, cart: pd.DataFrame, name: str, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
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
        cdi_ret = float(part.loc[is_cdi, "retorno_periodo"].iloc[0]) if is_cdi.any() else 0.0
        trigger = scenario_trigger(m, config)
        target = config.get("target")
        new_stock_weight = stock_weight
        motivo = "sem_acionamento"
        if trigger and target is not None and stock_weight < float(target) and stock_weight > 0:
            new_stock_weight = float(target)
            motivo = f"acionado: {m.get('regime_previsto')} apos IBOV anterior positivo; exposicao {stock_weight:.1%}->{new_stock_weight:.1%}"
        elif trigger:
            motivo = "gatilho_presente_mas_exposicao_ja_maior_ou_igual_ao_minimo"
        scale = new_stock_weight / stock_weight if stock_weight > 0 else 0.0
        part["cenario_t50"] = name
        part["peso_original"] = part["peso_final"]
        part.loc[~is_cdi, "peso_final"] = part.loc[~is_cdi, "peso_final"] * scale
        part.loc[is_cdi, "peso_final"] = max(0.0, 1.0 - float(part.loc[~is_cdi, "peso_final"].sum()))
        part["contribuicao_t50"] = part["peso_final"] * part["retorno_periodo"]
        part["motivo_t50"] = motivo
        adjusted_rows.append(part)
        ret = float(part["contribuicao_t50"].sum())
        ibov = float(m.get("retorno_ibov_base", m.get("retorno_ibov", np.nan)))
        rows.append({
            "mes": month,
            "cenario_t50": name,
            "regime_previsto": m.get("regime_previsto"),
            "ibov_anterior": m.get("ibov_anterior"),
            "retorno_ibov": ibov,
            "retorno_modelo": ret,
            "alfa_vs_ibov": ret - ibov,
            "bateu_ibov": ret > ibov,
            "peso_acoes_original": stock_weight,
            "peso_acoes_t50": float(part.loc[~is_cdi, "peso_final"].sum()),
            "peso_cdi_t50": float(part.loc[is_cdi, "peso_final"].sum()) if is_cdi.any() else 0.0,
            "gatilho_acionado": bool(trigger and motivo.startswith("acionado")),
            "motivo_t50": motivo,
            "tipo_regime_expost": m.get("tipo_regime_expost"),
            "ano": int(str(month)[:4]),
        })
    return pd.DataFrame(rows), pd.concat(adjusted_rows, ignore_index=True, sort=False)


def summarize(monthly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows = []
    for name, g in monthly.groupby("cenario_t50"):
        rows.append({
            "cenario_t50": name,
            "meses": len(g),
            "retorno_modelo": compound(g["retorno_modelo"]),
            "retorno_ibov": compound(g["retorno_ibov"]),
            "alfa_vs_ibov": compound(g["retorno_modelo"]) - compound(g["retorno_ibov"]),
            "taxa_acerto": float(g["bateu_ibov"].mean()),
            "drawdown": max_drawdown(g["retorno_modelo"]),
            "meses_acionados": int(g["gatilho_acionado"].sum()),
            "alfa_medio": float(g["alfa_vs_ibov"].mean()),
            "peso_acoes_medio": float(g["peso_acoes_t50"].mean()),
        })
    geral = pd.DataFrame(rows)
    by_year = monthly.groupby(["cenario_t50", "ano"], as_index=False).agg(
        meses=("mes", "count"),
        retorno_modelo=("retorno_modelo", compound),
        retorno_ibov=("retorno_ibov", compound),
        taxa_acerto=("bateu_ibov", "mean"),
        meses_acionados=("gatilho_acionado", "sum"),
        peso_acoes_medio=("peso_acoes_t50", "mean"),
    )
    by_year["alfa_vs_ibov"] = by_year["retorno_modelo"] - by_year["retorno_ibov"]
    cases = monthly[monthly["gatilho_acionado"]].copy()
    return geral, by_year, cases


def main():
    mes, cart = load_base()
    all_months = []
    all_carts = []
    desc = []
    for name, cfg in SCENARIOS.items():
        monthly, carts = run_scenario(mes, cart, name, cfg)
        all_months.append(monthly)
        all_carts.append(carts)
        desc.append({"cenario_t50": name, **cfg})
    monthly = pd.concat(all_months, ignore_index=True, sort=False)
    carts = pd.concat(all_carts, ignore_index=True, sort=False)
    resumo, ano, cases = summarize(monthly)
    base = monthly[monthly["cenario_t50"].eq("BASELINE_T49")][["mes", "alfa_vs_ibov", "retorno_modelo", "peso_acoes_t50"]].rename(columns={"alfa_vs_ibov": "alfa_base", "retorno_modelo": "retorno_base", "peso_acoes_t50": "peso_acoes_base"})
    monthly = monthly.merge(base, on="mes", how="left")
    monthly["delta_alfa_vs_base"] = monthly["alfa_vs_ibov"] - monthly["alfa_base"]
    with pd.ExcelWriter(OUT, engine="xlsxwriter") as writer:
        pd.DataFrame(desc).to_excel(writer, sheet_name="Descricao", index=False)
        resumo.to_excel(writer, sheet_name="Resumo Geral", index=False)
        ano.to_excel(writer, sheet_name="Resumo Ano", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        cases.to_excel(writer, sheet_name="Casos Acionados", index=False)
        carts.to_excel(writer, sheet_name="Carteiras Ajustadas", index=False)
    print(f"Arquivo gerado: {OUT}")
    print("\nResumo Geral")
    print(resumo.to_string(index=False))
    print("\nCasos acionados")
    print(cases[["mes","cenario_t50","regime_previsto","ibov_anterior","retorno_ibov","retorno_modelo","alfa_vs_ibov","peso_acoes_original","peso_acoes_t50","peso_cdi_t50","motivo_t50"]].to_string(index=False))
    print("\nComparativo contra baseline nos cenarios T50")
    comp = monthly[~monthly["cenario_t50"].eq("BASELINE_T49") & monthly["gatilho_acionado"]]
    if comp.empty:
        print("Nenhum acionamento.")
    else:
        print(comp[["mes","cenario_t50","retorno_modelo","retorno_base","retorno_ibov","alfa_vs_ibov","alfa_base","delta_alfa_vs_base","peso_acoes_t50","peso_acoes_base"]].to_string(index=False))

if __name__ == "__main__":
    main()
