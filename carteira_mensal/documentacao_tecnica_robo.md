# Documentacao tecnica do robo de carteira mensal

Documento gerado por leitura do codigo-fonte. Cada afirmacao operacional cita `arquivo:funcao:linha`. Onde ha inferencia, o texto esta marcado com `[interpretacao]`.

## Parte 1 - Pipeline de producao

### 1. Coleta de dados

- O ponto de entrada de producao e `main()`, que carrega configuracoes, universo, tickers, precos, indices e fundamentos antes de montar a analise: `src/main.py:main:2457-2477`.
- O universo e carregado por `load_universe(settings)`: em modo `custom_csv`, le `config/ativos.csv`; em modo `ibovespa_online`, chama a carteira teorica do IBOV da B3, valida os tickers no yfinance, salva o universo e retorna os ativos validados; se falhar e `fallback_to_custom_csv=true`, usa o CSV como fallback: `src/universe_loader.py:load_universe:216-254`.
- A fonte online do universo e o endpoint B3 `GetPortfolioDay`, com payload contendo `index: IBOV`; os tickers sao convertidos para yfinance adicionando `.SA`: `src/universe_loader.py:B3_IBOV_URL/_b3_payload/_ticker_to_yfinance:20-36`, `src/universe_loader.py:fetch_ibovespa_theoretical_portfolio:54-92`.
- A validacao do universo usa `yfinance.download` em lote por 2 meses e, se necessario, retry individual por 6 meses e fallbacks configurados: `src/universe_loader.py:_validate_with_yfinance:95-156`.
- A configuracao atual declara `universe.mode: ibovespa_online`, fallback para `config/ativos.csv` e salvamento do universo: `config/settings.yaml:universe:17-21`.
- Precos de ativos e indices usam yfinance. A funcao de precos de ativos importa `yfinance`, faz download em lote, tenta retries/fallbacks para ausentes e retorna um DataFrame concatenado: `src/data_loader.py:fetch_yfinance_prices:172-222`.
- A funcao de indices tambem usa yfinance e fallbacks de indice quando a serie principal falha: `src/data_loader.py:fetch_index_prices:225-252`.
- O campo de preco usado e `Adj Close` quando `adjusted=True`; se nao houver `Adj Close`, o codigo cai para `Close`: `src/data_loader.py:_price_column:21-37`, `src/data_loader.py:_series_from_batch:40-55`.
- A configuracao atual liga preco ajustado (`use_adjusted_prices: true`), fonte primaria `yfinance`, historico de 60 meses, janela de risco de 4 meses e retries 3: `config/settings.yaml:data:29-37`.
- Fundamentos sao coletados no Fundamentus pela URL `https://www.fundamentus.com.br/detalhes.php?papel={ticker}` com `requests` e `BeautifulSoup`: `src/fundamentals.py:fetch_fundamentus:90-107`.
- Os campos esperados de fundamentos incluem ROE, ROIC, margem bruta, P/L atual, liquidez media, setor e subsetor; o mapeamento tambem contempla margem EBIT, margem liquida, P/VP, dividend yield, divida liquida/patrimonio, crescimento de receita 5 anos e valor de mercado: `src/fundamentals.py:EXPECTED_FIELDS/LABEL_MAP:17-43`.
- Percentuais de fundamentos sao convertidos para decimal; P/L, P/VP, liquidez, divida/patrimonio e valor de mercado usam `safe_float`: `src/fundamentals.py:_parse_value:59-64`.
- `pl_anterior` nao e coletado da pagina de detalhes; o codigo registra como campo ausente: `src/fundamentals.py:fetch_fundamentus:118-120`.
- A coleta de fundamentos itera ticker a ticker chamando `fetch_fundamentus`: `src/fundamentals.py:collect_fundamentals:137-144`.

### 2. Indicadores tecnicos

