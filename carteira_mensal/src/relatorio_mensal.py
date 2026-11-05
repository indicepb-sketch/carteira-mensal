"""Relatorio mensal executivo da Carteira Recomendada (PDF).

Gera um PDF com capa, composicao, racional do regime, desempenho do mes,
historico do modelo e uma ficha por acao (ultimos 12 meses com os indicadores
que o robo usa: medias moveis semanais, Bollinger, RSI).

Uso via script: ``python scripts/gerar_relatorio_mensal.py --mes 2026-09``.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from portfolio_execution import executable_portfolio, is_cdi, norm, practical_partial_return
from technical_indicators import (
    bollinger_bands,
    classify_bollinger,
    classify_rsi,
    classify_trend,
    moving_average,
    rsi,
    weekly_close,
)

# ----------------------------------------------------------------------------
# Constantes e identidade visual
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
EXCEL_DIR = ROOT / "output" / "excel"
PDF_DIR = ROOT / "output" / "pdf"
CACHE_DIR = ROOT / "data" / "report_cache"
CDI_MONTHLY_PATH = ROOT / "data" / "processed" / "cdi_mensal_ipeadata.csv"
IBOV_MONTHLY_PATH = ROOT / "data" / "processed" / "ibov_mensal_oficial.csv"
OPERATIONAL_FILE = "shadow_teste49_top15_regime_capital.xlsx"

CAPITAL = 10_000.0
MIN_WEIGHT = 0.01
MAX_STOCKS = 15
CDI_IR = 0.225
MA_WINDOWS = (9, 21, 50, 100)

NAVY = "#0B1F3A"
NAVY_2 = "#16355E"
TEAL = "#0E7C86"
GOLD = "#C9A24B"
INK = "#1F2937"
MUTED = "#6B7280"
LINE = "#D9DEE7"
PAPER = "#F5F7FA"
GREEN = "#1E8E5A"
RED = "#C0392B"
PURPLE = "#6B5B95"
GREY = "#8A94A6"

MONTHS_PT = ["Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho", "Julho",
             "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
MONTHS_PT_ACC = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
                 "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
MONTHS_ABBR = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]

PAGE_W, PAGE_H = A4
MARGIN = 1.7 * cm
CONTENT_W = PAGE_W - 2 * MARGIN


# ----------------------------------------------------------------------------
# Formatacao pt-BR
# ----------------------------------------------------------------------------
def fnum(value: Any, default: float = np.nan) -> float:
    try:
        if value is None or pd.isna(value):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def fmt_pct(value: Any, digits: int = 2, signed: bool = False) -> str:
    number = fnum(value)
    if np.isnan(number):
        return "-"
    text = f"{number * 100:{'+' if signed else ''}.{digits}f}%"
    return text.replace(".", ",")


def fmt_pp(value: Any, digits: int = 2) -> str:
    number = fnum(value)
    if np.isnan(number):
        return "-"
    return f"{number * 100:+.{digits}f}".replace(".", ",") + " p.p."


def fmt_num(value: Any, digits: int = 1) -> str:
    number = fnum(value)
    if np.isnan(number):
        return "-"
    return f"{number:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_brl(value: Any) -> str:
    number = fnum(value)
    if np.isnan(number):
        return "-"
    return "R$ " + fmt_num(number, 2)


def month_label(month: str, upper: bool = False) -> str:
    year, mon = month.split("-")
    name = MONTHS_PT_ACC[int(mon) - 1]
    return f"{name.upper() if upper else name} / {year}"


_PT_WORDS = {
    "acoes": "ações", "liquido": "líquido", "favoravel": "favorável", "tendencia": "tendência",
    "atencao": "atenção", "saudavel": "saudável", "tecnico": "técnico", "preco": "preço",
    "indisponivel": "indisponível", "aceitavel": "aceitável", "protecao": "proteção",
    "minima": "mínima", "proximo": "próximo", "proxima": "próxima", "maximo": "máximo", "risco": "risco", "seletivo": "seletivo",
}
_COMPANY_NAMES = {
    "EMBRAER": "Embraer", "WEG": "WEG", "VIBRA": "Vibra", "GERDAU MET": "Gerdau Metalúrgica",
    "KLABIN S/A": "Klabin", "SANTANDER BR": "Santander Brasil", "ULTRAPAR": "Ultrapar",
    "BBSEGURIDADE": "BB Seguridade", "CSNMINERACAO": "CSN Mineração", "COPASA": "Copasa",
}


def pt_fix(value: Any) -> str:
    """Normaliza textos vindos das planilhas (sem acento / com underscore) para exibicao."""
    text = str(value if value is not None else "-").replace("_", " ").strip()
    if text.lower() == "nan":
        return "-"

    def swap(match: re.Match) -> str:
        word = match.group(0)
        new = _PT_WORDS[word.lower()]
        return new.capitalize() if word[0].isupper() else new

    pattern = "(?<![A-Za-z])(" + "|".join(_PT_WORDS) + ")(?![A-Za-z])"
    return re.sub(pattern, swap, text, flags=re.IGNORECASE)


def short_ticker(ticker: str) -> str:
    return str(ticker).upper().replace(".SA", "")


def title_name(name: Any) -> str:
    text = str(name or "").strip()
    if text.upper() in _COMPANY_NAMES:
        return _COMPANY_NAMES[text.upper()]
    return text.title() if text.isupper() else text


def sign_color(value: float) -> str:
    return GREEN if fnum(value, 0) >= 0 else RED


# ----------------------------------------------------------------------------
# Carga de dados do mes
# ----------------------------------------------------------------------------
def _file_key(path: Path) -> tuple[str, str, str]:
    version = re.search(r"v(\d{8}_\d{6})", path.stem)
    return (version.group(1) if version else "00000000_000000", path.name, "")


def _fields(path: Path, sheet: str) -> dict[str, Any]:
    frame = pd.read_excel(path, sheet_name=sheet)
    for key in ("campo", "metrica", "item"):
        if {key, "valor"}.issubset(frame.columns):
            return dict(zip(frame[key].astype(str), frame["valor"]))
    return {}


def _latest(pattern: str) -> Path | None:
    found = sorted(EXCEL_DIR.glob(pattern), key=_file_key)
    return found[-1] if found else None


def month_key(month: str) -> str:
    return month.replace("-", "_")


@dataclass
class MonthData:
    month: str
    forward_path: Path
    base_path: Path | None
    partial_path: Path | None
    forward: dict[str, Any]
    regime: dict[str, Any]
    database: dict[str, Any]
    partial: dict[str, Any]
    carteira: pd.DataFrame          # carteira aplicada (modelo, 10 ativos + CDI)
    executavel: pd.DataFrame        # carteira executavel Top15 / R$ 10 mil
    ativos_parcial: pd.DataFrame    # retornos por ativo no mes
    tecnicos: pd.DataFrame          # indicadores tecnicos do robo na data base
    cutoff: pd.Timestamp            # data limite dos dados de selecao
    end: pd.Timestamp               # data de avaliacao do mes
    fechado: bool


def load_month_data(month: str) -> MonthData:
    key = month_key(month)
    forward_path = EXCEL_DIR / f"carteira_forward_{key}.xlsx"
    manifest = ROOT / "output" / "production" / f"{month}.json"
    if manifest.exists():   # carteira oficial do mes (pode ser uma versao posterior a original, ver superseded/)
        official = EXCEL_DIR / str(json.loads(manifest.read_text(encoding="utf-8")).get("file", ""))
        if official.is_file():
            forward_path = official
    if not forward_path.exists():
        raise FileNotFoundError(f"Carteira forward de {month} nao encontrada: {forward_path.name}")
    forward = _fields(forward_path, "Resumo Forward")
    regime = _fields(forward_path, "Regime Mercado Base")
    database = _fields(forward_path, "Data Base Carteira")

    sheets = pd.ExcelFile(forward_path).sheet_names
    carteira = pd.read_excel(forward_path, sheet_name="Carteira Aplicada" if "Carteira Aplicada" in sheets
                             else "Carteira Forward")
    carteira = norm(carteira)
    carteira["ticker"] = carteira["ticker"].astype(str).str.upper()

    partials = sorted(EXCEL_DIR.glob(f"parcial_carteira_forward_{key}_v*.xlsx"), key=_file_key)
    partial_path = None
    for path in reversed(partials):
        status = str(_fields(path, "Resumo Parcial").get("status", ""))
        if "fechamento" in status:
            partial_path = path
            break
    if partial_path is None and partials:
        partial_path = partials[-1]
    partial = _fields(partial_path, "Resumo Parcial") if partial_path else {}
    ativos = pd.read_excel(partial_path, sheet_name="Ativos") if partial_path else pd.DataFrame()

    base_path = _latest(f"carteira_recomendada_{key}_v*.xlsx")
    tecnicos = pd.DataFrame()
    if base_path is not None:
        tecnicos = pd.read_excel(base_path, sheet_name="Indicadores Tecnicos")
        tecnicos["ticker"] = tecnicos["ticker"].astype(str).str.upper()

    executavel, _, _ = executable_portfolio(carteira, CAPITAL, MIN_WEIGHT, True, MAX_STOCKS)
    cutoff = pd.Timestamp(str(database.get("data_limite_dados_selecao", ""))[:10])
    end_text = str(partial.get("data_avaliacao_parcial") or database.get("ultimo_pregao_mes") or "")[:10]
    end = pd.Timestamp(end_text)
    if not partial:  # mes em andamento sem parcial oficial: usa precos ate ontem
        end = min(end, pd.Timestamp.today().normalize() - pd.Timedelta(days=1))
    fechado = "fechamento" in str(partial.get("status", "")).lower()
    return MonthData(month, forward_path, base_path, partial_path, forward, regime, database,
                     partial, carteira, executavel, ativos, tecnicos, cutoff, end, fechado)


def month_performance(data: MonthData) -> dict[str, Any]:
    """Retorno executavel do mes, contribuicoes e comparativos."""
    cdi_net = fnum(data.partial.get("retorno_cdi_liquido_periodo"))
    result, detail = practical_partial_return(data.executavel, data.ativos_parcial, cdi_net)
    ibov = fnum(data.partial.get("retorno_ibov_parcial"))
    return {
        "retorno": result,
        "ibov": ibov,
        "cdi": cdi_net,
        "alfa": result - ibov if not (np.isnan(result) or np.isnan(ibov)) else np.nan,
        "detalhe": detail,
        "retorno_modelo_100": fnum(data.partial.get("retorno_carteira_parcial_aplicada")),
    }


# ----------------------------------------------------------------------------
# Historico do modelo (mes a mes)
# ----------------------------------------------------------------------------
def load_history(upto_month: str) -> pd.DataFrame:
    """Serie mensal (modelo, IBOV, CDI liquido) ate o mes informado."""
    operational = EXCEL_DIR / OPERATIONAL_FILE
    frame = pd.read_excel(operational, sheet_name="Mes a Mes")
    cap = pd.to_numeric(frame["capital"], errors="coerce")
    frame = frame[frame["cenario"].astype(str).eq("TOP15") & cap.sub(CAPITAL).abs().lt(0.01)].copy()
    frame["mes"] = frame["mes"].astype(str).str[:7]
    rows = {r["mes"]: {"mes": r["mes"], "modelo": fnum(r["retorno"]), "ibov": fnum(r["retorno_ibov"]),
                       "cdi": np.nan} for _, r in frame.iterrows()}

    for path in sorted(EXCEL_DIR.glob("parcial_carteira_forward_*.xlsx"), key=_file_key):
        summary = _fields(path, "Resumo Parcial")
        mes = str(summary.get("mes", ""))[:7]
        if not mes or mes in rows or "fechamento" not in str(summary.get("status", "")).lower():
            continue
        if mes > upto_month:
            continue
        try:
            month_data = load_month_data(mes)
        except FileNotFoundError:
            continue
        perf = month_performance(month_data)
        if np.isnan(perf["retorno"]):
            continue
        rows[mes] = {"mes": mes, "modelo": perf["retorno"], "ibov": perf["ibov"], "cdi": perf["cdi"]}

    history = pd.DataFrame(sorted(rows.values(), key=lambda r: r["mes"]))
    history = history[history["mes"] <= upto_month].reset_index(drop=True)

    if CDI_MONTHLY_PATH.exists():
        cdi = pd.read_csv(CDI_MONTHLY_PATH)
        cdi["mes"] = cdi["mes"].astype(str).str[:7]
        cdi["cdi_liq"] = pd.to_numeric(cdi["cdi_bruto_mensal"], errors="coerce") * (1 - CDI_IR)
        history = history.merge(cdi[["mes", "cdi_liq"]], on="mes", how="left")
        history["cdi"] = history["cdi_liq"].combine_first(history["cdi"])
        history = history.drop(columns=["cdi_liq"])
    if IBOV_MONTHLY_PATH.exists():
        ibov = pd.read_csv(IBOV_MONTHLY_PATH)
        ibov["mes"] = ibov["mes"].astype(str).str[:7]
        ibov["ibov_oficial"] = pd.to_numeric(ibov["retorno_ibov_oficial"], errors="coerce")
        history = history.merge(ibov[["mes", "ibov_oficial"]], on="mes", how="left")
        history["ibov"] = history["ibov_oficial"].combine_first(history["ibov"])
        history = history.drop(columns=["ibov_oficial"])
    return history


def history_stats(history: pd.DataFrame) -> dict[str, Any]:
    model = history["modelo"].dropna()
    ibov = history["ibov"]
    curve = (1 + model).cumprod()
    peak = curve.cummax()
    drawdown = float((curve / peak - 1).min()) if len(curve) else np.nan
    both = history.dropna(subset=["modelo", "ibov"])
    return {
        "meses": int(len(model)),
        "acum_modelo": float((1 + model).prod() - 1) if len(model) else np.nan,
        "acum_ibov": float((1 + ibov.dropna()).prod() - 1) if ibov.notna().any() else np.nan,
        "acum_cdi": float((1 + history["cdi"].dropna()).prod() - 1) if history["cdi"].notna().any() else np.nan,
        "meses_positivos": int((model > 0).sum()),
        "bateu_ibov": int((both["modelo"] > both["ibov"]).sum()),
        "meses_comparaveis": int(len(both)),
        "melhor": (history.loc[model.idxmax(), "mes"], float(model.max())) if len(model) else ("-", np.nan),
        "pior": (history.loc[model.idxmin(), "mes"], float(model.min())) if len(model) else ("-", np.nan),
        "drawdown": drawdown,
    }


# ----------------------------------------------------------------------------
# Precos (yfinance com cache em disco)
# ----------------------------------------------------------------------------
def _yf_symbol(ticker: str) -> str:
    return ticker if ticker.startswith("^") or ticker.endswith(".SA") else f"{ticker}.SA"


def load_prices(ticker: str, start: pd.Timestamp, end: pd.Timestamp, allow_network: bool = True) -> pd.Series:
    """Fechamento ajustado diario. Usa cache local e baixa do yfinance se faltar cobertura."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{short_ticker(ticker)}.csv"
    series = pd.Series(dtype=float)
    if cache.exists():
        stored = pd.read_csv(cache, index_col=0, parse_dates=True)
        series = stored["adj_close"].dropna()
    covered = (not series.empty and series.index.min() <= start + pd.Timedelta(days=7)
               and series.index.max() >= end - pd.Timedelta(days=4))
    if not covered and allow_network:
        import yfinance as yf

        raw = yf.download(_yf_symbol(ticker), start=(start - pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
                          end=(end + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
                          auto_adjust=False, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        if not raw.empty and "Adj Close" in raw.columns:
            series = raw["Adj Close"].dropna().astype(float)
            series.index = pd.to_datetime(series.index).tz_localize(None)
            series.rename("adj_close").to_frame().to_csv(cache)
    if series.empty:
        raise RuntimeError(f"Sem precos para {ticker}: cache vazio e rede indisponivel")
    return series.sort_index()


# ----------------------------------------------------------------------------
# Graficos (matplotlib)
# ----------------------------------------------------------------------------
def _font_family() -> list[str]:
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Arial", "Liberation Sans", "Helvetica", "DejaVu Sans"):
        if name in available:
            return [name]
    return ["sans-serif"]


def _style() -> None:
    plt.rcParams.update({
        "font.family": _font_family(),
        "font.size": 8,
        "axes.edgecolor": LINE,
        "axes.labelcolor": MUTED,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": "#E8ECF2",
        "grid.linewidth": 0.6,
        "axes.axisbelow": True,
        "legend.frameon": False,
    })


def _png(fig: plt.Figure, width_cm: float, dpi: int = 200) -> Image:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    buffer.seek(0)
    pixel_w, pixel_h = fig.get_size_inches() * dpi
    return Image(buffer, width=width_cm * cm, height=width_cm * cm * pixel_h / pixel_w)


def chart_allocation(stock_weight: float, cdi_weight: float) -> Image:
    _style()
    fig, ax = plt.subplots(figsize=(2.6, 2.8))
    ax.pie([stock_weight, cdi_weight], colors=[TEAL, GOLD], startangle=90, counterclock=False,
           wedgeprops={"width": 0.34, "edgecolor": "white", "linewidth": 2})
    ax.text(0, 0.1, fmt_pct(stock_weight, 0), ha="center", va="center", fontsize=17,
            fontweight="bold", color=NAVY)
    ax.text(0, -0.22, "em ações", ha="center", va="center", fontsize=7.5, color=MUTED)
    ax.set(aspect="equal")
    ax.grid(False)
    ax.legend(["Ações", "CDI / Tesouro Selic"], loc="lower center", bbox_to_anchor=(0.5, -0.16),
              ncol=2, fontsize=6.8, handlelength=1, handleheight=1, columnspacing=1.0)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.99, bottom=0.14)
    return _png(fig, 6.6)


def chart_weights(table: pd.DataFrame, cdi_weight: float) -> Image:
    _style()
    names = [short_ticker(t) for t in table["ticker"]] + ["CDI"]
    values = list(table["peso_modelo_total"]) + [cdi_weight]
    palette = [TEAL] * len(table) + [GOLD]
    fig, ax = plt.subplots(figsize=(4.1, 2.7))
    positions = np.arange(len(names))[::-1]
    ax.barh(positions, values, color=palette, height=0.62)
    for pos, val in zip(positions, values):
        ax.text(val + 0.01, pos, fmt_pct(val, 1), va="center", fontsize=7, color=INK)
    ax.set_yticks(positions)
    ax.set_yticklabels(names, fontsize=7.5, color=INK)
    ax.set_xlim(0, max(values) * 1.16)
    ax.xaxis.set_visible(False)
    ax.grid(False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_color(LINE)
    fig.subplots_adjust(left=0.13, right=0.98, top=0.99, bottom=0.01)
    return _png(fig, 10.4)


def chart_sectors(table: pd.DataFrame) -> Image:
    _style()
    sectors = table.groupby("setor")["peso_modelo_total"].sum().sort_values()
    height = max(1.4, 0.2 * len(sectors) + 0.3)
    fig, ax = plt.subplots(figsize=(6.2, height))
    ax.barh(range(len(sectors)), sectors.values, color=NAVY_2, height=0.6)
    for i, val in enumerate(sectors.values):
        ax.text(val + 0.0015, i, fmt_pct(val, 1), va="center", fontsize=7.5, color=INK)
    ax.set_yticks(range(len(sectors)))
    ax.set_yticklabels([s[:38] for s in sectors.index], fontsize=7.5, color=INK)
    ax.set_xlim(0, sectors.max() * 1.15)
    ax.xaxis.set_visible(False)
    ax.grid(False)
    ax.spines["bottom"].set_visible(False)
    fig.subplots_adjust(left=0.36, right=0.98, top=0.99, bottom=0.01)
    return _png(fig, 15.7)


def chart_scores(table: pd.DataFrame) -> Image:
    _style()
    parts = [("score_tendencia", "Tendência", PURPLE), ("score_timing", "Timing", TEAL),
             ("score_fundamentos", "Fundamentos", GOLD), ("score_setor", "Setor", NAVY_2),
             ("score_risco", "Risco", GREY)]
    ordered = table.sort_values("nota_final", ascending=True)
    fig, ax = plt.subplots(figsize=(6.3, 0.22 * len(ordered) + 0.85))
    left = np.zeros(len(ordered))
    for column, label, color in parts:
        values = pd.to_numeric(ordered[column], errors="coerce").fillna(0).to_numpy()
        ax.barh(range(len(ordered)), values, left=left, color=color, height=0.62, label=label)
        left += values
    for i, total in enumerate(pd.to_numeric(ordered["nota_final"], errors="coerce").fillna(0)):
        ax.text(left[i] + 0.8, i, f"{total:.0f}", va="center", fontsize=7.5, color=INK, fontweight="bold")
    ax.set_yticks(range(len(ordered)))
    ax.set_yticklabels([short_ticker(t) for t in ordered["ticker"]], fontsize=8, color=INK)
    ax.set_xlim(0, max(left.max() * 1.15, 10))
    ax.set_xlabel("pontos da nota (0 a 100)", fontsize=7.5)
    ax.legend(ncol=5, fontsize=7, loc="lower center", bbox_to_anchor=(0.45, 1.0), handlelength=1,
              handleheight=1, columnspacing=1.2)
    ax.grid(axis="y", visible=False)
    fig.subplots_adjust(left=0.09, right=0.98, top=0.9, bottom=0.17)
    return _png(fig, 16.0)


def chart_month_compare(model: float, ibov: float, cdi: float, month: str) -> Image:
    _style()
    labels = ["Carteira", "IBOV", "CDI líquido"]
    values = [model, ibov, cdi]
    palette = [TEAL, NAVY_2, GOLD]
    fig, ax = plt.subplots(figsize=(2.85, 2.7))
    bars = ax.bar(labels, [v * 100 for v in values], color=palette, width=0.55)
    span = max(abs(v) for v in values if not np.isnan(v)) * 100
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, val * 100 + (span * 0.05 if val >= 0 else -span * 0.05),
                fmt_pct(val, 2, True), ha="center", va="bottom" if val >= 0 else "top",
                fontsize=8.5, fontweight="bold", color=INK)
    ax.axhline(0, color=GREY, linewidth=0.8)
    ax.set_ylabel("retorno no mês (%)", fontsize=7.5)
    ax.set_ylim(min(0, min(values) * 100) - span * 0.25, max(values) * 100 + span * 0.3)
    ax.grid(axis="x", visible=False)
    fig.subplots_adjust(left=0.18, right=0.98, top=0.95, bottom=0.1)
    return _png(fig, 7.2)


def chart_contributions(detail: pd.DataFrame) -> Image:
    _style()
    data = detail.copy()
    data["label"] = data.apply(lambda r: "CDI" if is_cdi(r) else short_ticker(r["ticker"]), axis=1)
    data = data.sort_values("contribuicao_pratica")
    fig, ax = plt.subplots(figsize=(3.85, 0.26 * len(data) + 0.9))
    values = data["contribuicao_pratica"] * 100
    ax.barh(range(len(data)), values, color=[GREEN if v >= 0 else RED for v in values], height=0.6)
    span = max(abs(values).max(), 0.01)
    for i, v in enumerate(values):
        ax.text(v + (span * 0.03 if v >= 0 else -span * 0.03), i, f"{v:+.2f}".replace(".", ",") + " p.p.",
                va="center", ha="left" if v >= 0 else "right", fontsize=7.5, color=INK)
    ax.axvline(0, color=GREY, linewidth=0.8)
    ax.set_yticks(range(len(data)))
    ax.set_yticklabels(data["label"], fontsize=8, color=INK)
    ax.set_xlim(values.min() - span * 0.45, values.max() + span * 0.45)
    ax.set_xlabel("contribuição para o retorno da carteira (p.p.)", fontsize=7.5)
    ax.grid(axis="y", visible=False)
    fig.subplots_adjust(left=0.13, right=0.98, top=0.98, bottom=0.16)
    return _png(fig, 9.8)


def chart_cumulative(history: pd.DataFrame) -> Image:
    _style()
    dates = pd.to_datetime(history["mes"] + "-01") + pd.offsets.MonthEnd(0)
    start = dates.iloc[0] - pd.offsets.MonthEnd(1)
    x = [start] + list(dates)

    def curve(series: pd.Series) -> list[float]:
        return [100.0] + list(100 * (1 + series.fillna(0)).cumprod())

    fig, ax = plt.subplots(figsize=(7.4, 3.0))
    series = [("Carteira (execução R$ 10 mil)", curve(history["modelo"]), TEAL, 2.2),
              ("IBOV", curve(history["ibov"]), NAVY_2, 1.5),
              ("CDI líquido de IR", curve(history["cdi"]), GOLD, 1.5)]
    for label, values, color, width in series:
        ax.plot(x, values, color=color, linewidth=width, label=label)
        ax.annotate(f"{values[-1]:.0f}", (x[-1], values[-1]), xytext=(4, 0), textcoords="offset points",
                    fontsize=8, color=color, va="center", fontweight="bold")
    ax.axhline(100, color=GREY, linewidth=0.7, linestyle=":")
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b/%y"))
    ax.set_ylabel("base 100", fontsize=7.5)
    ax.legend(loc="upper left", fontsize=7.5, ncol=3)
    ax.margins(x=0.02)
    fig.subplots_adjust(left=0.07, right=0.95, top=0.96, bottom=0.1)
    return _png(fig, CONTENT_W / cm)


def stock_series(ticker: str, cutoff: pd.Timestamp, end: pd.Timestamp,
                 allow_network: bool = True) -> dict[str, Any]:
    """Calcula os indicadores semanais exatamente como o robo (dados ate a data base)."""
    start = cutoff - pd.DateOffset(years=3)
    prices = load_prices(ticker, start, end, allow_network)
    history = prices[prices.index <= cutoff]
    weekly = weekly_close(history)
    return {
        "prices": prices,
        "weekly": weekly,
        "ma": {w: moving_average(weekly, w) for w in MA_WINDOWS},
        "bands": bollinger_bands(weekly, 20, 2.0),
        "rsi": rsi(weekly, 14),
        "last_date": history.index[-1],
    }


def tech_snapshot(series: dict[str, Any]) -> dict[str, Any]:
    """Leitura tecnica na data base, com as mesmas regras de classificacao do robo."""
    weekly: pd.Series = series["weekly"]
    price = float(weekly.iloc[-1])
    ma = {w: float(series["ma"][w].iloc[-1]) for w in MA_WINDOWS}
    bands = series["bands"].iloc[-1]
    rsi_value = float(series["rsi"].iloc[-1])
    trend = classify_trend(price, ma[9], ma[21], ma[50], ma[100])
    rsi_status, rsi_note = classify_rsi(rsi_value)
    boll_status, boll_note = classify_bollinger(price, float(bands["bollinger_middle"]),
                                                float(bands["bollinger_upper"]),
                                                float(bands["bollinger_lower"]), trend, rsi_value)
    return {
        "rsi": rsi_value, "rsi_status": rsi_status, "bollinger_status": boll_status,
        "bollinger_position": float(bands["bollinger_position"]), "tendencia": trend,
        "mm9": ma[9], "mm21": ma[21], "mm50": ma[50], "mm100": ma[100],
        "alertas_tecnicos": "; ".join(n for n in (rsi_note, boll_note) if n),
    }


def chart_stock(ticker: str, series: dict[str, Any], cutoff: pd.Timestamp, end: pd.Timestamp) -> Image:
    _style()
    start = cutoff - pd.DateOffset(months=12)
    weekly: pd.Series = series["weekly"].copy()
    # a ultima barra semanal e rotulada na sexta, mas o ultimo preco e o da data base
    relabeled = weekly.index.to_list()
    relabeled[-1] = series["last_date"]
    weekly.index = pd.DatetimeIndex(relabeled)

    def cut(values: pd.Series) -> pd.Series:
        out = values.copy()
        out.index = weekly.index
        return out[out.index >= start]

    price = weekly[weekly.index >= start]
    bands = series["bands"].copy()
    bands.index = weekly.index
    bands = bands[bands.index >= start]
    rsi_values = cut(series["rsi"])

    post = series["prices"]
    post = post[(post.index > cutoff) & (post.index <= end)]
    entry_price = float(price.iloc[-1])

    fig = plt.figure(figsize=(4.3, 3.4))
    grid = fig.add_gridspec(2, 1, height_ratios=[3.0, 1.1], hspace=0.07)
    ax = fig.add_subplot(grid[0])
    ax_rsi = fig.add_subplot(grid[1], sharex=ax)

    ax.fill_between(bands.index, bands["bollinger_lower"], bands["bollinger_upper"], color="#9DB4D3",
                    alpha=0.20, linewidth=0, label="Bollinger (20; 2σ)")
    ax.plot(bands.index, bands["bollinger_upper"], color="#9DB4D3", linewidth=0.7, linestyle="--")
    ax.plot(bands.index, bands["bollinger_lower"], color="#9DB4D3", linewidth=0.7, linestyle="--")
    ma_colors = {9: GOLD, 21: TEAL, 50: PURPLE, 100: GREY}
    for window in MA_WINDOWS:
        ax.plot(cut(series["ma"][window]).index, cut(series["ma"][window]).values,
                color=ma_colors[window], linewidth=1.0, label=f"MM{window}")
    ax.plot(price.index, price.values, color=NAVY, linewidth=1.7, label="Preço (fech. semanal)")
    ax.scatter([price.index[-1]], [entry_price], color=NAVY, s=26, zorder=5)

    ax.axvspan(cutoff, end + pd.Timedelta(days=2), color=GOLD, alpha=0.11, linewidth=0)
    ax_rsi.axvspan(cutoff, end + pd.Timedelta(days=2), color=GOLD, alpha=0.11, linewidth=0)
    if not post.empty:
        path = pd.concat([pd.Series([entry_price], index=[series["last_date"]]), post])
        ax.plot(path.index, path.values, color=NAVY, linewidth=1.1, linestyle=(0, (3, 1.5)))
        change = float(post.iloc[-1]) / entry_price - 1
        ax.text(cutoff + (end - cutoff) / 2, 0.97, fmt_pct(change, 1, True), transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=8, fontweight="bold", color=sign_color(change), zorder=10,
                bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "edgecolor": "none", "alpha": 0.9})
    ax.text(cutoff + (end - cutoff) / 2, 0.015, month_short(end), transform=ax.get_xaxis_transform(),
            ha="center", va="bottom", fontsize=5.6, color="#8C6D1F", fontweight="bold")

    handles, labels = ax.get_legend_handles_labels()
    order = [labels.index(n) for n in ("Preço (fech. semanal)", "MM9", "MM21", "MM50", "MM100",
                                        "Bollinger (20; 2σ)")]
    fig.legend([handles[i] for i in order], [labels[i] for i in order], loc="upper center", ncol=3,
               fontsize=6.2, handlelength=1.5, columnspacing=1.0, labelspacing=0.3,
               bbox_to_anchor=(0.55, 1.0))
    ax.set_ylabel("R$ (ajustado)", fontsize=6.8)
    plt.setp(ax.get_xticklabels(), visible=False)

    ax_rsi.axhspan(50, 65, color=GREEN, alpha=0.10, linewidth=0)
    ax_rsi.axhline(70, color=RED, linewidth=0.8, linestyle="--")
    ax_rsi.axhline(30, color=GREEN, linewidth=0.8, linestyle="--")
    ax_rsi.plot(rsi_values.index, rsi_values.values, color=TEAL, linewidth=1.3)
    ax_rsi.scatter([rsi_values.index[-1]], [rsi_values.iloc[-1]], color=TEAL, s=22, zorder=5)
    ax_rsi.annotate(f"{rsi_values.iloc[-1]:.1f}".replace(".", ","), (rsi_values.index[-1], rsi_values.iloc[-1]),
                    xytext=(-6, 7), textcoords="offset points", fontsize=7.5, color=TEAL,
                    fontweight="bold", ha="right")
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_yticks([30, 50, 70])
    ax_rsi.set_ylabel("RSI (14)", fontsize=6.8)
    ax_rsi.text(start + pd.Timedelta(days=4), 12, "faixa verde: zona ideal 50-65", fontsize=5.8,
                color=GREEN, va="center")
    ax_rsi.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax_rsi.xaxis.set_major_formatter(mdates.DateFormatter("%b/%y"))
    plt.setp(ax_rsi.get_xticklabels(), fontsize=6.5)
    ax_rsi.set_xlim(start, end + pd.Timedelta(days=24))
    fig.subplots_adjust(left=0.105, right=0.955, top=0.885, bottom=0.075)
    return _png(fig, 10.9)


def month_short(date: pd.Timestamp) -> str:
    return MONTHS_PT_ACC[date.month - 1].upper()


# ----------------------------------------------------------------------------
# Elementos de layout (reportlab)
# ----------------------------------------------------------------------------
def make_styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName="Helvetica", textColor=colors.HexColor(INK), leading=13, fontSize=9)
    return {
        "body": ParagraphStyle("body", alignment=TA_JUSTIFY, **base),
        "small": ParagraphStyle("small", **{**base, "fontSize": 7.5, "leading": 10,
                                            "textColor": colors.HexColor(MUTED)}),
        "tiny": ParagraphStyle("tiny", **{**base, "fontSize": 6.8, "leading": 8.6,
                                          "textColor": colors.HexColor(MUTED)}),
        "h1": ParagraphStyle("h1", **{**base, "fontName": "Helvetica-Bold", "fontSize": 17, "leading": 21,
                                      "textColor": colors.HexColor(NAVY)}),
        "h2": ParagraphStyle("h2", **{**base, "fontName": "Helvetica-Bold", "fontSize": 10.5, "leading": 14,
                                      "textColor": colors.HexColor(NAVY), "spaceBefore": 6, "spaceAfter": 3}),
        "kicker": ParagraphStyle("kicker", **{**base, "fontName": "Helvetica-Bold", "fontSize": 7.5,
                                              "textColor": colors.HexColor(TEAL), "leading": 10}),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 8, "leading": 10}),
        "cell_r": ParagraphStyle("cell_r", **{**base, "fontSize": 8, "leading": 10, "alignment": TA_RIGHT}),
        "cell_c": ParagraphStyle("cell_c", **{**base, "fontSize": 8, "leading": 10, "alignment": TA_CENTER}),
        "head": ParagraphStyle("head", **{**base, "fontName": "Helvetica-Bold", "fontSize": 7.5, "leading": 9.5,
                                          "textColor": colors.white}),
        "head_r": ParagraphStyle("head_r", **{**base, "fontName": "Helvetica-Bold", "fontSize": 7.5,
                                              "leading": 9.5, "textColor": colors.white, "alignment": TA_RIGHT}),
        "kpi_value": ParagraphStyle("kpi_value", **{**base, "fontName": "Helvetica-Bold", "fontSize": 17,
                                                    "leading": 20, "textColor": colors.HexColor(NAVY),
                                                    "alignment": TA_LEFT}),
        "kpi_label": ParagraphStyle("kpi_label", **{**base, "fontSize": 7.2, "leading": 9,
                                                    "textColor": colors.HexColor(MUTED)}),
    }


