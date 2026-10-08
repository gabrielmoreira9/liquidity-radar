# Liquidity Risk Engine V2

Implementação independente em `src/risk_v2.py`. Nenhum modelo anterior,
histórico, classificação, checkpoint ou backtest foi alterado. Execução local:

```sh
python -m src.risk_v2
python -m src.risk_v2 --audit-dir data/processed/audit/20261008T150552576490Z
```

O default fixa a versão final da auditoria, evitando mudar silenciosamente a
referência quando surgir outro diretório. Para dados novos, produzir nova
auditoria causal com histórico contínuo anterior e fornecer sua pasta explicitamente.
Os dois CSVs de entrada são `liquidity_features_causal_1m.csv` e
`exit_cost_stress_causal_1m.csv`, verificados contra SHA-256 do manifesto.

## Três indicadores separados

### Current Market Risk

Severidade da janela atual, disponível no fechamento:

- `volatility_risk`: percentil causal da volatilidade;
- `liquidity_risk`: 100 menos o percentil causal do volume;
- `flow_risk`: percentil causal de abs(flow_imbalance);
- `reference_exit_cost_risk`: percentil causal do exit cost **NORMAL para 100k USDC**.

`current_market_risk_score` é a média simples dos quatro componentes. Exige todos
os componentes; não calcula média parcial, não preenche NaN nem introduz pesos.
A referência fixa de 100k é uma escolha de especificação inicial, não calibração;
não muda por posição avaliada. O cenário de referência sempre é NORMAL.
Os scores/regimes e seus drivers são idênticos entre todas as posições e cenários
no mesmo minuto. Essa invariância é testada.

O exit cost de referência é proxy sujeito às limitações da auditoria, mesmo
quando entra no percentil de mercado. `reference_exit_extrapolation_warning`
e `reference_exit_model_reliability_status` mantêm essa ressalva explicitamente.

### Forward Liquidity Risk

`forward_liquidity_risk_score = mean(liquidity_risk, flow_risk)`.
Não contém volatilidade ou exit cost, é independente da posição/cenário e exige
os dois componentes. É um **sinal exploratório** de deterioração futura, não
probabilidade calibrada ou previsão comprovada. Ainda exige avaliação em dados
novos, sem escolher thresholds para melhorar as métricas.

Os dois scores usam os mesmos regimes heurísticos iniciais:
NORMAL <40; WATCH [40,60); STRESSED [60,80); CRITICAL >=80.
Score ausente recebe INDETERMINATE e motivo explícito. Os limites não são
estatisticamente calibrados. Drivers primário/secundário usam maior score entre
componentes disponíveis; empates têm ordem determinística (volatilidade,
liquidez, fluxo, exit de referência; no forward, liquidez antes de fluxo).
Drivers parciais não implicam existência de score agregado válido.

### Position Exit Risk

Seis posições, em cada cenário existente da auditoria: NORMAL, STRESS_25,
STRESS_50 e STRESS_75. Foram preservados todos os cenários para pesquisa, sem
contaminarem o score fixo de mercado ou o forward. A categoria operacional é
a maior severidade acionada por participação **ou** custo:

| Classe | Condição |
| --- | --- |
| LOW | participação <0,5 e custo <0,5% |
| MODERATE | participação >=0,5 ou custo >=0,5% |
| HIGH | participação >=1 ou custo >=1% |
| EXTREME | participação >=2 ou custo >=2% |
| INDETERMINATE | participação ou custo essencial ausente |

As fronteiras são inclusivas e heurísticas. Uma avaliação LOW não indica
confiabilidade de execução. Categoria e `model_reliability_status` são separados.
Os custos/participações atuais não precisam de percentis históricos para
classificação operacional: podem existir durante o warm-up dos scores de
mercado. `liquidity_multiple_causal` só existe quando a mediana histórica positiva
tem 120 observações válidas; sua ausência isolada não impede classificar as
medidas absolutas. Há motivo separado para essa indisponibilidade.

Custos, participação, liquidity_multiple causal, statuses e as três flags
auditadas são propagados. O motor confere a fórmula original e a coerência das
flags, sem alterar valores, eliminar linhas ou recalibrar coeficientes.
Nenhum custo >100% foi limitado ou substituído.

`extrapolation_warning` usa as fronteiras da auditoria (Q/V>1 ou impacto>=1);
a categoria HIGH começa em Q/V>=1. Portanto, exatamente Q/V=1 pode ser HIGH
sem warning de extrapolação. Isso preserva duas definições distintas, sem
modificar flags para fazê-las coincidir.

Q/V compara posição e turnover negociado, **não prova execução inviável**.
O custo é proxy não validado de volatilidade/turnover, não slippage executável
observado. `exit_cost_interpretation` sinaliza explicitamente extrapolação.

## Contrato temporal

`RiskV2Engine.evaluate` recebe somente um minuto de features e suas 24 linhas
de stress. Os arquivos são lidos sequencialmente; os cálculos não recebem linhas
futuras. O motor mantém amostras anteriores ordenadas e confere:

- Percentil = `100 × quantidade de valores anteriores <= atual / quantidade válida anterior`;
- Empates mantêm interpretação da ECDF; volume é invertido só depois do percentil;
- Counts e mediana causal do volume coincidem com histórico estritamente anterior;
- Exit percentil usa histórico separado por posição **e cenário**;
- Mínimo de 120 valores válidos anteriores por componente, sem reset diário;
- A observação atual só entra no estado **depois** de todos os cálculos daquele minuto.

Referências globais/descritivas não são carregadas nos scores. Campos recebidos
que aparentem ser causais, mas não coincidam com o histórico anterior, causam
erro antes de publicar resultados. A inspeção do manifesto inteiro é verificação
de integridade do arquivo; não utiliza dados futuros no cálculo de indicadores.

