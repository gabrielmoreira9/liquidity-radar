# Auditoria quantitativa e técnica — anterior ao Risk Engine V2

Auditoria local dos 214.464 swaps SIMPLE_SWAP de 30/06 a 03/07/2026.
Nenhuma consulta externa foi executada. Históricos, classificações, checkpoints,
consolidado, outputs estatísticos anteriores e módulos de risco/backtest foram
preservados. Não houve remoção de outliers, reclassificação ou tuning.

Resultados completos da execução final em
`data/processed/audit/20261008T150552576490Z/`. A execução inicial da auditoria
também foi preservada em `20261008T150138264167Z/`. A comparação antes/depois
usa cópias byte a byte dos sete CSVs estatísticos em `baseline/`, junto com
manifestos SHA-256, tamanho e mtime. O comando `python -m src.audit_pipeline`
produz uma nova versão, sem sobrescrever arquivos existentes.

## 1. Problemas confirmados e interpretação dos extremos

### Quantidades, unidades e preços

`data.py:parse_transaction` lê **uiAmountString**, já em unidades humanas,
com Decimal, soma os saldos por mint e owner do pool e calcula post menos pre.
Não há evidência no código de aplicar decimais duas vezes ou de subtrair grandes
saldos em float. Apenas os deltas finais são convertidos para float.
`classify_swap` calcula `abs(delta_USDC)/abs(delta_SOL)` e usa `abs(delta_USDC)`
como volume. Os tokens são USDC e WSOL (não saldo nativo de SOL do pagador).

Os dois caches locais observam 6 decimais para USDC e 9 para WSOL. Eles cobrem
somente os primeiros segundos/minutos de 30/06; **nenhuma das 224 transações
selecionadas está nos caches**. Logo, a precisão nominal é corroborada localmente,
mas quantidades brutas, instruções e transferências dos extremos não foram
verificadas por payload RPC. Os números abaixo são derivados dos CSVs e devem
ser interpretados nessa condição de evidência.

| Caso UTC | Signature | Delta USDC | Delta SOL | Preço USDC/SOL | Volume USDC |
| --- | --- | ---: | ---: | ---: | ---: |
| Mínimo, 30/06 20:15:20 | `242sVgqDBqyGZ8iBxQyWkRtgQ1eQtUUefBpQk5MTNXtaVfJzyRAz1UMRna76wALc8azyo6CekywM7RPoWzPCPJv2` | -0,000002 | +0,000000031 | 64,516129 | 0,000002 |
| Máximo, 01/07 15:07:07 | `5djgtBo8YpLgUuk84Ps6ZJgvzpLd1V1VyWFqWZmBcjevhzsCUvM9kjT3vz8fGJsybAed3wM4vSdweiNoYVtRARno` | +0,000002 | -0,000000012 | 166,666667 | 0,000002 |
| Janela problemática, 02/07 08:50:40 | `fKwWLEHC729LkfZbZAian2T4m7fLkgh1oAZZeAM4w9mQaYJ7YcunkvMxMSnvjyJ2JJ9XFdLaaBk1uG4wk8L6xYn` | -0,000003 | +0,000000040 | 75,000000 | 0,000003 |
| Janela problemática, 02/07 08:50:47 | `5iadUbJkxUqFTcYYpsxnfj42ThzV55H1pPDDtYb2nEN4S88oVkNGyeNkWy4psGDCt6Mb56fXnxuDRwPDpyXmQDSH` | +0,000003 | -0,000000025 | 120,000000 | 0,000003 |

O mínimo é a razão entre **2 unidades mínimas USDC e 31 unidades mínimas WSOL**.
O máximo é 2/12 após conversão de escala. Uma unidade adicional de USDC equivale
a 50% da perna USDC desses trades; uma unidade WSOL equivale a 3,23% e 8,33% das
respectivas pernas SOL. Assim, um preço extremamente diferente do mercado pode
ser uma razão legítima de valores quantizados, sem representar preço marginal
executável para uma posição grande. Rounding de transferência/fee é hipótese
plausível, não causa comprovada sem instruções. A igualdade com a fórmula foi
confirmada; a correspondência dessa razão às pernas exclusivas do swap não.

Todos os empates nos extremos absolutos, os cinco menores/maiores preços e
todos os swaps nas dez maiores volatilidades estão em `extreme_swaps.csv`:
224 signatures únicas, com timestamps, preços, deltas, volume, unidades mínimas,
resolução relativa, classificação e diagnóstico de influência na volatilidade.

