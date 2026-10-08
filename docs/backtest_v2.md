# Backtest temporal V2 — execução de 08/10/2026

Implementação: `src/backtest_v2.py`. Testes: `tests/test_backtest_v2.py`. Resultados calculados localmente, sem RPC e sem tuning. Nenhum módulo anterior foi alterado.

## Protocolo temporal e auditoria

Referência: 30/06 a 02/07/2026 UTC (4.320 minutos). Teste: 03/07/2026 UTC (1.440 minutos). O timestamp identifica a abertura da barra; a decisão ocorre em `available_at = timestamp + 1 minuto`. O alvo usa exclusivamente as próximas 5, 15, 30 ou 60 barras, com disponibilidade estritamente posterior à decisão. O horizonte não atravessa o fim do dia de teste. Um horizonte calendário incompleto recebe label ausente, mesmo quando há um cruzamento no trecho observado.

Foi usada a versão original `risk_v2_runs/20261008T153108969585Z`, vinculada à auditoria `audit/20261008T150552576490Z`. Os hashes do engine, indicadores e inputs causais foram conferidos. O replay cronológico auditou os 5.760 minutos, os históricos válidos, percentis, mediana causal de volume, liquidity_multiple, fórmula do exit cost, flags e disponibilidade. Cada linha só entra no histórico depois de seu próprio cálculo. Scores, componentes e regimes reconstruídos foram comparados com os originais; os scores persistidos originais foram usados na avaliação. Não foram usados percentis ou medianas globais descritivos como features.

Os indicadores V2 continuam com seu histórico expansivo causal original: no teste podem incorporar observações de teste já encerradas, nunca futuras. Isso preserva a versão online original, sem otimizar parâmetros. Os thresholds de eventos, cortes dos quartis e prevalências de referência estão congelados nos três dias anteriores. Não se implementou uma variante com distribuição do score congelada, pois alteraria o indicador.

A disponibilidade de fechamento é teórica: não há medição da latência real de ingestão. Além disso, 03/07 já havia sido inspecionado na auditoria e no desenvolvimento anterior. Esta é uma separação temporal computacional, não uma validação prospectiva inteiramente inédita.

## Eventos e baselines congelados

Eventos usam comparação estrita com quantis pandas lineares do treino:


- LOW_VOLUME_EVENT: mínimo futuro de volume < 492.743922350 USDC (p05).
- HIGH_VOLATILITY_EVENT: máximo futuro de volatilidade > 0.000591506941 (p95, razão).
- HIGH_EXIT_COST_EVENT: máximo futuro de custo NORMAL para 100k > 0.392406014% (p95).
- ANY_DETERIORATION: OR dos três eventos. Não há evento de fluxo nesta definição solicitada.


O exit cost é um proxy não validado baseado em volatilidade e volume observado, não slippage observado ou preço executável. Valores extremos são preservados.

Alerta dos dois indicadores: score >=60 (STRESSED ou CRITICAL), mantendo os regimes heurísticos 40/60/80. ROC-AUC usa o score contínuo; PR-AUC é average precision não interpolada, com empates agrupados. Métricas com denominador zero permanecem ausentes. Com uma só classe, as duas áreas ficam indisponíveis, com motivo registrado.

Baselines definidos antes de examinar a performance:

- LOW_CURRENT_VOLUME: alerta se volume atual < p05 do treino; score de ranking = -volume atual.
- RECENT_VOLUME_DROP: queda relativa contra a média das cinco barras estritamente anteriores; alerta para queda >0. Média anterior zero ou insuficiente torna o baseline indisponível.
- PERSISTENCE_PREVIOUS_CONDITION: condição do mesmo tipo de evento observada na barra anterior. Para ANY, usa o OR anterior. Score binário; não exige estabilidade do regime.
- PREVALENCE_TRAINING: frequência do evento/horizonte nas barras do treino, com futuro inteiramente no treino. Score constante, sem discriminação; alerta somente quando a prevalência histórica >=0,5. Nenhuma probabilidade calibrada é inferida.

A frequência do treino usa thresholds definidos por todo o treino, conhecidos no início do teste; é apenas referência para o teste e não avaliação causal de previsões dentro do treino. O baseline constante possui ROC-AUC 0,5 e AP igual à prevalência do teste quando há ambas as classes.

## Amostras, ausências e lead time

ALL_AVAILABLE usa as observações disponíveis para cada preditor. MATCHED usa a interseção dos seis preditores para permitir comparação justa por evento e horizonte. NON_OVERLAPPING_MATCHED fixa origens a cada h minutos a partir de 00:00 UTC, sem escolher origens com base em labels; os alvos são disjuntos. Isso reduz sobreposição, mas não elimina dependência serial do mercado. STRICT_COMPLETE_MATCHED exige todas as medições futuras do evento; para ANY, exige todas as três séries.

NaN nunca vira ausência de evento: em horizonte completo, um cruzamento observado comprova label 1 mesmo com outras medições ausentes; label 0 exige cobertura integral sem cruzamentos; caso contrário, o label é desconhecido. ANY é verdadeiro se algum evento é conhecido verdadeiro, falso somente se todos forem conhecidos falsos. Essa regra pode selecionar mais positivos nas amostras com falhas de medição; a amostra estrita expõe a sensibilidade, sem imputação.

