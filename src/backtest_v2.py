"""Frozen-event temporal holdout for ORIGINAL V2, never tuning or changing risk.

Train/reference: June 30--July 2 UTC; holdout: July 3. Decisions at minute close;
targets are the NEXT h completed minute bars, wholly inside the split. Training
quantiles, score quartiles and training event prevalences are frozen. Original
online causal V2 references may admit earlier holdout observations, as specified
by V2; its indicators are not refitted here. One previously inspected test day
is a computational holdout, not an untouched prospective validation sample.
"""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

try:
    from . import risk_v2
except ImportError:
    import risk_v2

ROOT = Path(__file__).resolve().parent.parent
RISK_VERSION = ROOT/"data/processed/risk_v2_runs/20261008T153108969585Z"
TRAIN_START = pd.Timestamp("2026-06-30", tz="UTC")
TEST_START = pd.Timestamp("2026-07-03", tz="UTC")
TEST_END = pd.Timestamp("2026-07-04", tz="UTC")
HORIZONS = (5, 15, 30, 60)
EVENT_METRICS = {"LOW_VOLUME_EVENT": ("volume_usdc", "volume_p05", "min", "lt"),
                 "HIGH_VOLATILITY_EVENT": ("volatility", "volatility_p95", "max", "gt"),
                 "HIGH_EXIT_COST_EVENT": ("estimated_exit_cost_pct", "exit_cost_p95", "max", "gt")}
EVENTS = (*EVENT_METRICS, "ANY_DETERIORATION")
MODEL_SCORES = {"CURRENT_MARKET_RISK": "current_market_risk_score",
                "FORWARD_LIQUIDITY_RISK": "forward_liquidity_risk_score"}
BASELINES = ("LOW_CURRENT_VOLUME", "RECENT_VOLUME_DROP", "PERSISTENCE_PREVIOUS_CONDITION", "PREVALENCE_TRAINING")
FACTORS = {**MODEL_SCORES, **{name.upper(): name for name in risk_v2.MARKET_COMPONENTS}}
SAMPLES = ("ALL_AVAILABLE", "MATCHED", "NON_OVERLAPPING_MATCHED", "STRICT_COMPLETE_MATCHED")
OUTPUTS = ("backtest_v2_events.csv", "backtest_v2_summary.csv", "backtest_v2_baselines.csv", "backtest_v2_diagnostics.csv")
LOOKBACK_MINUTES = 5  # Initial baseline definition; no test-data selection.


def nullable_or(flags):
    array = np.asarray(flags, dtype=float)
    return np.where((array == 1).any(axis=1), 1., np.where((array == 0).all(axis=1), 0., np.nan))


def current_conditions(frame, thresholds):
    flags = {}
    for event, (metric, key, _, operation) in EVENT_METRICS.items():
        values = frame[metric]
        flags[event] = np.where(values.isna(), np.nan,
                                (values < thresholds[key] if operation == "lt" else values > thresholds[key]).astype(float))
    flags["ANY_DETERIORATION"] = nullable_or(np.column_stack([flags[event] for event in EVENT_METRICS]))
    return pd.DataFrame(flags, index=frame.index)


def fit_reference(train, cutoff=TEST_START):
    """No test frame argument exists; all fitted reference information precedes cutoff."""
    if train.empty or not train.available_at.le(cutoff).all() or not train.timestamp.lt(cutoff).all():
        raise ValueError("Referência vazia ou contém informações posteriores ao corte de treino.")
    thresholds = {}
    for _, (metric, key, _, _) in EVENT_METRICS.items():
        values = train[metric].dropna()
        if len(values) < 120 or np.isinf(values).any():
            raise ValueError(f"Histórico válido insuficiente para {metric}.")
        thresholds[key] = float(values.quantile(.05 if key == "volume_p05" else .95))
    quartiles = {}
    for name, column in FACTORS.items():
        values = train[column].dropna()
        if len(values) < 120:
            raise ValueError(f"Scores históricos insuficientes para quartis de {name}.")
        quartiles[name] = values.quantile([.25, .5, .75]).tolist()
    return {"thresholds": thresholds, "quartiles": quartiles,
            "threshold_valid_counts": {metric: int(train[metric].count()) for metric, _, _, _ in EVENT_METRICS.values()},
            "reference_available_at": str(cutoff), "reference_last_available_at": str(train.available_at.max()),
            "baseline_lookback_minutes": LOOKBACK_MINUTES,
            "baseline_low_volume_percentile": .05, "baseline_volume_drop_alert_threshold": 0.,
            "prevalence_reference_alert_threshold": .5}


