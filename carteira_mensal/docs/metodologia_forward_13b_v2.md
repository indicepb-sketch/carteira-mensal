# Metodologia forward-13b-v2 (Modelo v2.0.0)

Vigente a partir de **outubro/2026** (a política foi estendida para refazer outubro). Substitui a `forward-13b-v1`, preservada.

## O que mudou
Apenas a **nota** passa a ter os cinco componentes usados nos testes de 2022 a 2025:
tendência (até 30), timing (até 20), fundamentos (até 20), setor (até 10) e risco (até 20).

Na v1, as bases mensais de 2026 traziam a aba `Candidatas Risco` sem as colunas lidas pelo score
(`mm9`, `mm21`, `mm50`, `mm100`, `preco_atual`, `retorno_ytd`, `roe`, `roic`, `margem_bruta`, `pl_atual`,
`tendencia_setorial`). O score era calculado antes do enriquecimento e saía com tendência = 0,
fundamentos = 0 e setor = 5 para todas as candidatas.

A v2 completa **somente as colunas ausentes**, a partir da aba `Analise Preliminar` da mesma base mensal
(dados de formação já gravados; sem informação posterior). Nada é sobrescrito.

## O que não mudou
Classificador de regime (13B conservador), sinais (V3 Momentum / Sinal A Defensivo), otimizador, λ de beta,
tetos por ativo e setor, exposição por regime (100% / 30%) e regras de execução (Top 15, mínimo de 1%, R$ 10 mil).

## Evidência (testes de sombra 59, 68, 69 e 70)
- Em 2022–2025 a nota tinha os cinco componentes (score de fundamentos médio de ~12,6 pontos); em 2026 só três.
- Reotimizando jan–out/2026 com o score completo: a carteira muda em 3 meses (jan, fev e out, os de regime "alta");
  em meses de queda (Sinal A) é idêntica. Retorno composto jan–out: −3,51% contra −4,54% (+1,04 p.p.), sem significância estatística.
- Novembro/2026: mesmas 8 ações e pesos idênticos; muda só a nota (de 17–41 para 70–85).
- O ganho não é o argumento principal: a v2 restaura a **consistência** com o sistema que foi testado.

## Como foi aplicado
- **Outubro e novembro/2026:** as carteiras foram regeneradas com a v2 e os manifestos substituídos por
  `production_registry.supersede_month`. Os manifestos e arquivos da v1 foram preservados em
  `output/production/superseded/` e `output/excel/` (`carteira_forward_2026_10.xlsx`, `carteira_forward_2026_11.xlsx`).
  Outubro: B3SA3 entra e VAMO3 sai; fechou em −11,43% (contra −12,11% na v1). Novembro: mesmas ações e pesos; muda só a nota.
- **Histórico 2022–out/2026 refeito pelo motor v2** (`scripts/historico_v2.py` → `output/excel/historico_v2_top15_capital.xlsx`).
  Como o sistema ainda não tem dinheiro aplicado, o objetivo foi ter um histórico único, produzido pelo mesmo motor que forma as
  carteiras oficiais (nota com 5 componentes, regime 13B conservador, Top 15, mínimo de 1%, lotes inteiros, R$ 10 mil).
  O histórico anterior (T49 até jun/2026 + parciais) foi gerado por uma cadeia de testes com outra lógica de regime
  (ex.: mar/abr/2026 eram "queda forte"; o motor atual os classifica "queda leve") e **foi substituído**.
  Validação: outubro coincide com a parcial oficial fechada (−11,425% contra −11,432%) e o IBOV coincide exatamente.

| Período | Histórico anterior (publicado) | Histórico v2 (motor) | IBOV |
|---|---|---|---|
| 2022–out/2026 (58 meses) | +100,5% | **+75,1%** | +60,2% |

A plataforma e os relatórios PDF passam a ler `historico_v2_top15_capital.xlsx`; o T49 fica como arquivo de pesquisa/reserva.

## Pontos de atenção
- Fundamentos: de jan a jun/2026 as bases históricas usaram fundamentos do Fundamentus coletados depois da formação
  (look-ahead; Teste 59). A partir de julho são coletados na própria data de formação.
- P/L histórico das bases da CVM é pouco confiável (valores ausentes ou implausíveis em ~65% dos casos).
- Resultados de backtest dependem de momento/tendência e de execução próxima do fechamento (Testes 61, 64 e 65).
- Em 2022–2025 o regime do histórico v2 usa só a amplitude de tendência (as demais entradas do 13B só existem nas bases de 2026); por isso o regime desses anos é uma aproximação do motor atual.
- Fundamentos de jan–jun/2026 do histórico v2 vêm do Fundamentus coletado depois da formação (look-ahead leve); a partir de jul/2026 são válidos.