Lead time: para cada minuto único de primeiro cruzamento, considera-se a primeira decisão com alerta que o antecipou dentro do horizonte. Mediana/p25/p75 constam nos CSVs. É uma medida condicional aos eventos detectados, dependente do horizonte; não representa episódios físicos independentes nem prova que o alerta iniciou antes de um episódio. Alertas constantes também podem ter lead time positivo por construção.

## Performance dos indicadores

Amostra MATCHED. Precision, recall, FPR, prevalência e AP são razões, não percentuais. Lift = precision / prevalência na mesma amostra. A tabela contém todos os tipos de evento e horizontes.


| event | horizon_minutes | predictor | n_valid | n_excluded | event_rate | precision | recall | false_positive_rate | lift | roc_auc | pr_auc_average_precision | lead_time_median_minutes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LOW_VOLUME_EVENT | 5 | CURRENT_MARKET_RISK | 1379 | 61 | 0.3096 | 0.3603 | 0.5316 | 0.4233 | 1.1636 | 0.5637 | 0.3674 | 4.0 |
| LOW_VOLUME_EVENT | 5 | FORWARD_LIQUIDITY_RISK | 1379 | 61 | 0.3096 | 0.3613 | 0.6042 | 0.479 | 1.167 | 0.5817 | 0.357 | 4.0 |
| HIGH_VOLATILITY_EVENT | 5 | CURRENT_MARKET_RISK | 1198 | 242 | 0.4007 | 0.4565 | 0.525 | 0.4178 | 1.1394 | 0.5755 | 0.4573 | 4.0 |
| HIGH_VOLATILITY_EVENT | 5 | FORWARD_LIQUIDITY_RISK | 1198 | 242 | 0.4007 | 0.4504 | 0.5771 | 0.4708 | 1.1241 | 0.5443 | 0.4175 | 4.0 |
| HIGH_EXIT_COST_EVENT | 5 | CURRENT_MARKET_RISK | 1183 | 257 | 0.3542 | 0.4312 | 0.5609 | 0.4058 | 1.2174 | 0.6136 | 0.4517 | 4.0 |
| HIGH_EXIT_COST_EVENT | 5 | FORWARD_LIQUIDITY_RISK | 1183 | 257 | 0.3542 | 0.4345 | 0.6253 | 0.4463 | 1.2267 | 0.5916 | 0.4107 | 4.0 |
| ANY_DETERIORATION | 5 | CURRENT_MARKET_RISK | 1354 | 86 | 0.5295 | 0.5862 | 0.5077 | 0.4035 | 1.1069 | 0.57 | 0.5851 | 3.0 |
| ANY_DETERIORATION | 5 | FORWARD_LIQUIDITY_RISK | 1354 | 86 | 0.5295 | 0.5949 | 0.5816 | 0.4458 | 1.1234 | 0.5678 | 0.5705 | 3.0 |
| LOW_VOLUME_EVENT | 15 | CURRENT_MARKET_RISK | 1371 | 69 | 0.5923 | 0.6433 | 0.4975 | 0.4007 | 1.0862 | 0.5649 | 0.653 | 6.0 |
| LOW_VOLUME_EVENT | 15 | FORWARD_LIQUIDITY_RISK | 1371 | 69 | 0.5923 | 0.6742 | 0.5887 | 0.4132 | 1.1383 | 0.6207 | 0.6828 | 6.0 |
| HIGH_VOLATILITY_EVENT | 15 | CURRENT_MARKET_RISK | 1125 | 315 | 0.8276 | 0.8493 | 0.4844 | 0.4124 | 1.0263 | 0.5476 | 0.8448 | 7.0 |
| HIGH_VOLATILITY_EVENT | 15 | FORWARD_LIQUIDITY_RISK | 1125 | 315 | 0.8276 | 0.8647 | 0.5424 | 0.4072 | 1.0449 | 0.574 | 0.8619 | 7.0 |
| HIGH_EXIT_COST_EVENT | 15 | CURRENT_MARKET_RISK | 1106 | 334 | 0.7396 | 0.7946 | 0.5061 | 0.3715 | 1.0744 | 0.5939 | 0.7968 | 6.0 |
| HIGH_EXIT_COST_EVENT | 15 | FORWARD_LIQUIDITY_RISK | 1106 | 334 | 0.7396 | 0.8118 | 0.5697 | 0.375 | 1.0977 | 0.6107 | 0.8047 | 5.0 |
| ANY_DETERIORATION | 15 | CURRENT_MARKET_RISK | 1353 | 87 | 0.867 | 0.8855 | 0.468 | 0.3944 | 1.0214 | 0.5581 | 0.89 | 4.0 |
| ANY_DETERIORATION | 15 | FORWARD_LIQUIDITY_RISK | 1353 | 87 | 0.867 | 0.901 | 0.5354 | 0.3833 | 1.0393 | 0.5854 | 0.8971 | 4.0 |
| LOW_VOLUME_EVENT | 30 | CURRENT_MARKET_RISK | 1357 | 83 | 0.7657 | 0.8084 | 0.4832 | 0.3742 | 1.0558 | 0.5673 | 0.81 | 6.0 |
| LOW_VOLUME_EVENT | 30 | FORWARD_LIQUIDITY_RISK | 1357 | 83 | 0.7657 | 0.83 | 0.5592 | 0.3742 | 1.084 | 0.6277 | 0.8376 | 6.0 |
| HIGH_VOLATILITY_EVENT | 30 | CURRENT_MARKET_RISK | 1193 | 247 | 0.9556 | 0.9713 | 0.4746 | 0.3019 | 1.0164 | 0.6002 | 0.9684 | 7.0 |
| HIGH_VOLATILITY_EVENT | 30 | FORWARD_LIQUIDITY_RISK | 1193 | 247 | 0.9556 | 0.971 | 0.5281 | 0.3396 | 1.0161 | 0.5963 | 0.9685 | 7.0 |
| HIGH_EXIT_COST_EVENT | 30 | CURRENT_MARKET_RISK | 1161 | 279 | 0.9165 | 0.9352 | 0.4746 | 0.3608 | 1.0204 | 0.5812 | 0.9322 | 6.0 |
| HIGH_EXIT_COST_EVENT | 30 | FORWARD_LIQUIDITY_RISK | 1161 | 279 | 0.9165 | 0.9402 | 0.532 | 0.3711 | 1.0259 | 0.5723 | 0.9319 | 6.0 |
| ANY_DETERIORATION | 30 | CURRENT_MARKET_RISK | 1348 | 92 | 0.9644 | 0.9724 | 0.4615 | 0.3542 | 1.0084 | 0.5802 | 0.9728 | 4.0 |
| ANY_DETERIORATION | 30 | FORWARD_LIQUIDITY_RISK | 1348 | 92 | 0.9644 | 0.9784 | 0.5223 | 0.3125 | 1.0145 | 0.6003 | 0.9749 | 4.0 |
| LOW_VOLUME_EVENT | 60 | CURRENT_MARKET_RISK | 1328 | 112 | 0.9157 | 0.9269 | 0.4589 | 0.3929 | 1.0123 | 0.5726 | 0.9343 | 6.0 |
| LOW_VOLUME_EVENT | 60 | FORWARD_LIQUIDITY_RISK | 1328 | 112 | 0.9157 | 0.9428 | 0.5288 | 0.3482 | 1.0297 | 0.6181 | 0.94 | 6.0 |
| HIGH_VOLATILITY_EVENT | 60 | CURRENT_MARKET_RISK | 1245 | 195 | 0.9928 | 0.993 | 0.4563 | 0.4444 | 1.0002 | 0.5179 | 0.9945 | 7.0 |
| HIGH_VOLATILITY_EVENT | 60 | FORWARD_LIQUIDITY_RISK | 1245 | 195 | 0.9928 | 0.9953 | 0.5129 | 0.3333 | 1.0025 | 0.5133 | 0.993 | 7.0 |
| HIGH_EXIT_COST_EVENT | 60 | CURRENT_MARKET_RISK | 1222 | 218 | 0.9926 | 0.9928 | 0.4559 | 0.4444 | 1.0002 | 0.5176 | 0.9944 | 6.0 |
| HIGH_EXIT_COST_EVENT | 60 | FORWARD_LIQUIDITY_RISK | 1222 | 218 | 0.9926 | 0.9952 | 0.5153 | 0.3333 | 1.0026 | 0.5142 | 0.993 | 6.0 |
| ANY_DETERIORATION | 60 | CURRENT_MARKET_RISK | 1319 | 121 | 0.997 | 0.9967 | 0.4532 | 0.5 | 0.9997 | 0.5025 | 0.9976 | 4.0 |
| ANY_DETERIORATION | 60 | FORWARD_LIQUIDITY_RISK | 1319 | 121 | 0.997 | 1.0 | 0.5141 | 0.0 | 1.003 | 0.7089 | 0.9989 | 4.0 |