STYLES = make_styles()


class SectionTitle(Flowable):
    """Titulo de secao: kicker em teal, titulo em navy e filete dourado."""

    def __init__(self, kicker: str, title: str, width: float = CONTENT_W):
        super().__init__()
        self.kicker, self.title, self.width, self.height = kicker, title, width, 1.55 * cm

    def wrap(self, *_: Any) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        canvas = self.canv
        canvas.setFillColor(colors.HexColor(TEAL))
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawString(0, self.height - 9, self.kicker.upper())
        canvas.setFillColor(colors.HexColor(NAVY))
        canvas.setFont("Helvetica-Bold", 17)
        canvas.drawString(0, self.height - 28, self.title)
        canvas.setStrokeColor(colors.HexColor(GOLD))
        canvas.setLineWidth(1.6)
        canvas.line(0, 1, 1.6 * cm, 1)


def kpi_cards(items: list[tuple[str, str, str | None]], width: float = CONTENT_W) -> Table:
    """items: (rotulo, valor, cor_do_valor)."""
    cells = []
    for label, value, color in items:
        style = ParagraphStyle("kv", parent=STYLES["kpi_value"], textColor=colors.HexColor(color or NAVY))
        cells.append([Paragraph(value, style), Paragraph(label, STYLES["kpi_label"])])
    column = width / len(items)
    table = Table([[c[0] for c in cells], [c[1] for c in cells]], colWidths=[column] * len(items))
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(PAPER)),
        ("LINEBEFORE", (1, 0), (-1, -1), 1.5, colors.white),
        ("LINEABOVE", (0, 0), (-1, 0), 2, colors.HexColor(GOLD)),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def data_table(header: list[str], rows: list[list[str]], widths: list[float],
               numeric_from: int = 3, zebra: bool = True) -> Table:
    head_cells = [Paragraph(h, STYLES["head_r"] if i >= numeric_from else STYLES["head"])
                  for i, h in enumerate(header)]
    body = [[Paragraph(str(v), STYLES["cell_r"] if i >= numeric_from else STYLES["cell"])
             for i, v in enumerate(row)] for row in rows]
    table = Table([head_cells] + body, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor(LINE)),
    ]
    if zebra:
        for i in range(2, len(body) + 1, 2):
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FAFBFD")))
    table.setStyle(TableStyle(style))
    return table