`timestamp` é início do minuto, `available_at=timestamp+1min` é seu fechamento,
e `historical_reference_before=timestamp` explicita o limite da referência.
Esse horário é mínimo **teórico**, não medição de chegada/finalização Solana.
Um backtest deve usar o fechamento como horário mínimo de decisão, e produção
deve respeitar atraso/finality efetivos. Nada aqui certifica esses horários reais.

## Outputs e rastreabilidade

- `data/processed/liquidity_risk_v2_1m.csv`: 138.240 linhas
  (5.760 minutos × seis posições × quatro cenários).
- `data/processed/risk_v2_summary.csv`: 26 linhas: duas distribuições de mercado/
  forward por minuto único e 24 distribuições operacionais por posição/cenário.
- `data/processed/risk_v2_runs/<UTC_run_id>/`: cópias versionadas dos dois CSVs
  e `metadata.json` com hashes dos inputs/outputs/código e políticas da execução.

Os paths principais apontam para a última execução bem-sucedida; versões
anteriores são preservadas. Inputs e resultados anteriores à implementação não
foram escritos. Contagens/volume/fluxo no detalhe são repetidos por posição e
cenário: **deduplicar timestamp antes de somar métricas de mercado**.

## Resultado nos quatro dias — sem tuning

| Indicador, por minuto único | NORMAL | WATCH | STRESSED | CRITICAL | Indeterminado | Mediana | p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Current Market Risk | 1.830 | 1.923 | 1.285 | 413 | 309 | 48,016883 | 82,388479 |
| Forward Liquidity Risk | 2.032 | 1.388 | 1.529 | 659 | 152 | 51,631609 | 91,205274 |

Mercado tem 5.451 scores válidos e forward 5.608. As ausências abrangem warm-up
e métricas atuais insuficientes; todos os minutos permanecem no dataset.
A primeira disponibilidade é 30/06 às 02:03 UTC para mercado e às 02:01 UTC
para forward. Medianas, p95 e distribuições desse resumo são descritivos dos
resultados do período; não realimentam referências ou thresholds dos indicadores.

Comparação operacional em NORMAL, com 5.760 observações por posição:

| Posição | LOW | MODERATE | HIGH | EXTREME | Indeterminado | Custo mediano (%) | Custo p95 (%) | Participação mediana | Extrapoladas |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100k | 661 | 1.095 | 1.413 | 2.402 | 189 | 0,031622 | 0,544392 | 1,686368 | 3.953 |
| 500k | 24 | 73 | 339 | 5.135 | 189 | 0,070708 | 1,217297 | 8,431839 | 5.630 |
| 1M | 4 | 20 | 73 | 5.474 | 189 | 0,099996 | 1,721518 | 16,863678 | 5.704 |

Todos os cenários: 106.676 avaliações com warning de extrapolação e 4.536
avaliações operacionais indeterminadas. Elas podem se sobrepor: participação
extrapolada com sigma ausente continua indeterminada. Os 230 custos >100%
permanecem preservados, incluindo máximo 23.223,215471% no STRESS_75 para 1M.
A predominância de EXTREME em posições grandes decorre sobretudo dos thresholds
de participação relativos ao turnover, não demonstra slippage dessa magnitude.

Conservação dos minutos únicos: 214.464 swaps, 559.136.789,004487 USDC de volume
e +5.646.214,418481 USDC de fluxo líquido. Todos os seis tamanhos/quatro cenários
cobrem todos os minutos, sem duplicatas; ausência de infinito e scores [0,100].

## Testes e prontidão para avaliação fora da amostra

47 testes passaram (31 anteriores e 16 de V2). Cobrem limites inclusivos,
histórico mínimo válido, médias completas, inversão do volume, ausência de LOW
artificial, referências fixas, independência do forward, monotonicidade com
posição, propagação exata das flags/custos, datas de disponibilidade, falsificação
de referência causal, conservação e publicação local preservando inputs.
Os testes de causalidade acrescentam futuro e alteram volatilidade/custo de
linhas posteriores, sem mudar o prefixo ou o score forward.

Na primeira execução da suíte, o teste de integração detectou a diferença entre
aliases de paths macOS `/var` e `/private/var` no manifesto. A comparação foi
corrigida normalizando paths com resolve; a suíte final passou. Não foi alteração
de metodologia nem tuning de resultado.

**Tecnicamente pronto para um novo backtest temporal fora da amostra.** Ainda não
é resultado validado fora da amostra: os quatro dias participaram da exploração
e das decisões de desenho. É necessário congelar especificação e avaliar dias
posteriores ainda não usados, com as referências expansivas anteriores mantidas,
sem escolher thresholds/posições para melhorar métricas. O backtest existente
não foi alterado ou executado e não valida automaticamente o V2.

Resultados que ainda exigem validação econômica: preços implícitos afetados por
dust/granularidade; exclusividade das pernas dos swaps; ordem canônica dos
empates; sigma por trade versus turnover por minuto; volume versus profundidade
executável; baseline square-root com coeficiente 1. Os limites operacionais
também são política inicial. Forward continua sinal exploratório, sem
probabilidade calibrada ou evidência nova de capacidade preditiva.

A execução final foi versionada em
`data/processed/risk_v2_runs/20261008T153108969585Z/`.
A conferência dos CSVs reais confirmou igualdade exata de custos, participação,
liquidity_multiple e flags com a auditoria; conservação por dia de swaps,
volume e fluxo; cobertura, cronologia, counts mínimos e disponibilidade.
Hashes/mtimes dos 91 arquivos existentes antes da implementação ficaram intactos.
