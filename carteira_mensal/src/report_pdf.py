from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from utils import ROOT


def _fmt(value: object, digits: int = 6) -> str:
    try:
        if pd.isna(value):
            return "indisponivel"
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_pct(value: object, digits: int = 2) -> str:
    try:
        if pd.isna(value):
            return "indisponivel"
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return str(value)

def _safe(value: object) -> str:
    return escape(str(value))


def _summary_value(summary: pd.DataFrame | None, metric: str, default: str = "") -> str:
    if summary is None or summary.empty or "metrica" not in summary or "valor" not in summary:
        return default
    values = summary.loc[summary["metrica"] == metric, "valor"]
    if values.empty:
        return default
    return str(values.iloc[0])


def _diagnosis_value(diagnosis: pd.DataFrame, category: str, indicator: str, default: str = "indisponivel") -> str:
    if diagnosis.empty or not {"categoria", "indicador", "valor"}.issubset(diagnosis.columns):
        return default
    values = diagnosis.loc[diagnosis["categoria"].eq(category) & diagnosis["indicador"].eq(indicator), "valor"]
    if values.empty or pd.isna(values.iloc[0]):
        return default
    return str(values.iloc[0])


def _diagnosis_subset(diagnosis: pd.DataFrame, category: str, limit: int | None = None) -> pd.DataFrame:
    if diagnosis.empty or "categoria" not in diagnosis:
        return pd.DataFrame()
    subset = diagnosis[diagnosis["categoria"].eq(category)].copy()
    if limit is not None:
        subset = subset.head(limit)
    return subset


def _next_versioned_path(directory: Path, prefix: str, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(prefix)}_v(\d+){re.escape(suffix)}$")
    versions = []
    for file in directory.glob(f"{prefix}_v*{suffix}"):
        match = pattern.match(file.name)
        if match:
            versions.append(int(match.group(1)))
    return directory / f"{prefix}_v{max(versions, default=0) + 1}{suffix}"


def _writable_pdf_path(path: Path) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        return path
    except FileExistsError:
        prefix = re.sub(r"_v\d+$", "", path.stem)
        return _writable_pdf_path(_next_versioned_path(path.parent, prefix, path.suffix))
    except PermissionError:
        prefix = re.sub(r"_v\d+$", "", path.stem)
        return _writable_pdf_path(_next_versioned_path(path.parent, prefix, path.suffix))


