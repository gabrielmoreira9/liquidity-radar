# Backend MVP — auditoria final e validação

Executado em 08/10/2026, para integração antes da submissão de 12/10/2026.
Escopo: camada HTTP sobre resultados existentes. Não houve pesquisa adicional,
coleta, treinamento, tuning, V3, reconstrução do pipeline, deploy ou commit/push.

## Auditoria dos módulos existentes

| Módulo | Verificação e conclusão |
| --- | --- |
| `src/data.py` | Checkpoints diários, preservação de histórico concluído, mesma página em retries, ciclos de recuperação e exit codes 0/1/130. Não foi importado/executado pela API nem iniciada coleta. |
| `src/classify_history.py` | CSV por dia, signatures persistidas como referência de resume, escrita exclusivamente pela thread principal, backoff compartilhado e diagnósticos de rede sem UNKNOWN artificial. É CLI legado (`python src/classify_history.py`), não componente de startup da API. |
| `src/clean_history.py` | Join por signature 1:1, somente SIMPLE_SWAP, conservação e validações de fonte. Suíte existente passou; não foi executado o processamento sobre fontes locais. |
| `src/liquidity.py` | CSVs 1m/5m/15m ordenados e contínuos, janelas vazias explícitas, volume/fluxo/contagem conservados. API confere correspondência com os minutos únicos do V2 e conserva totais entre os intervalos. |
| `src/features.py` | Estatísticas globais são descritivas e separadas das versões causais. A API não usa features globais como scores ou referência histórica. Testes de causalidade passaram. |
| `src/stress.py` | Fórmula e cenários originais preservados. A API consome custo/flags do output causal V2, evitando o liquidity_multiple global do CSV descritivo antigo. NaN não vira custo zero, custo >100% não é limitado. |
| `src/risk_v2.py` | Versão original fixada por manifesto/hash, 120 observações válidas anteriores, referência 100k/NORMAL, três indicadores separados. API preserva scores, componentes, classes, razões e flags; não executa o engine. |
| `src/backtest_v2.py` | Resultados anteriores preservados por hash, thresholds congelados no treino, targets posteriores à decisão e tratamento de NaN/horizonte incompleto. API informa que o resumo é retrospectivo e quando a execução original o tornou disponível. |

Não foi encontrado bloqueio metodológico novo que exigisse modificar esses oito
módulos para a demonstração. O import legado de classificação usa o diretório do
script para localizar diagnostics; não se promete `python -m src.classify_history`.
O servidor não importa esse módulo, nem o coletor.

Os bloqueios reais de integração eram FastAPI/Pydantic ausentes, `requirements.txt`
e README vazios e ausência de contrato HTTP sobre os CSVs. Foram resolvidos com
dependências testadas e fixadas, contratos explícitos, cache de startup, exemplos,
documentação e amostra derivada versionável. Formatos potencialmente incompatíveis
foram tratados na nova camada: timestamps CSV com +00:00 viram ISO UTC com Z;
NaN/inf viram null; flags permanecem booleanos; volume/fluxo são contados por minuto
único, sem multiplicar por 24 posições/cenários; campos de unidades ficam explícitos.

## Testes automatizados

Comando no interpretador configurado:

```sh
python -m unittest discover -s tests -v
```

**92 testes aprovados, zero falhas na execução final (13,296 segundos).**
São 62 anteriores e 30 novos de API/startup. A cobertura nova inclui:

- Health e contratos dos oito endpoints; OpenAPI estático/TypeScript sem divergência.
- ISO UTC, números finitos/null, NaN de custo sem classe LOW artificial.
- Parâmetros, enums, timezone, limites, ordem, paginação, filtros e erros HTTP.
- Corte `as_of`, somente prefixos fechados e barras 5m/15m ainda abertas excluídas.
- Scores 1m originais no fechamento das séries, sem média/recalculo em outra frequência.
- Scores/regimes/componentes consistentes; flags e custos propagados para seis
  posições/quatro cenários; custo extrapolado preservado sem clipping.
- Identificação HISTORICAL e distinção entre os três indicadores.
- Nenhuma chamada Helius, nenhuma leitura CSV dentro de requests, cache carregado
  uma vez e nenhuma mutação dos datasets/cache pelos endpoints.
- CORS com allowlist, inclusive nas respostas de erro; ausência de endpoints de escrita.
- Clone sem CSVs inicia em DEMO; conjunto completo parcial/corrompido fica indisponível
  com 503, sem fallback silencioso nem paths/secrets no JSON.
- Falhas inesperadas retornam 500 genérico, sem traceback ou texto da exceção.

Na primeira execução apenas da nova suíte, um fixture tentou corromper um regime
com NORMAL em uma linha já NORMAL; portanto não houve corrupção e a expectativa
de rejeição estava errada. O fixture passou a usar INVALID. Foi uma correção de
teste, sem alterar fórmula, score, threshold ou dataset. A execução final passou.