- O fechamento tecnico e semanal: `weekly_close` ordena a serie e usa `resample("W-FRI").last()`: `src/technical_indicators.py:weekly_close:11-15`.
- Medias moveis semanais usam rolling mean com `min_periods=window` sobre o fechamento semanal: `src/technical_indicators.py:moving_average/weekly_moving_averages:7-22`.
- A configuracao atual usa MM semanais `[9, 21, 50, 100]`: `config/settings.yaml:technical:64-65`.
- RSI e calculado sobre a serie semanal com periodo configurado; o calculo usa ganhos/perdas, media inicial simples e suavizacao de Wilder: `src/technical_indicators.py:rsi_components:25-59`, `src/technical_indicators.py:rsi:62-64`.
- O periodo de RSI configurado e 14: `config/settings.yaml:technical:66-69`.
- Bollinger usa media movel simples de 20 periodos, desvio padrao populacional (`ddof=0`), bandas superior/inferior em `media +/- 2*std`, e posicao `(preco-lower)/(upper-lower)`: `src/technical_indicators.py:bollinger_bands:67-79`.
- A configuracao atual de Bollinger e periodo 20 e desvio 2: `config/settings.yaml:technical:70-71`.
- O snapshot tecnico usa a serie semanal para RSI, Bollinger, MM9/MM21/MM50/MM100, preco atual e tendencia; tambem registra explicitamente `timeframe_tecnico`, `rsi_timeframe` e `bollinger_timeframe` como `1W`: `src/technical_indicators.py:calculate_technical_snapshot:145-191`.
- A tendencia tecnica classica classifica `Descarte` quando MM9<MM21, MM50<MM100 e preco<MM50; `Forte alta` quando MM9>MM21, MM50>MM100 e preco>MM50; `Aceitavel` quando MM9>MM21 e preco>MM50; `Fraca` quando MM9<MM21 ou preco<MM50: `src/technical_indicators.py:classify_trend:107-119`.
- RSI e classificado como sobrevenda abaixo de 30, zona fraca abaixo de 50, zona favoravel abaixo de 65, favoravel com atencao ate 70 e sobrecompra acima de 70: `src/technical_indicators.py:classify_rsi:93-104`.
- Bollinger classifica preco abaixo da banda inferior, favoravel, oportunidade, sobrecompra e alerta negativo conforme posicao relativa, tendencia e RSI: `src/technical_indicators.py:classify_bollinger:122-142`.
- A forca relativa contra IBOV e calculada em `add_relative_strength`: retornos relativos 1m, 4m e YTD sao retorno do ativo menos retorno IBOV; `forca_relativa_score` e booleano ponderado: `2*(rel_1m>0)+2*(rel_4m>0)+1*(rel_ytd>0)`: `src/main.py:add_relative_strength:173-203`.
- A configuracao atual liga a forca relativa e lista janelas 1m, 4m e YTD: `config/settings.yaml:relative_strength:150-153`.

### 3. Nota final

