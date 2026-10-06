"""Local temporal validation; no tuning, external calls or global baselines.

At t, expanding references contain only observations strictly before t.
Each component requires 120 valid preceding minute observations. ECDF uses
100 * count(history <= current) / count(history), including ties. Quantiles
use linear interpolation. NORMAL exit costs are ranked within position.
The descriptive features/risk CSVs are checked for alignment but their global
percentiles and scores NEVER enter predictions or deterioration thresholds.
40/60/80 remain initial policy boundaries, not calibrated thresholds.

Future intervals are (t, t+h], in clock minutes. Truncated intervals are not
evaluated. A witnessed breach is True even with missing future observations;
False requires all h values of that metric. Otherwise its flag is unknown.
An OR outcome is known if any flag is True or all four flags are known False.
Missing scores/outcomes are excluded explicitly, never imputed as negatives.

Lead time: first future breach of thresholds frozen at the origin t defines
the event minute. Within each position/horizon, deduplicate that minute and
find the earliest evaluated HIGH_RISK origin predicting it. Lead statistics
use these detected event minutes only; undetected minutes are counted too.
These are threshold-crossing minutes, not independent market episodes; event
identity can vary with the origin's evolving baseline. Overlapping horizons
and one-day samples do not support claims of statistical significance.

Diagnostics do not modify predictions. Component quartiles use strictly
preceding temporal component scores, with 120 valid scores required per
position. Q1 <= historical q25, Q2 <= q50, Q3 <= q75, otherwise Q4. Ties
stay together, so quartile groups need not be equal or even populated.
New exact exit-cost maxima/changes/multiples require all future minutes;
legacy observed extrema remain unchanged. Relative economic scenarios require
current cost > 0; zero denominators are not economic increase signals.
25/50/100% increases and absolute 0.50/1.00/2.00% costs are analysis scenarios,
NOT calibrated thresholds. Rates use each event's own known observations,
with explicit denominators, without filtering on the aggregated OR outcome.
"""
from bisect import bisect_right, insort
from pathlib import Path

import numpy as np
import pandas as pd


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data/processed"
MIN_HISTORY = 120
HORIZONS = (5, 15, 30, 60)
POSITIONS = (10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000)
REGIMES = ("NORMAL", "WATCH", "STRESSED", "CRITICAL")
COMPONENTS = ("volatility_risk", "liquidity_risk", "flow_risk", "exit_cost_risk")
METRICS = ("volatility", "volume_usdc", "abs_flow_imbalance", "estimated_exit_cost_pct")
FLAGS = ("volatility_deterioration", "volume_deterioration", "flow_deterioration", "exit_cost_deterioration")
EVENT_ALIASES = {
    "volatility_event": "volatility_deterioration",
    "low_volume_event": "volume_deterioration",
    "exit_cost_event": "exit_cost_deterioration",
    "flow_imbalance_event": "flow_deterioration",
    "any_deterioration": "deterioration",
}
RELATIVE_SCENARIOS = {"exit_cost_increase_25": 1.25, "exit_cost_increase_50": 1.50, "exit_cost_double": 2.00}
ABSOLUTE_SCENARIOS = {"exit_cost_absolute_050": .50, "exit_cost_absolute_100": 1., "exit_cost_absolute_200": 2.}
DIAGNOSTIC_EVENTS = (*EVENT_ALIASES, *RELATIVE_SCENARIOS, *ABSOLUTE_SCENARIOS)
QUARTILES = ("Q1", "Q2", "Q3", "Q4")


def expanding_reference(values, lower=False, min_history=MIN_HISTORY):
    """Score and quantile at each row BEFORE inserting its current value."""
    history = []
    scores, thresholds, counts = [], [], []
    for value in values:
        counts.append(len(history))
        eligible = len(history) >= min_history
        thresholds.append(np.quantile(history, .05 if lower else .95) if eligible else np.nan)
        percentile = 100.0 * bisect_right(history, value) / len(history) if eligible and pd.notna(value) else np.nan
        scores.append(100.0 - percentile if lower else percentile)
        if pd.notna(value):
            insort(history, float(value))
    return np.asarray(scores), np.asarray(thresholds), np.asarray(counts)


