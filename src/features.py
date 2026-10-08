"""Full-period descriptive features, never a causal historical prediction baseline.

Global percentiles, median and MAD include all four days. Using these as known-at-t
references in retrospective predictions would introduce look-ahead bias.
"""
from pathlib import Path
from bisect import bisect_right, insort

import numpy as np
import pandas as pd


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data/processed"
INPUT_PATH = OUTPUT_DIR / "liquidity_1m.csv"
MIN_HISTORY = 120  # Valid preceding minutes per metric; current minute excluded.
METRICS = (
    "swap_count",
    "volume_usdc",
    "avg_trade_size_usdc",
    "volatility",
    "net_flow_usdc",
    "flow_imbalance",
)


def baseline_statistics(values):
    """Compute global statistics over available observations, ignoring NaN."""
    median = values.median()
    percentiles = values.quantile([0.05, 0.25, 0.75, 0.95, 0.99])
    return {
        "median": median,
        "p05": percentiles.loc[0.05],
        "p25": percentiles.loc[0.25],
        "p75": percentiles.loc[0.75],
        "p95": percentiles.loc[0.95],
        "p99": percentiles.loc[0.99],
        "mad": (values - median).abs().median(),
    }


def empirical_percentile(values):
    """100 * fraction of valid observations <= x; ties share the same rank."""
    return values.rank(method="max", pct=True) * 100


def expanding_statistics(values, min_history=MIN_HISTORY):
    """Strictly prior valid observations. ECDF ties and quantiles match description.

    At row t the history contains rows < t, not the current observation. Missing
    current values can still have historical thresholds; their score remains NaN.
    The 120-observation default is an initial information policy, not calibration.
    """
    if not isinstance(min_history, int) or min_history < 1:
        raise ValueError("min_history deve ser inteiro positivo.")
    values = pd.to_numeric(values, errors="raise")
    if np.isinf(values).any():
        raise ValueError("Histórico contém infinito.")
    history, rows = [], []
    for value in values:
        row = {"history_count": len(history), "percentile": np.nan, "robust_z": np.nan,
               **{key: np.nan for key in ("median", "p05", "p25", "p75", "p95", "p99", "mad")}}
        if len(history) >= min_history:
            sample = np.asarray(history)
            q = np.quantile(sample, [.05, .25, .5, .75, .95, .99])
            row.update(dict(zip(("p05", "p25", "median", "p75", "p95", "p99"), q)))
            row["mad"] = np.median(np.abs(sample - row["median"]))
            if pd.notna(value):
                row["percentile"] = 100. * bisect_right(history, value) / len(history)
                if row["mad"] > 0:
                    row["robust_z"] = .6745 * (value - row["median"]) / row["mad"]
        rows.append(row)
        if pd.notna(value):
            insort(history, float(value))
    result = pd.DataFrame(rows, index=values.index)
    if np.isinf(result.select_dtypes(include="number").to_numpy()).any():
        raise ValueError("Estatísticas causais excedem a precisão numérica; nenhum infinito é permitido.")
    return result


def prepare_windows(windows):
    windows = windows.copy()
    if not {"timestamp", *METRICS} <= set(windows.columns):
        raise ValueError("Colunas obrigatórias ausentes.")
    windows["timestamp"] = pd.to_datetime(windows["timestamp"], utc=True, errors="raise")
    windows = windows.sort_values("timestamp", kind="stable").reset_index(drop=True)
    if windows.empty or windows["timestamp"].isna().any() or windows["timestamp"].duplicated().any():
        raise ValueError("Janelas vazias ou timestamps inválidos/duplicados.")
    if not windows["timestamp"].eq(windows["timestamp"].dt.floor("min")).all():
        raise ValueError("Timestamp deve identificar o início exato do minuto.")
    if not windows["timestamp"].diff().dropna().eq(pd.Timedelta(minutes=1)).all():
        raise ValueError("Entrada deve ter continuidade de um minuto.")
    for column in METRICS:
        windows[column] = pd.to_numeric(windows[column], errors="raise")
    if np.isinf(windows.select_dtypes(include="number").to_numpy()).any():
        raise ValueError("Entrada contém infinito.")
    if windows[["swap_count", "volume_usdc", "volatility"]].lt(0).any().any():
        raise ValueError("Contagem, volume ou volatilidade negativos.")
    if windows["flow_imbalance"].abs().gt(1 + 1e-12).any():
        raise ValueError("Flow imbalance fora de [-1, 1].")
    return windows


