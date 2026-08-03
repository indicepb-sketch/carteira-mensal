from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except Exception:
    go = None
    make_subplots = None

ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"

st.set_page_config(page_title="Robo de Carteira Mensal", layout="wide", initial_sidebar_state="expanded")


@dataclass(frozen=True)
class AppFiles:
    forward: Path | None
    partial: Path | None
    base: Path | None
    backtest: Path | None
    operational: Path | None


def fmt_pct(value: Any, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:.{digits}f}%"


def fmt_money(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def latest(pattern: str) -> Path | None:
    files = sorted(EXCEL_DIR.glob(pattern), key=lambda path: path.stat().st_mtime)
    return files[-1] if files else None


@st.cache_data(show_spinner=False)
def read_sheet(path: str, sheet: str) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet)
    except Exception:
        return pd.DataFrame()


@st.cache_data(show_spinner=False)
def read_fields_cached(path: str, sheet: str) -> dict[str, Any]:
    frame = read_sheet(path, sheet)
    if {"campo", "valor"}.issubset(frame.columns):
        return dict(zip(frame["campo"].astype(str), frame["valor"]))
    if {"metrica", "valor"}.issubset(frame.columns):
        return dict(zip(frame["metrica"].astype(str), frame["valor"]))
    return {}


def read_fields(path: Path, sheet: str) -> dict[str, Any]:
    return read_fields_cached(str(path), sheet)


def latest_base_from_forward(path: Path | None) -> Path | None:
    if path is None:
        return None
    fields = read_fields(path, "Resumo Forward")
    base_name = fields.get("base_mensal_usada")
    if base_name:
        base = EXCEL_DIR / str(base_name)
        if base.exists():
            return base
    return latest("carteira_recomendada_2026_07_v*.xlsx")


def discover_files() -> AppFiles:
    forward = latest("carteira_forward_2026_07*.xlsx")
    return AppFiles(
        forward=forward,
        partial=latest("parcial_carteira_forward_2026_07*.xlsx"),
        base=latest_base_from_forward(forward),
        backtest=latest("shadow_validacao_oos_2024_2026.xlsx"),
        operational=latest("shadow_teste35_modelo_consolidado_operacional.xlsx"),
    )


def load_forward(files: AppFiles) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if files.forward is None:
        return pd.DataFrame(), pd.DataFrame(), {}
    applied = read_sheet(str(files.forward), "Carteira Aplicada")
    model = read_sheet(str(files.forward), "Carteira Forward")
    summary = read_fields(files.forward, "Resumo Forward")
    return applied, model, summary


def load_partial(files: AppFiles) -> tuple[pd.DataFrame, dict[str, Any]]:
    if files.partial is None:
        return pd.DataFrame(), {}
    return read_sheet(str(files.partial), "Ativos"), read_fields(files.partial, "Resumo Parcial")