def temporal_quartiles(values, min_history=MIN_HISTORY):
    """Quantiles exclude the current score; deterministic tie handling."""
    history = []
    labels, thresholds, counts = [], [], []
    for value in values:
        counts.append(len(history))
        cuts = np.quantile(history, [.25, .50, .75]) if len(history) >= min_history else np.full(3, np.nan)
        thresholds.append(cuts)
        labels.append(QUARTILES[np.searchsorted(cuts, value, side="left")]
                      if np.isfinite(cuts).all() and pd.notna(value) else pd.NA)
        if pd.notna(value):
            insort(history, float(value))
    return pd.array(labels, dtype="string"), np.asarray(thresholds), np.asarray(counts)


def _table(frame, required, keys):
    if frame.empty or not set(required).issubset(frame.columns):
        raise ValueError("Entrada vazia ou sem colunas obrigatórias.")
    frame = frame[list(required)].copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    if frame[list(keys)].isna().any().any() or frame.duplicated(list(keys)).any():
        raise ValueError("Chaves ausentes/duplicadas.")
    for column in set(required) - {"timestamp", "stress_scenario"}:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if np.isinf(frame[column]).any():
            raise ValueError(f"Infinito em {column}.")
    return frame.sort_values(list(keys), kind="stable").reset_index(drop=True)


def prepare_inputs(windows, features, stress, descriptive_risk):
    columns = ("timestamp", "volatility", "volume_usdc", "flow_imbalance")
    windows = _table(windows, columns, ("timestamp",))
    features = _table(features, columns, ("timestamp",))
    if not windows["timestamp"].equals(features["timestamp"]):
        raise ValueError("Features e janelas não estão alinhadas.")
    for column in columns[1:]:
        if not np.allclose(windows[column], features[column], equal_nan=True, rtol=1e-10, atol=1e-12):
            raise ValueError(f"Features divergentes da origem: {column}.")
    if windows[["volatility", "volume_usdc"]].lt(0).any().any() or windows["flow_imbalance"].abs().gt(1).any():
        raise ValueError("Métricas fora de seu domínio.")
    if not windows["timestamp"].eq(windows["timestamp"].dt.floor("min")).all():
        raise ValueError("Timestamp fora da grade de um minuto.")
    stress = _table(stress, ("timestamp", "position_size_usdc", "stress_scenario", "estimated_exit_cost_pct"),
                    ("timestamp", "position_size_usdc", "stress_scenario"))
    normal = stress.loc[stress["stress_scenario"].eq("NORMAL")].copy()
    risk = _table(descriptive_risk, ("timestamp", "position_size_usdc", "risk_score"),
                  ("timestamp", "position_size_usdc"))
    expected = pd.MultiIndex.from_product([windows["timestamp"], POSITIONS], names=["timestamp", "position_size_usdc"])
    for name, frame in (("NORMAL", normal), ("risk descritivo", risk)):
        actual = pd.MultiIndex.from_frame(frame[["timestamp", "position_size_usdc"]])
        if len(actual) != len(expected) or len(expected.difference(actual)) or len(actual.difference(expected)):
            raise ValueError(f"Cobertura timestamp × seis posições inválida: {name}.")
    if normal["estimated_exit_cost_pct"].lt(0).any():
        raise ValueError("Exit cost negativo.")
    # Explicit minute grid prevents row offsets silently replacing clock time.
    grid = pd.date_range(windows.timestamp.min(), windows.timestamp.max(), freq="min")
    windows = windows.set_index("timestamp").reindex(grid).rename_axis("timestamp").reset_index()
    windows["abs_flow_imbalance"] = windows["flow_imbalance"].abs()
    return windows, normal