O Starlette 1.3.1 emite aviso de depreciação do TestClient com httpx 0.28.1. Essa
combinação está fixada/testada e funciona; o aviso não é falha da API. O teste
intencional de startup inválido registra apenas a classe DatasetError, sem paths
ou conteúdo da exceção. `python -m pip check` retornou **No broken requirements found**.

## Servidor real e frontend

O sandbox impediu inicialmente bind/connect em localhost; isso foi uma restrição
do ambiente, não um erro de startup/dados. Após autorizar acesso local, iniciou:

```sh
python -m uvicorn src.api:app --host 127.0.0.1 --port 8000 --env-file .env.example
```

O servidor em modo FULL respondeu HTTP 200 aos oito endpoints e também a `/docs`
e `/openapi.json`. Um cliente Node executou o `fetchRadar` do TypeScript gerado
contra esse servidor real, conferindo identidade HISTORICAL/FULL, erro 422 e
exclusão de uma barra 5m antes de seu fechamento. Parsing e execução nativos do
TypeScript ocorreram em Node 26.9.0; não foi executado um build do Next.js separado.

Totais do overview: 5.760 minutos, 214.464 swaps, 559.136.789,004487 USDC de volume.
Medições informais de uma passagem local, sem pretensão de benchmark: overview
15 ms, market 2 ms, forward 3 ms, positions 9 ms, exit cost 9 ms, history default
300 itens 54 ms, backtest 5 ms. Health inicial 75 ms incluiu inicialização do cliente.
Não houve releitura/recalculo para cada request.

Contratos do frontend estão em `contracts/openapi.json` e
`contracts/liquidity-radar.ts`; respostas reais em `docs/api_examples.json`.
O frontend pode ser desenvolvido de forma independente, com tipos/HTTP/CORS e
sem instalar Python ou acessar CSVs. Nenhum frontend foi implementado aqui.

## Preservação e segurança

110 arquivos preexistentes de dados/módulos foram fotografados antes da tarefa e
comparados depois por SHA-256 e mtime: todos intactos. Históricos, classificações,
checkpoints, fórmulas, risk V2, backtests e resultados anteriores não foram escritos.
Não houve commit, push ou deploy.

A amostra de 120 minutos contém 2.880 linhas de posição/cenário. Comparação
independente conferiu **igualdade exata dos 47 campos de risco selecionados** com os
CSVs originais, incluindo floats/flags/ausências. `snapshot.json.gz` tem 176.618
bytes, sem inventar observações. O manifesto interno registra hashes e período
de seleção, sem paths locais ou signatures. Não houve filtro de outliers.

A varredura comparou os valores reais de credenciais do `.env` local com os novos
códigos, contratos, documentação/exemplos e JSON descomprimido da amostra: nenhuma
credencial foi encontrada nesses entregáveis. O `.env` permaneceu intacto e não é
carregado pela API; o comando recomendado usa somente `.env.example`.

`git check-ignore` confirmou proteção de `.env`, CSVs processados e históricos;
`.env.example`, contratos e demo segura são versionáveis. Não há arquivos de dados
ou `.env` já rastreados pelo Git nesta verificação. A API não monta diretórios como
arquivos estáticos nem expõe signatures/checkpoints/dados brutos. O `.gitignore`
existente foi preservado, pois já atende a essas proteções.

## Arquivos desta entrega

Criados: `src/api.py`, `src/api_contract.py`, `src/api_data.py`, `tests/test_api.py`,
`.env.example`, `scripts/build_demo.py`, `scripts/export_api_contract.py`,
`contracts/openapi.json`, `contracts/liquidity-radar.ts`, `docs/api.md`,
`docs/api_examples.json`, este relatório, `data/demo/README.md` e
`data/demo/snapshot.json.gz`.

Modificados: somente `README.md` e `requirements.txt` entre os arquivos preexistentes.

## Limitações conhecidas e conclusão

- Snapshot histórico, sem LIVE/atualização automática; trocar snapshot exige restart.
- Apenas quatro dias de dados e um dia de teste previamente inspecionado; overlap,
  saturação e ausências impedem afirmações fortes de capacidade preditiva.
- Forward é sinal exploratório, não probabilidade calibrada; Current é severidade atual.
- Exit cost é proxy não validado de volatilidade/turnover, não slippage real/cotação;
  extrapolações permanecem explícitas e não demonstram profundidade executável.
- Fechamento é disponibilidade teórica, não medição de ingestão/finality.
- API local de leitura, sem autenticação/infrastrutura de produção/deploy. CORS não é
  autenticação. Um worker evita duplicar o cache completo em memória.
- O contrato foi testado por HTTP/TypeScript, mas o frontend Next.js permanece em
  outro projeto e seu build é responsabilidade da integração separada.

**Backend MVP pronto para integração e demonstração histórica.** Inicia localmente,
serve todos os endpoints, passa na suíte, mantém rastreabilidade e não altera os
modelos/outputs anteriores. A prontidão técnica não elimina as ressalvas econômicas
e preditivas que devem continuar visíveis no frontend.
