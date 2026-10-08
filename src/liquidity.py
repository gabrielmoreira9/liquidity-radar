from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_PATH = BASE_DIR / "data/processed/sol_usdc_swaps_clean_consolidated.csv"
OUTPUT_DIR = BASE_DIR / "data/processed"
WINDOWS = {"1m": "1min", "5m": "5min", "15m": "15min"}


def load_swaps(paths):
    """Read cleaned swaps without filtering observations or silently losing counts."""
    swaps = pd.concat([pd.read_csv(path) for path in paths], ignore_index=True)
    required = {"timestamp", "signature", "price", "volume_usdc", "usdc_delta", "sol_delta", "direction"}
    if not required <= set(swaps.columns) or swaps.empty:
        raise ValueError("Dataset vazio ou colunas obrigatórias ausentes.")
    swaps["timestamp"] = pd.to_datetime(swaps["timestamp"], utc=True, errors="raise")
    if swaps["timestamp"].isna().any() or swaps["signature"].isna().any() or swaps["signature"].duplicated().any():
        raise ValueError("Timestamp/signature ausente ou signature duplicada.")
    for column in ("price", "volume_usdc", "usdc_delta", "sol_delta"):
        swaps[column] = pd.to_numeric(swaps[column], errors="raise")
        if not np.isfinite(swaps[column]).all():
            raise ValueError(f"Dados não finitos em {column}.")
    if swaps["price"].le(0).any() or swaps["volume_usdc"].lt(0).any():
        raise ValueError("Preço não positivo ou volume negativo.")
    if not swaps["direction"].isin(["SOL_IN", "SOL_OUT"]).all():
        raise ValueError("Direção inválida.")
    validate_swap_economics(swaps)
    # Stable sorting preserves source order for swaps sharing a block timestamp.
    return swaps.sort_values("timestamp", kind="stable").reset_index(drop=True)


def validate_swap_economics(swaps):
    """Unit identities, not outlier filters. No change to source observations."""
    if swaps["sol_delta"].eq(0).any() or swaps["usdc_delta"].eq(0).any():
        raise ValueError("Swap com perna nula.")
    if not np.isclose(swaps["volume_usdc"], swaps["usdc_delta"].abs(), rtol=1e-12, atol=1e-12).all():
        raise ValueError("Volume inconsistente com delta USDC.")
    if not np.isclose(swaps["price"], (swaps["usdc_delta"] / swaps["sol_delta"]).abs(),
                      rtol=1e-12, atol=1e-12).all():
        raise ValueError("Preço inconsistente com as pernas SOL/USDC.")
    expected_in = swaps["sol_delta"].gt(0) & swaps["usdc_delta"].lt(0)
    expected_out = swaps["sol_delta"].lt(0) & swaps["usdc_delta"].gt(0)
    if not ((swaps["direction"].eq("SOL_IN") & expected_in) |
            (swaps["direction"].eq("SOL_OUT") & expected_out)).all():
        raise ValueError("Sinais das pernas incompatíveis com a direção.")


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
    if swaps.empty:
        raise ValueError("Dataset de swaps vazio.")
    swaps = swaps.sort_values("timestamp", kind="stable")
    grouped = swaps.groupby(
        pd.Grouper(key="timestamp", freq=window, closed="left", label="left", origin="start_day")
    )
    aggregations = grouped.apply(aggregate_window, include_groups=False).reset_index()
    # Complete UTC days, including empty windows at both edges and between days.
    grid = pd.date_range(swaps["timestamp"].min().floor("D"),
                         swaps["timestamp"].max().floor("D") + pd.Timedelta(days=1),
                         freq=window, inclusive="left", name="timestamp")
    aggregations = aggregations.set_index("timestamp").reindex(grid).reset_index()
    additive = ("swap_count", "volume_usdc", "net_flow_usdc", "sol_in_count",
                "sol_out_count", "sol_in_volume_usdc", "sol_out_volume_usdc")
    aggregations[list(additive)] = aggregations[list(additive)].fillna(0)
    for column in ("swap_count", "sol_in_count", "sol_out_count"):
        aggregations[column] = aggregations[column].astype("int64")
    validate_aggregations(swaps, aggregations, grid)
    return aggregations


def validate_aggregations(swaps, aggregations, grid):
    if not pd.DatetimeIndex(aggregations["timestamp"]).equals(grid):
        raise ValueError("Grade temporal incompleta ou desordenada.")
    if aggregations["swap_count"].sum() != len(swaps):
        raise ValueError("Contagem de swaps não conservada.")
    for source, target in (("volume_usdc", "volume_usdc"), ("usdc_delta", "net_flow_usdc")):
        if not np.isclose(swaps[source].sum(), aggregations[target].sum(), rtol=1e-12, atol=1e-6):
            raise ValueError(f"{target} não conservado.")
    if not (aggregations["sol_in_count"] + aggregations["sol_out_count"]).equals(aggregations["swap_count"]):
        raise ValueError("Contagens direcionais inconsistentes.")
    if not np.allclose(aggregations["sol_in_volume_usdc"] + aggregations["sol_out_volume_usdc"],
                       aggregations["volume_usdc"], rtol=1e-12, atol=1e-6):
        raise ValueError("Volumes direcionais inconsistentes.")
    if np.isinf(aggregations.select_dtypes(include="number").to_numpy()).any():
        raise ValueError("Agregação contém infinito.")


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
        print(f"{label}: janelas sem swaps = {aggregations['swap_count'].eq(0).sum()}; conservação OK")

    print(f"Swaps analisados: {len(swaps):,}")
    print(f"Período UTC: {swaps['timestamp'].iloc[0]} → {swaps['timestamp'].iloc[-1]}")
    print("Janelas: " + " | ".join(f"{label}: {count}" for label, count in window_counts.items()))
    print(f"Volume total USDC: {global_metrics['volume_usdc']:,.6f}")
    print(f"Fluxo líquido total USDC: {global_metrics['net_flow_usdc']:,.6f}")
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