def temporal_scores(windows, normal):
    base = windows.copy()
    for metric, component in zip(METRICS[:3], COMPONENTS[:3]):
        score, threshold, count = expanding_reference(base[metric], lower=metric == "volume_usdc")
        base[component] = score
        base[f"{metric}_threshold"] = threshold
        base[f"{metric}_history_count"] = count
    parts = []
    for size in POSITIONS:
        part = base.merge(normal.loc[normal.position_size_usdc.eq(size), ["timestamp", "estimated_exit_cost_pct"]],
                          on="timestamp", how="left", validate="one_to_one")
        part["position_size_usdc"] = size
        score, threshold, count = expanding_reference(part["estimated_exit_cost_pct"])
        part["exit_cost_risk"] = score
        part["estimated_exit_cost_pct_threshold"] = threshold
        part["estimated_exit_cost_pct_history_count"] = count
        part["risk_score"] = part[list(COMPONENTS)].mean(axis=1, skipna=False)
        part["risk_regime"] = pd.cut(part.risk_score, [-np.inf, 40, 60, 80, np.inf], labels=list(REGIMES), right=False)
        part["risk_signal"] = pd.Series(pd.NA, index=part.index, dtype="string")
        valid = part.risk_score.notna()
        part.loc[valid, "risk_signal"] = np.where(part.loc[valid, "risk_score"].ge(60), "HIGH_RISK", "LOW_RISK")
        for component in COMPONENTS:
            labels, cuts, count = temporal_quartiles(part[component])
            part[f"{component}_quartile"] = labels
            part[f"{component}_quartile_history_count"] = count
            for index, percentile in enumerate((25, 50, 75)):
                part[f"{component}_historical_q{percentile}"] = cuts[:, index]
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def future_outcomes(part, horizon):
    """Vectorized offset comparisons against each origin's frozen threshold."""
    result = part.copy()
    n = len(part)
    complete = np.arange(n) + horizon < n
    result["horizon_minutes"] = horizon
    result["horizon_complete"] = complete
    first_offset = np.full(n, np.nan)
    for metric, flag in zip(METRICS, FLAGS):
        values = part[metric]
        future = np.column_stack([values.shift(-offset).to_numpy() for offset in range(1, horizon + 1)])
        threshold = part[f"{metric}_threshold"].to_numpy()
        observed = np.isfinite(future)
        breaches = (future < threshold[:, None]) if metric == "volume_usdc" else (future > threshold[:, None])
        breaches &= observed & np.isfinite(threshold[:, None])
        any_breach = breaches.any(axis=1)
        known = complete & np.isfinite(threshold) & (any_breach | observed.all(axis=1))
        flags = pd.Series(pd.NA, index=result.index, dtype="boolean")
        flags.loc[known] = any_breach[known]
        result[flag] = flags
        result[f"future_{metric}_valid_count"] = observed.sum(axis=1)
        # Pandas aggregation ignores only missing values, including all-missing rows.
        future_frame = pd.DataFrame(future)
        aggregate = future_frame.min(axis=1) if metric == "volume_usdc" else future_frame.max(axis=1)
        result[f"future_{'min' if metric == 'volume_usdc' else 'max'}_{metric}"] = aggregate.to_numpy()
        offsets = np.where(any_breach, breaches.argmax(axis=1) + 1, np.nan)
        first_offset = np.fmin(first_offset, offsets)
    flags = result[list(FLAGS)]
    witnessed = flags.fillna(False).any(axis=1)
    known = complete & (witnessed | flags.notna().all(axis=1))
    outcome = pd.Series(pd.NA, index=result.index, dtype="boolean")
    outcome.loc[known] = witnessed[known]
    result["deterioration"] = outcome
    result["evaluation_valid"] = result.risk_score.notna() & outcome.notna() & complete
    first_offset[~complete] = np.nan
    result["first_deterioration_timestamp"] = result.timestamp + pd.to_timedelta(first_offset, unit="min")
    result["lead_time_minutes"] = np.nan
    eligible = result.evaluation_valid & result.deterioration.fillna(False)
    high = eligible & result.risk_signal.eq("HIGH_RISK").fillna(False)
    signals = result.loc[high].groupby("first_deterioration_timestamp")["timestamp"].min()
    if not signals.empty:
        first_signal = result.loc[eligible, "first_deterioration_timestamp"].map(signals)
        result.loc[eligible, "lead_time_minutes"] = (
            result.loc[eligible, "first_deterioration_timestamp"] - first_signal
        ).dt.total_seconds() / 60
    return add_exit_cost_diagnostics(result)