- A nota final e calculada em `score_assets`: soma `score_tendencia`, `score_timing`, `score_fundamentos`, `score_setor`, `score_risco`, subtrai `penalidade_cv` e `penalidade_timing`, depois limita a faixa 0-100: `src/scoring.py:score_assets:151-164`.
- `score_tendencia` tem teto 30: +8 se MM9>MM21, +8 se MM50>MM100, +8 se preco_atual>MM50, +6 se retorno_ytd>0: `src/scoring.py:score_technical:18-24`.
- `score_timing` tem teto 20: timing favoravel tendencia vale 20, favoravel com alerta vale 16, reversao/oportunidade vale 14, sobrecompra vale 0, fraqueza/reversao nao aprovada vale 2; caso contrario pontua RSI e Bollinger: `src/scoring.py:score_timing:27-65`.
- `score_fundamentos` tem teto 20: ROE >20% soma 7, ROE >=10% soma 4; ROIC >15% soma 7, ROIC >=8% soma 4; margem bruta positiva soma 3; P/L positivo soma 3: `src/scoring.py:score_fundamentals:68-82`.
- `score_setor` vale 10 para tendencia setorial alta, 5 para neutra e 0 para demais: `src/scoring.py:score_sector:85-91`.
- `score_risco` tem teto 20: +5 retorno medio positivo, +5 desvio padrao abaixo do limite diario, +5 CV entre 0 e limite, +3 beta abaixo/igual `beta_alert`, +2 correlacao IBOV abaixo/igual `correlation_alert`: `src/scoring.py:score_risk:140-148`.
- Penalidade de CV e 0 ate o limite, 5 ate o segundo nivel, 10 ate o terceiro, 18 acima disso: `src/scoring.py:cv_penalty:94-103`.
- Penalidade de timing e 15 para `timing_esticado_sobrecompra`; RSI acima de `attention_rsi_max` adiciona 5: `src/scoring.py:timing_penalty:106-114`.
- Penalizacoes de prioridade da otimizacao subtraem pontos do `score_prioridade_otimizacao` depois da nota final, usando tokens como watchlist flexivel, timing tardio, CV alto, beta alto e penalizacoes de regime: `src/scoring.py:optimization_priority_penalty:118-139`, `src/scoring.py:score_assets:162-164`.

### 4. Funil de decisao preliminar

- A decisao preliminar ajustada esta em `_preliminary_adjusted_decision`: `src/main.py:_preliminary_adjusted_decision:1312-1341`.
- Dados essenciais ausentes (`price_rows<=0` ou `preco_atual` ausente) geram `descartar_dados_insuficientes`: `src/main.py:_preliminary_adjusted_decision:1313-1315`.
- `fundamento_bloqueante=True` gera `descartar_fundamentalista`: `src/main.py:_preliminary_adjusted_decision:1316-1317`.
- Tendencia mensal `descarte_tecnico`, forca relativa mensal `fraca` e timing diferente de reversao/oportunidade geram `descartar_tecnico`: `src/main.py:_preliminary_adjusted_decision:1323-1324`.
- `candidata_para_risco` exige tendencia mensal em `alta_forte_mensal` ou `alta_aceitavel_ou_virada`, timing favoravel, forca relativa mensal forte/positiva e qualidade fundamentalista otima/boa/aceitavel: `src/main.py:_preliminary_adjusted_decision:1325-1327`.
- `watchlist_qualificada` ocorre quando bons fundamentos ainda nao tem confirmacao mensal, quando forca de medio prazo nao confirma o mes, ou quando timing exige espera/confirmacao: `src/main.py:_preliminary_adjusted_decision:1328-1336`.
- `candidata_com_restricao` ocorre quando ha tendencia mensal favoravel, forca relativa mensal positiva ou timing de reversao/oportunidade, mas nao passou como candidata limpa: `src/main.py:_preliminary_adjusted_decision:1337-1338`.
- Se qualidade fundamentalista e fraca ou classificacao setorial e fraca/critica, o ativo cai em `descartar_fundamentalista`; caso contrario, cai em `descartar_tecnico`: `src/main.py:_preliminary_adjusted_decision:1339-1341`.
- A lista de candidatas para risco e limitada por `strategy.pre_risk_candidates` e inclui apenas `aprovada_para_risco` e `moderada_para_risco`, ordenadas por `retorno_ytd` e `nota preliminar`: `src/main.py:select_pre_risk_candidates:1873-1879`, `config/settings.yaml:strategy:12-14`.

### 5. Veto fundamental

