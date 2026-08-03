# carteira_mensal

Projeto Python para automatizar a selecao mensal de acoes brasileiras e montar uma carteira recomendada para swing trade mensal.

Esta primeira versao coleta dados automaticamente quando as fontes online estiverem disponiveis, calcula indicadores tecnicos, fundamentos, risco, ranking, otimizacao de pesos e gera saidas em Excel, CSV e PDF. A planilha gerada e memoria de calculo e auditoria, nao fonte de entrada manual.

## Instalacao

```bash
cd carteira_mensal
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Configuracao

Os parametros principais ficam em `config/settings.yaml`:

- limites de peso, setor e numero de ativos;
- janelas de risco e historico;
- parametros de medias moveis, RSI e Bollinger;
- taxa livre de risco anual;
- fontes primarias de precos e fundamentos;
- regras de risco e alertas.

A lista inicial de ativos fica em `config/ativos.csv`. Use tickers no padrao do Yahoo Finance para a B3, por exemplo `PETR4.SA`, `VALE3.SA` e `ITUB4.SA`.

O arquivo `config/setores.csv` e auxiliar para mapear setores aos indices setoriais. Ele nao substitui dados coletados automaticamente.

## Execucao

```bash
python src/main.py
```

O sistema foi desenhado para rodar no primeiro dia util do mes, mas a execucao manual acima tambem funciona. Para agendamento, use o Agendador de Tarefas do Windows, cron ou outro orquestrador chamando o mesmo comando.

## Saidas

Os relatorios sao gravados em:

- `output/excel/carteira_recomendada_YYYY_MM_DD_MM_YYYY_HHMMSS.xlsx`
- `output/pdf/relatorio_carteira_YYYY_MM_DD_MM_YYYY_HHMMSS.pdf`

O Excel contem abas de auditoria: resumo da carteira, ranking, indicadores tecnicos, fundamentos, analise setorial, matrizes de correlacao e covariancia, otimizacao, ativos excluidos, alertas, fontes de dados e log de coleta.

## Fontes de dados

- Precos historicos e indices: `yfinance`.
- Fundamentos: tentativa automatica via Fundamentus.
- Taxa livre de risco: configurada em `settings.yaml` na primeira versao.

Se uma coleta falhar, o erro e registrado no log e nas abas de fontes/log. Dados fundamentalistas ausentes ficam como `NaN`; o sistema nao preenche zero artificialmente.

## Alertas

Os alertas indicam dados ausentes, RSI esticado, Bollinger desfavoravel, beta alto, correlacao elevada, tendencia setorial fraca ou violacoes de validacao da carteira. Um alerta nao significa eliminacao automatica, salvo quando fizer parte das regras eliminatorias documentadas.

## Testes

```bash
pytest
```

Os testes cobrem RSI, Bollinger, medias moveis, log-retorno, desvio padrao populacional, CV, beta, correlacao, covariancia, retorno/risco/beta/Sharpe da carteira, restricoes de peso, limite por setor, pontuacao final e tratamento de fundamentos ausentes.

## Limitacoes da primeira versao

- A disponibilidade de dados depende das fontes publicas, que podem mudar layout, bloquear requisicoes ou nao ter todos os campos.
- O P/L anterior esta modelado no pipeline, mas pode ficar `NaN` quando a fonte gratuita nao disponibilizar a serie historica.
- A taxa livre de risco online ainda nao foi implementada; a versao inicial usa a taxa anual configurada.
- Indices setoriais podem variar conforme o ticker aceito pelo Yahoo Finance.
- Este projeto e uma ferramenta quantitativa e auditavel, nao recomendacao individual de investimento.