def add_exit_cost_diagnostics(result):
    """Preserve old outcomes and distinguish exact maxima from lower bounds."""
    for event, source in EVENT_ALIASES.items():
        result[event] = result[source]
    current = result["estimated_exit_cost_pct"]
    observed_max = result["future_max_estimated_exit_cost_pct"]
    complete = result.horizon_complete
    all_values = result.future_estimated_exit_cost_pct_valid_count.eq(result.horizon_minutes)
    result["current_exit_cost_pct"] = current
    result["future_exit_cost_complete"] = complete & all_values
    result["future_max_exit_cost_pct"] = observed_max.where(result.future_exit_cost_complete)
    result["exit_cost_change_pct"] = result.future_max_exit_cost_pct - current
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        multiple = result.future_max_exit_cost_pct / current.where(current.gt(0))
    result["exit_cost_multiple"] = multiple.where(np.isfinite(multiple))
    for name, factor in {**RELATIVE_SCENARIOS, **ABSOLUTE_SCENARIOS}.items():
        relative = name in RELATIVE_SCENARIOS
        threshold = current * factor if relative else factor
        valid_origin = current.gt(0) if relative else pd.Series(True, index=result.index)
        breach = observed_max.ge(threshold) & observed_max.notna()
        known = complete & valid_origin & (breach | all_values)
        flag = pd.Series(pd.NA, index=result.index, dtype="boolean")
        flag.loc[known] = breach.loc[known]
        result[name] = flag
    return result


def build_events(windows, features, stress, descriptive_risk):
    windows, normal = prepare_inputs(windows, features, stress, descriptive_risk)
    scores = temporal_scores(windows, normal)
    events = pd.concat([
        future_outcomes(part.reset_index(drop=True), horizon)
        for _, part in scores.groupby("position_size_usdc", sort=True)
        for horizon in HORIZONS
    ], ignore_index=True).sort_values(["timestamp", "position_size_usdc", "horizon_minutes"], kind="stable").reset_index(drop=True)
    validate_events(events)
    return events


def validate_events(events):
    if events.duplicated(["timestamp", "position_size_usdc", "horizon_minutes"]).any():
        raise ValueError("Eventos duplicados.")
    if np.isinf(events.select_dtypes(include="number").to_numpy()).any():
        raise ValueError("Infinito nos resultados.")
    for column in (*COMPONENTS, "risk_score"):
        if not events[column].dropna().between(0, 100).all():
            raise ValueError(f"Score fora de [0, 100]: {column}.")
    for metric, component in zip(METRICS, COMPONENTS):
        if events.loc[events[f"{metric}_history_count"].lt(MIN_HISTORY), component].notna().any():
            raise ValueError("Score antes do warm-up mínimo.")
    if events.loc[~events.horizon_complete, "evaluation_valid"].any():
        raise ValueError("Horizonte truncado usado na avaliação.")
    if events.loc[events.evaluation_valid, ["risk_score", "risk_regime", "deterioration"]].isna().any().any():
        raise ValueError("Observação válida contém dados insuficientes.")
    for event, source in EVENT_ALIASES.items():
        if not events[event].equals(events[source]):
            raise ValueError("Alias de evento mudou resultado existente.")
    for component in COMPONENTS:
        invalid = events[f"{component}_quartile_history_count"].lt(MIN_HISTORY) | events[component].isna()
        if events.loc[invalid, f"{component}_quartile"].notna().any():
            raise ValueError("Quartil sem histórico suficiente.")
    if events.loc[events.current_exit_cost_pct.isna() | events.current_exit_cost_pct.le(0), "exit_cost_multiple"].notna().any():
        raise ValueError("Múltiplo com denominador inválido.")
    if events.loc[~events.future_exit_cost_complete, ["future_max_exit_cost_pct", "exit_cost_change_pct", "exit_cost_multiple"]].notna().any().any():
        raise ValueError("Máximo exato ou mudança com futuro insuficiente.")
    if events.loc[~events.horizon_complete, list(DIAGNOSTIC_EVENTS)].notna().any().any():
        raise ValueError("Evento em horizonte truncado.")


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else np.nan


