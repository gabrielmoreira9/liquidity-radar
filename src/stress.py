"""Local, reproducible square-root impact baseline, not a calibrated execution quote.

Impact (fraction) = sigma * sqrt(Q / V); estimated_exit_cost_pct = 100 * impact.
Sigma is the existing within-minute standard deviation of log returns, unchanged
and not annualized. The impact coefficient is explicitly 1 (no fitted constants).
Stress changes V only; sigma and flow_imbalance remain the observed values.
liquidity_multiple always uses the original global median minute volume.

Observed traded volume is a liquidity proxy, not executable order-book depth.
The estimate excludes fees, spread, execution duration and directional effects.
Participation is not capped at 1, and extreme observations are never removed.
Exit cost is a volatility/liquidity estimate, not real observed slippage.
The global median is descriptive only; using it as known-at-t in retrospective
prediction would introduce look-ahead bias.
"""
from pathlib import Path

import numpy as np
import pandas as pd

try:  # Both python src/stress.py and imports as src.stress.
    from .features import expanding_statistics, MIN_HISTORY
except ImportError:
    from features import expanding_statistics, MIN_HISTORY


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
    if not windows["timestamp"].diff().dropna().eq(pd.Timedelta(minutes=1)).all():
        raise ValueError("Entrada deve ter continuidade de um minuto.")
    if not windows["timestamp"].eq(windows["timestamp"].dt.floor("min")).all():
        raise ValueError("Timestamp deve identificar o início exato do minuto.")
    if windows["flow_imbalance"].abs().gt(1 + 1e-12).any():
        raise ValueError("Flow imbalance fora de [-1, 1].")
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
    return add_reliability_fields(detail)


def add_reliability_fields(detail):
    """Natural domain boundaries, not calibrated confidence or data filters.

    Q/V > 1: hypothetical order exceeds observed turnover (not proof of real
    depth shortage). Cost >= 100%: impact >= 1, outside a small-impact sale-cost
    interpretation with nonnegative proceeds. No valid row is clipped/dropped.
    Even inside these boundaries the output is BASELINE_PROXY_ONLY, not reliable
    execution pricing. Missing sigma is never filled with zero.
    """
    result = detail.copy()
    volume = result["available_volume_usdc"]
    result["available_at"] = result["timestamp"] + pd.Timedelta(minutes=1)
    result["insufficient_data_flag"] = volume.isna() | result["volatility"].isna()
    result["insufficient_liquidity_flag"] = volume.le(0) | result["participation_rate"].gt(1)
    extreme = result["estimated_exit_cost_pct"].ge(100)
    extrapolated = result["participation_rate"].gt(1)
    result["extrapolation_warning"] = extreme | extrapolated
    result["model_reliability_status"] = np.select(
        [result["insufficient_data_flag"] & volume.le(0),
         result["insufficient_data_flag"], volume.le(0), extreme, extrapolated],
        ["INSUFFICIENT_DATA_AND_LIQUIDITY", "INSUFFICIENT_DATA", "INSUFFICIENT_LIQUIDITY",
         "EXTREME_EXTRAPOLATION", "EXTRAPOLATION"], default="BASELINE_PROXY_ONLY")
    result["extrapolation_reason"] = np.select(
        [extreme & extrapolated, extreme, extrapolated],
        ["IMPACT_GE_ONE_AND_Q_GT_TURNOVER", "IMPACT_GE_ONE", "Q_GT_TURNOVER"], default="NONE")
    result["reference_scope"] = "DESCRIPTIVE_FULL_PERIOD"
    return result


def build_causal_stress(windows, detail=None, min_history=MIN_HISTORY):
    """Causal references per position AND scenario; no global median columns.

    NORMAL exit-cost percentile is therefore ranked against earlier NORMAL
    costs at the same position. No scores/regimes are implemented here.
    """
    detail = build_stress(windows) if detail is None else detail.copy()
    detail = detail.drop(columns=["median_volume_usdc", "liquidity_multiple", "reference_scope"])
    base = windows[["timestamp", "volume_usdc"]].copy()
    base["timestamp"] = pd.to_datetime(base["timestamp"], utc=True)
    base = base.sort_values("timestamp", kind="stable").reset_index(drop=True)
    reference = expanding_statistics(base["volume_usdc"], min_history)
    base["median_volume_usdc_causal"] = reference["median"]
    base["volume_history_count"] = reference["history_count"]
    detail = detail.merge(base.drop(columns="volume_usdc"), on="timestamp", how="left", validate="many_to_one")
    detail["liquidity_multiple_causal"] = detail["position_size_usdc"] / detail["median_volume_usdc_causal"].where(
        detail["median_volume_usdc_causal"].gt(0))
    parts = []
    for _, part in detail.groupby(["position_size_usdc", "stress_scenario"], sort=False):
        part = part.sort_values("timestamp", kind="stable").copy()
        reference = expanding_statistics(part["estimated_exit_cost_pct"], min_history)
        for column in ("percentile", "p95", "history_count"):
            part[f"exit_cost_causal_{column}"] = reference[column]
        parts.append(part)
    result = pd.concat(parts).sort_values(["timestamp", "position_size_usdc", "liquidity_multiplier"],
                                        ascending=[True, True, False], kind="stable").reset_index(drop=True)
    result["historical_reference_before"] = result["timestamp"]
    result["minimum_history_observations"] = min_history
    result["reference_scope"] = "STRICTLY_PREVIOUS_WINDOWS"
    validate_stress(result, len(base))
    return result


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
    build_causal_stress(windows, detail).to_csv(OUTPUT_DIR / "exit_cost_stress_causal_1m.csv", index=False)
    print("Impacto = 1.0 × volatility × sqrt(position_size / available_volume); custo (%) = 100 × impacto.")
    print("Stress reduz apenas o volume; liquidity_multiple usa a mediana global original.")
    print(f"Janelas: {len(windows)} | linhas: {len(detail)} | mediana volume USDC: {detail['median_volume_usdc'].iloc[0]:,.6f}")
    print(f"Custos NaN: {detail['estimated_exit_cost_pct'].isna().sum()} | participações NaN: {detail['participation_rate'].isna().sum()}")
    print("Validações OK: não negatividade, monotonicidade, ausência de infinitos e tratamento de dados insuficientes.")
    selected = summary.loc[summary["position_size_usdc"].isin([100_000, 500_000, 1_000_000])]
    print(selected.to_string(index=False, float_format=lambda value: f"{value:.6f}"))
    print("Estimativa exploratória: volume negociado é proxy de liquidez; sem spread/fees e sem calibração de execução.")
    print("Não é slippage real observado. Mediana global descritiva não é baseline causal para backtests.")
    print(f"Estimativas acima de 100% (preservadas): {detail['estimated_exit_cost_pct'].gt(100).sum()}")
    print(f"Arquivos: {DETAIL_PATH} | {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
