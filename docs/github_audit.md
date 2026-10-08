# Auditoria final para GitHub — 08/10/2026

Resultado: backend reproduzível com amostra segura e entregáveis preparados para
commit. Esta auditoria não executou commit, push, coleta ou alteração de modelos.
Nenhum dataset, checkpoint ou arquivo de credenciais foi removido do disco.

## Arquivos reais que compõem a entrega

- `src/`: todos os 16 módulos Python existentes, incluindo `api.py`, `api_data.py`,
  `api_contract.py`, coleta/classificação/limpeza, auditoria, modelos e backtests.
  `src/alerts.py` é um placeholder vazio já existente, sem implementação de alertas.
- `tests/`: seis arquivos de testes, incluindo `test_api.py` e as suítes anteriores.
- Raiz: `main.py`, `requirements.txt`, `README.md`, `.gitignore`, `.env.example`.
- `contracts/openapi.json`: especificação dos oito endpoints.
- `contracts/liquidity-radar.ts`: tipos gerados e cliente HTTP `fetchRadar`.
- `scripts/build_demo.py`: extração opcional sem inventar dados ou sobrescrever a amostra.
- `scripts/export_api_contract.py`: exportação offline de contratos e exemplos.
- `docs/api.md`, `docs/api_examples.json`, `docs/backend_mvp_validation.md`,
  `docs/backtest_v2.md`, `docs/risk_engine_v2.md`, `docs/statistical_pipeline.md`,
  `docs/quantitative_technical_audit.md` e este relatório.
- `data/demo/README.md` e `data/demo/snapshot.json.gz`: amostra real segura,
  176.618 bytes, 120 minutos e seis posições/quatro cenários. Os resultados do
  backtest dentro da amostra mantêm a avaliação completa original, identificada
  separadamente do recorte dos indicadores.

Não existem imagens, logo ou outros assets de frontend neste repositório. `app/`
está vazio. Não foram criados assets fictícios nem anunciado um frontend inexistente;
os assets do Next.js separado devem ser versionados no projeto onde forem produzidos.
Diretórios vazios não são arquivos versionáveis do Git e não bloqueiam o backend.

## Segurança e índice

Foram inspecionados os 40 arquivos inicialmente candidatos à entrega, somando
729.079 bytes antes das pequenas correções documentais e deste relatório. Nenhum
arquivo candidato excede 1 MB; o maior é a amostra comprimida de 176.618 bytes.
O JSON descomprimido também foi examinado: não contém signatures, transações
brutas, credenciais ou paths locais. A amostra não é confundida com LIVE.

A varredura procurou correspondências com credenciais reais do `.env` local,
variantes codificadas em URL, marcadores de chaves privadas, literais suspeitos de
tokens/passwords e URLs com segredos. Somente resultados e localização de possíveis
problemas foram tratados; os valores das credenciais não foram impressos.
Não foram encontradas credenciais nos candidatos.

O histórico Git acessível pelas refs locais também foi auditado, incluindo os
25 blobs existentes. Não foi encontrada credencial/chave privada nem `.env` com
credenciais nesse histórico. Não houve reescrita. A varredura não é prova universal
contra todo formato possível de segredo; se uma chave for identificada futuramente
em commits publicados, será necessário rotacioná-la, independentemente do .gitignore.

Não havia arquivo já rastreado que devesse estar ignorado. Portanto, não foi
necessário executar `git rm --cached`. Os novos módulos/contratos/testes/documentação
estavam inicialmente sem rastreamento; foram adicionados explicitamente ao índice,
junto com as alterações auditadas. Estão preparados para o próximo commit,
mas ainda não constituem um commit ou publicação no GitHub.

## .gitignore e correções

As regras existentes protegem `.env`/variantes, chaves privadas, datasets raw/
historical, CSVs e artefatos processados, checkpoints, logs, caches, venvs e IDE.
Documentação e `.gitkeep` das pastas de dados continuam permitidos. `.env.example`
contém apenas configurações públicas/fictícias e é explicitamente permitido.

Foi acrescentada proteção para CSVs processados comprimidos (`.csv.gz`, `.csv.zip`,
`.csv.zst`), JSON processado comprimido e parquet comprimido. Isso não ignora
`data/demo/snapshot.json.gz`, os JSONs de contratos/exemplos ou qualquer pasta de
código/documentação. Verificações de regras também cobriram arquivos simulados sem
criá-los no disco.

Três comandos documentados continham o caminho específico de um ambiente Conda.
Foram substituídos por comandos portáveis usando `python` em `README.md` e
`docs/backend_mvp_validation.md`. Os comandos de instalação via venv continuam
documentados. Nenhum caminho de máquina foi encontrado no código/contratos/amostra
que compõem a entrega. Modelos, fórmulas, thresholds e resultados não foram alterados.

## Reprodução sem dados ignorados

Foi criada uma cópia independente contendo somente arquivos candidatos ao Git,
sem `.git`, `.env`, IDE, caches, raw/historical ou CSVs processados completos.
Não foi um clone remoto: foi uma reprodução local da árvore candidata, permitindo
validar a entrega antes do commit. Um venv novo, sem herdar pacotes do ambiente,
instalou `requirements.txt` com sucesso; `pip check` não encontrou dependências
quebradas. O ambiente original do projeto não foi modificado por essa instalação.

Na cópia limpa, **92 testes passaram em 13,248 segundos**, zero falhas. A API
iniciou por Uvicorn usando `.env.example`; `RADAR_DATA_MODE=auto` selecionou DEMO.
Os oito endpoints responderam HTTP 200, assim como `/docs` e `/openapi.json`.
O overview confirmou os 120 minutos da amostra e respostas HISTORICAL/DEMO.
O servidor temporário foi encerrado depois da verificação. Nenhuma chamada Helius
foi feita; o único download externo foi a instalação das dependências Python.

Uma pessoa com Python 3.12 e acesso ao índice de pacotes pode executar:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m uvicorn src.api:app --host 127.0.0.1 --port 8000 --env-file .env.example
```

Swagger: `http://127.0.0.1:8000/docs`. O frontend usa a URL HTTP e os tipos/cliente
TypeScript de `contracts/`, com CORS configurável. Não precisa de CSVs completos,
credenciais, código Python instalado em seu próprio projeto ou nova coleta.
Os pipelines offline de reconstrução dos modelos exigem os dados locais ignorados;
eles não fazem parte do startup da API ou do requisito de demonstração em um clone.

## Estado final

Backend e demo prontos para commit e integração. Fontes originais, datasets,
classificações e checkpoints permanecem no disco. Os arquivos proibidos não fazem
parte do índice preparado. O próximo commit/publicação deve ser executado pelo
responsável, após revisar `git diff --cached --stat` e `git status --short`.