def summarize_events(events):
    rows = []
    for (size, horizon), all_rows in events.groupby(["position_size_usdc", "horizon_minutes"], sort=True):
        valid = all_rows.loc[all_rows.evaluation_valid]
        rates = []
        for regime in REGIMES:
            group = valid.loc[valid.risk_regime.eq(regime)]
            count = len(group)
            deteriorations = int(group.deterioration.sum())
            rate = _ratio(deteriorations, count)
            rates.append(rate)
            rows.append(dict(
                position_size_usdc=size, horizon_minutes=horizon, summary_type="regime", risk_regime=regime,
                observation_count=count, deterioration_count=deteriorations, deterioration_rate=rate,
                mean_future_max_exit_cost_pct=group.future_max_estimated_exit_cost_pct.mean(),
                median_future_max_exit_cost_pct=group.future_max_estimated_exit_cost_pct.median(),
                mean_future_max_volatility=group.future_max_volatility.mean(),
            ))
        high = valid.risk_signal.eq("HIGH_RISK")
        outcome = valid.deterioration.astype(bool)
        tp, fp = int((high & outcome).sum()), int((high & ~outcome).sum())
        tn, fn = int((~high & ~outcome).sum()), int((~high & outcome).sum())
        event_minutes = valid.loc[outcome].groupby("first_deterioration_timestamp").lead_time_minutes.max()
        leads = event_minutes.dropna()
        monotonic = bool(np.all(np.diff(rates) > 0)) if np.isfinite(rates).all() else pd.NA
        rows.append(dict(
            position_size_usdc=size, horizon_minutes=horizon, summary_type="binary", risk_regime="ALL",
            observation_count=len(valid), deterioration_count=int(outcome.sum()),
            deterioration_rate=_ratio(int(outcome.sum()), len(valid)),
            true_positives=tp, false_positives=fp, true_negatives=tn, false_negatives=fn,
            precision=_ratio(tp, tp + fp), recall=_ratio(tp, tp + fn), specificity=_ratio(tn, tn + fp),
            false_positive_rate=_ratio(fp, fp + tn),
            lead_time_median_minutes=leads.median(), lead_time_p25_minutes=leads.quantile(.25),
            lead_time_p75_minutes=leads.quantile(.75), unique_event_minute_count=len(event_minutes),
            detected_event_minute_count=len(leads), undetected_event_minute_count=int(event_minutes.isna().sum()),
            strictly_increasing_deterioration_rate=monotonic,
            total_origin_count=len(all_rows), missing_score_count=int(all_rows.risk_score.isna().sum()),
            truncated_horizon_count=int((~all_rows.horizon_complete).sum()),
            unknown_outcome_count=int((all_rows.horizon_complete & all_rows.deterioration.isna()).sum()),
        ))
    return pd.concat([pd.DataFrame(rows), summarize_diagnostics(events)], ignore_index=True)