Para ANY em 5m: Current tem precision 58,6%, recall 50,8%, FPR 40,3%, lift 1,107 e ROC 0,570; Forward tem precision 59,5%, recall 58,2%, FPR 44,6%, lift 1,123 e ROC 0,568. O ganho sobre acaso/prevalência é modesto e não constitui superioridade consistente sobre baselines simples. Current captura melhor o evento de exit cost em 5m (ROC 0,614), enquanto Forward apresenta sinal relativamente mais forte para baixo volume em 15–30m (ROC 0,621–0,628). Esses resultados são associações condicionais à amostra, sem comprovação econômica.

## Comparação com baselines

Amostra MATCHED para ANY. Os CSVs incluem também cada evento individual, todas as matrizes de confusão, exclusões e demais amostras.


| horizon_minutes | predictor | n_valid | event_rate | precision | recall | false_positive_rate | lift | roc_auc | pr_auc_average_precision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | LOW_CURRENT_VOLUME | 1354 | 0.5295 | 0.6806 | 0.0683 | 0.0361 | 1.2852 | 0.5795 | 0.59 |
| 5 | RECENT_VOLUME_DROP | 1354 | 0.5295 | 0.5439 | 0.5872 | 0.5542 | 1.0272 | 0.5327 | 0.5653 |
| 5 | PERSISTENCE_PREVIOUS_CONDITION | 1354 | 0.5295 | 0.615 | 0.1827 | 0.1287 | 1.1614 | 0.527 | 0.5452 |
| 5 | PREVALENCE_TRAINING | 1354 | 0.5295 | — | 0.0 | 0.0 | — | 0.5 | 0.5295 |
| 15 | LOW_CURRENT_VOLUME | 1353 | 0.867 | 0.9861 | 0.0605 | 0.0056 | 1.1374 | 0.5899 | 0.9058 |
| 15 | RECENT_VOLUME_DROP | 1353 | 0.867 | 0.8662 | 0.5686 | 0.5722 | 0.9992 | 0.5317 | 0.8918 |
| 15 | PERSISTENCE_PREVIOUS_CONDITION | 1353 | 0.867 | 0.9249 | 0.1679 | 0.0889 | 1.0668 | 0.5395 | 0.8767 |
| 15 | PREVALENCE_TRAINING | 1353 | 0.867 | 0.867 | 1.0 | 1.0 | 1.0 | 0.5 | 0.867 |
| 30 | LOW_CURRENT_VOLUME | 1348 | 0.9644 | 1.0 | 0.0546 | 0.0 | 1.0369 | 0.6329 | 0.9798 |
| 30 | RECENT_VOLUME_DROP | 1348 | 0.9644 | 0.9714 | 0.5754 | 0.4583 | 1.0073 | 0.5587 | 0.9746 |
| 30 | PERSISTENCE_PREVIOUS_CONDITION | 1348 | 0.9644 | 0.9857 | 0.1592 | 0.0625 | 1.0221 | 0.5484 | 0.9678 |
| 30 | PREVALENCE_TRAINING | 1348 | 0.9644 | 0.9644 | 1.0 | 1.0 | 1.0 | 0.5 | 0.9644 |
| 60 | LOW_CURRENT_VOLUME | 1319 | 0.997 | 1.0 | 0.051 | 0.0 | 1.003 | 0.4842 | 0.9975 |
| 60 | RECENT_VOLUME_DROP | 1319 | 0.997 | 0.9973 | 0.5696 | 0.5 | 1.0004 | 0.5095 | 0.9974 |
| 60 | PERSISTENCE_PREVIOUS_CONDITION | 1319 | 0.997 | 0.995 | 0.1506 | 0.25 | 0.998 | 0.4503 | 0.9967 |
| 60 | PREVALENCE_TRAINING | 1319 | 0.997 | 0.997 | 1.0 | 1.0 | 1.0 | 0.5 | 0.997 |


