"""Descriptive Liquidity Risk Engine using only local historical CSVs.

Percentile = 100 * fraction of valid historical observations <= the value
(maximum rank for ties). Volume risk is 100 minus its percentile; flow uses
abs(flow_imbalance). Exit costs use NORMAL only, ranked within each position.
The full historical sample is the reference: this is not a causal backtest.
No outliers are removed, no weights are fitted, and missing components are
not imputed: all four are required for the arithmetic mean.

Regime boundaries 40/60/80 are an initial classification policy, NOT
statistically calibrated thresholds. Position-specific exit percentiles can
coincide across sizes even when their absolute execution costs differ.
"""
from pathlib import Path

import numpy as np
import pandas as pd


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data/processed"
FEATURE_PATH = OUTPUT_DIR / "liquidity_features_1m.csv"
STRESS_PATH = OUTPUT_DIR / "exit_cost_stress_1m.csv"
DETAIL_PATH = OUTPUT_DIR / "liquidity_risk_1m.csv"
SUMMARY_PATH = OUTPUT_DIR / "risk_summary.csv"
POSITION_SIZES = (10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000)
COMPONENTS = ("volatility_risk", "liquidity_risk", "flow_risk", "exit_cost_risk")
REGIMES = ("NORMAL", "WATCH", "STRESSED", "CRITICAL")


def empirical_percentile(values):
    """Ignore missing observations; preserve missing values and tied ranks."""
    return values.rank(method="max", pct=True) * 100.0


def _prepare(frame, required, keys):
    if not set(required).issubset(frame.columns) or frame.empty:
        raise ValueError("Entrada vazia ou colunas obrigatórias ausentes.")
    frame = frame[list(required)].copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    if frame[list(keys)].isna().any().any() or frame.duplicated(list(keys)).any():
        raise ValueError("Chaves ausentes ou duplicadas na entrada.")
    for column in set(required) - {"timestamp", "stress_scenario"}:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if np.isinf(frame[column]).any():
            raise ValueError(f"Infinito na entrada: {column}.")
    return frame


def build_risk(features, stress):
    features = _prepare(
        features, ("timestamp", "volatility", "volume_usdc", "flow_imbalance"),
        ("timestamp",),
    ).sort_values("timestamp", kind="stable")
    stress = _prepare(
        stress, ("timestamp", "position_size_usdc", "stress_scenario", "estimated_exit_cost_pct"),
        ("timestamp", "position_size_usdc", "stress_scenario"),
    )
    for column in ("volatility", "volume_usdc"):
        if features[column].lt(0).any():
            raise ValueError(f"Valor negativo: {column}.")
    if features["flow_imbalance"].abs().gt(1).any():
        raise ValueError("Flow imbalance fora de [-1, 1].")
    if set(stress["position_size_usdc"]) != set(POSITION_SIZES):
        raise ValueError("Stress engine deve conter as seis posições esperadas.")
    normal = stress.loc[stress["stress_scenario"].eq("NORMAL")].copy()
    expected_keys = pd.MultiIndex.from_product(
        [features["timestamp"], POSITION_SIZES], names=["timestamp", "position_size_usdc"],
    )
    actual_keys = pd.MultiIndex.from_frame(normal[["timestamp", "position_size_usdc"]])
    if len(normal) != len(expected_keys) or not expected_keys.difference(actual_keys).empty or not actual_keys.difference(expected_keys).empty:
        raise ValueError("NORMAL não cobre exatamente todas as janelas × seis posições.")
    if normal["estimated_exit_cost_pct"].lt(0).any():
        raise ValueError("Exit cost NORMAL negativo.")
    features["volatility_risk"] = empirical_percentile(features["volatility"])
    features["liquidity_risk"] = 100.0 - empirical_percentile(features["volume_usdc"])
    features["flow_risk"] = empirical_percentile(features["flow_imbalance"].abs())
    normal["exit_cost_risk"] = normal.groupby("position_size_usdc")["estimated_exit_cost_pct"].transform(empirical_percentile)
    detail = normal.merge(features, on="timestamp", how="left", validate="many_to_one")
    # NORMAL cost may be missing only when volatility or positive volume is unavailable.
    insufficient = detail["volatility"].isna() | detail["volume_usdc"].isna() | detail["volume_usdc"].le(0)
    if not detail["estimated_exit_cost_pct"].isna().equals(insufficient):
        raise ValueError("NaN de exit cost incompatível com insuficiência dos dados de origem.")
    detail["risk_score"] = detail[list(COMPONENTS)].mean(axis=1, skipna=False)
    detail["risk_regime"] = pd.cut(
        detail["risk_score"], bins=[-np.inf, 40, 60, 80, np.inf],
        labels=list(REGIMES), right=False,
    )
    # Available components remain explainable even if the aggregate is missing.
    # Ties are resolved deterministically in COMPONENTS order.
    rankings = np.argsort(-detail[list(COMPONENTS)].fillna(-np.inf).to_numpy(), axis=1, kind="stable")
    for rank, column in enumerate(("primary_risk_driver", "secondary_risk_driver")):
        detail[column] = np.asarray(COMPONENTS)[rankings[:, rank]]
        detail.loc[detail[list(COMPONENTS)].notna().sum(axis=1).le(rank), column] = pd.NA
    validate_risk(detail, len(features))
    columns = ["timestamp", "position_size_usdc", "risk_score", "risk_regime", *COMPONENTS,
               "primary_risk_driver", "secondary_risk_driver", "estimated_exit_cost_pct", "stress_scenario"]
    return detail[columns].sort_values(["timestamp", "position_size_usdc"], kind="stable").reset_index(drop=True)


