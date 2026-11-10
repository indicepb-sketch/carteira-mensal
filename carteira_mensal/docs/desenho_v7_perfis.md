# Desenho da metodologia forward-13b-v7: dois perfis (Moderado e Arrojado) e funil de 35 candidatas

Status: IMPLEMENTADA na branch local `v7-perfis` (nao enviada). Vigencia: novembro/2026 (decisao do responsavel pelo produto em 2026-11-10). Aplicativo: os dois perfis aparecem juntos, sem seletor. PDF: um por perfil. A v7 parte da v6 (fundamentos da CVM): so deve ir para a `main` depois da v6.

## 1. O que muda

| | Moderado | Arrojado |
|---|---|---|
| Codigo interno | 20/20 | 20/100 |
| Defesa na queda forte | 20% em acoes, 80% em CDI | 100% em acoes (sem defesa) |
| Funil de entrada | 35 candidatas (era 25) | 35 candidatas |
| Todo o resto | igual a v6 | igual a v6 |

Evidencia (testes de sombra T96, T97 e T98, 82 meses, 2020-01..2026-10, treino 2020-24 e teste 2025-26):

| Funil de 35 | Saldo de R$ 10 mil | Acerto | Razao de informacao | Queda maxima | Pior ano |
|---|---|---|---|---|---|
| Moderado | R$ 23.488 | 61,0% | 0,12 | -21,4% | +1,7% |
| Arrojado | R$ 26.603 | 59,8% | 0,19 | -21,4% | +5,1% |

Com o funil atual de 25, os dois tem acerto de 52,4% a 53,7%. O ganho do funil aparece nos dois perfis, no treino e no teste.
Ressalvas: diferenca de retorno entre perfis e entre funis nao e estatisticamente significativa; a defesa nao tem poder preditivo (AUC 0,448); muitos testes foram feitos sobre os mesmos meses. O historico e um backtest, nao desempenho operado.

## 2. Metodologia v7 (config)

`config/methodologies/forward-13b-v7.json` = v6 mais:

- `selection.pre_risk_candidates`: 35 (hoje vem de `settings.yaml`, 25; passa a vir da metodologia).
- `selection.profiles`: `{"moderado": {"nome": "Moderado", "exposure_by_regime": {"queda_forte": 0.20}}, "arrojado": {"nome": "Arrojado", "exposure_by_regime": {"queda_forte": 1.00}}}`.
- `selection.default_profile`: `"moderado"`.

`regime_policy` passa a receber o perfil e le `exposure_queda_forte` do perfil (hoje le de `selection.exposure_by_regime`).

## 3. Layout de arquivos (implementado)

O perfil `moderado` mantem EXATAMENTE os nomes e caminhos de sempre; o `arrojado` usa subpastas proprias com os MESMOS nomes de arquivo (nenhum padrao de busca do Moderado alcanca o Arrojado).

| Item | Moderado (igual a hoje) | Arrojado |
|---|---|---|
| Manifestos e ativo | `output/production/` | `output/production/arrojado/` |
| Carteira forward e parcial | `output/excel/` | `output/excel/arrojado/` |
| Historico | `output/excel/historico_v2_top15_capital.xlsx` | `output/excel/arrojado/historico_v2_top15_capital.xlsx` |
| Relatorio PDF | `output/pdf/` | `output/pdf/arrojado/` |

A base mensal (`carteira_recomendada_AAAA_MM_v*.xlsx`) e unica e compartilhada. Codigo: `src/perfil.py` (caminhos e nomes). Historico: o funil de 35 e derivado da aba "Analise Preliminar" (as 25 primeiras reproduzem a aba "Candidatas Risco" nos 82 meses).

## 4. Pecas a alterar (impacto)

| Arquivo | O que muda | Tamanho |
|---|---|---|
| `config/methodologies/forward-13b-v7.json`, `methodology_display_names.json`, `production_methodology.json` | nova versao, nomes publicos | pequeno |
| `src/production_registry.py` | parametro `perfil` nos caminhos de `activate_month`, `supersede_month`, `load_active`, `load_month` (padrao `moderado`) | medio |
| `scripts/forward_test.py` | `--perfil`; `regime_policy`/`exposure_for_metrics` por perfil; `pre_risk_candidates` vindo da metodologia; uma execucao gera as duas carteiras do mes | medio |
| `scripts/atualizacao_diaria.py`, `scripts/forward_partial.py` | percorrer os perfis; parcial por perfil | medio |
| `scripts/historico_v2.py`, `scripts/coletor_historico_cvm.py` | `--perfil`; funil de 35 nas bases historicas; um historico por perfil | medio |
| `src/relatorio_mensal.py` | relatorio por perfil (ou secao por perfil) | medio |
| `app/streamlit_app_user.py` (708 linhas) | seletor de perfil; todos os carregadores de dados por perfil; legendas | grande |
| `.github/workflows/atualizacao-diaria.yml` | padroes de arquivo do Arrojado no commit automatico | pequeno |
| testes: `test_production_registry`, `test_candidate_completion`, `test_min_one_share`, `test_close_previous_month`, `test_official_partial_consistency`, `test_historico_v2`, `test_relatorio_mensal`, `test_streamlit_current_month_history` | cobrir os dois perfis | medio |

## 5. Fases propostas (cada uma termina com a suite de testes verde e nada enviado sem aprovacao)

1. **Motor e registro:** v7, perfil no registro e no `forward_test`, funil de 35 vindo da metodologia, testes.
2. **Historico:** bases mensais com funil de 35, dois historicos oficiais (82 meses), referencias v6 preservadas. Antes: conferir em 2 ou 3 meses que as bases geradas pelo coletor com 35 candidatas coincidem com as usadas nos testes de sombra.
3. **Rotina diaria e relatorios:** as duas carteiras do mes, parciais e PDFs por perfil, padroes da Action.
4. **Aplicativo:** seletor Moderado/Arrojado, historico e carteira por perfil.
5. **Ensaio de ponta a ponta** (copia isolada, como na v6) e relatorio de impacto na carteira de dezembro.

## 6. Decisoes que dependem do responsavel pelo produto

1. **Vigencia da v7:** novembro/2026 (substituiria de novo a carteira ativa de novembro, agora com funil de 35 e dois perfis) ou dezembro/2026 (primeira formacao real em 1/12).
2. **Perfil padrao no aplicativo:** Moderado (proposto).
3. **Relatorio em PDF:** um por perfil (proposto) ou um unico com as duas carteiras.
4. **Ordem das entregas:** a v7 so e enviada depois que a v6 estiver na `main`.

## 7. Riscos

- O aplicativo e a maior peca e a mais exposta a regressao; por isso o Moderado mantem os nomes atuais.
- A comunicacao do perfil "Moderado" nao pode sugerir protecao integral: a defesa forte so reduz a exposicao nos meses de alarme e a queda maxima historica foi igual nos dois perfis (-21,4%, mar/2020).
- O resultado da v7 depende de uma escolha (funil de 35) feita depois de muitos testes sobre os mesmos meses; so os meses de 2027 darao validacao fora da amostra.