### Efeito na volatilidade

A volatilidade é desvio padrão **amostral dos log-retornos entre trades** da
janela, sem ponderação por volume, sem anualização e sem retornos entre janelas.
Isso faz trades de 2 micro-USDC terem o mesmo papel na sequência de preços que
trades de milhares de USDC. A volatilidade 0,36629593 da janela 02/07 08:50 não
é a maior dos quatro dias: o máximo é **0,48405635 em 30/06 às 23:40**.

| Janela UTC | Volatilidade original | Trades de até 10 unidades mínimas USDC | Volume desses trades | Fração do volume | Fração da energia centrada dos retornos adjacentes |
| --- | ---: | ---: | ---: | ---: | ---: |
| 30/06 23:40 | 0,48405635 | 6 | 0,000018 USDC | 2,9443e-8 | 99,999978% |
| 02/07 08:50 | 0,36629593 | 2 | 0,000006 USDC | 6,0294e-7 | 99,943915% |
| 02/07 16:59 | 0,36346809 | 2 | 0,000004 USDC | 5,7379e-11 | 99,999923% |

Energia centrada significa `(log_return - média_dos_log_returns)^2`; o conjunto
de retornos adjacentes é contado sem duplicação nessa tabela. Isso mede influência
matemática, não causalidade econômica. A seleção de até 10 unidades mínimas
USDC corresponde a resolução de uma unidade >=10% da perna USDC. É **apenas
um recorte de diagnóstico**, não regra de limpeza, estatística calibrada ou
critério para invalidar swaps. Nenhum deles foi retirado da produção.

`extreme_swaps.csv` também mostra volatilidade sem cada swap individual, como
contrafactual explicitamente marcado `DIAGNOSTIC_ONLY`. A remoção de um swap
pode aumentar o desvio padrão, inclusive pela mudança dos retornos e do tamanho
amostral. Esses resultados não substituem a volatilidade original.

### Exclusividade das pernas e ordem

Problema metodológico confirmado no contrato dos dados: o extrator soma **todas
as contas** do owner do pool para cada mint, sem reconciliar cada fluxo com a CPI
do swap. A classificação SIMPLE_SWAP verifica presença de swap alvo e ausência
de operações de gerenciamento reconhecidas que afetam os vaults; não garante
um único swap alvo nem ausência de transferências auxiliares, fees ou múltiplas
interações com o pool. A suspeita de contaminação em signatures específicas
continua **não confirmada**. Não houve reclassificação.

Os CSVs preservam timestamps em segundos, mas não slot/transactionIndex. A ordem
estável da entrada é reproduzível, porém não prova ordem canônica das transações
com timestamps iguais. `window_sensitivity_DIAGNOSTIC_ONLY.csv` compara ordem
original, reversão dentro dos empates e ordenação por signature, para as dez
janelas extremas. Nenhuma dessas ordens alternativas foi promovida a produção.
Há 180.042 swaps compartilhando o segundo de timestamp com outra transação.
Em 30/06 23:40, ordenar empates por signature muda a volatilidade de 0,484056
para 0,442303; em 02/07 08:50, reverter empates muda 0,366296 para 0,349086.
Isso confirma sensibilidade à ordem, mas não identifica qual ordem é correta.

### Consultas propostas, ainda não executadas

`rpc_requests_NOT_EXECUTED.json` lista **45 signatures** prioritárias sem payload
local e suas consultas exatas `getTransaction`, `encoding=jsonParsed`,
`commitment=finalized`, `maxSupportedTransactionVersion=0`. A seleção prioriza
as pernas USDC cuja resolução unitária >=10%; não é coleta massiva.

Para cada resposta, seria necessário reconciliar:

1. `preTokenBalances/postTokenBalances`, `amount`, `decimals`, `uiAmountString`
   e accountIndex dos vaults, discriminando cada conta em vez de apenas mint.
2. Instrução swap/swap_v2, CPI e stackHeight, transferências SPL/WSOL efetivas,
   fee/rounding e movimentos extras nos mesmos vaults.
3. Sinais e somas dessas transferências versus os deltas registrados no CSV.
4. Múltiplos swaps alvo na mesma transação; slot e posição no bloco para empates.

