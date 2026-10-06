"""Local, reproducible square-root impact baseline, not a calibrated execution quote.

Impact (fraction) = sigma * sqrt(Q / V); estimated_exit_cost_pct = 100 * impact.
Sigma is the existing within-minute standard deviation of log returns, unchanged
and not annualized. The impact coefficient is explicitly 1 (no fitted constants).
Stress changes V only; sigma and flow_imbalance remain the observed values.
liquidity_multiple always uses the original global median minute volume.

Observed traded volume is a liquidity proxy, not executable order-book depth.
The estimate excludes fees, spread, execution duration and directional effects.
Participation is not capped at 1, and extreme observations are never removed.
"""
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_PATH = BASE_DIR / "data/processed/liquidity_1m.csv"
OUTPUT_DIR = BASE_DIR / "data/processed"
DETAIL_PATH = OUTPUT_DIR / "exit_cost_stress_1m.csv"
SUMMARY_PATH = OUTPUT_DIR / "exit_cost_summary.csv"
POSITION_SIZES = (10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000)
SCENARIOS = {"NORMAL": 1.00, "STRESS_25": 0.75, "STRESS_50": 0.50, "STRESS_75": 0.25}
IMPACT_COEFFICIENT = 1.0
PERCENT_SCALE = 100.0


def build_stress(windows):
    required = ["timestamp", "volume_usdc", "volatility", "flow_imbalance"]
    if not set(required) <= set(windows.columns):
        raise ValueError("Colunas de entrada obrigatórias ausentes.")
    windows = windows[required].copy()
    windows["timestamp"] = pd.to_datetime(windows["timestamp"], utc=True, errors="raise")
    if windows["timestamp"].isna().any() or windows["timestamp"].duplicated().any():
        raise ValueError("Timestamps ausentes ou duplicados na entrada.")
    if windows.empty:
        raise ValueError("Dataset de janelas vazio.")
    for column in required[1:]:
        windows[column] = pd.to_numeric(windows[column], errors="raise")
        if np.isinf(windows[column]).any():
            raise ValueError(f"Entrada contém infinito em {column}.")
    for column in ("volume_usdc", "volatility"):
        if windows[column].lt(0).any():
            raise ValueError(f"Entrada contém {column} negativo.")
    windows = windows.sort_values("timestamp", kind="stable").reset_index(drop=True)
    # Includes zero-volume windows; missing observations do not define a median.
    median_volume = windows["volume_usdc"].median()
    parts = []
    for size in POSITION_SIZES:
        for scenario, multiplier in SCENARIOS.items():
            available = windows["volume_usdc"] * multiplier
            participation = size / available.where(available.gt(0))
            # Missing sigma or unavailable volume gives NaN; sigma=0 gives zero impact.
            cost = PERCENT_SCALE * IMPACT_COEFFICIENT * windows["volatility"] * np.sqrt(participation)
            parts.append(pd.DataFrame({
                "timestamp": windows["timestamp"],
                "position_size_usdc": size,
                "stress_scenario": scenario,
                "liquidity_multiplier": multiplier,
                "available_volume_usdc": available,
                "participation_rate": participation,
                "median_volume_usdc": median_volume,
                "liquidity_multiple": size / median_volume if pd.notna(median_volume) and median_volume > 0 else np.nan,
                "volatility": windows["volatility"],
                "estimated_exit_cost_pct": cost,
                "flow_imbalance": windows["flow_imbalance"],
            }))
    detail = pd.concat(parts, ignore_index=True).sort_values(
        ["timestamp", "position_size_usdc", "liquidity_multiplier"],
        ascending=[True, True, False], kind="stable",
    ).reset_index(drop=True)
    validate_stress(detail, len(windows))
    return detail


def validate_stress(detail, window_count):
    keys = ["timestamp", "position_size_usdc", "stress_scenario"]
    if len(detail) != window_count * len(POSITION_SIZES) * len(SCENARIOS) or detail.duplicated(keys).any():
        raise ValueError("Combinações timestamp × posição × cenário incompletas ou duplicadas.")
    numeric = detail.select_dtypes(include=["number"])
    if np.isinf(numeric.to_numpy()).any():
        raise ValueError("Infinito detectado nos resultados; arquivos não serão salvos.")
    if detail["estimated_exit_cost_pct"].lt(0).any():
        raise ValueError("Exit cost negativo.")
    for grouping, ordering, ascending in (
        (["timestamp", "position_size_usdc"], "liquidity_multiplier", False),
        (["timestamp", "stress_scenario"], "position_size_usdc", True),
    ):
        ordered = detail.sort_values(grouping + [ordering], ascending=[True, True, ascending])
        differences = ordered.groupby(grouping)["estimated_exit_cost_pct"].diff()
        if differences.lt(0).any():
            raise ValueError("Exit cost viola monotonicidade de liquidez ou posição.")
    invalid_volume = detail["available_volume_usdc"].isna() | detail["available_volume_usdc"].le(0)
    if detail.loc[invalid_volume, ["participation_rate", "estimated_exit_cost_pct"]].notna().any().any():
        raise ValueError("Volume indisponível deve resultar em participação e custo NaN.")
    if detail.loc[detail["volatility"].isna(), "estimated_exit_cost_pct"].notna().any():
        raise ValueError("Volatilidade ausente deve resultar em custo NaN.")


def summarize_stress(detail):
    # Each metric ignores only its own missing observations, without outlier filters.
    return detail.groupby(["position_size_usdc", "stress_scenario"], sort=False).agg(
        median_exit_cost_pct=("estimated_exit_cost_pct", "median"),
        p95_exit_cost_pct=("estimated_exit_cost_pct", lambda values: values.quantile(0.95)),
        p99_exit_cost_pct=("estimated_exit_cost_pct", lambda values: values.quantile(0.99)),
        max_exit_cost_pct=("estimated_exit_cost_pct", "max"),
        median_participation_rate=("participation_rate", "median"),
        p95_participation_rate=("participation_rate", lambda values: values.quantile(0.95)),
        window_count=("timestamp", "size"),
        valid_exit_cost_count=("estimated_exit_cost_pct", "count"),
        valid_participation_count=("participation_rate", "count"),
    ).reset_index()


def main():
    windows = pd.read_csv(INPUT_PATH)
    detail = build_stress(windows)
    summary = summarize_stress(detail)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    detail.to_csv(DETAIL_PATH, index=False)
    summary.to_csv(SUMMARY_PATH, index=False)
    print("Impacto = 1.0 × volatility × sqrt(position_size / available_volume); custo (%) = 100 × impacto.")
    print("Stress reduz apenas o volume; liquidity_multiple usa a mediana global original.")
    print(f"Janelas: {len(windows)} | linhas: {len(detail)} | mediana volume USDC: {detail['median_volume_usdc'].iloc[0]:,.6f}")
    print(f"Custos NaN: {detail['estimated_exit_cost_pct'].isna().sum()} | participações NaN: {detail['participation_rate'].isna().sum()}")
    print("Validações OK: não negatividade, monotonicidade, ausência de infinitos e tratamento de dados insuficientes.")
    selected = summary.loc[summary["position_size_usdc"].isin([100_000, 500_000, 1_000_000])]
    print(selected.to_string(index=False, float_format=lambda value: f"{value:.6f}"))
    print("Estimativa exploratória: volume negociado é proxy de liquidez; sem spread/fees e sem calibração de execução.")
    print(f"Arquivos: {DETAIL_PATH} | {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