- A deterioracao fundamental tecnica basica retorna `True` se ROE, ROIC ou margem bruta forem negativos; tambem retorna `True` se P/L atual for menor ou igual a zero: `src/main.py:_fundamental_deterioration:676-681`.
- A qualidade fundamentalista absoluta/relativa calcula `critical_count` para ROE negativo, ROIC negativo, margem liquida negativa, P/L negativo, alavancagem elevada com baixa rentabilidade e crescimento negativo como alerta: `src/main.py:_fundamental_quality_fields:1240-1272`.
- `fundamento_bloqueante` fica verdadeiro se houver dois ou mais criticos, ROE < -5%, margem liquida < -5%, ou P/L negativo com pelo menos um critico: `src/main.py:_fundamental_quality_fields:1286-1307`.
- No funil preliminar, `fundamento_bloqueante=True` descarta fundamentalmente: `src/main.py:_preliminary_adjusted_decision:1316-1317`.
- Na passagem para otimizacao, fundamento bloqueante adiciona `bloqueio_por_fundamento_bloqueante`: `src/main.py:optimization_block_fields:1591-1593`.
- [interpretacao] A frase “ROE<0/margem<0/P-L<0” e parcialmente verdadeira como protecao de deterioracao: o codigo usa ROE/ROIC/margem bruta/P-L<=0 em `_fundamental_deterioration`, e usa ROE/margem liquida/P-L em regras sombra de deterioracao real; em producao o veto final `fundamento_bloqueante` exige combinacoes/limiares, nao todo valor negativo isolado. Referencias: `src/main.py:_fundamental_deterioration:676-681`, `src/main.py:_fundamental_quality_fields:1258-1287`, `scripts/shadow_simulacao.py:is_real_deterioration:1324-1325`.

### 6. Otimizacao e formacao de pesos em producao

- A configuracao de producao usa `candidate_counts: [5,6,8,10]`, peso minimo 5%, peso maximo 20%, maximo 2 ativos por setor, setor preferencial 30%, tolerado 35%, excepcional 40%, e objetivo declarado `minimize_portfolio_cv`: `config/settings.yaml:portfolio:94-134`.
- `_portfolio_config` le esses limites e outros caps de watchlist, beta/correlacao, reversao e blocos: `src/optimizer.py:_portfolio_config:30-69`.
- O pre-check rejeita combinacoes quando soma dos caps individuais nao fecha 100%, quando numero de ativos viola peso minimo, quando setor tem mais ativos que `max_assets_per_sector`, quando setores sao insuficientes para limite setorial, quando reversoes/watchlist excedem limites, ou quando blocos de risco duplicados violam regras: `src/optimizer.py:_combo_precheck_errors:159-209`.
- Blocos especiais incluem PETR3/PETR4 como PETROBRAS, GGBR/GOAU como GERDAU_GOAU, CPLE3/CPLE6, ITAU, BRADESCO, ELETROBRAS, VALE/BRAP e Santander: `src/optimizer.py:_risk_block_for_ticker:274-300`.
- Caps individuais partem do `max_weight`, reduzem watchlist flexivel, beta/correlacao baixos em mercado favoravel, caps por beta alto/timing/turnaround e timing tardio/com alerta: `src/optimizer.py:_asset_weight_caps:331-358`.
- A viabilidade inicial de pesos e resolvida por `linprog` com soma=1 e bounds entre peso minimo e caps individuais: `src/optimizer.py:_linear_feasible_weights:527-538`.
- A funcao objetivo de producao em `_optimize_subset` minimiza `risk/ret` e retorna custo alto se retorno esperado <=0: `src/optimizer.py:_optimize_subset:822-827`.
- A otimizacao usa SLSQP, bounds de peso minimo/cap individual e constraints de setor/bloco/reversao/watchlist: `src/optimizer.py:_optimize_subset:829-837`, `src/optimizer.py:_constraints_for_slsqp:457-470`.
- Se SLSQP falhar, o codigo usa o ponto factivel do `linprog` como fallback; depois valida pesos: `src/optimizer.py:_optimize_subset:838-849`.
- A validacao exige soma 100%, peso minimo, peso maximo global, peso abaixo do cap por regime/watchlist, limites setoriais/bloco, reversao e watchlist: `src/optimizer.py:_validate_weights:541-569`.
- Metricas da carteira calculam retorno por `weights . mean_returns`, risco por `sqrt(w @ cov @ w.T)`, beta por media ponderada dos betas, CV como risco/retorno se retorno positivo e Sharpe diario: `src/risk_analysis.py:portfolio_return/portfolio_risk/portfolio_beta/sharpe_ratio:65-80`, `src/optimizer.py:_portfolio_metrics:677-735`.
- Retorno mensal/anual esperado usa juros compostos sobre o retorno diario, e risco mensal/anual usa raiz do tempo: `src/optimizer.py:_compound_return/_scale_risk:18-27`, `src/optimizer.py:_portfolio_metrics:680-684`.
- A escolha final prioriza carteiras elegiveis por regime/setor/bloco, depois aderentes, depois validas; pode preferir 6/8 acoes por diversificacao se CV estiver dentro da tolerancia; o ranking ordena por aderencia, setor, bloco, peso flexivel, concentracao, diversificacao, CV, Sharpe, beta e correlacao: `src/optimizer.py:_rank_key:877-904`, `src/optimizer.py:_choose_final_portfolio:906-925`.