O volume baixo atual tem precision maior e FPR menor, porém recall muito baixo: em ANY/5m, 68,1% de precision, 6,8% de recall, FPR 3,6%, ROC 0,580 e AP 0,590. Os scores V2 alertam com maior frequência e maior cobertura; não é apropriado comparar somente a precision de pontos operacionais distintos. O ranking de volume simples permanece competitivo, sem evidência de vantagem incremental consistente do V2.

A queda recente de volume fornece pouca discriminação (ANY/5m ROC 0,533; lift 1,027). A persistência dá lift 1,161 em 5m, com recall 18,3% e ROC 0,527; portanto, maior precision que o V2 nesse ponto não significa melhor ranking global. A prevalência constante é uma referência sem discriminação, não um modelo validado.

## Saturação e dependência

Na amostra comum ANY, a prevalência cresce de 52,95% em 5m para 86,70%, 96,44% e 99,70% em 15/30/60m. Em 60m restam somente quatro negativos; o ROC 0,709 do Forward e FPR zero dependem desses quatro casos e são frágeis. A precision de 100% resulta em lift apenas 1,003. Volatilidade e exit cost individuais também chegam a aproximadamente 99,3% nesse horizonte. Não interpretar precision/AP elevados como forte capacidade de antecipação.

Diagnóstico de todos os labels conhecidos (antes da interseção de preditores):