def active_assets(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "ticker" not in frame:
        return pd.DataFrame()
    out = frame.copy()
    out["ticker"] = out["ticker"].astype(str).str.upper()
    return out[~out["ticker"].isin(["CAIXA", "CDI"])].copy()


def build_order_suggestion(
    applied: pd.DataFrame,
    stock_amount: float,
    fractional: bool,
    price_mode: str,
    min_relevant_weight: float = 0.02,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    if applied.empty:
        return pd.DataFrame(), pd.DataFrame(), {}
    price_col = "preco_atual" if price_mode == "Preco atual/parcial" and "preco_atual" in applied.columns else "preco_entrada_fechamento_mes_anterior"
    if price_col not in applied.columns:
        price_col = "preco_entrada"

    base = applied.copy()
    base["ticker"] = base["ticker"].astype(str).str.upper()
    base["peso_recomendado"] = pd.to_numeric(base["peso_recomendado"], errors="coerce").fillna(0.0)
    defensive_weight = float(base.loc[base["ticker"].isin(["CAIXA", "CDI"]), "peso_recomendado"].sum())
    assets = base[~base["ticker"].isin(["CAIXA", "CDI"])].copy()
    total_stock_weight = float(assets["peso_recomendado"].sum())
    if total_stock_weight <= 0:
        totals = {"capital_acoes": stock_amount, "investido_acoes": 0.0, "caixa_residual": stock_amount, "caixa_recomendada": 0.0, "capital_total_equivalente": stock_amount}
        return pd.DataFrame(), pd.DataFrame(), totals

    assets["peso_na_parte_acoes"] = assets["peso_recomendado"] / total_stock_weight
    removed = assets[assets["peso_na_parte_acoes"].lt(min_relevant_weight)].copy()
    executable = assets[assets["peso_na_parte_acoes"].ge(min_relevant_weight)].copy()
    if executable.empty:
        totals = {"capital_acoes": stock_amount, "investido_acoes": 0.0, "caixa_residual": stock_amount, "caixa_recomendada": 0.0, "capital_total_equivalente": stock_amount}
        return pd.DataFrame(), removed, totals

    executable["peso_executavel"] = executable["peso_na_parte_acoes"] / executable["peso_na_parte_acoes"].sum()
    rows = []
    invested = 0.0
    for _, row in executable.iterrows():
        ticker = str(row.get("ticker", "")).upper()
        weight = float(row.get("peso_executavel", 0.0) or 0.0)
        target_value = stock_amount * weight
        price = pd.to_numeric(row.get(price_col), errors="coerce")
        if pd.isna(price) or float(price) <= 0:
            qty = 0
            real_value = 0.0
        else:
            raw_qty = target_value / float(price)
            qty = int(np.floor(raw_qty if fractional else raw_qty / 100) * (1 if fractional else 100))
            real_value = qty * float(price)
            invested += real_value
        real_weight = real_value / stock_amount if stock_amount else 0.0
        rows.append({
            "ticker": ticker,
            "peso_original_total": row.get("peso_recomendado"),
            "peso_executavel_acoes": weight,
            "preco_usado": price,
            "valor_alvo": target_value,
            "quantidade": qty,
            "valor_real": real_value,
            "peso_real_acoes": real_weight,
            "desvio_peso": real_weight - weight,
        })

    residual_cash = max(stock_amount - invested, 0.0)
    cash_equivalent = stock_amount * defensive_weight / total_stock_weight if total_stock_weight else 0.0
    totals = {
        "capital_acoes": stock_amount,
        "investido_acoes": invested,
        "caixa_residual": residual_cash,
        "caixa_recomendada": cash_equivalent,
        "capital_total_equivalente": stock_amount + cash_equivalent,
    }
    return pd.DataFrame(rows), removed, totals

@st.cache_data(show_spinner=False)
def price_history(ticker: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf
    data = yf.download(ticker, start=start, end=end, auto_adjust=False, progress=False)
    if data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [col[-1] for col in data.columns]
    close_col = "Adj Close" if "Adj Close" in data.columns else "Close"
    out = pd.DataFrame(index=pd.to_datetime(data.index))
    out["close"] = pd.to_numeric(data[close_col], errors="coerce")
    out["mm9"] = out["close"].rolling(9).mean()
    out["mm21"] = out["close"].rolling(21).mean()
    out["mm50"] = out["close"].rolling(50).mean()
    out["mm100"] = out["close"].rolling(100).mean()
    mid = out["close"].rolling(20).mean()
    std = out["close"].rolling(20).std()
    out["bb_upper"] = mid + 2 * std
    out["bb_mid"] = mid
    out["bb_lower"] = mid - 2 * std
    delta = out["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi"] = 100 - (100 / (1 + rs))
    return out.dropna(how="all")


def technical_chart(ticker: str, entry_date: Any, allow_network: bool = False) -> None:
    if go is None or make_subplots is None:
        st.warning("Plotly nao esta instalado.")
        return
    if not allow_network:
        st.info("Grafico tecnico usa yfinance. Marque a autorizacao de consulta externa na barra lateral para carregar o historico.")
        return
    with st.spinner(f"Carregando historico de {ticker}..."):
        hist = price_history(ticker, "2025-01-01", pd.Timestamp.today().date().isoformat())
    if hist.empty:
        st.info("Historico de precos nao disponivel para o grafico.")
        return
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.05)
    fig.add_trace(go.Scatter(x=hist.index, y=hist["close"], name="Preco ajustado", line=dict(color="#1f77b4", width=2)), row=1, col=1)
    for col, color in [("mm9", "#2ca02c"), ("mm21", "#ff7f0e"), ("mm50", "#9467bd"), ("mm100", "#8c564b")]:
        fig.add_trace(go.Scatter(x=hist.index, y=hist[col], name=col.upper(), line=dict(color=color, width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=hist.index, y=hist["bb_upper"], name="Bollinger sup.", line=dict(color="#d62728", width=1, dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(x=hist.index, y=hist["bb_lower"], name="Bollinger inf.", line=dict(color="#17becf", width=1, dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(x=hist.index, y=hist["rsi"], name="RSI 14", line=dict(color="#6f42c1", width=1.5)), row=2, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color="#999", row=2, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="#999", row=2, col=1)
    if entry_date:
        try:
            fig.add_vline(x=pd.to_datetime(entry_date), line_dash="dash", line_color="#444")
        except Exception:
            pass
    fig.update_layout(height=640, margin=dict(l=20, r=20, t=20, b=20), legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    st.plotly_chart(fig, use_container_width=True)


def metric_row(items: list[tuple[str, Any, str]]) -> None:
    cols = st.columns(len(items))
    for col, (label, value, kind) in zip(cols, items):
        rendered = fmt_pct(value) if kind == "pct" else fmt_money(value) if kind == "money" else str(value)
        col.metric(label, rendered)


def main() -> None:
    files = discover_files()
    st.title("Robo de Carteira Mensal")
    st.caption("MVP local para carteira recomendada, montagem de ordens e acompanhamento parcial.")

    with st.sidebar:
        st.header("Arquivos")
        st.write("Carteira forward")
        st.code(files.forward.name if files.forward else "nao encontrada")
        st.write("Parcial")
        st.code(files.partial.name if files.partial else "nao encontrada")
        st.write("Base mensal")
        st.code(files.base.name if files.base else "nao encontrada")
        st.write("Backtest")
        st.code(files.backtest.name if files.backtest else "nao encontrado")
        st.write("Modelo operacional")
        st.code(files.operational.name if files.operational else "nao encontrado")
        st.divider()
        allow_market_data = st.checkbox("Autorizar consulta externa para graficos", value=False, help="Quando marcado, a aba Detalhe do Ativo consulta yfinance para montar os graficos tecnicos.")

    applied, model, forward_summary = load_forward(files)
    partial_assets, partial_summary = load_partial(files)
    if not partial_assets.empty and not applied.empty:
        cols = ["ticker", "preco_atual", "data_avaliacao", "retorno_periodo", "contribuicao"]
        applied = applied.merge(partial_assets[[c for c in cols if c in partial_assets.columns]], on="ticker", how="left", suffixes=("", "_parcial"))

    tab_port, tab_order, tab_track, tab_asset, tab_back = st.tabs(["Carteira", "Montagem", "Acompanhamento", "Detalhe do Ativo", "Backtests"])

    with tab_port:
        st.subheader("Carteira Recomendada")
        if applied.empty:
            st.warning("Carteira forward nao encontrada.")
        else:
            tickers_upper = applied["ticker"].astype(str).str.upper()
            exposure = pd.to_numeric(applied.loc[~tickers_upper.isin(["CAIXA", "CDI"]), "peso_recomendado"], errors="coerce").sum()
            cdi_weight = pd.to_numeric(applied.loc[tickers_upper.isin(["CAIXA", "CDI"]), "peso_recomendado"], errors="coerce").sum()
            metric_row([("Mes", forward_summary.get("mes_forward", "-"), "text"), ("Regime", forward_summary.get("subtipo_queda", forward_summary.get("regime_mercado", "-")), "text"), ("Exposicao acoes", exposure, "pct"), ("CDI liquido IR", cdi_weight, "pct"), ("Beta realizado", forward_summary.get("beta_realizado_carteira", "-"), "text")])
            st.dataframe(applied, use_container_width=True, hide_index=True)

    with tab_order:
        st.subheader("Montagem da Carteira")
        stock_amount = st.number_input("Capital destinado as acoes", min_value=1000.0, value=1000.0, step=500.0, format="%.2f")
        min_weight = st.number_input("Peso minimo relevante dentro das acoes", min_value=0.0, max_value=0.10, value=0.02, step=0.005, format="%.3f")
        fractional = st.toggle("Permitir mercado fracionario", value=True)
        price_options = ["Preco de entrada"]
        if "preco_atual" in applied.columns:
            price_options.insert(0, "Preco atual/parcial")
        price_mode = st.radio("Preco usado para simular compra", price_options, horizontal=True)
        orders, removed, totals = build_order_suggestion(applied, stock_amount, fractional, price_mode, min_weight)
        if stock_amount < 1000:
            st.warning("Capital minimo para execucao: R$ 1.000 em acoes.")
        if orders.empty:
            st.warning("Carteira nao disponivel para montar ordens.")
        else:
            metric_row([("Capital em acoes", totals["capital_acoes"], "money"), ("Investido em acoes", totals["investido_acoes"], "money"), ("Caixa residual das compras", totals["caixa_residual"], "money"), ("CDI recomendado equivalente", totals["caixa_recomendada"], "money"), ("Capital total equivalente", totals["capital_total_equivalente"], "money")])
            st.caption("A carteira executavel remove pesos irrelevantes e redistribui o capital apenas entre os ativos restantes.")
            st.dataframe(orders, use_container_width=True, hide_index=True)
            if not removed.empty:
                st.markdown("**Ativos removidos por peso irrelevante**")
                removed_cols = ["ticker", "peso_recomendado", "peso_na_parte_acoes", "preco_entrada_fechamento_mes_anterior"]
                st.dataframe(removed[[c for c in removed_cols if c in removed.columns]], use_container_width=True, hide_index=True)

    with tab_track:
        st.subheader("Acompanhamento Parcial")
        if not partial_summary:
            st.info("Parcial ainda nao encontrada.")
        else:
            metric_row([("Data entrada", partial_summary.get("data_entrada", "-"), "text"), ("Data avaliacao", partial_summary.get("data_avaliacao_parcial", "-"), "text"), ("Carteira parcial", partial_summary.get("retorno_carteira_parcial_aplicada"), "pct"), ("CDI liquido", partial_summary.get("retorno_cdi_liquido_periodo"), "pct"), ("IBOV parcial", partial_summary.get("retorno_ibov_parcial"), "pct"), ("Alfa parcial", partial_summary.get("alfa_parcial_vs_ibov"), "pct")])
            st.caption("Resultado parcial: mes em andamento, nao e fechamento oficial.")
            st.dataframe(partial_assets, use_container_width=True, hide_index=True)

    with tab_asset:
        st.subheader("Detalhe do Ativo")
        assets = active_assets(applied)
        if assets.empty:
            st.warning("Nenhum ativo selecionado.")
        else:
            ticker = st.selectbox("Ativo", assets["ticker"].tolist())
            selected = assets[assets["ticker"].eq(ticker)].iloc[0].to_dict()
            candidates = read_sheet(str(files.forward), "Candidatos Sombra") if files.forward else pd.DataFrame()
            candidate = candidates[candidates["ticker"].astype(str).str.upper().eq(ticker)].copy() if "ticker" in candidates else pd.DataFrame()
            data = selected | (candidate.iloc[0].to_dict() if not candidate.empty else {})
            cols = st.columns(4)
            cols[0].metric("Peso aplicado", fmt_pct(data.get("peso_recomendado")))
            cols[1].metric("Peso modelo", fmt_pct(data.get("peso_modelo_100pct")))
            cols[2].metric("Beta", "-" if pd.isna(data.get("beta", np.nan)) else f"{float(data.get('beta')):.2f}")
            cols[3].metric("Nota final", "-" if pd.isna(data.get("nota_final", np.nan)) else f"{float(data.get('nota_final')):.1f}")
            premiss_cols = ["decisao_preliminar_ajustada", "status_para_risco", "categoria_elegibilidade", "tipo_timing", "tipo_watchlist", "motivo_bloqueio_otimizacao", "penalizacoes_otimizacao", "fundamento_bloqueante", "qualidade_fundamentalista", "roe", "margem_liquida", "pl_atual", "forca_relativa_score", "retorno_medio", "cv", "correlacao_ibov"]
            premissas = pd.DataFrame([{"premissa": col, "valor": data.get(col)} for col in premiss_cols if col in data and not pd.isna(data.get(col))])
            st.markdown("**Premissas e sinais**")
            st.dataframe(premissas, use_container_width=True, hide_index=True)
            technical_chart(ticker, forward_summary.get("data_limite_dados_selecao"), allow_market_data)

    with tab_back:
        st.subheader("Historico e Backtests")
        if files.operational is not None:
            st.markdown("**Modelo Consolidado Operacional - Teste 35**")
            resumo_op = read_sheet(str(files.operational), "Resumo Operacional")
            mes_op = read_sheet(str(files.operational), "Mes a Mes")
            regime_op = read_sheet(str(files.operational), "Por Regime Real")
            carteira_op = read_sheet(str(files.operational), "Carteira Operacional")
            validacao_op = read_sheet(str(files.operational), "Validacao")
            resumo_map = dict(zip(resumo_op.get("metrica", pd.Series(dtype=str)).astype(str), resumo_op.get("valor", pd.Series(dtype=object)))) if not resumo_op.empty else {}
            metric_row([
                ("Retorno operacional", resumo_map.get("retorno_operacional_cdi"), "pct"),
                ("Retorno IBOV", resumo_map.get("retorno_ibov"), "pct"),
                ("Alfa", resumo_map.get("alfa_operacional_vs_ibov"), "pct"),
                ("Taxa de acerto", resumo_map.get("taxa_acerto_operacional"), "pct"),
                ("Exposicao media acoes", resumo_map.get("exposicao_media_acoes"), "pct"),
                ("Peso medio CDI", resumo_map.get("peso_medio_cdi"), "pct"),
            ])
            if go is not None and not mes_op.empty:
                fig = go.Figure()
                for col, name in [("retorno_total_operacional", "Modelo operacional"), ("retorno_modelo_zero", "Sem CDI no residual"), ("retorno_expost_ibov", "IBOV")]:
                    if col in mes_op:
                        cum = (1 + pd.to_numeric(mes_op[col], errors="coerce").fillna(0)).cumprod() - 1
                        fig.add_trace(go.Scatter(x=mes_op["mes"], y=cum, name=name, mode="lines+markers"))
                fig.update_layout(height=420, yaxis_tickformat=".0%", margin=dict(l=20, r=20, t=20, b=20))
                st.plotly_chart(fig, use_container_width=True)
            st.markdown("**Mes a mes**")
            st.dataframe(mes_op, use_container_width=True, hide_index=True)
            st.markdown("**Por regime real**")
            st.dataframe(regime_op, use_container_width=True, hide_index=True)
            st.markdown("**Carteira operacional: acoes + CDI**")
            st.dataframe(carteira_op, use_container_width=True, hide_index=True)
            with st.expander("Validacao de pesos e contribuicoes"):
                st.dataframe(validacao_op, use_container_width=True, hide_index=True)
        elif files.backtest is None:
            st.info("Arquivo consolidado de backtest nao encontrado.")
        else:
            summary = read_sheet(str(files.backtest), "resumo")
            month = read_sheet(str(files.backtest), "mes_a_mes")
            st.dataframe(summary, use_container_width=True, hide_index=True)
            if go is not None and not month.empty:
                fig = go.Figure()
                for col, name in [("ret100", "Consolidada 100%"), ("ret_def", "Exposicao defensiva"), ("ibov", "IBOV")]:
                    if col in month:
                        cum = (1 + pd.to_numeric(month[col], errors="coerce").fillna(0)).cumprod() - 1
                        fig.add_trace(go.Scatter(x=month["mes"], y=cum, name=name, mode="lines+markers"))
                fig.update_layout(height=420, yaxis_tickformat=".0%", margin=dict(l=20, r=20, t=20, b=20))
                st.plotly_chart(fig, use_container_width=True)
            st.dataframe(month, use_container_width=True, hide_index=True)

if __name__ == "__main__":
    main()




