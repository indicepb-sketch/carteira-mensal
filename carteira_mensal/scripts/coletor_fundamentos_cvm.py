"""Fundamentos calculados a partir da CVM com as correcoes validadas contra o Fundamentus (jul-nov/2026):

1. ESCALA: as contas vem em R$ mil (ESCALA_MOEDA = MIL) ou em unidades; tudo passa a R$ (antes P/L saia multiplicado por ~1.000).
2. 12 MESES MOVEIS (TTM) nos fluxos: DFP = exercicio; ITR = exercicio anterior + acumulado atual - acumulado do ano anterior (antes: acumulado x fator de anualizacao).
3. ROE = lucro liquido do controlador (3.11.01) TTM / patrimonio liquido do controlador (PL - nao controladores); margens = TTM; P/L = valor de mercado / lucro do controlador TTM.
4. ROI (campo `roic`) segundo Assaf Neto (Estrutura e Analise de Balancos; Financas Corporativas e Valor): lucro operacional liquido NOPAT = EBIT x (1 - 34%) / investimento
   (passivo oneroso + patrimonio liquido). EBIT negativo nao recebe escudo fiscal. NAO reproduz o ROIC do Fundamentus (formula propria do site): a definicao que vale e a de Assaf Neto.
   ROE tambem segue Assaf Neto: lucro liquido / patrimonio liquido MEDIO (media entre o PL atual e o do fim do ano anterior, ambos do controlador).
5. Instituicoes financeiras (plano de contas 2.08 / sem fornecedores): so ROE e P/L; ROIC e margens bruta/EBIT ficam vazios e nao contam como dado insuficiente.
6. Plano de contas por DESCRICAO (nao so por codigo): bancos IFRS (2.03 = passivos financeiros; PL em 2.07/2.08; lucro em 3.09/3.11/3.13), companhias que nao preenchem o lucro do controlador
   (3.11.01 = 0) e holdings sem receita (BBSE3, CXSE3, BRAP4: so ROE e P/L). Companhias sem demonstracao consolidada (so individual: Bradespar, TIM, Sendas...) usam o arquivo _ind.
Mesma assinatura de coletor_historico_2025.historical_fundamentals; mesmas colunas de saida.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import coletor_historico_2025 as col

TAX_RATE = 0.34      # IRPJ + CSLL nominais (convencao de Assaf Neto para o NOPAT)


def _rows(frame, cd, ref, code, order, ytd):
    data = frame[(frame["CD_CVM"].eq(cd)) & (pd.to_datetime(frame["DT_REFER"], errors="coerce").eq(ref)) & (frame["CD_CONTA"].eq(code))]
    if data.empty:
        return data
    ordem = data["ORDEM_EXERC"].astype(str).str.strip().str.upper()
    data = data[ordem.str.contains("LTIMO", na=False) & ~ordem.str.startswith("PEN", na=False)] if order == "last" else data[ordem.str.startswith("PEN", na=False)]
    if ytd and "DT_INI_EXERC" in data:
        data = data[pd.to_datetime(data["DT_INI_EXERC"], errors="coerce").dt.month.eq(1)]
    return data


def acc(frame, cd, ref, code, order="last", ytd=False) -> float:
    if frame is None or frame.empty:
        return np.nan
    data = _rows(frame, cd, ref, code, order, ytd)
    if data.empty:
        return np.nan
    row = data.iloc[0]
    value = pd.to_numeric(row["VL_CONTA"], errors="coerce")
    if pd.isna(value):
        return np.nan
    scale = 1000.0 if str(row.get("ESCALA_MOEDA", "")).strip().upper() == "MIL" else 1.0
    return float(value) * scale


def first_valid(*values) -> float:
    for value in values:
        if pd.notna(value):
            return float(value)
    return np.nan


def stmt(store, kind, year, part, cd):
    """Demonstracao consolidada; se a companhia nao a publica (so individual), usa o arquivo _ind."""
    con = store.read(kind, year, part)
    if (con["CD_CVM"] == cd).any():
        return con
    key = (kind, year, part + "_ind")
    if key not in store.frames:
        name = f"{kind.lower()}_cia_aberta_{part}_ind_{year}.csv"
        store.frames[key] = pd.read_csv(store.zips[(kind, year)].open(name), sep=";", encoding="latin1", dtype={"CD_CONTA": str})
    return store.frames[key]


def _level1(frame, cd, ref):
    data = frame[(frame["CD_CVM"].eq(cd)) & (pd.to_datetime(frame["DT_REFER"], errors="coerce").eq(ref))]
    ordem = data["ORDEM_EXERC"].astype(str).str.strip().str.upper()
    return data[ordem.str.contains("LTIMO", na=False) & ~ordem.str.startswith("PEN", na=False)]


def equity_codes(bpp, cd, ref):
    """(codigo do PL, codigo dos nao controladores ou None, financeira?) pela descricao das contas."""
    d = _level1(bpp, cd, ref)
    top = d[d["CD_CONTA"].astype(str).str.count(r"[.]") == 1]
    pl = top[top["DS_CONTA"].astype(str).str.contains("Patrim", na=False)]
    if pl.empty:
        return None, None, False
    code = str(pl.iloc[0]["CD_CONTA"])
    third = d[d["CD_CONTA"].astype(str).str.startswith(code + ".") & (d["CD_CONTA"].astype(str).str.count(r"[.]") == code.count(".") + 1)]
    mino = third[third["DS_CONTA"].astype(str).str.contains("N.o Controlador|Particip.*N.o", case=False, regex=True, na=False)]
    two03 = top[top["CD_CONTA"].astype(str) == "2.03"]
    financial = bool(not two03.empty and not two03["DS_CONTA"].astype(str).str.contains("Patrim", na=False).any())
    return code, (str(mino.iloc[0]["CD_CONTA"]) if not mino.empty else None), financial


def profit_codes(dre, cd, ref):
    """(codigo do lucro consolidado do periodo, do atribuido ao controlador, do atribuido a nao controladores)."""
    d = _level1(dre, cd, ref)
    top = d[d["CD_CONTA"].astype(str).str.count(r"[.]") == 1]
    desc = top["DS_CONTA"].astype(str)
    ok = desc.str.match(r"^Lucro", na=False) & desc.str.contains("Per", na=False) & ~desc.str.contains("antes|Opera|Participa|Descontinu", case=False, regex=True, na=False)
    c = top[ok]
    if c.empty:
        return "3.11", "3.11.01", "3.11.02"
    code = str(c.iloc[-1]["CD_CONTA"])
    kids = d[d["CD_CONTA"].astype(str).str.startswith(code + ".") & (d["CD_CONTA"].astype(str).str.count(r"[.]") == code.count(".") + 1)]
    kd = kids["DS_CONTA"].astype(str)
    nao = kd.str.contains("N.o", case=False, regex=True, na=False)
    ctrl = kids[kd.str.contains("Controlador", na=False) & ~nao]
    mino = kids[kd.str.contains("Controlador", na=False) & nao]
    return code, (str(ctrl.iloc[0]["CD_CONTA"]) if not ctrl.empty else code + ".01"), (str(mino.iloc[0]["CD_CONTA"]) if not mino.empty else code + ".02")


def prefix_total(frame, cd, ref, prefixes) -> float:
    data = frame[(frame["CD_CVM"].eq(cd)) & (pd.to_datetime(frame["DT_REFER"], errors="coerce").eq(ref))]
    ordem = data["ORDEM_EXERC"].astype(str).str.strip().str.upper()
    data = data[ordem.str.contains("LTIMO", na=False) & ~ordem.str.startswith("PEN", na=False)]
    mask = pd.Series(False, index=data.index)
    for prefix in prefixes:
        mask |= data["CD_CONTA"].astype(str).str.startswith(prefix)
    chosen = data[mask]
    if chosen.empty:
        return np.nan
    factor = np.where(chosen["ESCALA_MOEDA"].astype(str).str.upper().eq("MIL"), 1000.0, 1.0)
    return float((pd.to_numeric(chosen["VL_CONTA"], errors="coerce") * factor).sum())


def flow_ttm(store, kind, year, ref, cd, code, dre_current, code_dfp=None) -> float:
    """Fluxo dos ultimos 12 meses ate `ref` (ITR: exercicio anterior + acumulado atual - acumulado do ano anterior)."""
    if kind == "DFP":
        return acc(dre_current, cd, ref, code)
    fy_ref = pd.Timestamp(year=ref.year - 1, month=12, day=31)
    dfp = stmt(store, "DFP", ref.year - 1, "DRE", cd) if ("DFP", ref.year - 1) in store.zips else None
    fy = acc(dfp, cd, fy_ref, code_dfp or code)
    cur = acc(dre_current, cd, ref, code, ytd=True)
    prev = acc(dre_current, cd, ref, code, order="prev", ytd=True)
    if pd.notna(fy) and pd.notna(cur) and pd.notna(prev):
        return fy + cur - prev
    return np.nan


def shares_for_price(store, kind, year, cnpj, ref, ticker, price) -> float:
    """Acoes para multiplicar pelo preco: units (ticker ...11) usam so as ordinarias (1 ON por unit); contagens em milhares (companhias que declaram assim,
    sem coluna de escala) sao detectadas por valor de mercado implausivelmente pequeno e multiplicadas por 1.000."""
    try:
        comp = store.read(kind, year, "composicao_capital")
        data = comp[(comp["CNPJ_CIA"].astype(str).eq(str(cnpj))) & (pd.to_datetime(comp["DT_REFER"], errors="coerce").eq(ref))]
    except Exception:
        data = pd.DataFrame()
    if data.empty and ref.year < 2020:
        # A CVM so publica a composicao do capital a partir dos arquivos de 2020: para documentos de 2019 usa-se a PRIMEIRA composicao disponivel (ref. 2020).
        # Contagem de acoes muda devagar; unica informacao posterior a data de formacao usada (somente jan-mai/2020), documentada na auditoria.
        frames = []
        for k in ("ITR", "DFP"):
            if (k, 2020) in store.zips:
                try:
                    frames.append(store.read(k, 2020, "composicao_capital"))
                except Exception:
                    pass
        if frames:
            allc = pd.concat(frames, ignore_index=True)
            allc = allc[allc["CNPJ_CIA"].astype(str).eq(str(cnpj))].assign(_d=lambda f: pd.to_datetime(f["DT_REFER"], errors="coerce")).sort_values("_d")
            data = allc[allc["_d"].eq(allc["_d"].min())] if len(allc) else allc
    if data.empty:
        return np.nan
    if "VERSAO" in data:
        data = data[data["VERSAO"].eq(data["VERSAO"].max())]      # reapresentacoes: so a ultima versao (evita somar duas)
    num = lambda col_: float(pd.to_numeric(data[col_], errors="coerce").fillna(0).sum()) if col_ in data else 0.0  # noqa: E731
    if str(ticker).upper().endswith("11.SA") or str(ticker).upper().endswith("11"):
        shares = num("QT_ACAO_ORDIN_CAP_INTEGR") - num("QT_ACAO_ORDIN_TESOURO")
    else:
        shares = num("QT_ACAO_TOTAL_CAP_INTEGR") - num("QT_ACAO_TOTAL_TESOURO")
    if shares <= 0:
        return np.nan
    if pd.notna(price) and price * shares < 5e8 and price * shares * 1000 < 5e12:      # contagem em milhares
        shares *= 1000.0
    return float(shares)


def historical_fundamentals_ttm(store, metadata, prices_at_formation, formation_date):
    rows, audit = [], []
    for _, item in metadata.iterrows():
        ticker, cd_cvm = item["ticker"], item.get("cd_cvm")
        doc = col.latest_doc_for_company(store, cd_cvm, formation_date)
        if doc is None:
            rows.append({"ticker": ticker, "dados_fundamentalistas_insuficientes": True, "motivo_dado_insuficiente": "sem documento CVM com DT_RECEB anterior a data de formacao"})
            audit.append({"ticker": ticker, "cd_cvm": cd_cvm, "status": "sem_doc_valido"})
            continue
        kind, year, ref, cd = str(doc["TIPO_DOC_CVM"]), int(doc["ANO_ARQUIVO_CVM"]), pd.Timestamp(doc["DT_REFER"]), int(cd_cvm)
        dre, bpa, bpp = stmt(store, kind, year, "DRE", cd), stmt(store, kind, year, "BPA", cd), stmt(store, kind, year, "BPP", cd)
        pl_code, min_code, financial = equity_codes(bpp, cd, ref)
        if pl_code is None:
            pl_code, min_code = "2.03", "2.03.09"
        pl_total = acc(bpp, cd, ref, pl_code)
        minority = first_valid(acc(bpp, cd, ref, min_code) if min_code else np.nan, 0.0)
        pl_ctrl = pl_total - minority if pd.notna(pl_total) else np.nan
        pl_prev = acc(bpp, cd, ref, pl_code, order="prev")
        minority_prev = first_valid(acc(bpp, cd, ref, min_code, order="prev") if min_code else np.nan, 0.0)
        pl_ctrl_prev = pl_prev - minority_prev if pd.notna(pl_prev) else np.nan
        pl_ctrl_avg = float(np.nanmean([pl_ctrl, pl_ctrl_prev])) if pd.notna(pl_ctrl) or pd.notna(pl_ctrl_prev) else np.nan
        receita = flow_ttm(store, kind, year, ref, cd, "3.01", dre)
        rec_ytd = acc(dre, cd, ref, "3.01", ytd=True)
        ttm_invalid = bool(kind != "DFP" and pd.notna(receita) and pd.notna(rec_ytd) and receita < 0.999 * rec_ytd)
        if ttm_invalid:
            receita = np.nan        # exercicio anterior zerado/incompleto (companhia recem-registrada): 12 meses moveis invalidos
        lucro_bruto = flow_ttm(store, kind, year, ref, cd, "3.03", dre)
        ebit = flow_ttm(store, kind, year, ref, cd, "3.05", dre)
        if pd.isna(ebit):
            ebit = flow_ttm(store, kind, year, ref, cd, "3.04", dre)
        lc, cc, mc = profit_codes(dre, cd, ref)
        dfp_prev = stmt(store, "DFP", ref.year - 1, "DRE", cd) if (kind != "DFP" and ("DFP", ref.year - 1) in store.zips) else None
        lc_d, cc_d, mc_d = profit_codes(dfp_prev, cd, pd.Timestamp(year=ref.year - 1, month=12, day=31)) if dfp_prev is not None else (lc, cc, mc)
        lucro = flow_ttm(store, kind, year, ref, cd, lc, dre, lc_d)
        lucro_attr = flow_ttm(store, kind, year, ref, cd, cc, dre, cc_d)
        lucro_mino = flow_ttm(store, kind, year, ref, cd, mc, dre, mc_d)
        mino_v = lucro_mino if pd.notna(lucro_mino) else 0.0
        if pd.notna(lucro_attr) and pd.notna(lucro) and lucro != 0 and abs(lucro_attr + mino_v - lucro) <= 0.02 * abs(lucro):
            lucro_ctrl = lucro_attr          # atribuicao ao controlador consistente com o consolidado
        elif pd.notna(lucro):
            lucro_ctrl = lucro - mino_v      # atribuicao ausente/zerada (ex.: 3.11.01 = 0 no exercicio): consolidado menos nao controladores
        else:
            lucro_ctrl = np.nan
        if ttm_invalid:
            lucro = lucro_ctrl = lucro_bruto = ebit = np.nan
        if not financial and pd.notna(receita) and receita == 0:
            financial = True        # holding sem receita operacional (equivalencia patrimonial): so ROE e P/L
        ativo_total = acc(bpa, cd, ref, "1")
        caixa = acc(bpa, cd, ref, "1.01.01")
        fornecedores = acc(bpp, cd, ref, "2.01.02")
        divida_bruta = prefix_total(bpp, cd, ref, ["2.01.04", "2.02.01"])
        divida_liquida = divida_bruta - caixa if pd.notna(divida_bruta) and pd.notna(caixa) else np.nan
        passivo_oneroso = divida_bruta if pd.notna(divida_bruta) else 0.0
        invested = passivo_oneroso + pl_total if pd.notna(pl_total) else np.nan             # investimento = passivo oneroso + PL (Assaf Neto)
        nopat = ebit * (1.0 - TAX_RATE) if pd.notna(ebit) and ebit > 0 else ebit
        preco = prices_at_formation.get(ticker, np.nan)
        shares = shares_for_price(store, kind, year, str(doc["CNPJ_CIA"]), ref, ticker, preco)
        market_cap = preco * shares if pd.notna(preco) and pd.notna(shares) else np.nan
        ratio = lambda a, b: a / b if pd.notna(a) and pd.notna(b) and b != 0 else np.nan  # noqa: E731
        row = {
            "ticker": ticker, "roe": ratio(lucro_ctrl, pl_ctrl_avg),
            "roic": np.nan if financial else ratio(nopat, invested),
            "margem_bruta": np.nan if financial else ratio(lucro_bruto, receita),
            "margem_liquida": ratio(lucro, receita), "margem_ebit": np.nan if financial else ratio(ebit, receita),
            "pl_atual": ratio(market_cap, lucro_ctrl),
            "patrimonio_liquido": pl_total, "receita_liquida": receita, "lucro_bruto": lucro_bruto, "lucro_liquido": lucro,
            "lucro_liquido_anualizado": lucro_ctrl, "ebit": ebit, "ebit_anualizado": ebit, "ativo_total": ativo_total,
            "divida_bruta": divida_bruta, "caixa": caixa, "divida_liquida": divida_liquida, "divida_liquida_patrimonio": ratio(divida_liquida, pl_total), "passivo_oneroso_patrimonio": ratio(passivo_oneroso, pl_total),
            "acoes_total_cvm": shares, "valor_mercado_estimado": market_cap, "documento_cvm_tipo": kind, "documento_cvm_ano_arquivo": year,
            "documento_data_referencia": ref.date().isoformat(), "documento_dt_receb": pd.Timestamp(doc["DT_RECEB"]).date().isoformat(),
            "anti_lookahead_ok": bool(pd.Timestamp(doc["DT_RECEB"]) < formation_date), "instituicao_financeira": bool(financial),
            "dados_fundamentalistas_insuficientes": False,
        }
        required = ["roe", "pl_atual"] if financial else ["roe", "roic", "margem_bruta", "margem_liquida", "pl_atual"]
        missing = [key for key in required if pd.isna(row[key])]
        row["dados_fundamentalistas_insuficientes"] = bool(missing)
        row["motivo_dado_insuficiente"] = ("campos ausentes: " + ", ".join(missing)) if missing else ""
        rows.append(row)
        audit.append({"ticker": ticker, "cd_cvm": cd_cvm, "documento": kind, "data_referencia": ref.date().isoformat(), "dt_receb": pd.Timestamp(doc["DT_RECEB"]).date().isoformat(),
                      "data_formacao": formation_date.date().isoformat(), "anti_lookahead_ok": bool(pd.Timestamp(doc["DT_RECEB"]) < formation_date), "receita_liquida": receita,
                      "lucro_liquido_anualizado": lucro_ctrl, "patrimonio_liquido": pl_total, "roe": row["roe"], "preco_formacao": preco, "acoes_total_cvm": shares, "pl_atual": row["pl_atual"]})
    return pd.DataFrame(rows), pd.DataFrame(audit)