| event | horizon_minutes | n_valid | event_rate | lag1_label_autocorrelation | non_overlapping_known_labels |
| --- | --- | --- | --- | --- | --- |
| LOW_VOLUME_EVENT | 5 | 1435.0 | 0.3164 | 0.8081 | 287.0 |
| HIGH_VOLATILITY_EVENT | 5 | 1280.0 | 0.407 | 0.796 | 255.0 |
| HIGH_EXIT_COST_EVENT | 5 | 1264.0 | 0.3568 | 0.8113 | 252.0 |
| ANY_DETERIORATION | 5 | 1418.0 | 0.5374 | 0.7732 | 283.0 |
| LOW_VOLUME_EVENT | 15 | 1425.0 | 0.5993 | 0.9313 | 95.0 |
| HIGH_VOLATILITY_EVENT | 15 | 1203.0 | 0.8271 | 0.8913 | 80.0 |
| HIGH_EXIT_COST_EVENT | 15 | 1179.0 | 0.7422 | 0.9341 | 80.0 |
| ANY_DETERIORATION | 15 | 1416.0 | 0.8679 | 0.8852 | 94.0 |
| LOW_VOLUME_EVENT | 30 | 1410.0 | 0.7695 | 0.964 | 47.0 |
| HIGH_VOLATILITY_EVENT | 30 | 1284.0 | 0.9572 | 0.9521 | 44.0 |
| HIGH_EXIT_COST_EVENT | 30 | 1243.0 | 0.9171 | 0.9354 | 43.0 |
| ANY_DETERIORATION | 30 | 1410.0 | 0.9652 | 0.9366 | 47.0 |
| LOW_VOLUME_EVENT | 60 | 1380.0 | 0.9145 | 0.9444 | 23.0 |
| HIGH_VOLATILITY_EVENT | 60 | 1341.0 | 0.9918 | 0.9531 | 23.0 |
| HIGH_EXIT_COST_EVENT | 60 | 1318.0 | 0.9917 | 0.9531 | 23.0 |
| ANY_DETERIORATION | 60 | 1380.0 | 0.9964 | 0.7993 | 23.0 |


Targets de decisões adjacentes compartilham h-1 minutos futuros. Autocorrelações de labels próximas de 0,8–0,96 confirmam dependência; não foram produzidos p-valores ou intervalos que presumam independência.

Avaliação ANY com alvos não sobrepostos, na interseção dos preditores:


| horizon_minutes | predictor | n_valid | event_rate | precision | recall | false_positive_rate | lift | roc_auc | pr_auc_average_precision | lead_time_median_minutes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | CURRENT_MARKET_RISK | 275 | 0.5164 | 0.5766 | 0.4507 | 0.3534 | 1.1166 | 0.5556 | 0.584 | 2.0 |
| 5 | FORWARD_LIQUIDITY_RISK | 275 | 0.5164 | 0.5966 | 0.5 | 0.3609 | 1.1555 | 0.5663 | 0.5834 | 2.0 |
| 15 | CURRENT_MARKET_RISK | 91 | 0.8462 | 0.9062 | 0.3766 | 0.2143 | 1.071 | 0.6206 | 0.9041 | 5.0 |
| 15 | FORWARD_LIQUIDITY_RISK | 91 | 0.8462 | 0.9062 | 0.3766 | 0.2143 | 1.071 | 0.6122 | 0.9008 | 4.0 |
| 30 | CURRENT_MARKET_RISK | 45 | 0.9556 | 1.0 | 0.3953 | 0.0 | 1.0465 | 0.6163 | 0.9789 | 6.0 |
| 30 | FORWARD_LIQUIDITY_RISK | 45 | 0.9556 | 1.0 | 0.3721 | 0.0 | 1.0465 | 0.593 | 0.9771 | 8.0 |
| 60 | CURRENT_MARKET_RISK | 22 | 1.0 | 1.0 | 0.1818 | — | 1.0 | — | — | 12.0 |
| 60 | FORWARD_LIQUIDITY_RISK | 22 | 1.0 | 1.0 | 0.1818 | — | 1.0 | — | — | 11.5 |


Restam 275, 91, 45 e 22 observações comuns. Em 60m todas são positivas: ROC/AP/FPR não são definidos. Em 5m, o ROC não sobreposto permanece baixo (Current 0,556; Forward 0,566; volume simples 0,573). Em 30m existem somente dois negativos, impedindo conclusões robustas de ranking. A ausência de sobreposição não torna um único dia representativo de outros regimes de mercado.

Sensibilidade exigindo cobertura integral das três métricas futuras para ANY:


| horizon_minutes | predictor | n_valid | event_rate | precision | recall | lift | roc_auc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | CURRENT_MARKET_RISK | 1151 | 0.4466 | 0.5133 | 0.5272 | 1.1493 | 0.5826 |
| 5 | FORWARD_LIQUIDITY_RISK | 1151 | 0.4466 | 0.5112 | 0.5778 | 1.1447 | 0.5618 |
| 15 | CURRENT_MARKET_RISK | 852 | 0.7887 | 0.8234 | 0.4926 | 1.0439 | 0.5777 |
| 15 | FORWARD_LIQUIDITY_RISK | 852 | 0.7887 | 0.8349 | 0.5193 | 1.0586 | 0.5751 |
| 30 | CURRENT_MARKET_RISK | 601 | 0.9201 | 0.9393 | 0.4756 | 1.0208 | 0.5965 |
| 30 | FORWARD_LIQUIDITY_RISK | 601 | 0.9201 | 0.9462 | 0.4774 | 1.0284 | 0.5692 |
| 60 | CURRENT_MARKET_RISK | 348 | 0.9885 | 0.9882 | 0.4884 | 0.9997 | 0.5443 |
| 60 | FORWARD_LIQUIDITY_RISK | 348 | 0.9885 | 1.0 | 0.4797 | 1.0116 | 0.6919 |