def colored(text: str, color: str, bold: bool = True) -> str:
    inner = f"<b>{text}</b>" if bold else text
    return f'<font color="{color}">{inner}</font>'


# ----------------------------------------------------------------------------
# Tabelas de conteudo
# ----------------------------------------------------------------------------
def model_table(data: MonthData) -> pd.DataFrame:
    """Ativos da carteira (modelo) enriquecidos com plano de ordens executavel."""
    stocks = data.carteira[~data.carteira.apply(is_cdi, axis=1)].copy()
    stocks["peso_modelo_total"] = pd.to_numeric(stocks["peso_recomendado"], errors="coerce").fillna(0)
    ex = data.executavel[~data.executavel.apply(is_cdi, axis=1)][["ticker", "valor_alvo", "preco_referencia",
                                                                    "quantidade", "peso_executavel"]]
    stocks = stocks.merge(ex, on="ticker", how="left")
    if not data.tecnicos.empty:
        tech_cols = ["ticker", "rsi", "rsi_status", "bollinger_status", "bollinger_position", "tendencia",
                     "mm9", "mm21", "mm50", "mm100", "retorno_ytd", "alertas_tecnicos"]
        tech = data.tecnicos[[c for c in tech_cols if c in data.tecnicos.columns]].copy()
        tech = tech.rename(columns={c: f"{c}_tec" for c in tech.columns if c != "ticker"})
        stocks = stocks.merge(tech.drop_duplicates("ticker"), on="ticker", how="left")
    if not data.ativos_parcial.empty:
        returns = data.ativos_parcial[["ticker", "retorno_periodo", "preco_entrada", "preco_atual"]].copy()
        returns["ticker"] = returns["ticker"].astype(str).str.upper()
        stocks = stocks.merge(returns, on="ticker", how="left")
    stocks["peso_na_parte_acoes"] = stocks["peso_modelo_total"] / stocks["peso_modelo_total"].sum()
    return stocks.sort_values(["nota_final", "peso_modelo_total"], ascending=[False, False]).reset_index(drop=True)


