from pathlib import Path

import numpy as np
import pandas as pd


INPUT_PATH = Path(__file__).resolve().parent.parent / "data/processed/sol_usdc_swaps.csv"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data/processed"


def aggregate_window(group):
    prices = group["price"]
    volumes = group["volume_usdc"]
    log_returns = np.log(prices / prices.shift(1)).dropna()
    price_open = prices.iloc[0]
    price_close = prices.iloc[-1]

    return pd.Series(
        {
            "swap_count": len(group),
            "volume_usdc": volumes.sum(),
            "avg_trade_size_usdc": volumes.mean(),
            "median_trade_size_usdc": volumes.median(),
            "price_open": price_open,
            "price_close": price_close,
            "price_min": prices.min(),
            "price_max": prices.max(),
            "price_return_pct": (
                (price_close / price_open - 1) * 100
                if price_open != 0
                else np.nan
            ),
            # Volatility is the standard deviation of within-window log returns.
            "volatility": log_returns.std() if len(log_returns) > 1 else np.nan,
            # Summing usdc_delta preserves the pool's USDC flow convention.
            "net_flow_usdc": group["usdc_delta"].sum(),
            # Positive means net USDC inflow to the pool; negative means outflow.
            "flow_imbalance": (
                group["usdc_delta"].sum() / volumes.sum()
                if volumes.sum() > 0
                else np.nan
            ),
        }
    )


def build_liquidity_aggregations(swaps, window):
    grouped = swaps.groupby(pd.Grouper(key="timestamp", freq=window))
    aggregations = grouped.apply(aggregate_window, include_groups=False)
    return aggregations.reset_index()


if __name__ == "__main__":
    swaps = pd.read_csv(INPUT_PATH, parse_dates=["timestamp"])
    swaps["timestamp"] = pd.to_datetime(swaps["timestamp"], utc=True)
    swaps = swaps.sort_values("timestamp").reset_index(drop=True)

    total_volume = swaps["volume_usdc"].sum()
    log_returns = np.log(swaps["price"] / swaps["price"].shift(1)).dropna()
    first_price = swaps["price"].iloc[0]
    last_price = swaps["price"].iloc[-1]
    net_flow_usdc = swaps["usdc_delta"].sum()
    global_metrics = {
        "total_swaps": len(swaps),
        "total_volume_usdc": total_volume,
        "average_volume_usdc": swaps["volume_usdc"].mean(),
        "median_volume_usdc": swaps["volume_usdc"].median(),
        "initial_price": first_price,
        "final_price": last_price,
        "return_pct": (last_price / first_price - 1) * 100,
        "volatility": log_returns.std(),
        "net_flow_usdc": net_flow_usdc,
        "flow_imbalance": (
            net_flow_usdc / total_volume if total_volume > 0 else np.nan
        ),
        "sol_in_count": (swaps["direction"] == "SOL_IN").sum(),
        "sol_out_count": (swaps["direction"] == "SOL_OUT").sum(),
        "sol_in_volume_usdc": swaps.loc[
            swaps["direction"] == "SOL_IN", "volume_usdc"
        ].sum(),
        "sol_out_volume_usdc": swaps.loc[
            swaps["direction"] == "SOL_OUT", "volume_usdc"
        ].sum(),
    }

    liquidity_30s = build_liquidity_aggregations(swaps, "30s")
    liquidity_60s = build_liquidity_aggregations(swaps, "60s")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    liquidity_30s.to_csv(OUTPUT_DIR / "liquidity_30s.csv", index=False)
    liquidity_60s.to_csv(OUTPUT_DIR / "liquidity_60s.csv", index=False)

    print("Métricas globais:")
    for name, value in global_metrics.items():
        print(f"{name}: {value}")
    print("\nJanelas de 30 segundos:")
    print(liquidity_30s)
    print("\nJanelas de 60 segundos:")
    print(liquidity_60s)