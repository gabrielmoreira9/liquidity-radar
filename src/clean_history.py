"""Clean all matched daily histories, retaining only structural SIMPLE_SWAP.

No price/volume/volatility thresholds or outlier removal. Validate all daily
pairs and the consolidated dataset before publishing any derived CSV. Source
CSVs and classification/checkpoint files are read-only. Timestamp ties are
valid; duplicate signatures are not. UTC sorting preserves ties stably.
"""
from datetime import datetime
import filecmp
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
SWAPS_PATH = BASE_DIR / "data/historical/swaps/sol_usdc_swaps_2026-06-30.csv"
CLASSIFIED_PATH = BASE_DIR / "data/historical/classified/sol_usdc_classified_2026-06-30.csv"
OUTPUT_PATH = BASE_DIR / "data/processed/sol_usdc_swaps_clean_2026-06-30.csv"
SWAPS_DIR = BASE_DIR / "data/historical/swaps"
CLASSIFIED_DIR = BASE_DIR / "data/historical/classified"
OUTPUT_DIR = BASE_DIR / "data/processed"
COLUMNS = ["timestamp", "signature", "usdc_delta", "sol_delta", "price", "volume_usdc", "direction"]
CATEGORIES = {"SIMPLE_SWAP", "COMPOSITE_SWAP", "LIQUIDITY_OPERATION", "UNKNOWN"}
RTOL = 1e-9
ATOL = 1e-8


def validate_timestamps(values, day=None):
    if values.isna().any() or not values.astype(str).str.contains(r"(?:Z|[+-]\d{2}:\d{2})$", regex=True).all():
        raise ValueError("Timestamp ausente ou sem timezone explícito.")
    timestamps = pd.to_datetime(values, utc=True, errors="raise", format="mixed")
    if timestamps.isna().any():
        raise ValueError("Timestamp ausente.")
    if day is not None and not timestamps.dt.strftime("%Y-%m-%d").eq(day).all():
        raise ValueError(f"Timestamp fora do dia UTC {day}.")
    return timestamps


def clean_history(swaps, classified, day=None):
    for name, frame, required in (
        ("histórico", swaps, COLUMNS),
        ("classificação", classified, ["signature", "transaction_type"]),
    ):
        if not set(required) <= set(frame.columns):
            raise ValueError(f"Colunas obrigatórias ausentes no dataset de {name}.")
        if frame["signature"].isna().any() or frame["signature"].eq("").any():
            raise ValueError(f"Signatures ausentes no dataset de {name}.")
        if not frame["signature"].map(lambda value: isinstance(value, str) and bool(value.strip()) and value == value.strip()).all():
            raise ValueError(f"Signature inválida no dataset de {name}.")
        if frame["signature"].duplicated().any() or frame.duplicated().any():
            raise ValueError(f"Signatures duplicadas no dataset de {name}; nenhum registro foi removido automaticamente.")
    if not classified["transaction_type"].isin(CATEGORIES).all():
        raise ValueError("Classificação contém categorias inválidas ou ausentes.")
    source_keys, classified_keys = set(swaps["signature"]), set(classified["signature"])
    if source_keys != classified_keys:
        raise ValueError(f"Correspondência de signatures incompleta: {len(source_keys-classified_keys)} sem classificação; "
                         f"{len(classified_keys-source_keys)} classificações sem swap correspondente.")
    validate_timestamps(swaps["timestamp"], day)
    numeric = swaps[["usdc_delta", "sol_delta", "price", "volume_usdc"]].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Métricas numéricas ausentes ou infinitas no histórico.")
    if not swaps["direction"].isin(["SOL_IN", "SOL_OUT"]).all():
        raise ValueError("Direction inválida ou ausente no histórico.")
    if numeric[["price", "volume_usdc"]].lt(0).any().any():
        raise ValueError("Preço ou volume negativo no histórico.")
    if not np.isclose(numeric["volume_usdc"], numeric["usdc_delta"].abs(), rtol=RTOL, atol=ATOL).all():
        raise ValueError("volume_usdc inconsistente com abs(usdc_delta).")
    joined = swaps[COLUMNS].merge(
        classified[["signature", "transaction_type"]],
        on="signature", how="left", validate="one_to_one", sort=False,
    )
    if joined["transaction_type"].isna().any():
        raise ValueError("Há signatures sem classificação; conclua a classificação antes de limpar.")
    clean = joined.loc[joined["transaction_type"].eq("SIMPLE_SWAP")].copy()
    if not clean["transaction_type"].eq("SIMPLE_SWAP").all():
        raise ValueError("O resultado contém uma categoria diferente de SIMPLE_SWAP.")
    if clean["signature"].duplicated().any() or clean.duplicated().any():
        raise ValueError("O resultado contém duplicatas.")
    with np.errstate(divide="ignore", invalid="ignore"):
        expected_price = (clean["usdc_delta"] / clean["sol_delta"]).abs()
    consistent = (
        np.isfinite(clean["price"]) & np.isfinite(expected_price)
        & np.isclose(clean["price"], expected_price, rtol=RTOL, atol=ATOL)
    )
    if not consistent.all():
        raise ValueError(f"{int((~consistent).sum())} preços inconsistentes; nenhum arquivo foi salvo.")
    category_counts = joined["transaction_type"].value_counts()
    excluded_count = sum(int(category_counts.get(category, 0)) for category in CATEGORIES - {"SIMPLE_SWAP"})
    if len(joined) != len(swaps) or len(clean) != int(category_counts.get("SIMPLE_SWAP", 0)) or len(clean) + excluded_count != len(swaps):
        raise ValueError("Conservação das contagens violada.")
    # Sort using parsed UTC timestamps but preserve original timestamp strings.
    clean = clean.assign(_sort_timestamp=validate_timestamps(clean["timestamp"], day)).sort_values("_sort_timestamp", kind="stable")
    return clean[COLUMNS].reset_index(drop=True)