def reading_text(row: pd.Series) -> str:
    """Leitura curta e factual do papel, montada a partir dos indicadores do robo."""
    rsi_value = fnum(row.get("rsi_tec", row.get("rsi")))
    parts = [f"RSI semanal em {fmt_num(rsi_value, 1)} ({pt_fix(row.get('rsi_status_tec', row.get('rsi_status', '-')))})",
             f"Bollinger {pt_fix(row.get('bollinger_status_tec', row.get('bollinger_status', '-')))}",
             f"tendência {pt_fix(row.get('tendencia_tec', '-')).lower()}"]
    timing = pt_fix(row.get("qualidade_do_timing", ""))
    if timing and timing != "-":
        parts.append(timing.lower())
    text = "; ".join(parts) + "."
    alerts = row.get("alertas_tecnicos_tec")
    if isinstance(alerts, str) and alerts.strip():
        text += f" Alerta técnico: {alerts.strip().rstrip('.')}."
    return text


def stock_card(row: pd.Series, total_ret: float | None) -> Table:
    value_style = ParagraphStyle("cv", parent=STYLES["cell_r"], fontSize=7.4, leading=9)
    label_style = ParagraphStyle("cl", parent=STYLES["small"], fontSize=7, leading=9)

    def line(label: str, value: str) -> list[Paragraph]:
        return [Paragraph(label, label_style), Paragraph(value, value_style)]

    weight_total = fnum(row["peso_modelo_total"])
    executable = not pd.isna(row.get("quantidade"))
    exec_text = (f"{fmt_brl(row['valor_alvo'])} · {int(row['quantidade'])} un."
                 if executable else "abaixo de 1% (não exec.)")
    rows = [
        line("Peso na carteira", colored(fmt_pct(weight_total, 2), NAVY)),
        line("Peso em ações", fmt_pct(row["peso_na_parte_acoes"], 1)),
        line("Alvo (R$ 10 mil)", exec_text),
        line("Nota final do robô", colored(f"{fnum(row['nota_final']):.0f} / 100", TEAL)),
        line("Tendência · Timing", f"{fnum(row['score_tendencia']):.0f} · {fnum(row['score_timing']):.0f} pts"),
        line("Fund. · Setor · Risco",
             f"{fnum(row['score_fundamentos']):.0f} · {fnum(row['score_setor']):.0f} · {fnum(row['score_risco']):.0f} pts"),
        line("RSI (14 sem.)", f"{fmt_num(row.get('rsi_tec'), 1)} · {pt_fix(row.get('rsi_status_tec', '-'))}"),
        line("Bollinger (20; 2σ)",
             f"{pt_fix(row.get('bollinger_status_tec', '-'))} · %B {fmt_num(fnum(row.get('bollinger_position_tec')) * 100, 0)}%"),
        line("Tendência (MMs)", pt_fix(row.get("tendencia_tec", "-"))),
        line("Beta · ROE · P/L",
             f"{fmt_num(row.get('beta'), 2)} · {fmt_pct(row.get('roe'), 1)} · {fmt_num(row.get('pl_atual'), 1)}"),
        line("Perfil de risco", pt_fix(row.get("perfil_risco_empresa", "-"))),
    ]
    if total_ret is not None and not np.isnan(total_ret):
        rows.append(line("Retorno no mês", colored(fmt_pct(total_ret, 2, True), sign_color(total_ret))))
    table = Table(rows, colWidths=[3.0 * cm, CONTENT_W - 10.9 * cm - 3.0 * cm - 0.15 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(PAPER)),
        ("LINEABOVE", (0, 0), (-1, 0), 2, colors.HexColor(TEAL)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.white),
    ]))
    return table