def validate_risk(detail, window_count):
    if detail.duplicated(["timestamp", "position_size_usdc"]).any():
        raise ValueError("Duplicata timestamp × posição.")
    if set(detail["position_size_usdc"]) != set(POSITION_SIZES) or not detail.groupby("position_size_usdc").size().eq(window_count).all():
        raise ValueError("Cobertura incompleta das seis posições.")
    if not detail["stress_scenario"].eq("NORMAL").all():
        raise ValueError("Cenário de stress entrou no score.")
    if np.isinf(detail.select_dtypes(include="number").to_numpy()).any():
        raise ValueError("Infinito nos resultados.")
    for column in (*COMPONENTS, "risk_score"):
        values = detail[column].dropna()
        if not values.between(0, 100).all():
            raise ValueError(f"Score fora de [0, 100]: {column}.")
    for score, source in zip(COMPONENTS, ("volatility", "volume_usdc", "flow_imbalance", "estimated_exit_cost_pct")):
        if not detail[score].isna().equals(detail[source].isna()):
            raise ValueError(f"NaN sem justificativa de origem: {score}.")
    missing = detail[list(COMPONENTS)].isna().any(axis=1)
    if not detail["risk_score"].isna().equals(missing) or not detail["risk_regime"].isna().equals(missing):
        raise ValueError("Score/regime ausente incompatível com componentes.")


def summarize_risk(detail):
    grouped = detail.groupby("position_size_usdc", sort=True)
    summary = grouped["risk_score"].agg(
        median_risk_score="median", p95_risk_score=lambda values: values.quantile(0.95),
        max_risk_score="max", window_count="size", valid_score_count="count",
    )
    for regime in REGIMES:
        summary[regime] = detail["risk_regime"].eq(regime).groupby(detail["position_size_usdc"]).sum()
    summary["missing_score_count"] = summary["window_count"] - summary["valid_score_count"]
    return summary.reset_index()[["position_size_usdc", *REGIMES, "median_risk_score", "p95_risk_score",
                                  "max_risk_score", "window_count", "valid_score_count", "missing_score_count"]]


def main():
    detail = build_risk(pd.read_csv(FEATURE_PATH), pd.read_csv(STRESS_PATH))
    summary = summarize_risk(detail)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    detail.to_csv(DETAIL_PATH, index=False)
    summary.to_csv(SUMMARY_PATH, index=False)
    print("Percentis empíricos do histórico completo; média simples exige quatro componentes.")
    print("Limites 40/60/80: política inicial de classificação, não calibrada estatisticamente.")
    print(f"Validações OK | linhas: {len(detail)} | scores NaN: {detail['risk_score'].isna().sum()}")
    print(summary.loc[summary["position_size_usdc"].isin([100_000, 500_000, 1_000_000])].to_string(index=False))
    print("Top 10 para posição 1M (UTC; desempate cronológico):")
    top = detail.loc[detail["position_size_usdc"].eq(1_000_000)].dropna(subset=["risk_score"]).sort_values(
        ["risk_score", "timestamp"], ascending=[False, True], kind="stable",
    ).head(10)
    print(top.drop(columns=["position_size_usdc", "stress_scenario"]).to_string(index=False, float_format=lambda value: f"{value:.6f}"))


if __name__ == "__main__":
    main()
