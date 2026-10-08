# Contrato HTTP do backend MVP

Base URL local: `http://127.0.0.1:8000`. Swagger `/docs`; contrato `/openapi.json`
e `contracts/openapi.json`. API version 1.0.0. Todos os endpoints são somente GET.
Dados são **HISTORICAL**. Respostas de sucesso têm `{meta, data}`. Falhas têm
`{error: {code, message, details}}`, sem valores da query, paths locais ou traceback.

## Metadados e unidades

`meta.reference_at` é o fechamento escolhido, não o relógio atual. `requested_as_of`
é o filtro do usuário, convertido para UTC. `available_period` descreve a cobertura
do snapshot, com abertura inicial, fim exclusivo e primeira/última disponibilidade.
Essa cobertura não é uma feature nem autoriza acesso a observações futuras.
`dataset_mode` é FULL ou DEMO; `coverage_notice` esclarece o recorte. FULL tem
5.760 minutos de 30/06 a 03/07/2026; DEMO tem 120 minutos finais de 03/07.

Datas são ISO 8601 UTC, serializadas com Z. Valores ausentes/NaN/infinito tornam-se
`null`, nunca zero. Campos `_pct` são percentuais: 0.5 significa 0,5%, não 50%.
Campos `_ratio` são razões adimensionais; `_usdc` é USDC; `_usdc_per_sol` é preço
USDC/SOL. `volatility_std_log_return` é desvio padrão amostral de retornos log
intrajanela, sem anualização. Scores `_0_100` são percentis, não probabilidades.
Taxas, precision, recall e AUC do backtest variam de 0 a 1; lift é razão contra
prevalência da mesma amostra; lead time é em minutos. `meta.units` acompanha respostas.

## Seleção temporal

`timestamp` é abertura; `available_at` é fechamento. `as_of` aceita um instante com
timezone, preferencialmente Z, e seleciona o último fechamento <= instante. Sem
`as_of`, usa o último fechamento histórico do snapshot. Não retorna a última linha
válida anterior para esconder NaN: a janela escolhida pode ser INDETERMINATE.
Em `overview`, totais abrangem somente os minutos disponíveis até esse fechamento.

`health` informa estado atual do cache, sem `as_of`. O backtest é análise retrospectiva,
com `result_available_at` da execução original em outubro; não é feature histórica e
não aceita `as_of`. Parâmetros desconhecidos ou repetidos são rejeitados para evitar
que o consumidor presuma um filtro temporal que não foi aplicado.

## Endpoints e parâmetros

| Endpoint | Parâmetros | Resposta data |
| --- | --- | --- |
| `/api/health` | nenhum | Health |
| `/api/overview` | as_of? | Overview: totais, MarketRisk, ForwardRisk e posições NORMAL |
| `/api/market-risk` | as_of? | MarketRisk |
| `/api/forward-risk` | as_of? | ForwardRisk |
| `/api/positions` | scenario=NORMAL, as_of? | Positions: tamanhos disponíveis, cenários e seis itens |
| `/api/exit-cost` | position_size=100000, scenario=NORMAL, as_of? | PositionExit |
| `/api/liquidity/history` | interval=1m, start?, end?, as_of?, limit=300, offset=0 | History: itens, ordem e paginação |
| `/api/backtest/summary` | event=ANY_DETERIORATION, sample=MATCHED, horizon?, predictor? | Backtest: referência congelada e métricas dos modelos/baselines |

Posições válidas: 10000/50000/100000/250000/500000/1000000 USDC.
Cenários: NORMAL, STRESS_25, STRESS_50, STRESS_75. Intervalos: 1m/5m/15m.
Horizontes: 5/15/30/60 minutos. O OpenAPI e TypeScript enumeram regimes, eventos,
amostras e os seis preditores. São os nomes existentes do backtest, sem renomear
resultados ou recalibrar classificações.

## Scores e custos

MarketRisk contém score, regime, quatro componentes, drivers primário/secundário,
motivo de indisponibilidade e referência fixa 100k/NORMAL com custo/confiabilidade.
ForwardRisk contém somente componentes liquidity/flow, score/regime/drivers e
`is_calibrated_probability=false`. Ambos preservam os scores originais causais.
Os regimes 40/60/80 são política heurística não calibrada.