A amostra estrita cai para 1.151, 852, 601 e 348 casos. Em ANY/5m, sua prevalência é 44,7%, versus 53,0% na amostra comum com positivos comprovados parcialmente. Essa diferença deve ser considerada antes de generalizar as estimativas. Amostras próprias preservam mais observações do Forward quando volatilidade/custo atual estão ausentes; resultados constam de ALL_AVAILABLE e não devem ser comparados diretamente a modelos com seleção diferente.

## Ordenação, componentes e inversões

Quartis definidos somente no treino, usando <=q25, <=q50, <=q75 e acima de q75; empates não são divididos artificialmente. As quantidades no teste não são forçadas a 25%. Exemplo: ANY/5m.


| factor | group | n_valid | event_rate | training_q25 | training_q50 | training_q75 |
| --- | --- | --- | --- | --- | --- | --- |
| CURRENT_MARKET_RISK | Q1 | 155.0 | 0.4129 | 32.9982 | 45.8919 | 61.0664 |
| CURRENT_MARKET_RISK | Q2 | 271.0 | 0.5166 | 32.9982 | 45.8919 | 61.0664 |
| CURRENT_MARKET_RISK | Q3 | 328.0 | 0.4817 | 32.9982 | 45.8919 | 61.0664 |
| CURRENT_MARKET_RISK | Q4 | 600.0 | 0.5917 | 32.9982 | 45.8919 | 61.0664 |
| FORWARD_LIQUIDITY_RISK | Q1 | 143.0 | 0.3916 | 26.8875 | 47.2445 | 67.074 |
| FORWARD_LIQUIDITY_RISK | Q2 | 256.0 | 0.4766 | 26.8875 | 47.2445 | 67.074 |
| FORWARD_LIQUIDITY_RISK | Q3 | 467.0 | 0.5375 | 26.8875 | 47.2445 | 67.074 |
| FORWARD_LIQUIDITY_RISK | Q4 | 488.0 | 0.5902 | 26.8875 | 47.2445 | 67.074 |
| VOLATILITY_RISK | Q1 | 288.0 | 0.5278 | 24.2592 | 47.2973 | 72.7265 |
| VOLATILITY_RISK | Q2 | 245.0 | 0.4898 | 24.2592 | 47.2973 | 72.7265 |
| VOLATILITY_RISK | Q3 | 318.0 | 0.4937 | 24.2592 | 47.2973 | 72.7265 |
| VOLATILITY_RISK | Q4 | 503.0 | 0.5726 | 24.2592 | 47.2973 | 72.7265 |
| LIQUIDITY_RISK | Q1 | 150.0 | 0.38 | 21.8144 | 45.3033 | 70.4188 |
| LIQUIDITY_RISK | Q2 | 298.0 | 0.5 | 21.8144 | 45.3033 | 70.4188 |
| LIQUIDITY_RISK | Q3 | 394.0 | 0.5254 | 21.8144 | 45.3033 | 70.4188 |
| LIQUIDITY_RISK | Q4 | 512.0 | 0.5938 | 21.8144 | 45.3033 | 70.4188 |
| FLOW_RISK | Q1 | 198.0 | 0.4949 | 21.4474 | 45.0971 | 74.5445 |
| FLOW_RISK | Q2 | 263.0 | 0.4563 | 21.4474 | 45.0971 | 74.5445 |
| FLOW_RISK | Q3 | 534.0 | 0.5562 | 21.4474 | 45.0971 | 74.5445 |
| FLOW_RISK | Q4 | 359.0 | 0.5627 | 21.4474 | 45.0971 | 74.5445 |
| REFERENCE_EXIT_COST_RISK | Q1 | 240.0 | 0.4792 | 24.0984 | 46.3895 | 70.7016 |
| REFERENCE_EXIT_COST_RISK | Q2 | 220.0 | 0.4818 | 24.0984 | 46.3895 | 70.7016 |
| REFERENCE_EXIT_COST_RISK | Q3 | 318.0 | 0.5 | 24.0984 | 46.3895 | 70.7016 |
| REFERENCE_EXIT_COST_RISK | Q4 | 576.0 | 0.5851 | 24.0984 | 46.3895 | 70.7016 |


Forward e liquidity_risk têm crescimento monotônico nos quartis para os quatro eventos em 5m. Para ANY, as taxas Q1–Q4 são 39,2/47,7/53,7/59,0% no Forward e 38,0/50,0/52,5/59,4% em liquidity_risk. Current apresenta inversão Q2>Q3 (51,7% contra 48,2%).

Nos 16 pares evento × horizonte, quartis não decrescentes aparecem em 10 para Forward, 9 para liquidity_risk, 5 para Current, 2 para flow_risk, 2 para reference_exit_cost_risk e nenhum para volatility_risk. São contagens descritivas dependentes, incluindo horizontes saturados, sem teste de significância. Flow isolado tem sinal fraco em 5m (ROC 0,512–0,540); volatilidade isolada antecipa pouco baixo volume (ROC 0,519 em 5m, 0,491 em 15m, 0,483 em 30m). O resultado do flow em ANY/60m (ROC 0,802) depende de quatro negativos e não demonstra valor estável. Exit cost de referência apresenta associação modesta e incorpora volatilidade/volume matematicamente; seu desempenho contra evento de custo não valida execução real.