def discover_daily_pairs():
    def discover(directory, prefix):
        found = {}
        for path in sorted(directory.glob(f"{prefix}????-??-??.csv")):
            day = path.stem[len(prefix):]
            if datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") != day or not path.is_file():
                raise ValueError(f"Arquivo diário inválido: {path}.")
            found[day] = path
        return found
    swaps = discover(SWAPS_DIR, "sol_usdc_swaps_")
    classifications = discover(CLASSIFIED_DIR, "sol_usdc_classified_")
    if not swaps:
        raise ValueError("Nenhum histórico diário encontrado.")
    if set(swaps) != set(classifications):
        raise ValueError(f"Dias sem par correspondente: sem classificação={sorted(set(swaps)-set(classifications))}; "
                         f"sem swaps={sorted(set(classifications)-set(swaps))}.")
    return [(day, swaps[day], classifications[day]) for day in sorted(swaps)]


def build_outputs():
    outputs, summary_rows, seen = [], [], set()
    for day, swaps_path, classified_path in discover_daily_pairs():
        swaps = pd.read_csv(swaps_path)
        classified = pd.read_csv(classified_path)
        clean = clean_history(swaps, classified, day)
        if seen.intersection(swaps["signature"]):
            raise ValueError("Signatures duplicadas entre dias; arquivos não serão publicados.")
        seen.update(swaps["signature"])
        outputs.append((OUTPUT_DIR / f"sol_usdc_swaps_clean_{day}.csv", clean))
        counts = classified["transaction_type"].value_counts()
        summary_rows.append({"day": day, "original": len(swaps), "retained": len(clean), "excluded": len(swaps)-len(clean),
                             **{category: int(counts.get(category, 0)) for category in sorted(CATEGORIES)}})
    consolidated = pd.concat([frame for _, frame in outputs], ignore_index=True)
    timestamps = validate_timestamps(consolidated["timestamp"])
    consolidated = consolidated.assign(_sort_timestamp=timestamps).sort_values("_sort_timestamp", kind="stable")[COLUMNS].reset_index(drop=True)
    if consolidated["signature"].duplicated().any() or consolidated.duplicated().any():
        raise ValueError("Duplicatas no consolidado.")
    summary = pd.DataFrame(summary_rows)
    if len(consolidated) != summary["retained"].sum() or not summary["original"].eq(summary["retained"]+summary["excluded"]).all():
        raise ValueError("Contagem global não conservada.")
    if not validate_timestamps(consolidated["timestamp"]).is_monotonic_increasing:
        raise ValueError("Consolidado fora da ordem cronológica.")
    outputs.append((OUTPUT_DIR / "sol_usdc_swaps_clean_consolidated.csv", consolidated))
    return outputs, summary


def publish_outputs(outputs):
    """Stage all validated outputs first; replace each destination atomically."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    staged = []
    try:
        for path, frame in outputs:
            with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", dir=OUTPUT_DIR,
                                             prefix=f".{path.stem}_", suffix=".tmp", delete=False) as target:
                temporary = Path(target.name)
                staged.append((temporary, path))
                frame.to_csv(target, index=False)
                target.flush()
                os.fsync(target.fileno())
        for temporary, path in staged:
            if not path.exists() or not filecmp.cmp(temporary, path, shallow=False):
                temporary.replace(path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main():
    outputs, summary = build_outputs()
    publish_outputs(outputs)
    print(summary.to_string(index=False))
    original, retained, excluded = (int(summary[column].sum()) for column in ("original", "retained", "excluded"))
    print(f"Global: {len(summary)} dias | originais {original:,} | mantidas {retained:,} | excluídas {excluded:,} | "
          f"mantido {retained/original*100 if original else 0:.2f}%")
    print("Validações OK: join 1:1 com correspondência exata, signatures únicas por dia/global, integridade das colunas,")
    print(f"timestamps UTC/dia/ordem, contagens conservadas, somente SIMPLE_SWAP; price rtol={RTOL}, atol={ATOL}.")
    print("Nenhum filtro estatístico; históricos e classificações preservados.")
    for path, frame in outputs:
        print(f"Arquivo: {path} | linhas: {len(frame):,}")


if __name__ == "__main__":
    main()