Se a resposta não fornecer transactionIndex, a consulta adicional seria
`getBlock(slot, {encoding: "json", transactionDetails: "signatures",
rewards: false, maxSupportedTransactionVersion: 0})`, limitada aos slots dessas
signatures, para reconstruir a ordem. Isso é um plano de verificação, não uma
chamada realizada. Nenhuma API key consta nos arquivos da auditoria.

## 2. Modelo de Exit Cost

Baseline preservado:

`estimated_exit_cost_pct = 100 × sigma × sqrt(Q / V)`

Q e V são USDC; Q/V é adimensional e `participation_rate` é razão, não percentual.
Sigma é desvio padrão de log-retornos, também adimensional. A multiplicação
por 100 converte fração em unidades percentuais. A fórmula é matematicamente
consistente para V>0 e sigma finita. Ela cresce linearmente com sigma, com sqrt(Q)
e com 1/sqrt(V). Para V tendendo a zero, diverge. Para V=0, participação e custo
são NaN, não infinito ou zero. Sigma ausente também produz NaN.

Limitações econômicas confirmadas:

- V é **turnover observado em um minuto**, não profundidade executável, reservas
  da curva AMM ou volume disponível para uma ordem nova. Q>V evidencia extrapolação
  em relação ao turnover, não prova que o pool não executaria a posição.
- Sigma é volatilidade por transição de trade; o modelo usa turnover por minuto.
  A consistência dimensional não valida essa combinação de escalas. Trade count,
  dependência microestrutural, bid/ask/fees e granularidade afetam sigma.
- Coeficiente 1 e o horizonte implícito não foram calibrados como cotação de
  execução. Não se pode inferir capacidade real de execução a partir do baseline.
- Custo >=100% significa impacto >=1. Como perda relativa em uma venda spot sem
  taxas adicionais e com receita não negativa, ultrapassa o domínio econômico
  dessa interpretação. É uma saída da aproximação extrapolada, não slippage
  observado, perda realizada nem quantidade a ser automaticamente limitada.

O máximo permanece **23.223,215471%** para 1M em STRESS_75, 02/07 08:50:
V=2,487823 USDC; Q/V aproximadamente 401.958; sigma=0,36629593. Há 230 custos
>100% e **106.676 participações >1** entre as 138.240 linhas (incluindo linhas
com sigma insuficiente). A questão de extrapolação é muito mais ampla que os
230 custos extremos.

### Campos e critérios explícitos

`exit_cost_stress_audited_1m.csv` preserva todas as colunas numéricas originais e
adiciona flags independentes. `model_reliability_status` é diagnóstico de domínio,
não probabilidade de acerto ou confiança calibrada.

| Campo/status | Critério |
| --- | --- |
| `estimated_exit_cost_pct` | Fórmula original, sem clipping ou exclusão |
| `participation_rate` | Q/V quando V>0; NaN caso contrário |
| `insufficient_data_flag` | Volume ou sigma ausente; não inclui apenas Q>V |
| `insufficient_liquidity_flag` | V<=0 ou Q/V>1; significa insuficiência de **turnover observado** |
| `extrapolation_warning` | Q/V>1 ou impacto>=1 (custo>=100%) |
| `INSUFFICIENT_DATA_AND_LIQUIDITY` | Dado ausente e V<=0 |
| `INSUFFICIENT_DATA` | Volume/sigma ausente, após a condição anterior |
| `INSUFFICIENT_LIQUIDITY` | V<=0 com sigma presente |
| `EXTREME_EXTRAPOLATION` | Custo>=100%, quando dados permitem calculá-lo |
| `EXTRAPOLATION` | Q/V>1, quando dados permitem calculá-lo |
| `BASELINE_PROXY_ONLY` | Demais casos: proxy não calibrado, sem garantia econômica |

A ordem da tabela de statuses define a prioridade. As flags são independentes:
sigma ausente com Q>V mantém insuficiência de dados e warning simultaneamente.
Sigma zero e Q>V dá custo zero e continua sinalizando extrapolação. `extrapolation_reason`
distingue fronteira de impacto, fronteira de turnover ou ambas. Os limites 1 e
100% são fronteiras naturais das respectivas interpretações, não filtros para
embelezar estatísticas. Nenhum status foi chamado de "reliable execution".