### 7. Regime de mercado

- A classificacao geral usa a proporcao de ativos com tendencia favoravel: >40% = mercado favoravel, 20%-40% = mercado seletivo, abaixo de 20% = mercado fraco/desfavoravel: `src/main.py:_market_classification:1762-1768`.
- O diagnostico de mercado calcula snapshot do IBOV com retorno mensal/YTD, MM9/MM21/MM50/MM100, tendencia, RSI e Bollinger: `src/main.py:build_market_diagnosis:1844-1856`.
- O subtipo de mercado favoravel usa RSI do IBOV, Bollinger do IBOV e percentual de ativos positivos no mes; pode classificar esticado, cansado, limpo, indefinido ou nao aplicavel: `src/main.py:classify_favorable_market_subtype:226-280`.
- A configuracao de regime usa `rsi_ibov_esticado=75`, `rsi_ibov_cansado=70`, amplitude positiva cansada 50% e bloqueio de forca relativa fraca em mercado favoravel esticado: `config/settings.yaml:market_regime:155-163`.
- Na otimizacao, aderencia ao regime penaliza beta/correlacao baixos em mercado favoravel, beta/correlacao altos em mercado fraco, watchlist flexivel acima de limites e carteira minima de 5 em mercado favoravel: `src/optimizer.py:_regime_adherence:572-651`.
- Em mercado favoravel, a carteira pode ser marcada incompatível se score de aderencia ficar abaixo do minimo e beta/correlacao ficarem abaixo dos minimos configurados: `src/optimizer.py:_regime_minimum_status:235-266`, `config/settings.yaml:portfolio:98-105`.

### 8. Quando o robo nao forma carteira

- Se nenhuma cotacao for coletada, `main()` interrompe com erro: `src/main.py:main:2477-2478`.
- Antes da otimizacao, apenas ativos com `liberado_para_otimizacao=True` seguem para `score_assets`; bloqueios sao calculados em `optimization_block_fields`: `src/main.py:main:2574-2582`.
- O bloqueio para otimizacao ocorre por status nao aprovado/moderado, categoria nao elegivel, retorno medio negativo, CV hard filter, fundamento bloqueante, dados insuficientes, watchlist/timing, sobrecompra, forca relativa fraca, regime e qualidade minima em mercado fraco: `src/main.py:optimization_block_fields:1574-1669`.
- Se `weak_market` e modo seletivo estiverem ativos, uma carteira ja montada pode ser zerada e receber status `sem_carteira_recomendada` se violar regras seletivas: `src/main.py:main:2584-2591`.
- No diagnostico, se `carteira_valida` for falso, a causa principal e classificada como mercado desfavoravel/poucos ativos, ativos liberados insuficientes ou restricoes da otimizacao: `src/main.py:build_market_diagnosis:1862-1870`.
- Em tamanho livre consolidado de sombra, menos de 5 acoes aprovadas retorna carteira invalida/ativos insuficientes, mas isso nao e caminho de producao normal: `scripts/shadow_consolidada_6meses.py:consolidated_build_free_size_portfolio:99-113`.