def stock_header(row: pd.Series, rank: int) -> Table:
    name = f'<font size="13" color="{NAVY}"><b>{short_ticker(row["ticker"])}</b></font>' \
           f'<font size="9" color="{MUTED}">&nbsp;&nbsp;{title_name(row["nome"])} · {row["setor"]}</font>'
    pill = f'<font color="white" size="8"><b>#{rank} · {fmt_pct(row["peso_modelo_total"], 2)} da carteira</b></font>'
    table = Table([[Paragraph(name, STYLES["cell"]),
                    Paragraph(pill, ParagraphStyle("pill", parent=STYLES["cell_r"], textColor=colors.white))]],
                  colWidths=[CONTENT_W - 5.0 * cm, 5.0 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor(NAVY)),
        ("LINEBELOW", (0, 0), (0, 0), 0.8, colors.HexColor(GOLD)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def heatmap_table(history: pd.DataFrame) -> Table:
    years = sorted({int(m[:4]) for m in history["mes"]})
    by_month = {r["mes"]: r for _, r in history.iterrows()}
    header = ["ANO"] + MONTHS_ABBR + ["ANO"]
    rows: list[list[Any]] = [[Paragraph(f"<b>{h}</b>", ParagraphStyle(
        "hh", parent=STYLES["cell_c"], textColor=colors.white, fontSize=7)) for h in header]]
    style: list[tuple] = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
                          ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                          ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
                          ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]

    def tint(value: float) -> colors.Color:
        strength = min(abs(value) / 0.06, 1.0)
        base = colors.HexColor(GREEN if value >= 0 else RED)
        return colors.Color(1 - (1 - base.red) * strength * 0.85, 1 - (1 - base.green) * strength * 0.85,
                            1 - (1 - base.blue) * strength * 0.85)

    small = ParagraphStyle("hm", parent=STYLES["cell_c"], fontSize=7.2, leading=8.6)
    tiny = ParagraphStyle("hmi", parent=STYLES["cell_c"], fontSize=6.2, leading=7.4,
                          textColor=colors.HexColor(MUTED))
    for year in years:
        model_row, ibov_row = [Paragraph(f"<b>{year}</b>", small)], [""]
        model_vals, ibov_vals = [], []
        for month in range(1, 13):
            key = f"{year}-{month:02d}"
            record = by_month.get(key)
            col = month
            if record is None or pd.isna(record["modelo"]):
                model_row.append(Paragraph("·", tiny))
                ibov_row.append("")
                continue
            model_vals.append(record["modelo"])
            model_row.append(Paragraph(f"<b>{fmt_pct(record['modelo'], 1, True)[:-1]}</b>", small))
            ibov_row.append(Paragraph(fmt_pct(record["ibov"], 1, True)[:-1] if not pd.isna(record["ibov"]) else "-", tiny))
            ibov_vals.append(record["ibov"])
            style.append(("BACKGROUND", (col, len(rows)), (col, len(rows)), tint(record["modelo"])))
        year_model = float(np.prod([1 + v for v in model_vals]) - 1) if model_vals else np.nan
        year_ibov = float(np.prod([1 + v for v in ibov_vals if not pd.isna(v)]) - 1) if ibov_vals else np.nan
        model_row.append(Paragraph(f"<b>{fmt_pct(year_model, 1, True)}</b>", small))
        ibov_row.append(Paragraph(fmt_pct(year_ibov, 1, True), tiny))
        base_row = len(rows)
        style += [("SPAN", (0, base_row), (0, base_row + 1)),
                  ("BACKGROUND", (0, base_row), (0, base_row + 1), colors.HexColor(PAPER)),
                  ("BACKGROUND", (13, base_row), (13, base_row), colors.HexColor(PAPER)),
                  ("BACKGROUND", (13, base_row + 1), (13, base_row + 1), colors.HexColor(PAPER)),
                  ("LINEBELOW", (0, base_row + 1), (-1, base_row + 1), 0.5, colors.HexColor(LINE))]
        rows.extend([model_row, ibov_row])
    widths = [1.15 * cm] + [(CONTENT_W - 1.15 * cm - 1.55 * cm) / 12] * 12 + [1.55 * cm]
    table = Table(rows, colWidths=widths)
    table.setStyle(TableStyle(style))
    return table


# ----------------------------------------------------------------------------
# Paginas
# ----------------------------------------------------------------------------
def on_cover(canvas: Any, doc: Any, month: str, status_text: str) -> None:
    canvas.saveState()
    canvas.setFillColor(colors.HexColor(NAVY))
    canvas.rect(0, PAGE_H - 12.4 * cm, PAGE_W, 12.4 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor(NAVY_2))
    canvas.circle(PAGE_W - 2.2 * cm, PAGE_H - 3.0 * cm, 5.2 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor("#1B4272"))
    canvas.circle(PAGE_W - 0.6 * cm, PAGE_H - 9.8 * cm, 2.6 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor(GOLD))
    canvas.rect(MARGIN, PAGE_H - 4.45 * cm, 1.8 * cm, 0.09 * cm, stroke=0, fill=1)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(colors.HexColor(GOLD))
    canvas.drawString(MARGIN, PAGE_H - 3.7 * cm, "CARTEIRA MENSAL  ·  RELATÓRIO")
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 38)
    canvas.drawString(MARGIN, PAGE_H - 6.9 * cm, "Carteira")
    canvas.drawString(MARGIN, PAGE_H - 8.5 * cm, "Recomendada")
    canvas.setFont("Helvetica", 22)
    canvas.setFillColor(colors.HexColor("#DCE6F5"))
    canvas.drawString(MARGIN, PAGE_H - 10.1 * cm, month_label(month))
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(colors.HexColor("#9FB3D1"))
    canvas.drawString(MARGIN, PAGE_H - 11.35 * cm, status_text)
    canvas.restoreState()
    footer(canvas, doc, month, cover=True)