Não substituí a fórmula. Uma alternativa futura defensável para slippage exigiria
simular a curva/estado da AMM na mesma altura de bloco (reservas, ticks/liquidez,
fees e direção) e comparar com execuções/cotações locais reproduzíveis. Uma
volatilidade baseada em preço marginal verificado ou medidas que explicitem
resolução/ponderação seria outro estudo comparativo. Faltam esses dados para
eleger um modelo superior; alterar coeficiente ou limitar o custo agora mascararia
o problema.

## 3. Integridade temporal e correções implementadas

As features globais e `liquidity_multiple = Q / mediana_global(volume)` usam todos
os quatro dias. São corretas como **descrição do período**, mas não como dados
conhecidos no passado. Permanecem separadas e seus arquivos não foram alterados.

Novos contratos causais:

- `liquidity_features_causal_1m.csv`: somente medições e colunas explicitamente
  `_causal_`; não copia percentis/robust z globais. Inclui percentis empíricos,
  mediana, p05/p25/p75/p95/p99, MAD, robust z e contagem válida anterior por
  métrica, incluindo abs(flow_imbalance).
- `exit_cost_stress_causal_1m.csv`: remove mediana e liquidity_multiple globais;
  adiciona `median_volume_usdc_causal`, `liquidity_multiple_causal`, percentile/p95
  e contagem anterior de exit cost por **posição e cenário**. NORMAL é comparado
  exclusivamente contra custos NORMAL anteriores da mesma posição.
- Históricos expansivos, **estritamente anteriores à janela atual**, atravessam
  dias sem reset. Mínimo inicial de **120 observações válidas de minutos distintos
  por métrica**. NaN não conta para atingir o mínimo. Trata-se de política de
  informação mínima, não threshold estatisticamente calibrado.
- `timestamp` identifica início da janela; `available_at=timestamp+1min` identifica
  fechamento. Os limiares históricos são conhecidos no início da janela, mas a
  medição atual/percentil atual só pode ser usada depois do fechamento.
  `historical_reference_before` explicita o corte estrito.
- `available_at` é o **mínimo teórico pela convenção das barras**. Não é horário
  medido de ingestão ou finalização Solana. Não existem esses horários no CSV;
  em produção deve-se respeitar latência/finality e disponibilidade efetiva.
- MAD zero deixa robust z NaN; mediana histórica zero deixa liquidity_multiple
  causal NaN. Não se preenche volatilidade ausente com zero.

Há 5.451 percentis causais válidos de volatilidade e de exit cost NORMAL **por
posição**, contra 5.571 valores físicos calculáveis. O primeiro percentil causal
de volatilidade/exit cost NORMAL está disponível em **30/06 02:03 UTC**. A
diferença de 120 observações é warm-up histórico, não descarte de swaps.

Para o futuro Risk Engine, selecionar explicitamente os outputs causais, usar
`available_at` como horário mínimo de decisão, conferir counts/warm-up e carregar
as flags de domínio do modelo. Percentis/medianas globais não podem entrar nos
indicadores retrospectivos. Não foi criado score, regime ou Risk Engine V2.
O Risk Engine V1 e o backtest não foram alterados nem executados; esta auditoria
não certifica a causalidade ou a atualização dos outputs existentes desses módulos.

## 4. Invariantes e comparação antes/depois

Validação mais estrita no loader: preço deve corresponder às pernas; volume deve
ser abs(delta_USDC); pernas não podem ser zero e devem ter sinais coerentes com
SOL_IN/SOL_OUT. Falhas geram erro, sem limpeza silenciosa. O consolidado corresponde
exatamente às signatures SIMPLE_SWAP e linhas históricas de cada dia.

| Métrica | Antes | Depois da auditoria |
| --- | ---: | ---: |
| Swaps | 214.464 | 214.464 |
| Volume USDC | 559.136.789,004487 | 559.136.789,004487 |
| Fluxo líquido USDC | +5.646.214,418481 | +5.646.214,418481 |
| Janelas 1m / 5m / 15m | 5.760 / 1.152 / 384 | 5.760 / 1.152 / 384 |
| Preço mínimo / máximo | 64,516129 / 166,666667 | Inalterados |
| Volatilidade máxima | 0,48405635 | Inalterada |
| Custos >100% / custo máximo | 230 / 23.223,215471% | Inalterados |
| Features globais | Descritivas, não causais | Preservadas separadamente |
| Referências causais | Ausentes nesses módulos | Novos outputs com warm-up e disponibilidade |
| Diagnóstico de domínio do stress | Sem flags explícitas | Flags e statuses, sem alterar custos |