Regimes para ANY:


| factor | horizon_minutes | group | n_valid | event_rate |
| --- | --- | --- | --- | --- |
| CURRENT_MARKET_RISK | 5 | NORMAL | 267.0 | 0.4382 |
| CURRENT_MARKET_RISK | 5 | WATCH | 466.0 | 0.5064 |
| CURRENT_MARKET_RISK | 5 | STRESSED | 448.0 | 0.5781 |
| CURRENT_MARKET_RISK | 5 | CRITICAL | 173.0 | 0.6069 |
| FORWARD_LIQUIDITY_RISK | 5 | NORMAL | 292.0 | 0.4315 |
| FORWARD_LIQUIDITY_RISK | 5 | WATCH | 361.0 | 0.482 |
| FORWARD_LIQUIDITY_RISK | 5 | STRESSED | 557.0 | 0.6014 |
| FORWARD_LIQUIDITY_RISK | 5 | CRITICAL | 144.0 | 0.5694 |
| CURRENT_MARKET_RISK | 15 | NORMAL | 269.0 | 0.8253 |
| CURRENT_MARKET_RISK | 15 | WATCH | 464.0 | 0.8664 |
| CURRENT_MARKET_RISK | 15 | STRESSED | 449.0 | 0.8731 |
| CURRENT_MARKET_RISK | 15 | CRITICAL | 171.0 | 0.9181 |
| FORWARD_LIQUIDITY_RISK | 15 | NORMAL | 292.0 | 0.8322 |
| FORWARD_LIQUIDITY_RISK | 15 | WATCH | 364.0 | 0.8297 |
| FORWARD_LIQUIDITY_RISK | 15 | STRESSED | 559.0 | 0.8998 |
| FORWARD_LIQUIDITY_RISK | 15 | CRITICAL | 138.0 | 0.9058 |
| CURRENT_MARKET_RISK | 30 | NORMAL | 270.0 | 0.9481 |
| CURRENT_MARKET_RISK | 30 | WATCH | 461.0 | 0.9631 |
| CURRENT_MARKET_RISK | 30 | STRESSED | 445.0 | 0.9685 |
| CURRENT_MARKET_RISK | 30 | CRITICAL | 172.0 | 0.9826 |
| FORWARD_LIQUIDITY_RISK | 30 | NORMAL | 292.0 | 0.9452 |
| FORWARD_LIQUIDITY_RISK | 30 | WATCH | 362.0 | 0.953 |
| FORWARD_LIQUIDITY_RISK | 30 | STRESSED | 556.0 | 0.9784 |
| FORWARD_LIQUIDITY_RISK | 30 | CRITICAL | 138.0 | 0.9783 |
| CURRENT_MARKET_RISK | 60 | NORMAL | 268.0 | 1.0 |
| CURRENT_MARKET_RISK | 60 | WATCH | 453.0 | 0.9956 |
| CURRENT_MARKET_RISK | 60 | STRESSED | 431.0 | 0.9954 |
| CURRENT_MARKET_RISK | 60 | CRITICAL | 167.0 | 1.0 |
| FORWARD_LIQUIDITY_RISK | 60 | NORMAL | 290.0 | 0.9966 |
| FORWARD_LIQUIDITY_RISK | 60 | WATCH | 353.0 | 0.9915 |
| FORWARD_LIQUIDITY_RISK | 60 | STRESSED | 540.0 | 1.0 |
| FORWARD_LIQUIDITY_RISK | 60 | CRITICAL | 136.0 | 1.0 |


Current ordena os quatro regimes em 5/15/30m para todos os eventos, mas a diferença perde utilidade com saturação. No Forward, em 5m CRITICAL tem menor taxa que STRESSED para ANY (56,9% contra 60,1%), volatilidade e exit cost. Em 15m ANY apresenta NORMAL>WATCH; em 30m STRESSED>CRITICAL por diferença mínima. Em 60m aparecem inversões nos dois indicadores; não houve mudança dos thresholds. A tabela REGIME_ORDERING do diagnóstico registra todas as inversões também na amostra não sobreposta.

## Contagens, integridade e arquivos

Os outputs têm 5.760 linhas de eventos (1.440 × quatro horizontes), 128 linhas de métricas dos indicadores, 256 dos baselines e 1.424 de diagnósticos. Uma observação é um minuto × horizonte para o mercado; não foi replicada por seis posições. O custo usado no alvo é sempre 100k/NORMAL.

Exclusões e matriz do Current na amostra comum (n_valid é idêntico para os seis preditores do mesmo evento/horizonte):