def build_causal_features(windows, min_history=MIN_HISTORY):
    """Separate output: no global percentile, robust z or summary is copied.

    timestamp is window start, available_at its close (minimum theoretical
    availability, not a measured ingestion/finality time). Prior baselines are
    known at window start; current measurements become known only at its close.
    """
    windows = prepare_windows(windows)
    # Whitelist measurements: never accidentally propagate descriptive features.
    measurements = ["timestamp", *METRICS]
    result = windows[measurements].copy()
    result["available_at"] = result["timestamp"] + pd.Timedelta(minutes=1)
    result["historical_reference_before"] = result["timestamp"]
    result["minimum_history_observations"] = min_history
    for metric in (*METRICS, "abs_flow_imbalance"):
        values = windows["flow_imbalance"].abs() if metric == "abs_flow_imbalance" else windows[metric]
        reference = expanding_statistics(values, min_history)
        for column in reference:
            result[f"{metric}_causal_{column}"] = reference[column]
    return result


def build_features(windows):
    windows = prepare_windows(windows)
    summary_rows = []
    # This descriptive baseline uses the whole dataset, not a causal rolling baseline.
    for metric in METRICS:
        statistics = baseline_statistics(windows[metric])
        summary_rows.append({"metric": metric, **statistics,
                             "valid_count": windows[metric].count(),
                             "missing_count": windows[metric].isna().sum()})
        mad = statistics["mad"]
        windows[f"{metric}_robust_z"] = (
            0.6745 * (windows[metric] - statistics["median"]) / mad
            if pd.notna(mad) and mad > 0
            else np.nan
        )
    for metric in ("volume_usdc", "volatility"):
        windows[f"{metric}_percentile"] = empirical_percentile(windows[metric])
    windows["abs_flow_imbalance_percentile"] = empirical_percentile(
        windows["flow_imbalance"].abs()
    )
    if np.isinf(windows.select_dtypes(include="number").to_numpy()).any():
        raise ValueError("Features contêm infinito.")
    return windows, pd.DataFrame(summary_rows)


def main():
    windows = pd.read_csv(INPUT_PATH)
    features, summary = build_features(windows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    features.to_csv(OUTPUT_DIR / "liquidity_features_1m.csv", index=False)
    summary.to_csv(OUTPUT_DIR / "liquidity_baseline_summary.csv", index=False)
    build_causal_features(windows).to_csv(OUTPUT_DIR / "liquidity_features_causal_1m.csv", index=False)

    print(f"Janelas analisadas: {len(features)}")
    print("Estatísticas globais descritivas; não usar como baseline histórico em backtests (look-ahead bias).")
    print(f"Período UTC: {features['timestamp'].min()} → {features['timestamp'].max()}")
    print(f"Volatilidade insuficiente (NaN): {features['volatility'].isna().sum()}")
    for metric in ("volume_usdc", "volatility"):
        statistics = summary.set_index("metric").loc[metric]
        print(f"{metric}: mediana {statistics['median']:.8f} | p95 {statistics['p95']:.8f}")
    absolute_flow = features["flow_imbalance"].abs()
    statistics = baseline_statistics(absolute_flow)
    print(
        f"abs(flow_imbalance): mediana {statistics['median']:.8f} | "
        f"p95 {statistics['p95']:.8f}"
    )
    print("Janelas com abs(robust_z) > 3:")
    for metric in METRICS:
        count = (features[f"{metric}_robust_z"].abs() > 3).sum()
        print(f"  {metric}: {count}")
    for label, values in (
        ("abs(flow_imbalance)", absolute_flow),
        ("volatility", features["volatility"]),
    ):
        print(f"Top 5 janelas por {label} (UTC):")
        # Ignore missing observations and preserve chronological order on ties.
        for index, value in values.dropna().sort_values(ascending=False, kind="stable").head(5).items():
            print(f"  {features.loc[index, 'timestamp'].isoformat()} | {value:.8f}")


if __name__ == "__main__":
    main()