def summarize_diagnostics(events):
    """Event-specific denominators; no selection on any_deterioration."""
    rows = []
    for (size, horizon), origins in events.groupby(["position_size_usdc", "horizon_minutes"], sort=True):
        complete = origins.loc[origins.horizon_complete]
        scored = complete.loc[complete.risk_score.notna()]
        for regime in REGIMES:
            group = scored.loc[scored.risk_regime.eq(regime)]
            cost, multiple = group.future_max_exit_cost_pct, group.exit_cost_multiple
            row = dict(position_size_usdc=size, horizon_minutes=horizon, summary_type="regime_diagnostic",
                       risk_regime=regime, eligible_origin_count=len(group),
                       future_exit_cost_observation_count=int(cost.count()), exit_cost_multiple_observation_count=int(multiple.count()),
                       diagnostic_median_future_max_exit_cost_pct=cost.median(), p95_future_max_exit_cost_pct=cost.quantile(.95),
                       median_exit_cost_multiple=multiple.median(), p95_exit_cost_multiple=multiple.quantile(.95))
            for event in DIAGNOSTIC_EVENTS:
                count = int(group[event].count())
                number = int(group[event].sum())
                row[f"{event}_observation_count"] = count
                row[f"{event}_count"] = number
                row[f"{event}_rate"] = _ratio(number, count)
            rows.append(row)
        for component in COMPONENTS:
            quartile = f"{component}_quartile"
            eligible = complete.loc[complete[quartile].notna()]
            for event in DIAGNOSTIC_EVENTS:
                rates, counts = [], []
                for label in QUARTILES:
                    group = eligible.loc[eligible[quartile].eq(label)]
                    count = int(group[event].count())
                    number = int(group[event].sum())
                    rate = _ratio(number, count)
                    rates.append(rate)
                    counts.append(count)
                    rows.append(dict(position_size_usdc=size, horizon_minutes=horizon, summary_type="component_quartile",
                                     component=component, component_quartile=label, event_type=event,
                                     eligible_origin_count=len(group), observation_count=count, event_count=number,
                                     unknown_event_count=len(group)-count, event_rate=rate))
                available = np.isfinite(rates).all()
                rank_correlation = np.nan
                if available and len(set(rates)) > 1:
                    rank_correlation = np.corrcoef(np.arange(4), pd.Series(rates).rank().to_numpy())[0, 1]
                rows.append(dict(position_size_usdc=size, horizon_minutes=horizon, summary_type="component_association",
                                 component=component, event_type=event, observation_count=sum(counts),
                                 q1_event_rate=rates[0], q2_event_rate=rates[1], q3_event_rate=rates[2], q4_event_rate=rates[3],
                                 minimum_quartile_observation_count=min(counts),
                                 q4_minus_q1_event_rate=rates[3]-rates[0] if available else np.nan,
                                 quartile_rate_spearman=rank_correlation,
                                 strictly_increasing_event_rate=bool(np.all(np.diff(rates) > 0)) if available else pd.NA,
                                 nondecreasing_event_rate=bool(np.all(np.diff(rates) >= 0)) if available else pd.NA))
    return pd.DataFrame(rows)


