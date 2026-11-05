# Metodologia forward-13b-v2 (Modelo v2.0.0)

Vigente a partir de **novembro/2026**. Substitui a `forward-13b-v1` (vigente em outubro/2026, preservada e validada).

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

## Como foi aplicado em novembro
A carteira de novembro foi regenerada com a v2 (`carteira_forward_2026_11_v20261104_233545.xlsx`) e o manifesto
foi substituído por `production_registry.supersede_month`. O manifesto e o arquivo da v1 foram preservados:
`output/production/superseded/2026-11_forward-13b-v1.json` e `output/excel/carteira_forward_2026_11.xlsx`.

## Pontos de atenção
- Fundamentos: de jan a jun/2026 as bases históricas usaram fundamentos do Fundamentus coletados depois da formação
  (look-ahead; Teste 59). A partir de julho são coletados na própria data de formação.
- P/L histórico das bases da CVM é pouco confiável (valores ausentes ou implausíveis em ~65% dos casos).
- Resultados de backtest dependem de momento/tendência e de execução próxima do fechamento (Testes 61, 64 e 65).