def future_events(frame, reference, horizons=HORIZONS):
    """Three-valued targets: positive observed hit, negative fully observed, unknown.

    Missing future values never imply no event. Even a known partial hit is not
    labelled for an incomplete calendar horizon. Complete-time horizons with a
    known hit may have missing other measurements; strict-complete robustness
    evaluates a separate sample to expose that ascertainment selection.
    """
    frame = frame.reset_index(drop=True)
    if frame.empty or not frame.timestamp.diff().dropna().eq(pd.Timedelta(minutes=1)).all():
        raise ValueError("Targets requerem grade contínua não vazia.")
    conditions = current_conditions(frame, reference["thresholds"])
    results = []
    for horizon in horizons:
        part = frame.copy()
        part["horizon_minutes"] = horizon
        part["decision_at"] = frame.available_at
        part["future_start_at"] = frame.available_at
        part["future_end_at"] = frame.available_at + pd.Timedelta(minutes=horizon)
        complete = np.arange(len(frame)) + horizon < len(frame)
        part["horizon_complete"] = complete
        part["horizon_unavailable_reason"] = np.where(complete, "AVAILABLE", "INCOMPLETE_FUTURE_HORIZON")
        for event, (metric, threshold_key, aggregation, operation) in EVENT_METRICS.items():
            future = frame[metric].shift(-1).iloc[::-1].rolling(horizon, min_periods=1)
            extreme = getattr(future, aggregation)().iloc[::-1].reset_index(drop=True)
            count = future.count().iloc[::-1].fillna(0).reset_index(drop=True)
            hit = extreme < reference["thresholds"][threshold_key] if operation == "lt" else extreme > reference["thresholds"][threshold_key]
            flag = np.where(complete & hit, 1., np.where(complete & count.eq(horizon), 0., np.nan))
            part[event] = flag
            part[f"{event}_complete_data"] = complete & count.eq(horizon)
            part[f"{event}_valid_future_count"] = count.astype(int)
            part[f"future_{aggregation}_{metric}"] = extreme
            part[f"{event}_unavailable_reason"] = np.where(~complete, "INCOMPLETE_FUTURE_HORIZON",
                np.where(np.isnan(flag), "MISSING_FUTURE_MEASUREMENTS", "AVAILABLE"))
        part["ANY_DETERIORATION"] = nullable_or(part[list(EVENT_METRICS)].to_numpy())
        part.loc[~complete, "ANY_DETERIORATION"] = np.nan
        part["ANY_DETERIORATION_complete_data"] = part[[f"{event}_complete_data" for event in EVENT_METRICS]].all(axis=1)
        part["ANY_DETERIORATION_unavailable_reason"] = np.where(~complete, "INCOMPLETE_FUTURE_HORIZON",
            np.where(part.ANY_DETERIORATION.isna(), "MISSING_FUTURE_MEASUREMENTS", "AVAILABLE"))
        for event in EVENTS:
            first_offset = np.zeros(len(frame), dtype=int)
            for offset in range(1, horizon+1):
                hit = conditions[event].shift(-offset).eq(1).to_numpy()
                first_offset[(first_offset == 0) & hit] = offset
            first_time = frame.available_at + pd.to_timedelta(first_offset, unit="min")
            part[f"{event}_first_hit_at"] = first_time.where(part[event].eq(1), pd.NaT)
        # Fixed offset chosen without labels, not greedily chosen after exclusions.
        part["non_overlapping_grid"] = np.arange(len(frame)) % horizon == 0
        for key, value in reference["thresholds"].items():
            part[f"frozen_{key}"] = value
        results.append(part)
    return pd.concat(results, ignore_index=True).sort_values(["timestamp", "horizon_minutes"], kind="stable").reset_index(drop=True)


