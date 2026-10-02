# Forward-Test Operacional - Teste 14 / 13B Conservador

Status: metodologia de producao `forward-13b-v1`, vigente a partir de 2026-10.
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

O metodo vigente esta em `config/production_methodology.json`. Outubro/2026 foi
ativado como `forward-13b-v1`, com corte de dados em 2026-09-30. Alteracoes
metodologicas exigem nova versao e `effective_from` explicito antes do proximo
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