def footer(canvas: Any, doc: Any, month: str, cover: bool = False) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor(LINE))
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 1.45 * cm, PAGE_W - MARGIN, 1.45 * cm)
    canvas.setFont("Helvetica", 6.8)
    canvas.setFillColor(colors.HexColor(MUTED))
    canvas.drawString(MARGIN, 0.95 * cm,
                      "Simulação de carteira-modelo, com fins informativos. Não constitui recomendação de investimento.")
    if not cover:
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor(colors.HexColor(NAVY))
        canvas.drawRightString(PAGE_W - MARGIN, 0.95 * cm, f"{month_label(month)}  ·  {doc.page}")
    canvas.restoreState()


def on_page(canvas: Any, doc: Any, month: str) -> None:
    canvas.saveState()
    canvas.setFillColor(colors.HexColor(NAVY))
    canvas.rect(0, PAGE_H - 0.55 * cm, PAGE_W, 0.55 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor(GOLD))
    canvas.rect(0, PAGE_H - 0.62 * cm, 3.2 * cm, 0.07 * cm, stroke=0, fill=1)
    canvas.restoreState()
    footer(canvas, doc, month)


def paragraph(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, STYLES[style])


def build_report(month: str, output: Path | None = None, allow_network: bool = True) -> Path:
    data = load_month_data(month)
    perf = month_performance(data)
    history = load_history(month)
    stats = history_stats(history)
    table = model_table(data)

    stock_weight = float(table["peso_modelo_total"].sum())
    cdi_weight = 1.0 - stock_weight
    ret_by_ticker = ({r["ticker"]: fnum(r["retorno_periodo"]) for _, r in table.iterrows()}
                     if "retorno_periodo" in table else {})

    if data.fechado:
        status_text = f"Fechamento do mês · avaliação em {data.end:%d/%m/%Y}"
    elif data.partial:
        status_text = f"Parcial do mês · avaliação até {data.end:%d/%m/%Y}"
    else:
        status_text = f"Carteira do mês · parcial oficial ainda não disponível · preços até {data.end:%d/%m/%Y}"
    regime_name = pt_fix(data.forward.get("regime_mercado", "-")).capitalize()
    subtype = ""
    for key in ("subtipo_queda", "subtipo_mercado_favoravel"):
        candidate = pt_fix(data.forward.get(key, ""))
        if candidate not in {"-", "", "nao aplicavel"}:
            subtype = candidate
            break
    signal = pt_fix(data.forward.get("sinal_usado", "-")).title()

    story: list[Any] = []

    # ---- Capa ---------------------------------------------------------------
    story.append(Spacer(1, 12.9 * cm))
    story.append(kpi_cards([
        ("Exposição em ações", fmt_pct(stock_weight, 0), TEAL),
        ("CDI / Tesouro Selic líquido", fmt_pct(cdi_weight, 0), GOLD),
        ("Ações na carteira", str(len(table)), NAVY),
        ("Regime de mercado", f'<font size="12">{regime_name}</font>', NAVY),
    ]))
    story.append(Spacer(1, 0.7 * cm))
    if not np.isnan(perf["retorno"]):
        story.append(paragraph("RESULTADO DO MÊS" if data.fechado else "RESULTADO PARCIAL DO MÊS", "kicker"))
        story.append(Spacer(1, 0.15 * cm))
        story.append(kpi_cards([
            ("Carteira (execução R$ 10 mil)", fmt_pct(perf["retorno"], 2, True), sign_color(perf["retorno"])),
            ("IBOV", fmt_pct(perf["ibov"], 2, True), sign_color(perf["ibov"])),
            ("CDI líquido de IR", fmt_pct(perf["cdi"], 2, True), sign_color(perf["cdi"])),
            ("Diferença vs IBOV", fmt_pp(perf["alfa"]), sign_color(perf["alfa"])),
        ]))
        story.append(Spacer(1, 0.7 * cm))
    else:
        story.append(paragraph("O resultado do mês entra neste relatório quando a parcial oficial estiver disponível. "
                               "As cotações após a data de entrada, nas fichas, são apenas informativas.", "small"))
        story.append(Spacer(1, 0.5 * cm))
    story.append(paragraph("Sobre esta edição", "h2"))
    cdi_clause = (f" e <b>{fmt_pct(cdi_weight, 0)}</b> em CDI/Tesouro Selic líquido de IR"
                  if cdi_weight >= 0.005 else "")
    posture = (f"O robô classificou o mercado como <b>{regime_name.lower()}</b>"
               f"{f' ({subtype})' if subtype else ''} e adotou o sinal <b>{signal}</b>: "
               f"<b>{fmt_pct(stock_weight, 0)}</b> da carteira em ações selecionadas{cdi_clause} "
               f"(beta realizado {fmt_num(data.forward.get('beta_realizado_carteira'), 2)} "
               f"para um alvo de {fmt_num(data.forward.get('beta_alvo'), 2)}).")
    outcome = ""
    if not np.isnan(perf["retorno"]):
        verb = "rendeu" if data.fechado else "rendeu até agora"
        outcome = (f"No mês, a carteira operacional {verb} <b>{fmt_pct(perf['retorno'], 2, True)}</b> contra "
                   f"<b>{fmt_pct(perf['ibov'], 2, True)}</b> do IBOV (diferença de <b>{fmt_pp(perf['alfa'])}</b>) e "
                   f"<b>{fmt_pct(perf['cdi'], 2, True)}</b> do CDI líquido. ")
        if cdi_weight >= 0.3:
            outcome += (f"Com {fmt_pct(cdi_weight, 0)} em CDI, a carteira amortece quedas do mercado, mas "
                        "também captura só parte das altas. ")
        outcome += "Este relatório apresenta o resultado como ele ocorreu."
    story.append(paragraph(posture))
    if outcome:
        story.append(Spacer(1, 0.2 * cm))
        story.append(paragraph(outcome))
    story.append(Spacer(1, 0.45 * cm))
    story.append(paragraph(
        "<b>Conteúdo:</b> composição e plano de ordens · racional do regime e da seleção · desempenho do mês · "
        "histórico do modelo · uma ficha por ação com os últimos 12 meses e os indicadores usados pelo robô "
        "(médias móveis semanais, Bandas de Bollinger e IFR/RSI) · metodologia.", "small"))
    story.append(PageBreak())

    # ---- Composicao ---------------------------------------------------------
    story.append(SectionTitle("Composição", "Carteira recomendada"))
    story.append(Spacer(1, 0.25 * cm))
    visual = Table([[chart_allocation(stock_weight, cdi_weight), chart_weights(table, cdi_weight)]],
                   colWidths=[6.6 * cm, CONTENT_W - 6.6 * cm])
    visual.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(visual)
    story.append(Spacer(1, 0.3 * cm))
    story.append(paragraph("Plano de ordens (referência de R$ 10.000)", "h2"))
    rows = []
    for _, r in table.iterrows():
        executable = not pd.isna(r.get("quantidade"))
        rows.append([
            f"<b>{short_ticker(r['ticker'])}</b>",
            f"{title_name(r['nome'])}<br/><font size='6.5' color='{MUTED}'>{r['setor']}</font>",
            fmt_pct(r["peso_modelo_total"], 2),
            fmt_pct(r["peso_na_parte_acoes"], 1),
            fmt_brl(r["preco_referencia"]) if executable else "-",
            str(int(r["quantidade"])) if executable else "-",
            fmt_brl(r["valor_alvo"]) if executable else "abaixo de 1%",
        ])
    cdi_value = float(data.executavel.loc[data.executavel.apply(is_cdi, axis=1), "valor_estimado"].sum())
    rows.append(["<b>CDI</b>", f"CDI / Tesouro Selic líquido<br/><font size='6.5' color='{MUTED}'>Renda fixa</font>",
                 fmt_pct(cdi_weight, 2), "-", "-", "-", fmt_brl(cdi_value)])
    story.append(data_table(
        ["Ativo", "Empresa / setor", "Peso total", "Peso em ações", "Preço ref.", "Qtd.", "Valor alvo"],
        rows, [1.7 * cm, 5.6 * cm, 1.9 * cm, 2.1 * cm, 2.0 * cm, 1.4 * cm, 3.1 * cm], numeric_from=2))
    story.append(Spacer(1, 0.18 * cm))
    story.append(paragraph(
        f"Preço de referência: fechamento ajustado de {data.cutoff:%d/%m/%Y}. A execução considera o máximo de "
        f"{MAX_STOCKS} ações, peso mínimo de {fmt_pct(MIN_WEIGHT, 0)} e quantidades inteiras; "
        "a sobra e as posições abaixo do mínimo ficam no CDI.", "tiny"))
    story.append(PageBreak())

    # ---- Racional -----------------------------------------------------------
    story.append(SectionTitle("Racional", "Regime de mercado e seleção"))
    story.append(Spacer(1, 0.2 * cm))
    regime_rows = [
        ["Classificação do mercado", regime_name],
        ["Subtipo do regime", subtype or "-"],
        ["Sinal adotado", signal],
        ["RSI do IBOV na data base", fmt_num(data.forward.get("rsi_ibov_data_base"), 1)],
        ["Bollinger do IBOV", pt_fix(data.forward.get("bollinger_ibov_data_base", "-"))],
        ["Ativos do universo com retorno 1m positivo", fmt_pct(data.forward.get("pct_ativos_positivos_1m"), 1)],
        ["Exposição investida em ações", fmt_pct(data.forward.get("exposicao_investida"), 0)],
        ["Beta alvo · beta realizado", f"{fmt_num(data.forward.get('beta_alvo'), 2)} · "
                                       f"{fmt_num(data.forward.get('beta_realizado_carteira'), 2)}"],
    ]
    story.append(data_table(["Indicador do regime", "Leitura do robô"], regime_rows,
                            [9.2 * cm, CONTENT_W - 9.2 * cm], numeric_from=1))
    why = str(data.forward.get("motivo_exposicao", "")).strip()
    if why and why != "nan":
        story.append(Spacer(1, 0.15 * cm))
        story.append(paragraph(f"<b>Por que esta exposição:</b> {pt_fix(why)}.", "small"))
    story.append(Spacer(1, 0.3 * cm))
    story.append(paragraph("Composição da nota de cada ação", "h2"))
    story.append(paragraph(
        "A nota final (0 a 100) soma os pontos de tendência, timing de entrada, fundamentos, setor e risco. "
        "Ela define o ranking dentro do universo do IBOV e o peso inicial no otimizador.", "small"))
    story.append(Spacer(1, 0.1 * cm))
    story.append(chart_scores(table))
    zeroed = [label for column, label in (("score_tendencia", "tendência"), ("score_fundamentos", "fundamentos"))
              if pd.to_numeric(table[column], errors="coerce").fillna(0).eq(0).all()]
    if zeroed:
        story.append(paragraph(
            f"Nesta edição, nenhuma ação pontuou em {' nem em '.join(zeroed)}; a nota veio de timing, setor e risco.",
            "tiny"))
    story.append(Spacer(1, 0.25 * cm))
    story.append(paragraph("Exposição por setor (% da carteira total)", "h2"))
    story.append(chart_sectors(table))
    story.append(PageBreak())

    # ---- Desempenho ---------------------------------------------------------
    if not np.isnan(perf["retorno"]):
        story.append(SectionTitle("Desempenho", f"{MONTHS_PT_ACC[data.end.month - 1]} em números"))
        story.append(Spacer(1, 0.2 * cm))
        pair = Table([[chart_month_compare(perf["retorno"], perf["ibov"], perf["cdi"], month),
                       chart_contributions(perf["detalhe"])]],
                     colWidths=[CONTENT_W * 0.42, CONTENT_W * 0.58])
        pair.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story.append(pair)
        story.append(Spacer(1, 0.25 * cm))
        story.append(paragraph("Retorno por ativo", "h2"))
        detail = perf["detalhe"]
        rows = []
        for _, r in detail.iterrows():
            if is_cdi(r):
                rows.append(["<b>CDI</b>", "CDI líquido de IR", fmt_pct(r["peso_executavel"], 2), "-", "-",
                             colored(fmt_pct(r["retorno_pratico"], 2, True), sign_color(r["retorno_pratico"])),
                             colored(fmt_pp(r["contribuicao_pratica"]), sign_color(r["contribuicao_pratica"]))])
            else:
                rows.append([f"<b>{short_ticker(r['ticker'])}</b>", title_name(r["nome"]),
                             fmt_pct(r["peso_executavel"], 2), fmt_brl(r.get("preco_entrada")),
                             fmt_brl(r.get("preco_atual")),
                             colored(fmt_pct(r["retorno_pratico"], 2, True), sign_color(r["retorno_pratico"])),
                             colored(fmt_pp(r["contribuicao_pratica"]), sign_color(r["contribuicao_pratica"]))])
        rows.append(["<b>Total</b>", "Carteira (execução R$ 10 mil)", "100,00%", "", "",
                     colored(fmt_pct(perf["retorno"], 2, True), sign_color(perf["retorno"])),
                     colored(fmt_pp(perf["retorno"]), sign_color(perf["retorno"]))])
        story.append(data_table(
            ["Ativo", "Empresa", "Peso exec.", "Entrada", "Fim do mês", "Retorno", "Contribuição"],
            rows, [1.7 * cm, 4.6 * cm, 1.9 * cm, 2.2 * cm, 2.2 * cm, 2.1 * cm, 2.6 * cm], numeric_from=2))
        story.append(Spacer(1, 0.15 * cm))
        story.append(paragraph(
            f"Entrada em {pd.Timestamp(data.partial.get('data_entrada')):%d/%m/%Y}, avaliação em {data.end:%d/%m/%Y}. "
            "Retornos em preços ajustados por proventos (yfinance); CDI pela série 12 do Banco Central, líquido de "
            "IR de 22,5%. O retorno da carteira usa o plano de ordens executável (quantidades inteiras, "
            f"mínimo de 1% por ação); sem essa regra, a carteira do modelo rendeu "
            f"{fmt_pct(perf['retorno_modelo_100'], 2, True)}.", "tiny"))
        story.append(PageBreak())

    # ---- Historico ----------------------------------------------------------
    story.append(SectionTitle("Histórico", "Desempenho do modelo mês a mês"))
    story.append(Spacer(1, 0.2 * cm))
    story.append(kpi_cards([
        ("Carteira acumulado", fmt_pct(stats["acum_modelo"], 1, True), sign_color(stats["acum_modelo"])),
        ("IBOV acumulado", fmt_pct(stats["acum_ibov"], 1, True), sign_color(stats["acum_ibov"])),
        ("CDI líquido acumulado", fmt_pct(stats["acum_cdi"], 1, True), sign_color(stats["acum_cdi"])),
        ("Pior queda (mensal composta)", fmt_pct(stats["drawdown"], 1), RED),
    ]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(chart_cumulative(history))
    story.append(Spacer(1, 0.2 * cm))
    story.append(paragraph("Retorno mensal do modelo (linha de cima) e do IBOV (linha de baixo), em %", "h2"))
    story.append(heatmap_table(history))
    story.append(Spacer(1, 0.2 * cm))
    best_month, best = stats["melhor"]
    worst_month, worst = stats["pior"]
    story.append(paragraph(
        f"{stats['meses']} meses · {stats['meses_positivos']} com retorno positivo · carteira acima do IBOV em "
        f"{stats['bateu_ibov']} de {stats['meses_comparaveis']} meses · melhor mês {fmt_pct(best, 1, True)} "
        f"({best_month}) · pior mês {fmt_pct(worst, 1, True)} ({worst_month}). "
        "Resultados de backtest e de forward-test; não incluem custos de corretagem e impostos sobre ações.",
        "tiny"))
    story.append(PageBreak())

    # ---- Fichas por acao ----------------------------------------------------
    rank = 0
    pending: list[Any] = []
    first_stock_page = True
    for _, row in table.iterrows():
        rank += 1
        series = stock_series(row["ticker"], data.cutoff, data.end, allow_network)
        row = row.copy()
        for key, value in tech_snapshot(series).items():
            if key == "alertas_tecnicos" and isinstance(row.get("alertas_tecnicos_tec"), str):
                continue  # mantem o texto oficial da planilha quando existir
            row[f"{key}_tec"] = value
        chart = chart_stock(row["ticker"], series, data.cutoff, data.end)
        block = KeepTogether([
            stock_header(row, rank),
            Spacer(1, 0.18 * cm),
            _side_by_side(chart, stock_card(row, ret_by_ticker.get(row["ticker"]))),
            Spacer(1, 0.1 * cm),
            paragraph(f"<b>Leitura do robô:</b> {reading_text(row)}", "small"),
            Spacer(1, 0.55 * cm),
        ])
        pending.append(block)
        if len(pending) == 2 or rank == len(table):
            if first_stock_page:
                story.append(SectionTitle("Ações", "Fichas dos últimos 12 meses"))
                story.append(paragraph(
                    "Preço semanal (linha azul), médias móveis semanais de 9, 21, 50 e 100 semanas, Bandas de "
                    "Bollinger (20 semanas, 2 desvios) e IFR/RSI de 14 semanas, os mesmos indicadores e parâmetros "
                    "usados pelo robô. A faixa dourada mostra o mês da carteira, com o preço diário após a data de "
                    "entrada.", "small"))
                story.append(Spacer(1, 0.3 * cm))
                first_stock_page = False
            story.extend(pending)
            pending = []
            if rank != len(table):
                story.append(PageBreak())
    story.append(PageBreak())

    # ---- Metodologia --------------------------------------------------------
    story.append(SectionTitle("Metodologia", "Como a carteira é montada"))
    story.append(Spacer(1, 0.2 * cm))
    for title, text in METHOD_SECTIONS:
        story.append(paragraph(title, "h2"))
        story.append(paragraph(text))
    story.append(Spacer(1, 0.4 * cm))
    story.append(paragraph(
        f"<b>Dados e fontes:</b> preços ajustados via yfinance; fundamentos via Fundamentus; CDI via Banco Central "
        f"(SGS 12); IBOV oficial (^BVSP). {methodology_note(month)}"
        f"Relatório gerado em {datetime.now():%d/%m/%Y %H:%M}.", "small"))
    story.append(Spacer(1, 0.3 * cm))
    story.append(paragraph(
        "<b>Aviso importante:</b> este material tem caráter exclusivamente informativo e educacional, descreve uma "
        "carteira-modelo simulada e não constitui oferta, recomendação personalizada ou consultoria de "
        "investimento. Investimentos em renda variável envolvem risco de perda de capital. Rentabilidade passada "
        "não é garantia de resultados futuros.", "small"))

    suffix = "" if data.fechado else "_parcial"
    output = output or (PDF_DIR / f"carteira_recomendada_{month_key(month)}{suffix}.pdf")
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(str(output), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=1.5 * cm, bottomMargin=1.9 * cm,
                          title=f"Carteira Recomendada - {month_label(month)}",
                          author="Carteira Mensal", subject="Relatório mensal da carteira")
    frame = Frame(MARGIN, 1.9 * cm, CONTENT_W, PAGE_H - 1.5 * cm - 1.9 * cm, id="main", leftPadding=0,
                  rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame], onPage=lambda c, d: on_cover(c, d, month, status_text),
                     autoNextPageTemplate="inner"),
        PageTemplate(id="inner", frames=[frame], onPage=lambda c, d: on_page(c, d, month)),
    ])
    doc.build(story)
    if data.fechado:  # o relatorio final substitui o parcial do mesmo mes
        (PDF_DIR / f"carteira_recomendada_{month_key(month)}_parcial.pdf").unlink(missing_ok=True)
    return output