def add_baseline_inputs(all_minutes, reference):
    frame = all_minutes.copy()
    frame["baseline_low_volume_score"] = -frame.volume_usdc
    frame["baseline_low_volume_alert"] = np.where(frame.volume_usdc.isna(), np.nan,
        frame.volume_usdc.lt(reference["thresholds"]["volume_p05"]).astype(float))
    previous_mean = frame.volume_usdc.shift(1).rolling(LOOKBACK_MINUTES, min_periods=LOOKBACK_MINUTES).mean()
    frame["baseline_prior_5m_mean_volume"] = previous_mean
    frame["baseline_volume_drop_score"] = (previous_mean-frame.volume_usdc)/previous_mean.where(previous_mean.gt(0))
    frame["baseline_volume_drop_alert"] = np.where(frame.baseline_volume_drop_score.isna(), np.nan,
        frame.baseline_volume_drop_score.gt(0).astype(float))
    conditions = current_conditions(frame, reference["thresholds"])
    for event in EVENTS:
        frame[f"baseline_previous_{event}"] = conditions[event].shift(1)
    return frame


def training_prevalences(train, reference, horizons=HORIZONS):
    events = future_events(train, reference, horizons)
    result = {}
    for horizon, part in events.groupby("horizon_minutes"):
        for event in EVENTS:
            result[(int(horizon), event)] = {"rate": float(part[event].mean()),
                                            "valid_count": int(part[event].count()),
                                            "excluded_count": int(part[event].isna().sum())}
    return result


def auc_metrics(labels, scores):
    """ROC via average-rank U; PR area is non-interpolated average precision.

    Group tied scores at the same threshold. Constant score: ROC=.5 and AP equals
    prevalence. Both areas are undefined for a one-class evaluation sample.
    """
    labels, scores = np.asarray(labels, dtype=float), np.asarray(scores, dtype=float)
    positives = int(labels.sum())
    negatives = len(labels)-positives
    if not positives or not negatives:
        return np.nan, np.nan, "SINGLE_CLASS_OR_EMPTY"
    ranks = pd.Series(scores).rank(method="average").to_numpy()
    roc = (ranks[labels == 1].sum()-positives*(positives+1)/2)/(positives*negatives)
    grouped = pd.DataFrame({"score": scores, "y": labels}).groupby("score", sort=True).y.agg(["sum", "size"]).iloc[::-1]
    tp = grouped["sum"].cumsum().to_numpy()
    count = grouped["size"].cumsum().to_numpy()
    recall = tp/positives
    ap = np.sum(np.diff(np.r_[0., recall]) * tp/count)
    return float(roc), float(ap), "AVAILABLE"


def predictor_arrays(part, event, prevalence):
    predictors = {}
    for name, column in MODEL_SCORES.items():
        score = part[column].to_numpy(dtype=float)
        alert = np.where(np.isnan(score), np.nan, (score >= 60).astype(float))
        predictors[name] = (score, alert)
    predictors["LOW_CURRENT_VOLUME"] = (part.baseline_low_volume_score.to_numpy(), part.baseline_low_volume_alert.to_numpy())
    predictors["RECENT_VOLUME_DROP"] = (part.baseline_volume_drop_score.to_numpy(), part.baseline_volume_drop_alert.to_numpy())
    previous = part[f"baseline_previous_{event}"].to_numpy()
    predictors["PERSISTENCE_PREVIOUS_CONDITION"] = (previous, previous)
    value = prevalence["rate"]
    predictors["PREVALENCE_TRAINING"] = (np.full(len(part), value), np.full(len(part), float(value >= .5) if pd.notna(value) else np.nan))
    return predictors


