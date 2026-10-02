# Forward-Test Operacional - Teste 14 / 13B Conservador

Status: `Modelo v1.0.0` em producao desde 2026-10.
`forward-13b-v1` permanece como identificador interno dos registros historicos.
Esta e uma unica versao para a selecao 13B e sua conversao operacional Top 15.
Nao ha uma segunda metodologia de producao chamada T49: esse nome identifica
apenas a serie historica usada como referencia no painel.
Os meses anteriores permanecem historicos de teste; nao foram reclassificados retroativamente.
A carteira base `carteira_recomendada_YYYY_MM_v1.xlsx` e uma entrada do calculo,
enquanto a carteira oficial exibida no aplicativo vem exclusivamente do registro
`output/production/active.json` e do respectivo arquivo mensal imutavel.
O nome interno `shadow.forward_test` e herdado do motor e nao define o status do resultado.

## Configuracao congelada

- Diagnostico de mercado: 13B conservador.
- Carteira: tamanho livre.
- Sinal em alta: V3 momentum, combinando nota_final e forca_relativa_score.
- Sinal em queda: SINAL_A_DEFENSIVO.
- Beta-alvo por regime: ligado, lambda_beta = 1.5.
- Controle de CV: lambda_cv = 0.5.
- D3 estendida: veto tecnico vira penalizacao em regimes favoraveis; veto fundamental continua bloqueante.
- Diversificacao: maximo de 2 acoes por setor.
- Teto individual: 25% no modelo 100%.
- Exposicao defensiva: alta/oportunidade = 100%; queda_leve = 60%; queda_forte = 30%.
- Parcela defensiva: aplicada em CDI/Tesouro Selic. Na parcial/fechamento, o script busca o CDI diario automaticamente no Banco Central SGS serie 12 e calcula retorno liquido com IR regressivo sobre o rendimento.

## Execucao da mesma versao

- A selecao 13B pode gerar mais de 15 acoes (17 em outubro/2026).
- O aplicativo ordena por `nota_final` e, em empate, por `peso_recomendado`;
  considera no maximo 15 acoes e aplica o filtro de peso minimo (padrao 1%).
- Converte os pesos em quantidades compraveis para o aporte informado,
  usando a escolha do usuario entre acoes fracionarias e lotes de 100.
- Posicoes excluidas e sobra de arredondamento permanecem em CDI/reserva.
- A carteira de referencia de R$ 10 mil usa as opcoes padrao. Alterar o aporte
  ou as opcoes da interface muda a simulacao, nao a versao metodologica.

## Comando mensal

Rodar no primeiro dia util do mes, dentro de `carteira_mensal`:

```powershell
.\.venv\Scripts\python.exe scripts\forward_test.py --mes YYYY-MM
```

Exemplo:

```powershell
.\.venv\Scripts\python.exe scripts\forward_test.py --mes 2026-08
```

O script gera `output/excel/carteira_forward_YYYY_MM*.xlsx` e registra log em `output/logs/`.
Para a rotina de producao, usar `scripts/atualizacao_diaria.py`: ela valida as
duas abas de restricoes, as datas e os pesos antes de ativar um novo mes.
O aplicativo recusa um arquivo ausente, modificado ou fora do registro.
`--force-forward` nao pode substituir um mes ja ativado.

## Versionamento

O nome publico vigente e `Modelo v1.0.0`. A equivalencia com o identificador
interno `forward-13b-v1` esta em `config/methodology_display_names.json`.
O nome publico nao representa uma nova estrategia nem altera a carteira ja
ativada. Versoes anteriores nao foram numeradas retroativamente.

O ponteiro vigente esta em `config/production_methodology.json`; a definicao
completa e unica de selecao e execucao esta em
`config/methodologies/forward-13b-v1.json`. Outubro/2026 foi
ativado como `forward-13b-v1`, com corte de dados em 2026-09-30. Alteracoes
na selecao ou na execucao exigem nova definicao de versao e `effective_from` explicito antes do proximo
mes; os registros mensais anteriores e seus hashes nao devem ser reescritos.
Uma mudanca intrames exige decisao documentada e nova publicacao, nunca troca
silenciosa do arquivo ativo. A publicacao nao executa ordens em corretora.

## Parcial do mes

Por padrao, a parcial tenta usar cache local para precos. Para buscar automaticamente o CDI no Banco Central e calcular o CDI liquido de IR, use `--cdi-auto`:

```powershell
.\.venv\Scripts\python.exe scriptsorward_partial.py --mes YYYY-MM --cdi-auto
```

Para atualizar precos via yfinance, e necessario autorizar explicitamente. Isso envia os tickers da carteira ao yfinance:

```powershell
.\.venv\Scripts\python.exe scriptsorward_partial.py --mes YYYY-MM --allow-network --cdi-auto
```

## Regra de disciplina

- Nao recalibrar a metodologia com parcial de mes aberto.
- Fechar o resultado somente apos o ultimo pregao do mes.
- Comparar sempre carteira aplicada vs IBOV no mesmo periodo.
- Registrar alfa, contribuicoes por ativo e regime detectado.
