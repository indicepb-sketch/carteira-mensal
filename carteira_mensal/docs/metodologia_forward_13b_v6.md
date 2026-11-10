# Metodologia forward-13b-v6: fundamentos da CVM

Vigencia: formacao de dez/2026 em diante (`config/production_methodology.json`). Novembro/2026 permanece na v5 (manifesto ja ativado).

## O que muda em relacao a v5
Somente a fonte e a convencao dos fundamentos. Selecao, pesos de minima variancia, regime 13B, defesa da v5, regra de 1 acao e execucao ficam iguais.

- **Fonte:** demonstracoes DFP/ITR da CVM, usando so documentos recebidos antes da data de formacao (sem olhar o futuro). O Fundamentus deixa de ser fonte dos indicadores.
- **Janela:** 12 meses moveis (DFP = exercicio; ITR = exercicio anterior + acumulado atual - acumulado do ano anterior).
- **Base:** controlador (lucro e patrimonio liquido descontados dos nao controladores).
- **ROE:** lucro do controlador / patrimonio liquido medio do controlador.
- **ROIC:** NOPAT = EBIT x (1 - 34%) dividido por (passivo oneroso + patrimonio liquido).
- **P/L:** valor de mercado / lucro do controlador (12 meses).
- **Bancos e holdings:** reconhecidos pelo plano de contas; so ROE e P/L.
- **Setor:** `config/setores_papeis.csv` (mesmo mapa que refaz o historico). Sem ele, todos os papeis cairiam em "Outros" e a regra de no maximo 2 por setor esvaziaria a carteira.
- **Codigo CVM de cada papel:** `config/cvm_codigos.csv`.
- Campos que a CVM nao fornece (dividend yield, crescimento da receita em 5 anos, liquidez media) ficam vazios; so alimentam comparacoes setoriais e alertas, nunca a nota.

## Arquivos
- `scripts/coletor_fundamentos_cvm.py`: calculo dos indicadores a partir dos arquivos da CVM.
- `scripts/fundamentos_cvm_live.py`: adapta a saida ao formato da formacao mensal.
- `scripts/forward_test.py`: escolhe a fonte pela metodologia vigente (`fundamentals_source`).
- `scripts/coletor_historico_cvm.py`: gera as bases mensais historicas (`carteira_historica_AAAA_MM_corrigida2.xlsx`) lidas por `scripts/historico_v2.py`.
- Downloads da CVM: novas tentativas, cache em disco (`data/cvm_cache/`, nao versionado) e, se a rede falhar de vez, uso do cache vencido com aviso.

## Evidencia (testes de sombra, 82 meses, 2020-01..2026-10)
- Ranking das notas: correlacao de 0,97 a 0,98 contra a base anterior; 13 das 15 melhores acoes em comum em media.
- 99% das acoes escolhidas sao iguais entre as convencoes de ROE/ROIC testadas; resultado sem diferenca estatisticamente significativa.
- Papeis sem dados por mes: de 7-13 para 0-4.
- Ensaio da formacao de novembro/2026 com a fonte CVM: as mesmas 8 acoes da carteira oficial (v5) mais CXSE3; sobreposicao de pesos de 87,5%.

## Historico
Refeito retroativamente e estendido a jan/2020 (82 meses): modelo +127,1% contra IBOV +44,8%. E um backtest, nao desempenho operado. A versao anterior fica em `output/excel/historico_v5_fundamentos_anteriores_referencia.xlsx`.
Limitacoes conhecidas: em jan-mai/2020 a quantidade de acoes usa o primeiro arquivo da CVM com esse dado (so o P/L desses meses olha um pouco adiante); o universo e a composicao atual do IBOV (vies de sobrevivencia).