def safe_div(numerator, denominator):
    return numerator/denominator if denominator else np.nan


def evaluate_metrics(part, event, score, alert, eligible, sample):
    candidate = part.non_overlapping_grid.to_numpy() if sample == "NON_OVERLAPPING_MATCHED" else np.ones(len(part), dtype=bool)
    full = part.horizon_complete.to_numpy()
    labels = part[event].to_numpy(dtype=float)
    known = np.isfinite(labels)
    strict = part[f"{event}_complete_data"].to_numpy() if sample == "STRICT_COMPLETE_MATCHED" else np.ones(len(part), dtype=bool)
    valid = candidate & full & known & strict & eligible
    y, s, a = labels[valid], np.asarray(score)[valid], np.asarray(alert)[valid]
    tp, fp = int(((y == 1) & (a == 1)).sum()), int(((y == 0) & (a == 1)).sum())
    tn, fn = int(((y == 0) & (a == 0)).sum()), int(((y == 1) & (a == 0)).sum())
    rate, precision = safe_div(tp+fn, len(y)), safe_div(tp, tp+fp)
    roc, ap, area_reason = auc_metrics(y, s)
    tp_rows = part.loc[valid & (labels == 1) & (np.asarray(alert) == 1)]
    # Deduplicate first-breach minute; does not create independent physical episodes.
    if len(tp_rows):
        first_alert = tp_rows.groupby(f"{event}_first_hit_at").decision_at.min()
        lead = (first_alert.index.to_series()-first_alert).dt.total_seconds()/60
        lead_stats = {"lead_time_median_minutes": lead.median(), "lead_time_p25_minutes": lead.quantile(.25),
                      "lead_time_p75_minutes": lead.quantile(.75), "alerted_first_breach_minutes": len(lead)}
    else:
        lead_stats = {"lead_time_median_minutes": np.nan, "lead_time_p25_minutes": np.nan,
                      "lead_time_p75_minutes": np.nan, "alerted_first_breach_minutes": 0}
    return {"n_candidates": int(candidate.sum()), "n_valid": len(y), "n_excluded": int(candidate.sum())-len(y),
            "n_excluded_incomplete_horizon": int((candidate & ~full).sum()),
            "n_excluded_unknown_label": int((candidate & full & ~known).sum()),
            "n_excluded_strict_missing_future_data": int((candidate & full & known & ~strict).sum()),
            "n_excluded_predictor": int((candidate & full & known & strict & ~eligible).sum()),
            "event_count": tp+fn, "event_rate": rate, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": precision, "recall": safe_div(tp, tp+fn), "false_positive_rate": safe_div(fp, fp+tn),
            "specificity": safe_div(tn, fp+tn), "lift": precision/rate if pd.notna(rate) and rate > 0 else np.nan,
            "roc_auc": roc, "pr_auc_average_precision": ap, "auc_unavailable_reason": area_reason,
            "alert_rate": safe_div(tp+fp, len(y)), **lead_stats,
            "observed_first_breach_minutes": part.loc[valid & (labels == 1), f"{event}_first_hit_at"].nunique()}