PositionExit separa `operational_risk` de `model_reliability_status`; contém custo,
participação, liquidity_multiple causal, motivos e todas as flags de extrapolação/
informação/liquidez. `is_observed_slippage=false`. Ausência essencial é INDETERMINATE.
Um custo pequeno pode coexistir com extrapolação devido a participação >1; o frontend
deve mostrar a flag, sem inferir confiança a partir do número ou da categoria LOW.

## Séries e paginação

`start <= timestamp < end`; `end` é exclusivo. Somente barras fechadas no instante
selecionado entram no resultado. `items` está em ordem ASC; `pagination.total` é o
tamanho filtrado antes de paginar, `next_offset=null` indica fim. Default 300,
limite máximo 2.000, configurável por `RADAR_HISTORY_MAX_LIMIT` (1..10000). Offset
além do fim retorna lista vazia. Para obter minutos recentes, use start/end; a
primeira página não muda silenciosamente para o final da série.

Cada barra tem `risk_at_close`: **snapshot dos indicadores 1m originais no fechamento
da barra**, inclusive em 5m/15m. Não é média/recalculo de score em outra frequência.
Isso permite gráficos de risco/liquidez com uma única chamada. As métricas de volume/
volatilidade são as agregações originais do intervalo solicitado. `volatility_status`
distingue ausência de swaps, preços insuficientes ou valor não finito; a volatilidade
nunca é preenchida com zero. `flow_status` distingue ausência de volume/informação.

## Backtest

Default retorna 24 linhas: seis preditores × quatro horizontes para ANY/MATCHED.
Cada linha contém prevalência, precision/recall/FPR/specificity/lift, ROC-AUC,
PR-AUC (average precision não interpolada), TP/FP/TN/FN, lead time mediano/p25/p75,
observações válidas/excluídas e seus motivos. A amostra NON_OVERLAPPING_MATCHED permite
inspecionar a robustez sem targets sobrepostos. Áreas indefinidas ficam null com
`auc_unavailable_reason`, por exemplo quando todos os alvos são positivos.
Os thresholds congelados dos três dias de referência são expostos com unidades.

## Erros e operação

- 200: resposta válida.
- 404: nenhuma janela já fechada no instante solicitado, ou rota inexistente.
- 405: método não permitido; não há escrita/execução.
- 422: parâmetro, timezone, enum, limite ou intervalo de datas inválido.
- 503: datasets inexistentes, incompletos ou inválidos. Health também retorna 503.
- 500: falha inesperada com mensagem genérica.

Os CSVs são lidos e validados apenas no startup. Scores, componentes, classes e flags
nunca são recalculados para servir requests. Para trocar datasets, reinicie. No modo
full, hashes conferem os manifests originais do V2 e backtest, e validações adicionais
conferem cronologia, cobertura, unicidade, componentes e conservação das agregações.
Datasets parciais não geram respostas de sucesso com zeros falsos.

## Exemplos reais e integração

`docs/api_examples.json` contém respostas reais dos oito endpoints em modo DEMO.
`contracts/liquidity-radar.ts` exporta os tipos de resposta e `fetchRadar`. Sua geração
usa o OpenAPI do aplicativo sem coleta/RPC. O helper não substitui validação em runtime
de dados arbitrários; o servidor valida seus próprios contratos Pydantic.

```sh
curl -s 'http://127.0.0.1:8000/api/market-risk?as_of=2026-07-03T23:00:00Z'
curl -s 'http://127.0.0.1:8000/api/positions?scenario=STRESS_75'
curl -s 'http://127.0.0.1:8000/api/liquidity/history?interval=15m&limit=8'
curl -s 'http://127.0.0.1:8000/api/backtest/summary?horizon=60&sample=NON_OVERLAPPING_MATCHED'
```

A API não monta diretórios locais como arquivos estáticos nem publica swaps,
signatures, checkpoints, credenciais ou transações brutas. CORS permite somente as
origens configuradas, sem wildcard. Isso não é autenticação: o MVP é uma API local de
leitura de dados históricos públicos, sem infraestrutura de produção ou deploy.