def methodology_note(month: str) -> str:
    manifest = ROOT / "output" / "production" / f"{month}.json"
    if not manifest.exists():
        return ""
    import json

    version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    return f"Versão da metodologia: {version}. " if version else ""


def _side_by_side(chart: Image, card: Table) -> Table:
    table = Table([[chart, card]], colWidths=[10.9 * cm, CONTENT_W - 10.9 * cm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (0, 0), 4), ("TOPPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return table


METHOD_SECTIONS = [
    ("1. Universo e dados",
     "Todo mês, o robô parte das ações do Ibovespa e coleta preços ajustados, fundamentos e a série do CDI. "
     "A seleção usa apenas informações disponíveis até o último pregão do mês anterior, sem olhar para frente."),
    ("2. Indicadores técnicos semanais",
     "Cada ação é analisada em base semanal (fechamento de sexta-feira): médias móveis de 9, 21, 50 e 100 semanas "
     "definem a tendência; as Bandas de Bollinger (20 semanas, 2 desvios) indicam a posição do preço dentro do "
     "canal; e o IFR/RSI de 14 semanas mede o momentum. Zona favorável de entrada: RSI entre 50 e 65; "
     "acima de 70, sobrecompra; abaixo de 30, sobrevenda."),
    ("3. Nota de 0 a 100",
     "A nota combina tendência, timing de entrada, fundamentos (ROE, ROIC, margens, P/L), setor e risco "
     "(volatilidade, correlação com o IBOV e beta). Filtros eliminam papéis com sinais de deterioração, "
     "força relativa fraca ou fundamentos bloqueantes."),
    ("4. Regime de mercado e exposição",
     "O robô lê o regime do IBOV (RSI, Bollinger, amplitude do mercado e tendência dos ativos) e decide quanto "
     "investir em ações. Em regimes de queda, a exposição cai (por exemplo, 30% em ações) e o restante vai para CDI/"
     "Tesouro Selic líquido de IR; em regimes favoráveis, a exposição sobe."),
    ("5. Otimização e execução",
     "Um otimizador distribui pesos buscando o beta-alvo do regime, com tetos por ativo e por setor. A execução de "
     "referência usa até 15 ações, peso mínimo de 1%, quantidades inteiras e capital de R$ 10.000; "
     "a sobra permanece no CDI líquido."),
]