def print_report(summary):
    selected = summary.loc[summary.position_size_usdc.isin([100_000, 500_000, 1_000_000])]
    print("Expanding estritamente anterior a t; mínimo 120 valores válidos por componente; somente NORMAL.")
    print("40/60/80: política inicial não calibrada; sem tuning. Futuro: (t, t+h], sem horizontes truncados.")
    print("Taxas de deterioração por regime:")
    rates = selected.loc[selected.summary_type.eq("regime")].pivot(
        index=["position_size_usdc", "horizon_minutes"], columns="risk_regime", values="deterioration_rate",
    ).reindex(columns=list(REGIMES))
    print(rates.to_string(float_format=lambda x: f"{x:.4f}"))
    binary = selected.loc[selected.summary_type.eq("binary")]
    print("HIGH_RISK e lead time (minutos; minutos de evento deduplicados):")
    print(binary[["position_size_usdc", "horizon_minutes", "observation_count", "precision", "recall",
                  "false_positive_rate", "lead_time_median_minutes", "lead_time_p25_minutes",
                  "lead_time_p75_minutes", "detected_event_minute_count", "unique_event_minute_count"]].to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    for _, row in binary.iterrows():
        increasing = row.strictly_increasing_deterioration_rate
        if pd.isna(increasing) or not increasing:
            message = "não verificável (regime sem observações)" if pd.isna(increasing) else "NÃO aumenta estritamente NORMAL < WATCH < STRESSED < CRITICAL"
            print(f"ATENÇÃO: posição {int(row.position_size_usdc):,}, {int(row.horizon_minutes)}m: {message}.")
    print("Horizontes sobrepostos; lead time condicionado a eventos detectados, não a todos os eventos.")


def print_diagnostic_report(summary):
    selected = summary.loc[summary.position_size_usdc.isin([100_000, 500_000, 1_000_000])]
    regimes = selected.loc[selected.summary_type.eq("regime_diagnostic")]
    keys = ["position_size_usdc", "horizon_minutes", "risk_regime"]
    print("\nDIAGNÓSTICO: thresholds econômicos são cenários de análise, não calibrados; nenhum tuning.")
    print("Quartis dos scores temporais anteriores a t; 120 scores válidos, empates preservados.")
    print("Taxas por evento (denominadores próprios, registrados no CSV):")
    print(regimes[keys + [f"{event}_rate" for event in EVENT_ALIASES]].to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("Future Exit Cost por regime: máximo exato exige todos os minutos futuros.")
    print(regimes[keys + ["future_exit_cost_observation_count", "diagnostic_median_future_max_exit_cost_pct",
                          "p95_future_max_exit_cost_pct", "median_exit_cost_multiple", "p95_exit_cost_multiple",
                          *[f"{event}_rate" for event in RELATIVE_SCENARIOS]]].to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("Cenários de custo absoluto: 0.50%, 1.00%, 2.00% (unidade do CSV: pontos percentuais).")
    print(regimes[keys + [f"{event}_rate" for event in ABSOLUTE_SCENARIOS]].to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    association = selected.loc[selected.summary_type.eq("component_association")]
    one_million = association.loc[association.position_size_usdc.eq(1_000_000)]
    print("Quartis Q1–Q4 para 1M: any_deterioration e custo dobrando; demais eventos no CSV.")
    print(one_million.loc[one_million.event_type.isin(["any_deterioration", "exit_cost_double"]),
                          ["horizon_minutes", "component", "event_type", "observation_count", "minimum_quartile_observation_count",
                           "q1_event_rate", "q2_event_rate", "q3_event_rate", "q4_event_rate"]].to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("Associação por componente/evento em 1M: média descritiva dos horizontes; sem teste de significância.")
    strength = one_million.groupby(["component", "event_type"], sort=False).agg(
        mean_quartile_rate_spearman=("quartile_rate_spearman", "mean"),
        mean_q4_minus_q1_rate=("q4_minus_q1_event_rate", "mean"),
        increasing_horizon_count=("strictly_increasing_event_rate", lambda x: int(x.fillna(False).sum())),
        evaluable_horizon_count=("strictly_increasing_event_rate", "count"),
    ).reset_index()
    print(strength.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("Componentes com pouca ou nenhuma evidência de ordenação positiva:")
    for component, group in strength.groupby("component", sort=False):
        increasing = int(group.increasing_horizon_count.sum())
        evaluated = int(group.evaluable_horizon_count.sum())
        aggregate = group.loc[group.event_type.eq("any_deterioration")].iloc[0]
        qualifier = "pouca ou nenhuma ordenação monotônica positiva" if increasing == 0 else "ordenação positiva em parte dos eventos"
        print(f"  {component}: {qualifier}; {increasing}/{evaluated} combinações evento/horizonte estritamente crescentes; "
              f"any_deterioration Spearman médio={aggregate.mean_quartile_rate_spearman:.3f}.")
    for event in EVENT_ALIASES:
        total = regimes[f"{event}_observation_count"].sum()
        positives = regimes[f"{event}_count"].sum()
        if total and positives in (0, total):
            print(f"ATENÇÃO: {event} não tem variação nos regimes/horizontes selecionados; associação não identificável.")
    if regimes.flow_imbalance_event_count.sum() == 0:
        print("Flow: nenhum cruzamento observado; abs(flow_imbalance) é limitado a 1, e p95=1 impede evento estrito >p95.")
    for event in DIAGNOSTIC_EVENTS:
        group = strength.loc[strength.event_type.eq(event)]
        positive = group.loc[group.mean_quartile_rate_spearman.gt(0) & group.mean_q4_minus_q1_rate.gt(0)]
        if positive.empty:
            print(f"{event}: nenhum componente com associação média positiva consistente nos dois indicadores.")
        else:
            strongest = positive.sort_values(["increasing_horizon_count", "mean_quartile_rate_spearman", "mean_q4_minus_q1_rate"], ascending=False).iloc[0]
            print(f"{event}: relação positiva mais ordenada: {strongest.component}; "
                  f"{int(strongest.increasing_horizon_count)}/{int(strongest.evaluable_horizon_count)} horizontes estritamente crescentes; "
                  f"Spearman médio={strongest.mean_quartile_rate_spearman:.3f}, ΔQ4−Q1={strongest.mean_q4_minus_q1_rate:.3f}.")
    print("Ordenação de Future Exit Cost por regime (aumento estrito de mediana e p95):")
    ordering = []
    for (size, horizon), group in regimes.groupby(["position_size_usdc", "horizon_minutes"]):
        group = group.set_index("risk_regime").reindex(REGIMES)
        median = group.diagnostic_median_future_max_exit_cost_pct.to_numpy(dtype=float)
        p95 = group.p95_future_max_exit_cost_pct.to_numpy(dtype=float)
        ordering.append(dict(position_size_usdc=size, horizon_minutes=horizon,
                             increasing_median_adjacent_pairs=int((np.diff(median) > 0).sum()) if np.isfinite(median).all() else pd.NA,
                             increasing_p95_adjacent_pairs=int((np.diff(p95) > 0).sum()) if np.isfinite(p95).all() else pd.NA,
                             critical_above_stressed_median=bool(median[3] > median[2]) if np.isfinite(median[2:]).all() else pd.NA,
                             critical_above_stressed_p95=bool(p95[3] > p95[2]) if np.isfinite(p95[2:]).all() else pd.NA))
    ordering = pd.DataFrame(ordering)
    print(ordering.to_string(index=False))
    reference_order = ordering.loc[ordering.position_size_usdc.eq(1_000_000)].copy()
    reference_order["ordered_pairs"] = reference_order.increasing_median_adjacent_pairs + reference_order.increasing_p95_adjacent_pairs
    if reference_order.ordered_pairs.notna().any():
        best = reference_order.loc[reference_order.ordered_pairs.eq(reference_order.ordered_pairs.max()), "horizon_minutes"].tolist()
        print(f"Horizontes com melhor ordenação descritiva em 1M (mediana+p95, até 6 pares adjacentes): {best} minutos.")
    print("Diferenças entre 100k, 500k e 1M (mesmo horizonte/regime):")
    for column in [*[f"{event}_rate" for event in DIAGNOSTIC_EVENTS], "median_exit_cost_multiple", "diagnostic_median_future_max_exit_cost_pct"]:
        pivot = regimes.pivot(index=["horizon_minutes", "risk_regime"], columns="position_size_usdc", values=column)
        equal = all(np.allclose(pivot.iloc[:, 0], pivot.iloc[:, index], equal_nan=True) for index in range(1, len(pivot.columns)))
        print(f"  {column}: {'coincidem' if equal else 'diferem'}.")
    print("Limitações: apenas um dia, horizontes sobrepostos, quartis pequenos/desiguais, dependência temporal,")
    print("eventos saturados nos horizontes longos e máximos incompletos excluídos das distribuições de custos.")
    print("Spearman aqui ordena quatro taxas agregadas; não mede causalidade nem significância ou desempenho fora da amostra.")


def main():
    events = build_events(*[pd.read_csv(OUTPUT_DIR / name) for name in (
        "liquidity_1m.csv", "liquidity_features_1m.csv", "exit_cost_stress_1m.csv", "liquidity_risk_1m.csv",
    )])
    summary = summarize_events(events)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    events.to_csv(OUTPUT_DIR / "backtest_events.csv", index=False)
    summary.to_csv(OUTPUT_DIR / "backtest_summary.csv", index=False)
    print_report(summary)
    print_diagnostic_report(summary)
    print(f"Validações OK; {len(events):,} linhas; {int(events.evaluation_valid.sum()):,} avaliações válidas.")


if __name__ == "__main__":
    main()
