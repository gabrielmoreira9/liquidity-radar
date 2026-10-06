from pathlib import Path

import numpy as np
import pandas as pd


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data/processed"
INPUT_PATH = OUTPUT_DIR / "liquidity_1m.csv"
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


def build_features(windows):
    windows = windows.copy()
    windows["timestamp"] = pd.to_datetime(windows["timestamp"], utc=True)
    windows = windows.sort_values("timestamp", kind="stable").reset_index(drop=True)
    summary_rows = []
    # This descriptive baseline uses the whole dataset, not a causal rolling baseline.
    for metric in METRICS:
        statistics = baseline_statistics(windows[metric])
        summary_rows.append({"metric": metric, **statistics})
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
    return windows, pd.DataFrame(summary_rows)


def main():
    windows = pd.read_csv(INPUT_PATH)
    features, summary = build_features(windows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    features.to_csv(OUTPUT_DIR / "liquidity_features_1m.csv", index=False)
    summary.to_csv(OUTPUT_DIR / "liquidity_baseline_summary.csv", index=False)

    print(f"Janelas analisadas: {len(features)}")
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