def evaluate(events, reference):
    model_rows, baseline_rows, diagnostics = [], [], []
    for horizon, part in events.groupby("horizon_minutes", sort=True):
        part = part.reset_index(drop=True)
        for event in EVENTS:
            prevalence = reference["training_prevalences"][(int(horizon), event)]
            predictors = predictor_arrays(part, event, prevalence)
            common = np.logical_and.reduce([np.isfinite(score) & np.isfinite(alert) for score, alert in predictors.values()])
            for sample in SAMPLES:
                for name, (score, alert) in predictors.items():
                    eligible = np.isfinite(score) & np.isfinite(alert) if sample == "ALL_AVAILABLE" else common
                    row = {"predictor": name, "horizon_minutes": horizon, "event": event, "sample": sample,
                           "training_event_rate": prevalence["rate"], "training_event_valid_count": prevalence["valid_count"],
                           **evaluate_metrics(part, event, score, alert, eligible, sample)}
                    (model_rows if name in MODEL_SCORES else baseline_rows).append(row)
            # Diagnose ordering on common cases (including non-overlap robustness).
            for sample in ("MATCHED", "NON_OVERLAPPING_MATCHED"):
                selected = common & part.horizon_complete.to_numpy() & part[event].notna().to_numpy()
                if sample == "NON_OVERLAPPING_MATCHED":
                    selected &= part.non_overlapping_grid.to_numpy()
                for factor, column in FACTORS.items():
                    cuts = reference["quartiles"][factor]
                    values = part[column].to_numpy(dtype=float)
                    quartiles = np.searchsorted(cuts, values, side="left") + 1
                    rates = []
                    for quartile in range(1, 5):
                        subset = selected & np.isfinite(values) & (quartiles == quartile)
                        rate = part.loc[subset, event].mean()
                        rates.append(rate)
                        diagnostics.append({"diagnostic": "QUARTILE_EVENT_RATE", "factor": factor, "horizon_minutes": horizon,
                            "event": event, "sample": sample, "group": f"Q{quartile}", "n_valid": int(subset.sum()),
                            "event_rate": rate, "training_q25": cuts[0], "training_q50": cuts[1], "training_q75": cuts[2],
                            "training_boundaries_tied": len(set(cuts)) < 3})
                    available = np.isfinite(rates).all()
                    diagnostics.append({"diagnostic": "QUARTILE_ORDERING", "factor": factor, "horizon_minutes": horizon,
                        "event": event, "sample": sample, "nondecreasing": bool(np.all(np.diff(rates) >= 0)) if available else pd.NA,
                        "q4_minus_q1_event_rate": rates[3]-rates[0] if available else np.nan,
                        "unavailable_reason": "AVAILABLE" if available else "EMPTY_QUARTILE",
                        "inversions": ";".join(f"Q{i+1}>Q{i+2}" for i in range(3) if pd.notna(rates[i]) and pd.notna(rates[i+1]) and rates[i] > rates[i+1])})
                    if factor not in MODEL_SCORES:
                        roc, ap, reason = auc_metrics(part.loc[selected, event], values[selected])
                        diagnostics.append({"diagnostic": "COMPONENT_RANKING", "factor": factor, "horizon_minutes": horizon,
                            "event": event, "sample": sample, "n_valid": int(selected.sum()), "roc_auc": roc,
                            "pr_auc_average_precision": ap, "unavailable_reason": reason})
                for model in MODEL_SCORES:
                    regime_column = MODEL_SCORES[model].replace("_score", "_regime")
                    rates = []
                    for regime in risk_v2.REGIMES:
                        subset = selected & part[regime_column].eq(regime).to_numpy()
                        rate = part.loc[subset, event].mean()
                        rates.append(rate)
                        diagnostics.append({"diagnostic": "REGIME_EVENT_RATE", "factor": model, "horizon_minutes": horizon,
                            "event": event, "sample": sample, "group": regime, "n_valid": int(subset.sum()), "event_rate": rate})
                    diagnostics.append({"diagnostic": "REGIME_ORDERING", "factor": model, "horizon_minutes": horizon,
                        "event": event, "sample": sample,
                        "nondecreasing": bool(np.all(np.diff(rates) >= 0)) if np.isfinite(rates).all() else pd.NA,
                        "inversions": ";".join(f"{risk_v2.REGIMES[i]}>{risk_v2.REGIMES[i+1]}" for i in range(3)
                                               if pd.notna(rates[i]) and pd.notna(rates[i+1]) and rates[i] > rates[i+1])})
            known = part[event].notna() & part.horizon_complete
            y = part.loc[known, event]
            pairs = pd.concat([part[event], part[event].shift(1)], axis=1).dropna()
            autocorrelation = pairs.iloc[:, 0].corr(pairs.iloc[:, 1]) if len(pairs) > 1 and pairs.nunique().gt(1).all() else np.nan
            diagnostics.append({"diagnostic": "OVERLAP_AND_PREVALENCE", "horizon_minutes": horizon, "event": event,
                "n_valid": len(y), "event_rate": y.mean(), "lag1_label_autocorrelation": autocorrelation,
                "max_shared_future_minutes_adjacent_origins": horizon-1,
                "non_overlapping_known_labels": int((known & part.non_overlapping_grid).sum()),
                "precision_context": "Compare lift/ROC/AP, not precision alone; overlapping targets are dependent"})
    return pd.DataFrame(model_rows), pd.DataFrame(baseline_rows), pd.DataFrame(diagnostics)