Os arrays recalculados de agregações e das colunas originais do stress coincidem
com os CSVs anteriores dentro da precisão numérica (rtol/atol 1e-12). Os arquivos
originais são verificados por hash e mtime, não só por tolerância.

| Dia | Swaps | Volume USDC | Fluxo líquido USDC |
| --- | ---: | ---: | ---: |
| 30/06 | 52.778 | 133.187.407,944454 | -1.880.573,971732 |
| 01/07 | 68.370 | 180.115.632,981738 | +3.766.168,571688 |
| 02/07 | 55.168 | 153.953.347,145198 | +1.995.143,940190 |
| 03/07 | 38.148 | 91.880.400,933097 | +1.765.475,878335 |

Conservação foi verificada por dia em 1m, 5m e 15m, com erros de ponto flutuante
muito abaixo de 0,000001 USDC. Grade UTC contínua, timestamps únicos/cronológicos,
contagens/volumes direcionais conservados; preços e volumes de origem finitos e
válidos; nenhuma divergência de preço/volume/sign/direção encontrada.

Dos **189 minutos sem volatilidade**: 32 vazios, 62 com um preço e 95 com dois.
São ausências justificadas: exige-se ao menos dois log-retornos para desvio padrão
amostral. Os 32 vazios têm contagens/volume/fluxo zero e preços, volatilidade e
imbalance NaN. Flow imbalance é net_flow/volume quando volume>0 e fica em [-1,1],
permitindo tolerância exclusivamente numérica de 1e-12. Não há infinitos. O
stress tem 4.536 custos NaN (189×24) e 768 participações NaN (32×24), coerentes
com insuficiência de dados, não falhas de divisão ou imputation.

## 5. Testes, entregáveis e prontidão

**31 testes locais passaram; nenhuma falha na execução final.** Cobrem os 16
testes anteriores e 15 novos: dust, preços extremos, deltas incoerentes,
volumes próximos de zero, extrapolação, sigma zero versus ausente, warm-up,
ties/quantis/MAD, exclusão da observação atual, invariância ao acrescentar futuro,
baselines por posição/cenário, fechamento das janelas, correspondência com
históricos e preservação de bytes. Os testes não fazem chamadas Helius.

Entregáveis na versão final:

- `baseline/`: sete CSVs anteriores preservados;
- `extreme_swaps.csv` e `window_sensitivity_DIAGNOSTIC_ONLY.csv`;
- `local_raw_evidence.json` e `rpc_requests_NOT_EXECUTED.json`;
- `conservation_by_day.csv` e `source_correspondence.csv`;
- `liquidity_features_causal_1m.csv`;
- `exit_cost_stress_audited_1m.csv`, `exit_cost_stress_causal_1m.csv`;
- `exit_cost_summary_audited.csv`, `model_reliability_summary.csv`;
- `summary.json`, `manifest_before.json`, `manifest_code.json`, `manifest_outputs.json`.

**Recomendação: pronto para pesquisa causal controlada; ainda não pronto para
tratar o exit cost como custo financeiro confiável ou validar V2 para uso real.**
A integridade numérica e o novo contrato temporal foram testados. Persistem
sensibilidade de preço implícito à quantização, exclusividade das pernas não
comprovada, ordem canônica de empates indisponível, somente quatro dias de dados,
turnover como proxy e modelo de impacto sem validação de execução. O passo
seguinte deve ser a inspeção direcionada das 45 signatures propostas e uma
comparação fundamentada com estado/execução da AMM, antes de definir o tratamento
de qualidade e confiabilidade no V2. Não há justificativa para apagar os extremos
ou ajustar coeficientes para reduzir seus números.

Conferência final dos artefatos reais: os 65 arquivos do manifesto pré-execução
permaneceram idênticos, incluindo a versão inicial da auditoria; os arquivos
protegidos desde o início da tarefa também conservaram hashes e mtimes. Foram
conferidos os manifestos de código/outputs, ausência de infinitos, disponibilidade
no fechamento e referências anteriores em minutos selecionados (123, 1.500,
4.000 e 5.759), além de p95 NORMAL por posição calculado independentemente.
