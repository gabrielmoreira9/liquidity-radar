# Snapshot de demonstração seguro

`snapshot.json.gz` é um JSON comprimido com gzip (176.618 bytes), derivado somente
dos datasets processados existentes. Não contém signatures, transações brutas,
API keys ou paths locais. Inclui 120 minutos reais de 03/07/2026 (22:00–24:00 UTC),
as seis posições/quatro cenários, agregações 1m/5m/15m e as métricas completas do
backtest original de quatro dias. Metadados explicam explicitamente as coberturas
distintas. Não representa LIVE nem novos resultados estatísticos.

O recorte mantém todas as linhas e valores dos períodos escolhidos, incluindo NaN
serializado como null e flags de extrapolação. Não há filtro de outliers ou tuning.
Scores já possuem o histórico causal original anterior ao recorte; não foram
calculados usando apenas duas horas. O gzip é determinístico (mtime=0) e o JSON
preserva round trips dos floats Python. Proveniência inclui hashes dos CSVs fonte
sem seus paths absolutos.

O backend descomprime uma vez no startup; o frontend usa apenas HTTP. Em um clone
sem os datasets locais ignorados pelo Git, `RADAR_DATA_MODE=auto` seleciona DEMO.
FULL exige todos os datasets/manifests. A amostra é deliberadamente versionável;
os datasets processados completos continuam ignorados por `.gitignore`.

Extração local opcional: `python -m scripts.build_demo`, que recusa sobrescrever
esta amostra. Não é necessária para iniciar o servidor ou executar os testes.