def load_and_audit(risk_version_dir=RISK_VERSION):
    version = Path(risk_version_dir).resolve()
    metadata = json.loads((version/"metadata.json").read_text())
    if risk_v2.sha256(Path(risk_v2.__file__)) != metadata["engine_sha256"]:
        raise ValueError("Código V2 mudou em relação à versão original dos indicadores.")
    audit_dir = Path(metadata["source_audit"])
    risk_path = version/"liquidity_risk_v2_1m.csv"
    if risk_v2.sha256(risk_path) != metadata["output_sha256"][risk_path.name]:
        raise ValueError("Indicadores originais não correspondem ao manifesto.")
    for name, digest in metadata["inputs_sha256"].items():
        if risk_v2.sha256(Path(name)) != digest:
            raise ValueError("Features causais não correspondem à versão V2.")
    score_cols = ["timestamp", "available_at", "position_size_usdc", "stress_scenario", "market_reference_position_usdc",
                  "market_reference_scenario", *risk_v2.MARKET_COMPONENTS, *MODEL_SCORES.values(),
                  "current_market_risk_regime", "forward_liquidity_risk_regime"]
    original = pd.read_csv(risk_path, usecols=score_cols, float_precision="round_trip", parse_dates=["timestamp", "available_at"])
    if len(original) != 5760*24 or original.duplicated(["timestamp", "position_size_usdc", "stress_scenario"]).any():
        raise ValueError("Cobertura de indicadores inválida.")
    for column in (*risk_v2.MARKET_COMPONENTS, *MODEL_SCORES.values()):
        if original.groupby("timestamp")[column].nunique(dropna=False).gt(1).any():
            raise ValueError("Score original depende de posição/cenário.")
    persisted = original.loc[original.position_size_usdc.eq(100000) & original.stress_scenario.eq("NORMAL")].set_index("timestamp")
    engine, rows = risk_v2.RiskV2Engine(), []
    feature_path, exit_path = audit_dir/"liquidity_features_causal_1m.csv", audit_dir/"exit_cost_stress_causal_1m.csv"
    with feature_path.open(newline="") as f, exit_path.open(newline="") as e:
        exits = csv.DictReader(e)
        for index, feature in enumerate(csv.DictReader(f)):
            current = [next(exits, None) for _ in range(24)]
            if any(row is None for row in current):
                raise ValueError("Stress causal incompleto.")
            evaluated = engine.evaluate(feature, current)
            reference = next(row for row in evaluated if row["position_size_usdc"] == 100000 and row["stress_scenario"] == "NORMAL")
            prior = persisted.loc[reference["timestamp"]]
            for column in (*risk_v2.MARKET_COMPONENTS, *MODEL_SCORES.values()):
                risk_v2.same(prior[column], reference[column], f"original indicator {column}")
            for column in ("available_at", "current_market_risk_regime", "forward_liquidity_risk_regime",
                           "market_reference_position_usdc", "market_reference_scenario"):
                if prior[column] != reference[column]:
                    raise ValueError(f"Metadado de indicador divergente: {column}")
            rows.append({"timestamp": reference["timestamp"], "available_at": prior["available_at"],
                         "volume_usdc": reference["volume_usdc"], "volatility": reference["volatility"],
                         "estimated_exit_cost_pct": reference["estimated_exit_cost_pct"],
                         "reference_exit_extrapolation_warning": reference["extrapolation_warning"],
                         **{column: prior[column] for column in (*risk_v2.MARKET_COMPONENTS, *MODEL_SCORES.values(),
                              "current_market_risk_regime", "forward_liquidity_risk_regime")}})
            if (index+1) % 1440 == 0:
                print(f"Auditoria causal/replay sem alterar V2: {index+1}/5760 minutos", flush=True)
        if next(exits, None) is not None:
            raise ValueError("Stress causal excedente.")
    frame = pd.DataFrame(rows)
    expected = pd.date_range(TRAIN_START, TEST_END, freq="min", inclusive="left")
    if not pd.DatetimeIndex(frame.timestamp).equals(expected) or not frame.available_at.eq(frame.timestamp+pd.Timedelta(minutes=1)).all():
        raise ValueError("Grade/disponibilidade incompatível com treino e teste especificados.")
    inputs = {str(path): risk_v2.sha256(path) for path in (feature_path, exit_path, risk_path, version/"metadata.json")}
    return frame, inputs