| horizon_minutes | event | n_valid | n_excluded_incomplete_horizon | n_excluded_unknown_label | n_excluded_predictor | tp | fp | tn | fn |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | LOW_VOLUME_EVENT | 1379 | 5 | 0 | 56 | 227 | 403 | 549 | 200 |
| 5 | HIGH_VOLATILITY_EVENT | 1198 | 5 | 155 | 82 | 252 | 300 | 418 | 228 |
| 5 | HIGH_EXIT_COST_EVENT | 1183 | 5 | 171 | 81 | 235 | 310 | 454 | 184 |
| 5 | ANY_DETERIORATION | 1354 | 5 | 17 | 64 | 364 | 257 | 380 | 353 |
| 15 | LOW_VOLUME_EVENT | 1371 | 15 | 0 | 54 | 404 | 224 | 335 | 408 |
| 15 | HIGH_VOLATILITY_EVENT | 1125 | 15 | 222 | 78 | 451 | 80 | 114 | 480 |
| 15 | HIGH_EXIT_COST_EVENT | 1106 | 15 | 246 | 73 | 414 | 107 | 181 | 404 |
| 15 | ANY_DETERIORATION | 1353 | 15 | 9 | 63 | 549 | 71 | 109 | 624 |
| 30 | LOW_VOLUME_EVENT | 1357 | 30 | 0 | 53 | 502 | 119 | 199 | 537 |
| 30 | HIGH_VOLATILITY_EVENT | 1193 | 30 | 126 | 91 | 541 | 16 | 37 | 599 |
| 30 | HIGH_EXIT_COST_EVENT | 1161 | 30 | 167 | 82 | 505 | 35 | 62 | 559 |
| 30 | ANY_DETERIORATION | 1348 | 30 | 0 | 62 | 600 | 17 | 31 | 700 |
| 60 | LOW_VOLUME_EVENT | 1328 | 60 | 0 | 52 | 558 | 44 | 68 | 658 |
| 60 | HIGH_VOLATILITY_EVENT | 1245 | 60 | 39 | 96 | 564 | 4 | 5 | 672 |
| 60 | HIGH_EXIT_COST_EVENT | 1222 | 60 | 62 | 96 | 553 | 4 | 5 | 660 |
| 60 | ANY_DETERIORATION | 1319 | 60 | 0 | 61 | 596 | 2 | 2 | 719 |


Exclusões têm motivos disjuntos e conservam n_candidates. TP+FP+TN+FN=n_valid. Não há infinito, duplicatas ou alteração de scores; labels positivos têm primeiro cruzamento disponível depois da decisão. Os 17.280 labels individuais e 5.760 labels ANY foram conferidos independentemente por slices das próximas barras. As matrizes, áreas, lead time mediano/p25/p75 e contagem de minutos de primeiro cruzamento constam dos outputs.

Os 62 testes passaram (15 novos), incluindo mudança de valores do teste sem alterar referências, choque futuro sem alterar baselines anteriores, exclusão da barra atual, mínimo histórico, missingness, horizonte incompleto, comparações estritas, AUC com empates/uma classe, deduplicação de primeiro cruzamento, conservação de contagens, não sobreposição e recusa de sobrescrever outputs.

101 arquivos existentes protegidos foram comparados antes/depois por SHA-256 e mtime: todos intactos. Isso inclui dados históricos, classificações, checkpoints, outputs anteriores e módulos existentes. Os quatro CSVs canônicos são idênticos à cópia versionada; o manifesto registra hashes dos inputs/outputs, código e referência congelada.

Arquivos:

- `data/processed/backtest_v2_events.csv`
- `data/processed/backtest_v2_summary.csv`
- `data/processed/backtest_v2_baselines.csv`
- `data/processed/backtest_v2_diagnostics.csv`
- Versão e manifesto: `data/processed/backtest_v2_runs/20261008T160739629858Z/`

Execução: `python -m src.backtest_v2`. Os quatro destinos existentes bloqueiam nova execução antes de processar inputs. Para outra versão, usar `python -m src.backtest_v2 --output-dir data/processed/backtest_v2_repeat`; não apagar resultados anteriores. Não há argumentos para tuning de thresholds ou pesos.

## Recomendação

**Manter V2 como baseline explicável de severidade atual e pesquisa; não promovê-lo a preditor validado.** Há informação modesta, sobretudo em volume/liquidity_risk, mas não superioridade consistente sobre volume simples. O Forward oferece maior recall em alguns horizontes, com muitos falsos positivos e inversões de regime. Current Market Risk mede também severidade contemporânea; falta de previsão forte não invalida automaticamente essa função.

Antes de alterar componentes ou investigar um V3, coletar novos dias não utilizados no desenho, fixar o protocolo previamente e avaliar a contribuição incremental de flow e demais componentes em múltiplos regimes, preservando V2 como controle. Este resultado não fundamenta tuning dos thresholds 40/60/80, pesos ou features. A validação econômica do exit cost, dust/outliers de preço preservados, latência de ingestão, não estacionariedade e seleção por dados ausentes permanecem limitações. Três dias de referência e um dia já inspecionado não sustentam significância estatística forte, probabilidades calibradas ou prontidão para alertas de execução.
