from pathlib import Path

import numpy as np
import pandas as pd


input_path = Path(__file__).resolve().parent.parent / "data/processed/sol_usdc_swaps.csv"

swaps = pd.read_csv(input_path, parse_dates=["timestamp"])
swaps = swaps.sort_values("timestamp").reset_index(drop=True)

swaps["direction"] = np.select(
    [
        (swaps["sol_delta"] > 0) & (swaps["usdc_delta"] < 0),
        (swaps["sol_delta"] < 0) & (swaps["usdc_delta"] > 0),
    ],
    ["SOL_IN", "SOL_OUT"],
    default="OTHER",
)
swaps["price_change_pct"] = swaps["price"].pct_change()
swaps["log_return"] = np.log(swaps["price"] / swaps["price"].shift(1))

valid_swaps = swaps[swaps["direction"].isin(["SOL_IN", "SOL_OUT"])].copy()
valid_swaps = valid_swaps.sort_values("timestamp").reset_index(drop=True)
valid_swaps["price_change_pct"] = valid_swaps["price"].pct_change()
valid_swaps["log_return"] = np.log(
    valid_swaps["price"] / valid_swaps["price"].shift(1)
)
valid_swaps["volume_log"] = np.log1p(valid_swaps["volume_usdc"])

output_path = (
    Path(__file__).resolve().parent.parent / "data/processed/sol_usdc_features.csv"
)
output_path.parent.mkdir(parents=True, exist_ok=True)
valid_swaps.to_csv(output_path, index=False)

print(f"Número de observações antes da filtragem: {len(swaps)}")
print(f"Número de observações válidas: {len(valid_swaps)}")
print(f"Quantidade removida como OTHER: {len(swaps) - len(valid_swaps)}")
print("Quantidade por direction:")
print(valid_swaps["direction"].value_counts())
print("Estatísticas descritivas:")
print(
    valid_swaps[
        ["price", "volume_usdc", "price_change_pct", "log_return"]
    ].describe()
)
print("Primeiras 10 linhas do dataset final:")
print(valid_swaps.head(10))