def build_backtest(frame, train_start=TRAIN_START, test_start=TEST_START, test_end=TEST_END, horizons=HORIZONS):
    if frame.timestamp.duplicated().any() or not frame.timestamp.is_monotonic_increasing:
        raise ValueError("Timestamp duplicado/desordenado.")
    if not frame.available_at.eq(frame.timestamp+pd.Timedelta(minutes=1)).all():
        raise ValueError("Features disponíveis antes/depois do fechamento definido.")
    train = frame.loc[frame.timestamp.ge(train_start) & frame.timestamp.lt(test_start)].copy()
    test = frame.loc[frame.timestamp.ge(test_start) & frame.timestamp.lt(test_end)].copy()
    reference = fit_reference(train, cutoff=test_start)
    reference["training_prevalences"] = training_prevalences(train, reference, horizons)
    with_baselines = add_baseline_inputs(frame, reference)
    test = with_baselines.loc[with_baselines.timestamp.ge(test_start) & with_baselines.timestamp.lt(test_end)].copy()
    events = future_events(test, reference, horizons)
    summary, baselines, diagnostics = evaluate(events, reference)
    validate_backtest(events, summary, baselines, test, horizons)
    return events, summary, baselines, diagnostics, reference


def validate_backtest(events, summary, baselines, test, horizons):
    if len(events) != len(test)*len(horizons) or events.duplicated(["timestamp", "horizon_minutes"]).any():
        raise ValueError("Observações do backtest perdidas/duplicadas.")
    if np.isinf(events.select_dtypes("number").to_numpy()).any():
        raise ValueError("Infinito no backtest.")
    for event in EVENTS:
        if not events.loc[~events.horizon_complete, event].isna().all():
            raise ValueError("Horizonte incompleto ganhou label.")
        positives = events[event].eq(1)
        if not events.loc[positives, f"{event}_first_hit_at"].gt(events.loc[positives, "decision_at"]).all():
            raise ValueError("Evento usa informação na própria janela ou anterior à decisão.")
    metrics = pd.concat([summary, baselines], ignore_index=True)
    if not metrics.n_valid.eq(metrics.tp+metrics.fp+metrics.tn+metrics.fn).all():
        raise ValueError("Matriz de confusão não conserva avaliações.")
    excluded = metrics[["n_excluded_incomplete_horizon", "n_excluded_unknown_label", "n_excluded_strict_missing_future_data", "n_excluded_predictor"]].sum(axis=1)
    if not metrics.n_excluded.eq(excluded).all():
        raise ValueError("Exclusões não conservadas.")
    for horizon, part in events.groupby("horizon_minutes"):
        grid = part.loc[part.non_overlapping_grid & part.horizon_complete]
        if not grid.future_start_at.iloc[1:].reset_index(drop=True).ge(grid.future_end_at.iloc[:-1].reset_index(drop=True)).all():
            raise ValueError("Amostra não sobreposta tem interseção de targets.")