## Parte 2 - Modo sombra

### Flags shadow.* existentes

- `shadow.enable_partial_portfolio=false`: habilita carteira parcial no script sombra; parametros de piso/teto investido estao em 40%-70%: `config/settings.yaml:shadow:182-185`, `scripts/shadow_simulacao.py:partial_settings:1136-1142`.
- `shadow.enable_objetivo_retorno=false`: habilita sinais V1/V2/V3 e objetivo retorno/CV no caminho sombra; variante e lambdas estao em settings: `config/settings.yaml:shadow:186-189`, `scripts/shadow_simulacao.py:add_objetivo_retorno_signals:681-694`, `scripts/shadow_simulacao.py:_optimize_subset_beta_target:1051-1065`.
- `shadow.enable_beta_target=false`: habilita beta-alvo por regime no caminho sombra; alvos por regime/subtipo estao configurados entre 0,70 e 1,15: `config/settings.yaml:shadow:190-212`, `scripts/shadow_simulacao.py:beta_target_profile:700-735`.
- `shadow.enable_composicao_ampliada=false`: altera candidate_counts para incluir 12/15, Top N e caps por qualidade no caminho sombra: `config/settings.yaml:shadow:213-221`, `scripts/shadow_simulacao.py:prepare_settings:1283-1308`.
- `shadow.enable_carteira_tamanho_livre=false`: habilita carteira de tamanho livre; parametros de teto 25%, minimo 5 e piso de sinal 0,01 estao em settings: `config/settings.yaml:shadow:222`, `config/settings.yaml:shadow:237-239`, `scripts/shadow_consolidada_6meses.py:consolidated_build_free_size_portfolio:99-190`.
- `shadow.enable_sinal_defensivo_quedas=false`: habilita teste de sinal defensivo em queda; os setores defensivos configurados sao energia eletrica, agua/saneamento, bebidas, telecom e saude: `config/settings.yaml:shadow:223`, `config/settings.yaml:shadow:228-231`, `scripts/shadow_simulacao.py:parse_args:2497-2500`.
- `shadow.enable_sinal_reversao_estrito=false`: habilita reversao estrita em quedas com RSI max 40, retorno 4m max 0 e distancia Bollinger 0,10: `config/settings.yaml:shadow:224-227`, `scripts/shadow_simulacao.py:parse_args:2499-2500`.
- `shadow.enable_beta_regime_tamanho_livre=false`: habilita tamanho livre com beta por regime em lambdas SUAVE/MEDIO/FORTE: `config/settings.yaml:shadow:232-236`, `scripts/shadow_simulacao.py:parse_args:2497-2498`.
- `shadow.forward_test` nao esta declarado em `settings.yaml`; o script de forward o injeta em memoria (`base_settings.setdefault("shadow", {})["forward_test"] = True`): `scripts/forward_test.py:main:445-447`.

### O que muda na configuracao consolidada de forward-test

