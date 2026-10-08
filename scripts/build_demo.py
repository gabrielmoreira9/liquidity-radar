"""Produce a SMALL real-data snapshot; never invent or recompute observations.

Run explicitly: python -m scripts.build_demo. Existing demo files are protected.
"""
import json
import gzip
from pathlib import Path

import numpy as np
import pandas as pd

from src.api_data import DatasetStore, REQUIRED_FILES, ROOT, Settings, digest


def records(frame):
    # Preserve Python float round trips; avoid pandas.to_json's precision truncation.
    def clean(value):
        if value is None or pd.isna(value):
            return None
        if isinstance(value, pd.Timestamp):
            return value.isoformat().replace("+00:00", "Z")
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, float) and not np.isfinite(value):
            return None
        return value
    return [{key: clean(value) for key, value in row.items()} for row in frame.to_dict("records")]


def build(destination=ROOT/"data/demo/snapshot.json.gz"):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("Demo already exists; use a new destination to preserve it.")
    settings = Settings(mode="full")
    store = DatasetStore.load(settings)
    start, end = pd.Timestamp("2026-07-03T22:00:00Z"), pd.Timestamp("2026-07-04T00:00:00Z")
    risk = store.risk.loc[store.risk.timestamp.ge(start) & store.risk.available_at.le(end)]
    liquidity = {k: records(f.loc[f.timestamp.ge(start) & f.available_at.le(end)].drop(columns="available_at"))
                 for k, f in store.liquidity.items()}
    bundle = {"format_version": 1, "provenance": {**store.provenance,
        "source_sha256": {name: digest(settings.processed_dir/name) for name in REQUIRED_FILES},
        "selection": "Unmodified closed windows 2026-07-03T22:00:00Z to 2026-07-04T00:00:00Z; full existing backtest metrics.",
        "source_kind": "EXISTING_PROCESSED_DATA_ONLY"},
        "risk_rows": records(risk), "liquidity": liquidity, "backtest_rows": records(store.backtest)}
    # Prevent accidentally shipping local paths through inherited provenance.
    text = json.dumps(bundle, separators=(",", ":"), allow_nan=False)
    assert str(ROOT) not in text and "api-key=" not in text.lower()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as compressed:
            compressed.write(text.encode("utf-8"))
    print(f"Created real-data demo: {len(risk)//24} minutes, {destination.stat().st_size:,} bytes.")


if __name__ == "__main__":
    build()
