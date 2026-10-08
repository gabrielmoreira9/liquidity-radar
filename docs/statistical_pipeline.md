# Pipeline estatístico consolidado

Entrada: `data/processed/sol_usdc_swaps_clean_consolidated.csv`.
Execução local, em ordem: `src/liquidity.py`, `src/features.py`, `src/stress.py`.
Nenhum módulo faz chamadas externas. Histórico, classificações e checkpoints
não são escritos por esses scripts.

## Tempo e metodologia

As agregações cobrem dias UTC completos entre a primeira e a última observação,
com janelas fechadas à esquerda em 1, 5 e 15 minutos. Empates de timestamp
preservam a ordem original dos swaps. Signatures duplicadas interrompem a
execução; nenhuma observação é descartada silenciosamente.

Janelas sem swaps têm contagens, volumes e fluxo líquido zero. Preços, tamanhos
de trades, retorno, volatilidade e desequilíbrio de fluxo ficam NaN nessas
janelas. Não há preenchimento de preços. A volatilidade continua sendo o desvio
padrão amostral dos log-retornos **dentro da janela**, sem anualização; menos
de três preços não permitem calculá-la. Fluxo líquido continua sendo a soma dos
deltas USDC, positivo quando entra USDC no pool. O desequilíbrio é fluxo/volume.

Features preservam medianas, percentis, MAD e robust z com coeficiente 0,6745.
Os percentis empíricos são `100 × proporção de observações válidas <= valor`.
NaN é ignorado em cada estatística individual; MAD zero produz robust z NaN.
Extremos são reportados, nunca removidos ou limitados.

**As estatísticas globais dos quatro dias são descritivas.** Usá-las como
referência conhecida em t em previsões retrospectivas causaria look-ahead bias.
O backtest não foi alterado nem executado, e os outputs de risco/backtest não
foram recalculados nesta etapa.

O stress mantém seis posições (10k, 50k, 100k, 250k, 500k, 1M USDC) e quatro
cenários (NORMAL, STRESS_25, STRESS_50, STRESS_75), com multiplicadores de volume
1, 0,75, 0,50 e 0,25. A fórmula permanece:

`estimated_exit_cost_pct = 100 × volatility × sqrt(position_size / available_volume)`

O coeficiente de impacto continua 1. Volatilidade e fluxo não mudam entre
cenários. Volume indisponível ou volatilidade insuficiente produzem custo NaN.
`liquidity_multiple` usa a mediana global descritiva original; ela também não
deve ser usada como referência causal retrospectiva.

**Custo de saída é uma estimativa baseada em volatilidade e liquidez, não
slippage real observado.** Volume negociado não equivale a profundidade
executável. A fórmula não inclui spread, fees ou duração da execução e não
limita participação ou custos extremos. Não houve recalibração ou tuning.

## Resultado local: 30/06 a 03/07/2026

- 214.464 swaps; volume total 559.136.789,004487 USDC.
- Fluxo líquido +5.646.214,418481 USDC; retorno inicial/final +9,674572%.
- 5.760 janelas 1m, 1.152 janelas 5m e 384 janelas 15m.
- 32 minutos sem swaps e 189 minutos com volatilidade insuficiente.
- Volume 1m: mediana 58.724,54205250 USDC; p95 301.587,20191515 USDC.
- Volatilidade 1m: mediana 0,00029026; p95 0,00061411.
- Stress: 138.240 linhas, 24 combinações por minuto, 24 linhas de resumo.
- 4.536 custos NaN (189 minutos × 24) e 768 participações NaN (32 × 24).

| Posição | Custo NORMAL mediano (%) | Custo NORMAL p95 (%) |
| --- | ---: | ---: |
| 100k | 0,031622 | 0,544392 |
| 500k | 0,070708 | 1,217297 |
| 1M | 0,099996 | 1,721518 |

Foram preservadas 230 estimativas acima de 100%. O máximo é 23.223,215471%,
para 1M em STRESS_75 em 02/07 às 08:50 UTC: volume disponível 2,487823 USDC e
volatilidade 0,36629593. Esse extremo demonstra a extrapolação da fórmula em
volume muito baixo; não é evidência de slippage observado. Preços de origem
variam de 64,516129 a 166,666667; volumes de trades de 0,000002 a 217.205,055503
USDC. Nenhum foi removido por critério estatístico.

## Validações e outputs

16 testes locais passaram (10 de limpeza existentes e 6 novos). Os novos
testes cobrem continuidade nos quatro dias, lacunas, conservação, volatilidade
amostral, empates, duplicatas, percentis e MAD zero, NaN e fórmula do stress.
A auditoria dos CSVs reais verificou conservação **por dia e frequência** de
swaps, volume e fluxo líquido; continuidade, ausência de duplicatas/infinitos
e NaN somente quando esperado. Hashes e mtimes de 38 arquivos protegidos
permaneceram idênticos, incluindo históricos, classificações, checkpoints,
consolidado, módulos não envolvidos e outputs de risco/backtest.

Arquivos gerados em `data/processed/`:

- `liquidity_1m.csv`, `liquidity_5m.csv`, `liquidity_15m.csv`;
- `liquidity_features_1m.csv`, `liquidity_baseline_summary.csv`;
- `exit_cost_stress_1m.csv`, `exit_cost_summary.csv`.