def _cell(value: object, max_len: int = 56) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        text = f"{value:.4f}"
    else:
        text = str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _table(frame: pd.DataFrame, cols: list[str]) -> Table:
    cols = list(dict.fromkeys(cols))
    frame = frame.loc[:, ~frame.columns.duplicated()].copy()
    if frame.empty:
        data = [cols, ["" for _ in cols]]
    else:
        data = [cols] + [[_cell(value) for value in row] for row in frame.reindex(columns=cols).fillna("").round(4).values.tolist()]
    available_width = landscape(A4)[0] - 48
    col_widths = [available_width / max(len(cols), 1)] * len(cols)
    table = Table(data, repeatRows=1, colWidths=col_widths)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 5), ("LEADING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 1.5), ("RIGHTPADDING", (0, 0), (-1, -1), 1.5), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return table
def write_pdf(
    portfolio: pd.DataFrame,
    metrics: dict,
    alerts: pd.DataFrame,
    year_month: str,
    universe_summary: pd.DataFrame | None = None,
    optimization_full: pd.DataFrame | None = None,
    comparison: pd.DataFrame | None = None,
    market_diagnosis: pd.DataFrame | None = None,
    timing_summary: pd.DataFrame | None = None,
    watchlist: pd.DataFrame | None = None,
    relative_strength: pd.DataFrame | None = None,
    sector_market: pd.DataFrame | None = None,
    optimization_block_audit: pd.DataFrame | None = None,
    market_participation: pd.DataFrame | None = None,
    hard_filter_settings: pd.DataFrame | None = None,
    performance_realizada: pd.DataFrame | None = None,
    diagnostico_pos_selecao: pd.DataFrame | None = None,
) -> Path:
    output_dir = ROOT / "output" / "pdf"
    base_path = _next_versioned_path(output_dir, f"relatorio_carteira_{year_month}", ".pdf")
    path = _writable_pdf_path(base_path)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    status = metrics.get("status_carteira", "indefinido")
    valid_text = "sim" if metrics.get("carteira_valida", False) else "nao"
    optimization_full = optimization_full if optimization_full is not None else pd.DataFrame()
    comparison = comparison if comparison is not None else pd.DataFrame()
    market_diagnosis = market_diagnosis if market_diagnosis is not None else pd.DataFrame()
    timing_summary = timing_summary if timing_summary is not None else pd.DataFrame()
    watchlist = watchlist if watchlist is not None else pd.DataFrame()
    relative_strength = relative_strength if relative_strength is not None else pd.DataFrame()
    sector_market = sector_market if sector_market is not None else pd.DataFrame()
    optimization_block_audit = optimization_block_audit if optimization_block_audit is not None else pd.DataFrame()
    market_participation = market_participation if market_participation is not None else pd.DataFrame()
    hard_filter_settings = hard_filter_settings if hard_filter_settings is not None else pd.DataFrame()
    performance_realizada = performance_realizada if performance_realizada is not None else pd.DataFrame()
    diagnostico_pos_selecao = diagnostico_pos_selecao if diagnostico_pos_selecao is not None else pd.DataFrame()
    zero_weight = optimization_full[optimization_full.get("peso_final", pd.Series(dtype=float)).fillna(0) == 0].copy() if not optimization_full.empty else pd.DataFrame()
    sector_weights = portfolio.groupby("setor")["peso_recomendado"].sum().sort_values(ascending=False) if not portfolio.empty else pd.Series(dtype=float)
    concentration_text = "; ".join(f"{sector}: {weight:.1%}" for sector, weight in sector_weights.items()) or "indisponivel"
    market_class = _diagnosis_value(market_diagnosis, "Classificacao", "classificacao geral do mercado")
    invalidity_cause = _diagnosis_value(market_diagnosis, "Explicacao carteira invalida", "causa principal", "")

    story = [
        Paragraph("Relatorio da Carteira Mensal", styles["Title"]),
        Paragraph("Objetivo: selecionar acoes brasileiras para swing trade mensal com metodologia auditavel.", styles["BodyText"]),
        Spacer(1, 12),
        Paragraph("Universo e etapas", styles["Heading2"]),
        Paragraph(f"Modo: {_safe(_summary_value(universe_summary, 'modo_configurado', 'indefinido'))}", styles["BodyText"]),
        Paragraph(f"Fonte: {_safe(_summary_value(universe_summary, 'fonte_do_universo', 'indefinida'))}", styles["BodyText"]),
        Paragraph(f"Ativos analisados: {metrics.get('ativos_analisados', 0)}", styles["BodyText"]),
        Paragraph(f"Candidatas preliminares: {metrics.get('candidatas_preliminares', 0)}", styles["BodyText"]),
        Paragraph(f"Candidatas levadas para risco: {metrics.get('candidatas_risco', 0)}", styles["BodyText"]),
        Paragraph(f"Aprovadas para risco: {metrics.get('aprovadas_para_risco', 0)}", styles["BodyText"]),
        Paragraph(f"Moderadas para risco: {metrics.get('moderadas_para_risco', 0)}", styles["BodyText"]),
        Paragraph(f"Bloqueadas para risco: {metrics.get('bloqueadas_para_risco', 0)}", styles["BodyText"]),
        Paragraph(f"Ativos permitidos para otimizacao: {metrics.get('ativos_permitidos_otimizacao', 0)}", styles["BodyText"]),
        Paragraph(f"Ativos bloqueados com peso: {metrics.get('ativos_bloqueados_com_peso', 0)}", styles["BodyText"]),
        Spacer(1, 12),
        Paragraph("Data-base e Avaliacao da Carteira", styles["Heading2"]),
        Paragraph(f"Mes de referencia: {_safe(metrics.get('mes_referencia', 'indisponivel'))}", styles["BodyText"]),
        Paragraph(f"Carteira formada em: {_safe(metrics.get('data_formacao_carteira', 'indisponivel'))}; dados de selecao usados ate: {_safe(metrics.get('data_limite_dados_selecao', 'indisponivel'))}.", styles["BodyText"]),
        Paragraph(f"Calendario usado: {_safe(metrics.get('calendario_mercado', 'B3'))}; fonte: {_safe(metrics.get('calendario_fonte', ''))}; status: {_safe(metrics.get('calendario_status', ''))}.", styles["BodyText"]),
        Paragraph(f"Performance avaliada ate: {_safe(metrics.get('data_avaliacao_carteira', 'indisponivel'))}; periodo avaliado: {_safe(metrics.get('periodo_avaliacao_performance', 'indisponivel'))}.", styles["BodyText"]),
        Paragraph("Os indicadores de selecao foram calculados apenas com dados disponiveis ate a data de formacao da carteira. Dados posteriores entram somente na avaliacao de performance realizada.", styles["BodyText"]),
        Paragraph("Quando a data de formacao e anterior a data de avaliacao, a carteira e tratada como carteira simulada na data-base para avaliacao historica, nao como recomendacao emitida em tempo real.", styles["BodyText"]),
        Paragraph(f"Timing favoravel tendencia: {metrics.get('timing_favoravel_tendencia', 0)}", styles["BodyText"]),
        Paragraph(f"Timing reversao/oportunidade: {metrics.get('timing_reversao_oportunidade', 0)}", styles["BodyText"]),
        Paragraph(f"Timing esticado/sobrecompra: {metrics.get('timing_esticado_sobrecompra', 0)}", styles["BodyText"]),
        Paragraph(f"Ativos enviados para Watchlist: {metrics.get('watchlist_timing', 0)}", styles["BodyText"]),
        Spacer(1, 12),
        Paragraph("Timing de Entrada", styles["Heading2"]),
        Paragraph("Acoes em tendencia com bom ponto, reversoes controladas e entradas esticadas sao tratadas separadamente antes da otimizacao.", styles["BodyText"]),
        _table(timing_summary, ["grupo", "quantidade", "tickers", "observacao"]),
        Spacer(1, 6),
        Paragraph("Watchlist - boas, mas sem ponto de entrada adequado", styles["Heading3"]),
        _table(watchlist.head(15), ["ticker", "setor", "tipo_timing", "sinal_timing", "motivo_watchlist"]),
        Spacer(1, 12),
        Spacer(1, 12),
        Paragraph("Refinamento de Timing e Watchlist", styles["Heading2"]),
        Paragraph("Nem toda watchlist bloqueia a carteira. Watchlist bloqueante impede entrada; watchlist flexivel permite disputa com penalizacao; watchlist de monitoramento apenas registra alerta. Alta forte mensal pode gerar alerta de possivel sinal tardio quando RSI, Bollinger e forca relativa indicam extensao. Retorno medio de 4 meses negativo continua impedindo entrada na carteira principal.", styles["BodyText"]),
        _table(optimization_full.head(20), ["ticker", "retorno_medio", "tipo_watchlist", "qualidade_do_timing", "alerta_sinal_tardio", "penalizacoes_otimizacao", "liberado_para_otimizacao", "motivo_bloqueio_otimizacao"]),
        Spacer(1, 12),
        Paragraph("Diagnostico de Mercado", styles["Heading2"]),
        Paragraph(f"Classificacao geral: {_safe(market_class)}", styles["BodyText"]),
        Paragraph(f"Explicacao da carteira invalida: {_safe(invalidity_cause or 'nao aplicavel')}", styles["BodyText"]),
        Paragraph(f"Justificativa da carteira: {_safe(metrics.get('justificativa_carteira', ''))}", styles["BodyText"]),
        Spacer(1, 6),
        Paragraph("Situacao do IBOV", styles["Heading3"]),
        _table(_diagnosis_subset(market_diagnosis, "IBOV"), ["indicador", "valor", "detalhe"]),
        Spacer(1, 6),
        Paragraph("Amplitude do mercado", styles["Heading3"]),
        _table(_diagnosis_subset(market_diagnosis, "Amplitude"), ["indicador", "quantidade", "percentual", "detalhe"]),
        Spacer(1, 6),
        Paragraph("Diagnostico setorial", styles["Heading3"]),
        _table(_diagnosis_subset(market_diagnosis, "Setorial", 25), ["indicador", "valor", "quantidade", "percentual", "detalhe"]),
        Spacer(1, 6),
        Paragraph("Diagnostico Setorial e Participacao de Mercado", styles["Heading3"]),
        Paragraph("A participacao por valor de mercado e usada como proxy quando o peso oficial do Ibovespa nao estiver disponivel; ela nao deve ser interpretada como peso oficial do indice.", styles["BodyText"]),
        _table(sector_market.head(20), ["setor", "quantidade_empresas_analisadas", "quantidade_tendencia_mensal_favoravel", "percentual_tendencia_mensal_favoravel", "quantidade_forca_relativa_positiva_mes", "percentual_forca_relativa_positiva_mes", "retorno_medio_mes", "retorno_medio_ano", "sentimento_setorial"]),
        Spacer(1, 6),
        _table(sector_market.head(20), ["setor", "principais_acoes_por_nota_preliminar", "principais_acoes_por_forca_relativa", "principais_acoes_por_valor_mercado"]),
        Spacer(1, 12),
        Paragraph(f"Status da carteira: {_safe(status)}", styles["Heading2"]),
        Paragraph(f"Carteira valida: {valid_text}", styles["BodyText"]),
        Paragraph(f"Criterio de formacao: {_safe(metrics.get('criterio_formacao', 'indefinido'))}", styles["BodyText"]),
        Paragraph(f"Restricoes/alertas de carteira: {_safe(metrics.get('restricoes_violadas', '') or 'nenhuma')}", styles["BodyText"]),
        Paragraph(f"Concentracao por setor: {_safe(concentration_text)}", styles["BodyText"]),
        Spacer(1, 12),
        Paragraph(f"Retorno esperado diario: {_fmt_pct(metrics.get('retorno_carteira_diario', metrics.get('retorno_carteira')))}", styles["BodyText"]),
        Paragraph(f"Retorno esperado mensal: {_fmt_pct(metrics.get('retorno_carteira_mensal'))} ({metrics.get('dias_uteis_mes_retorno', 21)} pregoes B3 reais do mes)", styles["BodyText"]),
        Paragraph(f"Retorno esperado anual: {_fmt_pct(metrics.get('retorno_carteira_anual', metrics.get('retorno_anual')))} ({metrics.get('dias_uteis_ano_retorno', 252)} pregoes)", styles["BodyText"]),
        Paragraph(f"Risco esperado diario: {_fmt_pct(metrics.get('risco_carteira_diario', metrics.get('risco_carteira')))}", styles["BodyText"]),
        Paragraph(f"Risco esperado mensal: {_fmt_pct(metrics.get('risco_carteira_mensal'))}", styles["BodyText"]),
        Paragraph(f"Risco esperado anual: {_fmt_pct(metrics.get('risco_carteira_anual', metrics.get('risco_anual')))}", styles["BodyText"]),
        Paragraph(f"CV da carteira: {_fmt(metrics.get('cv_carteira'), 4)}", styles["BodyText"]),
        Paragraph(f"Beta da carteira: {_fmt(metrics.get('beta_carteira'), 4)}", styles["BodyText"]),
        Paragraph(f"Correlacao carteira x IBOV: {_fmt(metrics.get('correlacao_carteira_ibov'), 4)}", styles["BodyText"]),
        Paragraph(f"Score de aderencia ao regime: {_fmt(metrics.get('score_aderencia_regime'), 2)} - {_safe(metrics.get('aderencia_carteira_ao_regime', ''))}", styles["BodyText"]),
        Paragraph(f"Watchlist flexivel na carteira: {metrics.get('quantidade_watchlist_flexivel', 0)} ativos; peso total {_fmt_pct(metrics.get('peso_total_watchlist_flexivel'))}.", styles["BodyText"]),
        Paragraph(f"Sharpe diario: {_fmt(metrics.get('sharpe_diario'), 4)}", styles["BodyText"]),
        Paragraph(f"Carteiras testadas: {_safe(metrics.get('carteiras_testadas', ''))}", styles["BodyText"]),
        Paragraph(f"Composicao escolhida: {metrics.get('quantidade_acoes', 0)} acoes", styles["BodyText"]),
        Paragraph(f"Motivo da escolha: {_safe(metrics.get('motivo_escolha_carteira', 'nenhuma composicao valida foi encontrada sem usar ativos bloqueados.'))}", styles["BodyText"]),
    ]
    story += [Spacer(1, 12), Paragraph("Aderencia ao Regime, Diversificacao e Concentracao Setorial", styles["Heading2"])]
    story.append(Paragraph("A decisao final prioriza carteira valida, aderencia minima ao regime de mercado, ausencia de bloqueios individuais por baixa aderencia, limites de watchlist flexivel, peso setorial e blocos de risco; somente depois entram diversificacao, CV, Sharpe, beta e correlacao. Em mercado favoravel, ativos em watchlist flexivel com beta/correlacao muito baixos podem nao capturar a alta do IBOV. Em mercado fraco, beta e correlacao baixos podem funcionar como protecao. O limite de 2 acoes por setor foi mantido, mas a carteira agora tambem controla peso setorial e sobreposicoes economicas por bloco de risco.", styles["BodyText"]))
    story.append(Paragraph(f"Regime: {_safe(metrics.get('regime_mercado_data_base', metrics.get('mercado_classificacao', '')))}; aderencia: {_safe(metrics.get('aderencia_carteira_ao_regime', ''))}; motivo: {_safe(metrics.get('motivo_aderencia_regime', metrics.get('motivo_incompatibilidade_regime', '')))}", styles["BodyText"]))
    story += [Spacer(1, 12), Paragraph("Mercado Favoravel Esticado e Forca Relativa", styles["Heading2"])]
    story.append(Paragraph(f"Subtipo de mercado favoravel: {_safe(metrics.get('subtipo_mercado_favoravel', 'nao_aplicavel'))}; motivo: {_safe(metrics.get('motivo_subtipo_mercado_favoravel', ''))}", styles["BodyText"]))
    story.append(Paragraph(f"RSI IBOV data-base: {_fmt(metrics.get('rsi_ibov_data_base'), 2)}; Bollinger IBOV: {_safe(metrics.get('bollinger_ibov_data_base', ''))}; ativos positivos no mes: {_fmt_pct(metrics.get('pct_ativos_positivos_1m'))}", styles["BodyText"]))
    story.append(Paragraph("Em mercado favoravel esticado/cansado, forca relativa fraca contra o IBOV deixa de ser candidata normal. Beta alto continua sendo alerta/teto de peso, nao bloqueio isolado; bloqueia apenas quando combinado com forca relativa fraca, fundamentos frageis ou alerta de realizacao pos-rali.", styles["BodyText"]))
    story.append(Paragraph(f"Bloqueios por forca relativa fraca: {metrics.get('ativos_bloqueados_forca_relativa_fraca', 0)}; alertas pos-rali: {metrics.get('ativos_alerta_realizacao_pos_rali', 0)}; alertas beta alto em mercado esticado: {metrics.get('ativos_alerta_beta_alto_mercado_esticado', 0)}; turnaround especulativo: {metrics.get('ativos_turnaround_especulativo', 0)}.", styles["BodyText"]))
    story.append(_table(optimization_full.head(20), ["ticker", "classificacao_forca_relativa", "retorno_1m_relativo_ibov", "beta", "perfil_risco_empresa", "alerta_realizacao_pos_rali", "bloqueio_forca_relativa_fraca", "peso_maximo_permitido_ativo", "motivo_bloqueio_otimizacao"]))
    if not comparison.empty:
        story.append(_table(comparison, ["quantidade de acoes", "CV", "beta", "correlacao_carteira_ibov", "score_aderencia_regime", "maior_peso_setorial", "setor_mais_concentrado", "quantidade_blocos_risco_duplicados", "carteira_elegivel_para_escolha_final", "motivo de escolha ou rejeicao"]))

    if metrics.get("criterio_formacao") == "criterios flexibilizados":
        story.append(Paragraph("A carteira foi formada com flexibilizaÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o controlada dos critÃƒÆ’Ã‚Â©rios de risco, preservando tendÃƒÆ’Ã‚Âªncia tÃƒÆ’Ã‚Â©cnica positiva e retorno recente.", styles["BodyText"]))
    if not metrics.get("carteira_valida", False):
        story.append(Paragraph("Nao houve carteira valida sem usar ativos bloqueados; as simulacoes permaneceram inviaveis pelas restricoes configuradas.", styles["BodyText"]))
    elif metrics.get("restricoes_violadas"):
        story.append(Paragraph("Houve relaxamento setorial ou alerta estrutural registrado porque as candidatas disponiveis nao permitiram melhor diversificacao sem violar pesos minimos e maximos.", styles["BodyText"]))


    risk_methodology = pd.DataFrame(
        [
            ("Medias moveis semanais", "MM9/MM21 indicam entrada mensal; MM50/MM100 contextualizam estrutura."),
            ("RSI/IFR semanal", "Filtro de timing para evitar entrada tardia e detectar sobrevenda com confirmacao."),
            ("Bollinger semanal", "Mede proximidade de bandas para oportunidade, atencao ou sobrecompra."),
            ("Forca relativa", "Compara retorno contra IBOV, com prioridade para a janela de 1 mes."),
            ("Fundamentos", "Filtro minimo de qualidade e deterioracao, nao valuation completo."),
            ("Margem bruta", "Leitura complementar de poder de preco/eficiencia contra mediana setorial."),
            ("Valor de mercado", "Proxy de participacao setorial/universo quando peso oficial nao estiver disponivel."),
            ("Beta", "Sensibilidade do ativo ao IBOV calculada por covariancia/variancia."),
            ("Correlacao", "Diversificacao: correlacao com IBOV e media com demais candidatas."),
            ("CV/Risco", "CV = risco/retorno esperado; otimizacao busca menor CV da carteira."),
        ],
        columns=["Indicador", "Papel na metodologia"],
    )
    story += [Spacer(1, 12), Paragraph("Carteira Simulada na Data-base", styles["Heading2"])]
    if portfolio.empty:
        story.append(Paragraph("Nenhuma composicao valida foi formada na data-base.", styles["BodyText"]))
    else:
        story.append(Paragraph("A composicao abaixo foi formada usando apenas dados disponiveis ate a data-base. Ela representa uma simulacao historica auditavel e nao deve ser interpretada como recomendacao em tempo real quando a avaliacao ocorre em data posterior.", styles["BodyText"]))
        story.append(_table(portfolio, ["ticker", "setor", "peso_recomendado", "status_para_risco", "categoria_elegibilidade", "retorno_medio", "beta", "cv", "alertas_nao_bloqueantes"]))
    story += [Spacer(1, 12), Paragraph("Metodologia de Risco: Beta e Correlacao", styles["Heading2"])]
    story.append(Paragraph(f"Janela de risco: {_safe(metrics.get('janela_risco_meses', metrics.get('risk_window_months', 'indisponivel')))} meses; inicio: {_safe(metrics.get('janela_risco_inicio', 'indisponivel'))}; fim: {_safe(metrics.get('janela_risco_fim', 'indisponivel'))}; observacoes: {_safe(metrics.get('quantidade_observacoes_risco', 'indisponivel'))}; periodicidade: {_safe(metrics.get('periodicidade_risco', metrics.get('risk_return_periodicity', 'diaria')))}; retorno: {_safe(metrics.get('tipo_retorno_risco', metrics.get('risk_return_type', 'log-retornos')))}; fonte primaria: {_safe(metrics.get('risk_price_source', 'yfinance'))}; benchmark: {_safe(metrics.get('risk_benchmark', '^BVSP'))}.", styles["BodyText"]))
    story.append(Paragraph(f"A janela historica de risco utilizada nesta simulacao vai de {_safe(metrics.get('janela_risco_inicio', 'indisponivel'))} ate {_safe(metrics.get('janela_risco_fim', 'indisponivel'))}. A janela termina na data de formacao da carteira ou no ultimo pregao anterior disponivel, evitando uso de dados posteriores a selecao dos ativos.", styles["BodyText"]))
    story.append(Paragraph("Beta individual = covariancia entre o log-retorno diario da acao e o log-retorno diario do IBOV dividida pela variancia do log-retorno diario do IBOV. O beta da carteira e a media ponderada dos betas dos ativos selecionados.", styles["BodyText"]))
    story.append(Paragraph("A correlacao com IBOV mede sensibilidade conjunta ao indice; a correlacao media com demais candidatas ajuda a avaliar diversificacao. A matriz completa fica na aba Matriz de Correlacao do Excel.", styles["BodyText"]))
    story.append(Paragraph("Beta e correlacao sao usados principalmente na etapa de risco e otimizacao. Beta alto e correlacao alta nao bloqueiam automaticamente quando hard filters estao desativados. Em mercado favoravel, beta/correlacao negativos podem bloquear ativos de watchlist flexivel por baixa aderencia ao regime; em mercado fraco, beta/correlacao baixos podem ser defensivos.", styles["BodyText"]))
    story.append(_table(optimization_full.head(20), ["ticker", "beta", "correlacao_ibov", "tipo_watchlist", "bloqueio_aderencia_regime", "motivo_bloqueio_aderencia_regime", "grupo_economico_ou_bloco_risco", "peso_setor", "peso_bloco_risco", "peso_final", "bloqueado_otimizacao"]))
    story += [Spacer(1, 12), Paragraph("Papel dos Indicadores", styles["Heading2"]), _table(risk_methodology, ["Indicador", "Papel na metodologia"])]
    story += [Spacer(1, 12), Paragraph("Forca Relativa contra o IBOV", styles["Heading2"])]
    story.append(Paragraph(_safe(metrics.get("justificativa_carteira", "")), styles["BodyText"]))
    if not portfolio.empty:
        story.append(Paragraph("Ativos selecionados com retorno relativo contra o IBOV", styles["Heading3"]))
        story.append(_table(portfolio, ["ticker", "setor", "retorno_1m_relativo_ibov", "retorno_4m_relativo_ibov", "retorno_ytd_relativo_ibov", "classificacao_forca_relativa", "peso_recomendado"]))
    story.append(Paragraph("Melhores ativos por forca relativa", styles["Heading3"]))
    story.append(_table(relative_strength.head(12), ["ticker", "setor", "retorno_1m_relativo_ibov", "retorno_4m_relativo_ibov", "retorno_ytd_relativo_ibov", "forca_relativa_score", "classificacao_forca_relativa"]))
    story += [Spacer(1, 12), Paragraph("Performance Realizada", styles["Heading2"])]
    if performance_realizada.empty or not metrics.get("performance_realizada_calculada", False):
        story.append(Paragraph("Sem carteira valida formada na data-base; performance da carteira nao aplicavel. O retorno do IBOV do periodo permanece registrado na aba Performance Realizada.", styles["BodyText"]))
    else:
        perf_cols = ["ticker", "peso_recomendado", "preco_formacao", "preco_avaliacao", "retorno_realizado_periodo", "contribuicao_para_retorno_carteira", "retorno_ibov_periodo", "alfa_vs_ibov"]
        story.append(_table(performance_realizada[performance_realizada.get("tipo_linha", pd.Series(dtype=str)).eq("ativo")], perf_cols))
        story.append(Paragraph(f"Retorno realizado da carteira: {_fmt(metrics.get('retorno_realizado_carteira_periodo'), 4)}; retorno IBOV: {_fmt(metrics.get('retorno_realizado_ibov_periodo'), 4)}; alfa: {_fmt(metrics.get('alfa_realizado_vs_ibov'), 4)}.", styles["BodyText"]))
        summary_rows = performance_realizada[performance_realizada.get("tipo_linha", pd.Series(dtype=str)).eq("resumo")]
        if not summary_rows.empty:
            story.append(_table(summary_rows, ["ticker", "retorno_realizado_periodo", "retorno_ibov_periodo", "alfa_vs_ibov", "preco_formacao"]))
    story += [Spacer(1, 12), Paragraph("Diagnostico Pos-Selecao", styles["Heading2"])]
    story.append(Paragraph("A carteira foi formada na data-base e a performance foi medida ate a data de avaliacao. Se a avaliacao ocorrer antes do fechamento do mes, o resultado deve ser lido como parcial. O objetivo desta secao e verificar se os sinais usados pelo robo foram confirmados pelo desempenho posterior.", styles["BodyText"]))
    if diagnostico_pos_selecao.empty:
        story.append(Paragraph("Diagnostico pos-selecao indisponivel.", styles["BodyText"]))
    else:
        diag_detail = diagnostico_pos_selecao[diagnostico_pos_selecao.get("tipo_linha", pd.Series(dtype=str)).eq("ativo")]
        diag_summary = diagnostico_pos_selecao[diagnostico_pos_selecao.get("tipo_linha", pd.Series(dtype=str)).eq("resumo")]
        story.append(_table(diag_detail, ["ticker", "peso_recomendado", "retorno_realizado_periodo", "retorno_ibov_periodo", "alfa_individual_vs_ibov", "contribuicao_para_retorno_carteira", "leitura_diagnostica"]))
        summary_map = dict(zip(diag_summary.get("metrica", pd.Series(dtype=str)), diag_summary.get("valor", pd.Series(dtype=object)))) if not diag_summary.empty else {}
        story.append(Paragraph(f"Resumo: retorno carteira {_fmt(summary_map.get('retorno_realizado_carteira'), 4)}; retorno IBOV {_fmt(summary_map.get('retorno_realizado_ibov'), 4)}; alfa {_fmt(summary_map.get('alfa_realizado_vs_ibov'), 4)}; ativos acima do IBOV {_safe(summary_map.get('quantidade_ativos_superaram_ibov', ''))}; ativos abaixo do IBOV {_safe(summary_map.get('quantidade_ativos_abaixo_ibov', ''))}; melhor ativo {_safe(summary_map.get('melhor_ativo', ''))}; pior ativo {_safe(summary_map.get('pior_ativo', ''))}; diagnostico geral {_safe(summary_map.get('diagnostico_geral_da_carteira', ''))}.", styles["BodyText"]))
        falsos = summary_map.get("principais_falsos_positivos", "")
        if str(falsos):
            story.append(Paragraph(f"Principais falsos positivos ou sinais tardios: {_safe(falsos)}", styles["BodyText"]))
    story += [Spacer(1, 12), Paragraph("Comparativo das simulacoes", styles["Heading2"])]
    if comparison.empty:
        story.append(Paragraph("Comparativo indisponivel.", styles["BodyText"]))
    else:
        story.append(_table(comparison, ["quantidade de acoes", "retorno esperado diario", "risco", "CV", "beta", "correlacao_carteira_ibov", "score_aderencia_regime", "maior_peso_setorial", "setor_mais_concentrado", "quantidade_blocos_risco_duplicados", "carteira_elegivel_para_escolha_final", "Sharpe", "status de validade", "motivo de escolha ou rejeicao"]))
    story += [Spacer(1, 12), Paragraph("Acoes finais selecionadas", styles["Heading2"])]
    story.append(_table(portfolio, ["ticker", "setor", "status_para_risco", "categoria_elegibilidade", "nota_final", "peso_recomendado"]))
    story += [Spacer(1, 12), Paragraph("Candidatas com peso zero", styles["Heading2"])]
    story.append(_table(zero_weight.head(15), ["ticker", "setor", "status_para_risco", "categoria_elegibilidade", "bloqueado_otimizacao", "peso_final"]))
    story += [Spacer(1, 12), Paragraph("Auditoria de Bloqueios para Otimizacao", styles["Heading2"])]
    story.append(_table(optimization_block_audit, ["ticker", "status_para_risco", "categoria_elegibilidade", "regime_mercado_data_base", "tipo_watchlist", "beta", "correlacao_ibov", "bloqueio_aderencia_regime", "motivo_bloqueio_aderencia_regime", "grupo_economico_ou_bloco_risco", "bloqueado_otimizacao", "motivo_bloqueio_otimizacao", "liberado_para_otimizacao"]))
    story += [Spacer(1, 12), Paragraph("Parametros Hard Filter", styles["Heading2"])]
    story.append(_table(hard_filter_settings, ["parametro", "valor_atual", "ativo", "impacto_otimizacao"]))
    story += [Spacer(1, 12), Paragraph("Valor de Mercado e Participacao no Setor", styles["Heading2"])]
    story.append(Paragraph("A participacao por valor de mercado e uma proxy de relevancia no universo analisado e nao representa necessariamente o peso oficial da carteira teorica do Ibovespa.", styles["BodyText"]))
    story.append(_table(market_participation, ["ticker", "setor", "valor_mercado", "participacao_empresa_no_setor", "participacao_empresa_no_universo", "ranking_valor_mercado_setor", "ranking_valor_mercado_universo", "observacao_peso_ibov"]))
    story += [Spacer(1, 12), Paragraph("Principais alertas", styles["Heading2"])]
    if alerts.empty:
        story.append(Paragraph("Sem alertas registrados.", styles["BodyText"]))
    else:
        for _, row in alerts.head(30).iterrows():
            story.append(Paragraph(f"{_safe(row.get('ticker', 'geral'))}: {_safe(row.get('alerta', ''))}", styles["BodyText"]))
    doc.build(story)
    return path










