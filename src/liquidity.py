from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_PATH = BASE_DIR / "data/processed/sol_usdc_swaps_clean_2026-06-30.csv"
OUTPUT_DIR = BASE_DIR / "data/processed"
WINDOWS = {"1m": "1min", "5m": "5min", "15m": "15min"}


def load_swaps(paths):
    """Accept daily files; remove only identical records, including signature."""
    swaps = pd.concat([pd.read_csv(path) for path in paths], ignore_index=True)
    swaps["timestamp"] = pd.to_datetime(swaps["timestamp"], utc=True)
    swaps = swaps.drop_duplicates()
    # Stable sorting preserves source order for swaps sharing a block timestamp.
    return swaps.sort_values("timestamp", kind="stable").reset_index(drop=True)


def aggregate_window(group):
    prices = group["price"]
    volumes = group["volume_usdc"]
    log_returns = np.log(prices / prices.shift(1)).dropna()
    price_open = prices.iloc[0] if len(prices) else np.nan
    price_close = prices.iloc[-1] if len(prices) else np.nan
    total_volume = volumes.sum()
    net_flow = group["usdc_delta"].sum()
    sol_in = group["direction"] == "SOL_IN"
    sol_out = group["direction"] == "SOL_OUT"

    return pd.Series(
        {
            "swap_count": len(group),
            "volume_usdc": total_volume,
            "avg_trade_size_usdc": volumes.mean(),
            "median_trade_size_usdc": volumes.median(),
            "price_open": price_open,
            "price_close": price_close,
            "price_min": prices.min(),
            "price_max": prices.max(),
            "price_return_pct": (
                (price_close / price_open - 1) * 100
                if prices.count() >= 2 and price_open != 0
                else np.nan
            ),
            # Sample standard deviation of within-window log returns (no annualization).
            "volatility": log_returns.std() if len(log_returns) > 1 else np.nan,
            # Positive USDC flow means inflow to the pool.
            "net_flow_usdc": net_flow,
            "flow_imbalance": net_flow / total_volume if total_volume > 0 else np.nan,
            "sol_in_count": sol_in.sum(),
            "sol_out_count": sol_out.sum(),
            "sol_in_volume_usdc": volumes.loc[sol_in].sum(),
            "sol_out_volume_usdc": volumes.loc[sol_out].sum(),
        }
    )


def build_liquidity_aggregations(swaps, window):
    grouped = swaps.groupby(
        pd.Grouper(key="timestamp", freq=window, closed="left", label="left", origin="start_day")
    )
    aggregations = grouped.apply(aggregate_window, include_groups=False).reset_index()
    for column in ("swap_count", "sol_in_count", "sol_out_count"):
        aggregations[column] = aggregations[column].astype("int64")
    return aggregations


def main():
    swaps = load_swaps([INPUT_PATH])
    if swaps.empty:
        raise ValueError("O dataset não contém swaps para analisar.")
    global_metrics = aggregate_window(swaps)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    window_counts = {}
    for label, frequency in WINDOWS.items():
        aggregations = build_liquidity_aggregations(swaps, frequency)
        aggregations.to_csv(OUTPUT_DIR / f"liquidity_{label}.csv", index=False)
        window_counts[label] = len(aggregations)

    print(f"Swaps analisados: {len(swaps):,}")
    print(f"Período UTC: {swaps['timestamp'].iloc[0]} → {swaps['timestamp'].iloc[-1]}")
    print("Janelas: " + " | ".join(f"{label}: {count}" for label, count in window_counts.items()))
    print(f"Volume total USDC: {global_metrics['volume_usdc']:,.6f}")
    print(
        f"Preço inicial/final: {global_metrics['price_open']:.8f} / "
        f"{global_metrics['price_close']:.8f} | "
        f"retorno total: {global_metrics['price_return_pct']:.6f}%"
    )
    print(
        f"Volatilidade global: {global_metrics['volatility']:.8f} | "
        f"flow imbalance global: {global_metrics['flow_imbalance']:.8f}"
    )


if __name__ == "__main__":
    main()