- [interpretacao] O modo sombra nao e acionado por `src/main.py`; ele e implementado por scripts que reaproveitam funcoes de producao e fazem monkeypatch temporario. A producao permanece separada porque `main()` nao le flags `shadow.*` e `forward_test.py` verifica que `src/main.py`, `src/optimizer.py` e `src/scoring.py` nao contem `forward_test`: `scripts/forward_test.py:production_intact_check:89-105`, `src/main.py:main:2457-2582`.
- O forward gera uma base mensal usando `main.main()`, mas monkeypatcha `load_settings`, `fetch_yfinance_prices` e `fetch_index_prices` apenas durante a chamada, cortando as series em `selection_cutoff`: `scripts/forward_test.py:generate_base_workbook:109-160`.
- O forward roda a carteira chamando o caminho sombra `run_free_size_for_month`, substituindo temporariamente `build_free_size_portfolio` pela versao consolidada, `technical_veto_to_penalty_in_opportunity` pela D3 estendida e `load_candidate_input` por loader enriquecido: `scripts/forward_test.py:run_forward_portfolio:189-205`.
- O teste-ancora de forward roda junho antes de formar a carteira e aborta se a ancora falhar: `scripts/forward_test.py:main:421-434`, `scripts/forward_test.py:anchor_june:211-242`.
- Tamanho livre consolidado: seleciona pool por `selected_free_size_pool`, exige minimo de acoes, pondera por sinal V3 ajustado por beta, aplica teto individual, calcula retorno/risco/CV/beta/Sharpe e retorna pesos sem SLSQP: `scripts/shadow_consolidada_6meses.py:consolidated_build_free_size_portfolio:99-190`.
- O modulador de beta multiplica o sinal base por `1/(1+lambda_beta*abs(beta-beta_target))`: `scripts/shadow_consolidada_6meses.py:consolidated_beta_adjusted_signal:86-96`.
- O sinal V3 e a media de z-scores de nota_final e forca_relativa_score, normalizada por min-max: `scripts/shadow_simulacao.py:add_objetivo_retorno_signals:681-694`.
- O beta-alvo por regime e definido lendo a aba `Regime Mercado`: fraco/desfavoravel, favoravel_oportunidade, favoravel_amplo, favoravel_estreitando, cansado ou fallback; depois busca `target/min/max` em settings: `scripts/shadow_simulacao.py:beta_target_profile:700-735`, `config/settings.yaml:shadow:192-212`.
- A D3 estendida so atua quando o sinal ativo e V3_MOMENTUM (`subtipo_queda == alta`) e subtipo beta alvo esta no conjunto permitido; entao chama a D3 original fingindo `favoravel_oportunidade` para transformar vetos tecnicos em penalizacao: `scripts/shadow_consolidada_6meses.py:make_extended_d3:395-420`.
- As correcoes sombra estruturais incluem alerta de realizacao seletivo por 3 de 5 sinais, retorno medio negativo virando penalizacao sem deterioracao real, e cap de 10% para beta<0,30 e correlacao<0,20 em mercado favoravel: `scripts/shadow_simulacao.py:selective_realization_alert:1328-1346`, `scripts/shadow_simulacao.py:apply_shadow_fixes:1360-1407`.
- O script geral sombra sempre roda teste-ancora com `shadow_fixes=False`; se a ancora nao passar, nao executa as correcoes: `scripts/shadow_simulacao.py:main:2520-2544`.

## Parte 3 - Mapa de decisao

### Fluxo de producao

1. `main()` carrega settings e referencia mensal: `src/main.py:main:2457-2461`.
2. `load_universe()` define os tickers pelo modo configurado: `src/main.py:main:2462-2464`, `src/universe_loader.py:load_universe:216-254`.
3. yfinance coleta precos dos ativos e indices; Fundamentus coleta fundamentos: `src/main.py:main:2466-2477`.
4. O contexto temporal corta precos e indices ate `data_limite_dados_selecao`, evitando dados posteriores a formacao: `src/main.py:main:2480-2489`.
5. O robo calcula tecnicos semanais, retornos acumulados, indices setoriais, junta ativos+tecnicos+retornos+fundamentos e calcula forca relativa: `src/main.py:main:2491-2503`.
6. `build_preliminary()` enriquece a aba preliminar e classifica timing/fundamentos/decisao preliminar: `src/main.py:main:2504-2510`, `src/main.py:_preliminary_adjusted_decision:1312-1341`.
7. Diagnostico de mercado e subtipo sao calculados e gravados em settings runtime: `src/main.py:main:2511-2519`.
8. Watchlist/timing/regime esticado sao aplicados antes da selecao de risco: `src/main.py:main:2521-2527`.
9. As candidatas para risco sao ordenadas e limitadas por `pre_risk_candidates`: `src/main.py:select_pre_risk_candidates:1873-1879`.
10. A janela de risco usa precos ate a data limite, log-retornos diarios, retorno IBOV, matriz de correlacao/covariancia e metricas individuais: `src/main.py:main:2529-2535`, `src/risk_analysis.py:log_returns/risk_metrics:7-54`.
11. Elegibilidade, alertas, bloqueios e score sao calculados; apenas `liberado_para_otimizacao=True` entra no `score_assets`: `src/main.py:main:2550-2580`.
12. `optimize_weights()` testa composicoes, minimiza CV da carteira, valida restricoes e escolhe a melhor carteira: `src/main.py:main:2582-2583`, `src/optimizer.py:_optimize_subset:787-874`, `src/optimizer.py:_choose_final_portfolio:906-925`.
13. Em mercado fraco, regras seletivas podem invalidar uma carteira ja montada e transformar o resultado em `sem_carteira_recomendada`: `src/main.py:main:2584-2591`.
14. O Excel/PDF recebem otimizacao, auditorias, metricas, status e campos de data-base: `src/main.py:main:2600-2720`.