def run(risk_version_dir=RISK_VERSION, output_dir=ROOT/"data/processed"):
    output_dir = Path(output_dir).resolve()
    if any((output_dir/name).exists() for name in OUTPUTS):
        raise FileExistsError("Outputs V2 de backtest já existem; use --output-dir novo para preservar versões anteriores.")
    frame, inputs = load_and_audit(risk_version_dir)
    events, summary, baselines, diagnostics, reference = build_backtest(frame)
    for name, digest in inputs.items():
        if risk_v2.sha256(Path(name)) != digest:
            raise ValueError("Input original alterado durante backtest.")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    version_dir = output_dir/"backtest_v2_runs"/run_id
    version_dir.mkdir(parents=True, exist_ok=False)
    outputs = (events, summary, baselines, diagnostics)
    for name, output in zip(OUTPUTS, outputs):
        output.to_csv(version_dir/name, index=False)
    serial_reference = {**reference, "training_prevalences": {
        f"{horizon}m:{event}": value for (horizon, event), value in reference["training_prevalences"].items()}}
    metadata = {"run_id": run_id, "inputs_sha256": inputs, "source_risk_version": str(Path(risk_version_dir).resolve()),
                "backtest_code_sha256": risk_v2.sha256(Path(__file__)), "frozen_reference": serial_reference,
                "train_start": str(TRAIN_START), "test_start": str(TEST_START), "test_end_exclusive": str(TEST_END),
                "test_minutes": len(frame.loc[frame.timestamp.ge(TEST_START)]), "sample_policies": SAMPLES,
                "lead_time_definition": "Earliest alerted decision mapped to unique first-breach minute; not independent episodes",
                "pr_auc_definition": "Non-interpolated average precision; ties grouped",
                "limitations": "Single previously inspected test day; overlap, missing labels, proxy exit costs; no tuning",
                "output_sha256": {name: risk_v2.sha256(version_dir/name) for name in OUTPUTS}}
    (version_dir/"metadata.json").write_text(json.dumps(metadata, indent=2))
    for name in OUTPUTS:
        with (output_dir/name).open("xb") as target, (version_dir/name).open("rb") as source:
            shutil.copyfileobj(source, target)
    print("Holdout temporal: 03/07/2026; eventos/quartis congelados nos três dias anteriores.")
    print(json.dumps(reference["thresholds"], indent=2))
    chosen = summary.loc[summary.event.eq("ANY_DETERIORATION") & summary["sample"].eq("MATCHED")]
    print(chosen[["predictor", "horizon_minutes", "n_valid", "event_rate", "precision", "recall", "false_positive_rate", "lift", "roc_auc", "pr_auc_average_precision"]].to_string(index=False))
    print("Precision isolada não mede discriminação em targets frequentes; conferir lift e avaliação não sobreposta.")
    print(f"Outputs: {output_dir} | versão: {version_dir}")
    return events, summary, baselines, diagnostics, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--risk-version-dir", type=Path, default=RISK_VERSION)
    parser.add_argument("--output-dir", type=Path, default=ROOT/"data/processed")
    args = parser.parse_args()
    run(args.risk_version_dir, args.output_dir)


if __name__ == "__main__":
    main()
