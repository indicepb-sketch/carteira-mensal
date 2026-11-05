"""Teste 56 (sombra): exposicao continua + confirmacao pela tendencia do IBOV.

Pergunta: trocar os saltos de regime (100% / 60% / 30% em acoes) por uma exposicao
mais suave, e/ou exigir que o proprio IBOV confirme a tendencia de queda, teria
dado resultado melhor do que o modelo operacional (T49) que realmente rodou?

Regras do experimento
- Somente leitura sobre os dados de producao. Escreve apenas
  output/excel/shadow_teste56_exposicao_continua_confirmacao_ibov.xlsx e o cache
  data/shadow_cache/ibov_bvsp.csv. Nao toca em output/production, em novembro/2026
  nem em nenhuma carteira forward; aborta se algum arquivo de producao mudar.
- Sem olhar o futuro: a decisao de cada mes usa apenas (a) as entradas de formacao
  gravadas na base mensal (dados ate a data de selecao) e (b) precos do IBOV ate a
  data de selecao. Os retornos do mes so entram na contabilidade, nunca na decisao.
- Selecao de hipoteses somente walk-forward / fora da amostra.

Aproximacao: a selecao de acoes de cada mes e mantida; so muda a exposicao
(peso em acoes escalado pela razao entre exposicao nova e exposicao efetiva do T49;
o restante vai para CDI liquido do mes).
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from technical_indicators import moving_average, rsi, weekly_close  # noqa: E402

EXCEL_DIR = ROOT / "output" / "excel"
CACHE_DIR = ROOT / "data" / "shadow_cache"
OUT = EXCEL_DIR / "shadow_teste56_exposicao_continua_confirmacao_ibov.xlsx"
BASE_T49 = EXCEL_DIR / "shadow_teste49_top15_regime_capital.xlsx"
IBOV_CSV = ROOT / "data" / "processed" / "ibov_mensal_oficial.csv"
CAPITAL = 10000
FIRST_MONTH, LAST_MONTH = "2022-01", "2026-09"   # outubro/2026 sem base de formacao; novembro e producao
TRAIN_END = "2024-12"
WARMUP_MONTHS = 12
BOOTSTRAPS = 5000
PROTECTED = [
    ROOT / "output" / "production" / "active.json",
    ROOT / "output" / "production" / "2026-11.json",
    EXCEL_DIR / "carteira_forward_2026_11.xlsx",
    EXCEL_DIR / "carteira_recomendada_2026_11_v1.xlsx",
]

# Exposicao em acoes que o T49 realmente aplicou por regime (queda_leve cai no padrao de 100%).
EFFECTIVE_EXPOSURE = {"alta": 1.0, "oportunidade": 1.0, "queda_leve": 1.0, "queda_forte": 0.30}


# ----------------------------------------------------------------------------
# Protecao da producao
# ----------------------------------------------------------------------------
def fingerprint() -> dict[str, str]:
    result = {}
    for path in PROTECTED:
        result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "ausente"
    return result


# ----------------------------------------------------------------------------
# Features do IBOV (apenas dados ate a data de selecao)
# ----------------------------------------------------------------------------
def load_ibov_prices(allow_network: bool = True) -> pd.Series:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "ibov_bvsp.csv"
    series = pd.Series(dtype=float)
    if cache.exists():
        series = pd.read_csv(cache, index_col=0, parse_dates=True)["close"].dropna()
    needs = series.empty or series.index.max() < pd.Timestamp("2026-09-25") or series.index.min() > pd.Timestamp("2020-07-15")
    if needs and allow_network:
        import yfinance as yf

        raw = yf.download("^BVSP", start="2020-06-01", end="2026-10-05", auto_adjust=False, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        series = raw["Close"].dropna().astype(float)
        series.index = pd.to_datetime(series.index).tz_localize(None)
        series.rename("close").to_frame().to_csv(cache)
    if series.empty:
        raise RuntimeError("Sem precos do IBOV (cache vazio e rede indisponivel)")
    return series.sort_index()


def selection_cutoff(month: str) -> pd.Timestamp:
    """Ultimo dia do mes anterior: a carteira de `month` so enxerga dados ate aqui."""
    return pd.Timestamp(f"{month}-01") - pd.Timedelta(days=1)


def ibov_features(prices: pd.Series, month: str) -> dict[str, Any]:
    cutoff = selection_cutoff(month)
    weekly = weekly_close(prices[prices.index <= cutoff])
    mm9, mm21, mm50 = (moving_average(weekly, w).iloc[-1] for w in (9, 21, 50))
    close = float(weekly.iloc[-1])
    return {
        "ibov_close": close,
        "ibov_mm9": float(mm9),
        "ibov_mm21": float(mm21),
        "ibov_mm50": float(mm50),
        "ibov_rsi": float(rsi(weekly, 14).iloc[-1]),
        "ibov_ret_4s": float(weekly.iloc[-1] / weekly.iloc[-5] - 1) if len(weekly) >= 5 else np.nan,
        "ibov_alta_estrita": bool(close > mm50 and mm9 > mm21),
        "ibov_alta_leve": bool(close > mm21),
    }


# ----------------------------------------------------------------------------
# Entradas de formacao e retornos
# ----------------------------------------------------------------------------
def formation_inputs() -> pd.DataFrame:
    """Entradas gravadas na base mensal (dados ate a data de selecao), via funcoes do robo."""
    import shadow_consolidado_14_13b as rg14

    months: dict[str, str] = {}
    for period in pd.period_range(FIRST_MONTH, LAST_MONTH, freq="M"):
        key = str(period)
        year, mon = key.split("-")
        historic = EXCEL_DIR / f"carteira_historica_{year}_{mon}.xlsx"
        if historic.exists():
            months[key] = historic.name
            continue
        found = sorted(EXCEL_DIR.glob(f"carteira_recomendada_{year}_{mon}_v*.xlsx"), key=lambda p: p.stat().st_mtime)
        if found:
            months[key] = found[-1].name
    rg14.MONTHS = months
    empty_expost = pd.DataFrame(columns=["mes", "ticker", "retorno_realizado_periodo"])
    rows = []
    for month in months:
        data = rg14.month_audit_inputs(month, empty_expost)
        rows.append({"mes": month, "pct_tendencia_favoravel": data.get("pct_tendencia_favoravel", np.nan),
                     "pct_ativos_positivos_1m": data.get("pct_ativos_positivos_1m", np.nan),
                     "pct_preco_acima_mm50": data.get("pct_preco_acima_mm50", np.nan)})
    return pd.DataFrame(rows)


def load_returns() -> pd.DataFrame:
    """Por mes: regime do T49, peso/retorno da parcela em acoes, CDI liquido e IBOV."""
    mes = pd.read_excel(BASE_T49, sheet_name="Mes a Mes")
    cart = pd.read_excel(BASE_T49, sheet_name="Carteiras")
    mes = mes[(mes["cenario"].eq("TOP15")) & (mes["capital"].eq(CAPITAL))].copy()
    cart = cart[(cart["cenario"].eq("TOP15")) & (cart["capital"].eq(CAPITAL))].copy()
    mes["mes"] = mes["mes"].astype(str).str[:7]
    cart["mes"] = cart["mes"].astype(str).str[:7]
    official = pd.read_csv(IBOV_CSV)
    official["mes"] = official["mes"].astype(str).str[:7]
    official = dict(zip(official["mes"], pd.to_numeric(official["retorno_ibov_oficial"], errors="coerce")))

    rows = []
    for _, row in mes.sort_values("mes").iterrows():
        part = cart[cart["mes"].eq(row["mes"])]
        cdi = part["tipo_linha"].astype(str).str.lower().eq("cdi") | part["ticker"].astype(str).str.upper().eq("CDI")
        stocks = part[~cdi]
        w0 = float(pd.to_numeric(stocks["peso_final"], errors="coerce").sum())
        contrib = float(pd.to_numeric(stocks["contribuicao"], errors="coerce").sum())
        r_cdi = float(pd.to_numeric(part.loc[cdi, "retorno_periodo"], errors="coerce").iloc[0]) if cdi.any() else 0.0
        rows.append({"mes": row["mes"], "regime": str(row["regime_previsto"]), "w0": w0,
                     "r_acoes": contrib / w0 if w0 > 0 else 0.0, "r_cdi": r_cdi,
                     "retorno_base": float(row["retorno"]),
                     "ibov": float(official.get(row["mes"], row["retorno_ibov"]))})

    # julho a setembro/2026: carteiras forward ja fechadas (nao ha novembro aqui)
    from relatorio_mensal import is_cdi, load_month_data, month_performance

    for month in ("2026-07", "2026-08", "2026-09"):
        data = load_month_data(month)
        perf = month_performance(data)
        detail = perf["detalhe"]
        if detail.empty or not data.fechado:
            continue
        cash = detail.apply(is_cdi, axis=1)
        stocks = detail[~cash]
        w0 = float(stocks["peso_executavel"].sum())
        regime = str(data.forward.get("bucket_13b_conservador") or data.forward.get("subtipo_queda") or "queda_forte")
        regime = {"queda_leve_lateral": "queda_leve"}.get(regime, regime)
        rows.append({"mes": month, "regime": regime, "w0": w0,
                     "r_acoes": float(stocks["contribuicao_pratica"].sum()) / w0 if w0 > 0 else 0.0,
                     "r_cdi": float(perf["cdi"]), "retorno_base": float(perf["retorno"]),
                     "ibov": float(official.get(month, perf["ibov"]))})
    frame = pd.DataFrame(rows).drop_duplicates("mes", keep="last").sort_values("mes").reset_index(drop=True)
    frame["recon_base"] = frame["w0"] * frame["r_acoes"] + (1 - frame["w0"]) * frame["r_cdi"]
    frame["erro_reconstrucao"] = frame["recon_base"] - frame["retorno_base"]
    return frame[(frame["mes"] >= FIRST_MONTH) & (frame["mes"] <= LAST_MONTH)]


# ----------------------------------------------------------------------------
# Hipoteses: cada uma decide a exposicao do mes SO com features de formacao
# ----------------------------------------------------------------------------
def linear_exposure(trend: float, low: float, high: float) -> float:
    if pd.isna(trend):
        return 1.0
    return float(0.30 + 0.70 * np.clip((trend - low) / (high - low), 0.0, 1.0))


def effective_exposure(regime: str, w0: float) -> float:
    """Exposicao que o T49 realmente aplicou. Mes rotulado queda_forte mas ~investido
    (ex.: julho/2026, antes da regra de exposicao) conta como 100%."""
    exposure = EFFECTIVE_EXPOSURE.get(regime, 1.0)
    return 1.0 if regime == "queda_forte" and w0 > 0.70 else exposure


def h_base(f: dict[str, Any]) -> float:
    return effective_exposure(f["regime"], f["w0"])


def h_continua(low: float, high: float) -> Callable[[dict[str, Any]], float]:
    return lambda f: linear_exposure(f["pct_tendencia_favoravel"], low, high)


def h_confirma(key: str) -> Callable[[dict[str, Any]], float]:
    def decide(f: dict[str, Any]) -> float:
        exposure = h_base(f)
        return max(exposure, 0.60) if exposure < 1.0 and f[key] else exposure
    return decide


def h_histerese(f: dict[str, Any]) -> float:
    exposure = h_base(f)
    if h_base(f) < 1.0 and f.get("regime_anterior") != "queda_forte" and f["pct_tendencia_favoravel"] >= 0.20:
        return 0.60
    return exposure


def h_continua_confirma(low: float, high: float, key: str) -> Callable[[dict[str, Any]], float]:
    def decide(f: dict[str, Any]) -> float:
        exposure = linear_exposure(f["pct_tendencia_favoravel"], low, high)
        return max(exposure, 0.60) if exposure < 1.0 and f[key] else exposure
    return decide


HYPOTHESES: dict[str, tuple[str, Callable[[dict[str, Any]], float]]] = {
    "BASELINE_T49": ("modelo operacional atual (regimes 100/100/30)", h_base),
    "T56A_CONTINUA_20_60": ("exposicao linear 30%->100% com tendencia favoravel de 20% a 60%", h_continua(0.20, 0.60)),
    "T56B_CONTINUA_15_50": ("exposicao linear 30%->100% com tendencia favoravel de 15% a 50%", h_continua(0.15, 0.50)),
    "T56C_CONFIRMA_IBOV_ESTRITA": ("regime defensivo, mas piso 60% se IBOV > MM50 e MM9 > MM21", h_confirma("ibov_alta_estrita")),
    "T56D_CONFIRMA_IBOV_LEVE": ("regime defensivo, mas piso 60% se IBOV > MM21", h_confirma("ibov_alta_leve")),
    "T56E_HISTERESE": ("queda forte so com 2 meses seguidos ou tendencia < 20%; senao 60%", h_histerese),
    "T56F_CONTINUA_20_60_CONFIRMA_ESTRITA": ("A + piso 60% com IBOV em alta estrita", h_continua_confirma(0.20, 0.60, "ibov_alta_estrita")),
    "T56G_CONTINUA_20_60_CONFIRMA_LEVE": ("A + piso 60% com IBOV > MM21", h_continua_confirma(0.20, 0.60, "ibov_alta_leve")),
}


def simulate(frame: pd.DataFrame, decide: Callable[[dict[str, Any]], float]) -> pd.DataFrame:
    rows = []
    for _, r in frame.iterrows():
        features = r.to_dict()
        base_exposure = effective_exposure(r["regime"], r["w0"])
        new_exposure = float(decide(features))
        w1 = float(np.clip(r["w0"] * new_exposure / base_exposure, 0.0, 1.0)) if r["w0"] > 0 else 0.0
        ret = w1 * r["r_acoes"] + (1.0 - w1) * r["r_cdi"]
        rows.append({"mes": r["mes"], "exposicao_nova": new_exposure, "peso_acoes": w1, "retorno": ret,
                     "mudou": abs(w1 - r["w0"]) > 1e-9})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Metricas
# ----------------------------------------------------------------------------
def compound(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float((1 + values).prod() - 1) if len(values) else np.nan


def max_drawdown(series: pd.Series) -> float:
    curve = (1 + pd.to_numeric(series, errors="coerce").fillna(0)).cumprod()
    return float((curve / curve.cummax() - 1).min())


def bootstrap_delta(delta: pd.Series, seed: int = 56) -> dict[str, float]:
    values = delta.dropna().to_numpy()
    if len(values) == 0:
        return {"delta_medio": np.nan, "ic95_inf": np.nan, "ic95_sup": np.nan, "p_delta_menor_igual_zero": np.nan}
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(BOOTSTRAPS, len(values)), replace=True).mean(axis=1)
    return {"delta_medio": float(values.mean()), "ic95_inf": float(np.percentile(means, 2.5)),
            "ic95_sup": float(np.percentile(means, 97.5)), "p_delta_menor_igual_zero": float((means <= 0).mean())}


def summarize(name: str, scenario: pd.DataFrame, base: pd.DataFrame, ibov: pd.Series) -> dict[str, Any]:
    delta = scenario["retorno"].to_numpy() - base["retorno"].to_numpy()
    changed = scenario["mudou"].to_numpy()
    stats = bootstrap_delta(pd.Series(delta))
    return {
        "cenario": name, "meses": len(scenario),
        "retorno": compound(scenario["retorno"]), "retorno_ibov": compound(ibov),
        "alfa_vs_ibov": compound(scenario["retorno"]) - compound(ibov),
        "delta_vs_baseline": compound(scenario["retorno"]) - compound(base["retorno"]),
        "drawdown": max_drawdown(scenario["retorno"]),
        "taxa_acerto_vs_ibov": float((scenario["retorno"].to_numpy() > ibov.to_numpy()).mean()),
        "peso_acoes_medio": float(scenario["peso_acoes"].mean()),
        "meses_alterados": int(changed.sum()),
        "meses_melhorou": int(((delta > 1e-9) & changed).sum()),
        "meses_piorou": int(((delta < -1e-9) & changed).sum()),
        "soma_delta_meses_alterados": float(delta[changed].sum()),
        **stats,
    }


def walk_forward(frame: pd.DataFrame, simulations: dict[str, pd.DataFrame], margin: float) -> pd.DataFrame:
    """Em cada mes escolhe o cenario com melhor retorno acumulado dos meses ANTERIORES."""
    months = frame["mes"].tolist()
    rows = []
    for i, month in enumerate(months):
        if i < WARMUP_MONTHS:
            continue
        history = {name: compound(sim["retorno"].iloc[:i]) for name, sim in simulations.items()}
        base_history = history["BASELINE_T49"]
        best = max(history, key=history.get)
        chosen = best if history[best] - base_history > margin else "BASELINE_T49"
        rows.append({"mes": month, "escolhido": chosen,
                     "retorno": float(simulations[chosen]["retorno"].iloc[i]),
                     "retorno_base": float(simulations["BASELINE_T49"]["retorno"].iloc[i]),
                     "ibov": float(frame["ibov"].iloc[i])})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Execucao
# ----------------------------------------------------------------------------
def build_dataset(allow_network: bool = True) -> pd.DataFrame:
    frame = load_returns()
    inputs = formation_inputs()
    prices = load_ibov_prices(allow_network)
    features = pd.DataFrame([{"mes": m, **ibov_features(prices, m)} for m in frame["mes"]])
    frame = frame.merge(inputs, on="mes", how="left").merge(features, on="mes", how="left")
    frame["regime_anterior"] = frame["regime"].shift(1)
    return frame


def main() -> None:
    allow_network = "--offline" not in sys.argv
    before = fingerprint()
    frame = build_dataset(allow_network)
    simulations = {name: simulate(frame, decide) for name, (_, decide) in HYPOTHESES.items()}
    base = simulations["BASELINE_T49"]

    def period(mask: pd.Series) -> pd.DataFrame:
        rows = []
        for name, sim in simulations.items():
            rows.append(summarize(name, sim[mask.to_numpy()].reset_index(drop=True),
                                  base[mask.to_numpy()].reset_index(drop=True), frame.loc[mask, "ibov"]))
        return pd.DataFrame(rows)

    everything = frame["mes"].ge(FIRST_MONTH)
    train = frame["mes"].le(TRAIN_END)
    test = frame["mes"].gt(TRAIN_END)
    total_summary, train_summary, test_summary = period(everything), period(train), period(test)

    best_train = train_summary.loc[train_summary["cenario"] != "BASELINE_T49"].sort_values("retorno", ascending=False).iloc[0]
    chosen_name = best_train["cenario"] if best_train["retorno"] > train_summary.loc[train_summary["cenario"].eq("BASELINE_T49"), "retorno"].iloc[0] else "BASELINE_T49"

    wf = walk_forward(frame, simulations, margin=0.0)
    wf_margin = walk_forward(frame, simulations, margin=0.01)

    def wf_row(label: str, table: pd.DataFrame) -> dict[str, Any]:
        stats = bootstrap_delta(table["retorno"] - table["retorno_base"])
        return {"metodo": label, "meses": len(table), "retorno": compound(table["retorno"]),
                "retorno_baseline": compound(table["retorno_base"]), "retorno_ibov": compound(table["ibov"]),
                "delta_vs_baseline": compound(table["retorno"]) - compound(table["retorno_base"]),
                "meses_diferentes_do_baseline": int((table["escolhido"] != "BASELINE_T49").sum()), **stats}

    wf_summary = pd.DataFrame([wf_row("walk_forward_sem_margem", wf), wf_row("walk_forward_margem_1pp", wf_margin)])

    monthly = frame[["mes", "regime", "pct_tendencia_favoravel", "ibov_alta_estrita", "ibov_alta_leve",
                     "w0", "r_acoes", "r_cdi", "retorno_base", "ibov", "erro_reconstrucao"]].copy()
    for name, sim in simulations.items():
        monthly[f"ret_{name}"] = sim["retorno"].to_numpy()
        monthly[f"exp_{name}"] = sim["exposicao_nova"].to_numpy()
    cases = monthly[monthly["regime"].ne("alta")][["mes", "regime", "pct_tendencia_favoravel", "ibov_alta_estrita",
                                                    "ibov_alta_leve", "ibov", "retorno_base"]]
    limits = pd.DataFrame({"limitacao": [
        f"{len(frame)} meses ({FIRST_MONTH} a {LAST_MONTH}); outubro/2026 fora (base de formacao ausente) e novembro/2026 e producao",
        f"Apenas {int((frame['regime'] == 'queda_forte').sum())} meses de queda_forte e {int((frame['regime'] == 'queda_leve').sum())} de queda_leve: amostra pequena",
        "As regras atuais (limites de 35%, 40%) foram calibradas olhando este mesmo historico; o baseline e otimista",
        "Selecao de acoes mantida; so a exposicao muda (sem reotimizar pesos, tetos e minimos)",
        "queda_leve rodou com ~97% em acoes no T49 (padrao de 100%), nao com 60% nominal",
        f"{len(HYPOTHESES) - 1} hipoteses testadas: a melhor no historico inteiro nao deve ser lida como evidencia",
        "Walk-forward: warm-up de 12 meses; escolhe o cenario com melhor retorno acumulado ate o mes anterior",
    ]})
    EXCEL_DIR.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUT, engine="xlsxwriter") as writer:
        pd.DataFrame([{"cenario": n, "descricao": d} for n, (d, _) in HYPOTHESES.items()]).to_excel(writer, sheet_name="Descricao", index=False)
        total_summary.to_excel(writer, sheet_name="Resumo Total", index=False)
        train_summary.to_excel(writer, sheet_name=f"Treino ate {TRAIN_END}", index=False)
        test_summary.to_excel(writer, sheet_name="Teste fora da amostra", index=False)
        wf_summary.to_excel(writer, sheet_name="Walk Forward Resumo", index=False)
        wf.to_excel(writer, sheet_name="Walk Forward Meses", index=False)
        monthly.to_excel(writer, sheet_name="Mes a Mes", index=False)
        cases.to_excel(writer, sheet_name="Meses nao alta", index=False)
        limits.to_excel(writer, sheet_name="Limitacoes", index=False)

    after = fingerprint()
    if before != after:
        raise SystemExit("ERRO: arquivos de producao foram alterados; teste invalido")

    show = ["cenario", "retorno", "alfa_vs_ibov", "delta_vs_baseline", "drawdown", "peso_acoes_medio",
            "meses_alterados", "meses_melhorou", "meses_piorou", "delta_medio", "ic95_inf", "ic95_sup"]
    fmt = {c: "{:.4f}".format for c in show if c not in ("cenario", "meses_alterados", "meses_melhorou", "meses_piorou")}
    for title, table in (("TOTAL", total_summary), (f"TREINO ate {TRAIN_END}", train_summary), ("TESTE fora da amostra", test_summary)):
        print(f"\n=== {title} ({int(table['meses'].iloc[0])} meses) ===")
        print(table[show].to_string(index=False, formatters=fmt))
    print(f"\nMelhor cenario no treino: {chosen_name}")
    print("\n=== WALK-FORWARD ===")
    print(wf_summary.to_string(index=False))
    print("\nEscolhas do walk-forward (sem margem):")
    print(wf["escolhido"].value_counts().to_string())
    print(f"\nErro maximo de reconstrucao do baseline: {frame['erro_reconstrucao'].abs().max():.6f}")
    print(f"Arquivo gerado: {OUT}")
    print("Producao intacta (hashes conferidos).")


if __name__ == "__main__":
    main()