### Onde uma acao pode ser eliminada ou apenas penalizada em producao

- Eliminada por dados insuficientes ou preco ausente no funil preliminar: `src/main.py:_preliminary_adjusted_decision:1313-1315`.
- Eliminada por fundamento bloqueante no funil preliminar: `src/main.py:_preliminary_adjusted_decision:1316-1317`.
- Desviada para watchlist por timing/confirmacao insuficiente: `src/main.py:_preliminary_adjusted_decision:1328-1336`, `src/main.py:build_watchlist:1771-1792`.
- Eliminada antes da otimizacao por status/categoria, retorno medio negativo, fundamento, dados, watchlist, sobrecompra, forca relativa, beta/correlacao e regras de mercado fraco: `src/main.py:optimization_block_fields:1574-1669`.
- Penalizada sem bloqueio por beta alto, correlacao alta, CV alto quando `cv_as_hard_filter=false`, watchlist flexivel e timing tardio/com alerta: `src/main.py:optimization_alerts_and_penalties:1523-1571`.
- Rejeitada dentro da composicao por peso/caps, setor, reversao, watchlist flexivel ou bloco de risco: `src/optimizer.py:_combo_precheck_errors:159-209`, `src/optimizer.py:_validate_weights:541-569`.

### Fluxo sombra/forward consolidado

1. O script de forward verifica que flags shadow default false e que producao nao contem `forward_test`: `scripts/forward_test.py:production_intact_check:89-105`.
2. Ele gera/reusa a base mensal de producao, mas corta series no fechamento anterior ao mes de formacao: `scripts/forward_test.py:generate_base_workbook:109-160`, `scripts/forward_test.py:main:436-443`.
3. Roda ancora de junho antes de formar o mes novo: `scripts/forward_test.py:main:430-434`.
4. Ativa `shadow.forward_test` em memoria e executa `run_forward_portfolio`: `scripts/forward_test.py:main:445-447`.
5. O forward substitui temporariamente o construtor de carteira por tamanho livre consolidado, aplica D3 estendida e loader enriquecido: `scripts/forward_test.py:run_forward_portfolio:189-205`.
6. O tamanho livre escolhe todas as acoes aprovadas pelo crivo, exige minimo de 5, calcula sinal V3 ajustado por beta-alvo, aplica teto individual de 25% e redistribui via `capped_proportional_weights`: `scripts/shadow_consolidada_6meses.py:consolidated_build_free_size_portfolio:99-190`, `config/settings.yaml:shadow:237-239`.
7. [interpretacao] No modo consolidado, o CV nao e usado como alocador principal; ele permanece como metrica de risco calculada e como parte de testes sombra anteriores. O codigo consolidado calcula peso por sinal V3 ajustado por beta e depois calcula CV: `scripts/shadow_consolidada_6meses.py:consolidated_build_free_size_portfolio:116-144`.
