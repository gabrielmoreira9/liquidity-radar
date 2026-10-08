"""Causal, explainable V2: market severity, forward signal and position exit.

Only audited causal CSVs are inputs. The stateful engine sees one closed minute
at a time and independently verifies supplied ECDFs/counts/medians against its
strictly prior history. No full-period descriptive feature enters a score.
Market = equal mean of four components; reference position = 100k NORMAL.
Forward = equal mean of liquidity and absolute flow components only.
40/60/80 and operational .5/1/2 boundaries are initial heuristic policies.
Neither forward scores nor exit estimates are calibrated probabilities/quotes.
"""
import argparse
from bisect import bisect_right, insort
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_DIR = ROOT / "data/processed/audit/20261008T150552576490Z"
OUTPUT_DIR = ROOT / "data/processed"
MIN_HISTORY = 120
REFERENCE_POSITION = 100_000
POSITIONS = (10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000)
SCENARIOS = {"NORMAL": 1., "STRESS_25": .75, "STRESS_50": .5, "STRESS_75": .25}
REGIMES = ("NORMAL", "WATCH", "STRESSED", "CRITICAL")
EXIT_CLASSES = ("LOW", "MODERATE", "HIGH", "EXTREME")
FLAGS = ("extrapolation_warning", "insufficient_liquidity_flag", "insufficient_data_flag")
MARKET_COMPONENTS = ("volatility_risk", "liquidity_risk", "flow_risk", "reference_exit_cost_risk")
FEATURE_NUMERIC = ("swap_count", "volume_usdc", "net_flow_usdc", "volatility", "flow_imbalance",
    "volume_usdc_causal_history_count", "volume_usdc_causal_percentile", "volume_usdc_causal_median",
    "volatility_causal_history_count", "volatility_causal_percentile",
    "abs_flow_imbalance_causal_history_count", "abs_flow_imbalance_causal_percentile")
EXIT_NUMERIC = ("position_size_usdc", "liquidity_multiplier", "available_volume_usdc", "participation_rate",
    "volatility", "estimated_exit_cost_pct", "flow_imbalance", "median_volume_usdc_causal",
    "volume_history_count", "liquidity_multiple_causal", "exit_cost_causal_percentile", "exit_cost_causal_history_count")
TIME_FIELDS = ("timestamp", "available_at", "historical_reference_before")


def number(value):
    result = np.nan if value is None or value is pd.NA or (isinstance(value, str) and not value.strip()) else float(value)
    if np.isinf(result):
        raise ValueError("Valor infinito na entrada causal.")
    return result


def same(actual, expected, name):
    if not ((pd.isna(actual) and pd.isna(expected)) or
            (pd.notna(actual) and pd.notna(expected) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12))):
        raise ValueError(f"Referência/medição inconsistente: {name}: {actual} != {expected}")


def prepare(row, numeric, is_exit=False):
    required = {*TIME_FIELDS, "minimum_history_observations", *numeric}
    if is_exit:
        required |= {*FLAGS, "stress_scenario", "model_reliability_status", "extrapolation_reason", "reference_scope"}
    if not required <= set(row):
        raise ValueError(f"Colunas causais obrigatórias ausentes: {sorted(required-set(row))}")
    result = {key: row[key] for key in sorted(required)}
    for key in numeric:
        result[key] = number(result[key])
    for key in TIME_FIELDS:
        result[key] = pd.Timestamp(result[key])
        if pd.isna(result[key]) or result[key].tzinfo is None:
            raise ValueError("Timestamp ausente ou sem timezone.")
        result[key] = result[key].tz_convert("UTC")
    timestamp = result["timestamp"]
    if timestamp != timestamp.floor("min") or result["available_at"] != timestamp + pd.Timedelta(minutes=1):
        raise ValueError("Disponibilidade deve corresponder ao fechamento da janela 1m.")
    if result["historical_reference_before"] != timestamp or number(result["minimum_history_observations"]) != MIN_HISTORY:
        raise ValueError("Corte histórico ou mínimo de 120 observações inválido.")
    if is_exit:
        if result["reference_scope"] != "STRICTLY_PREVIOUS_WINDOWS":
            raise ValueError("Stress não causal.")
        for key in FLAGS:
            value = result[key]
            if isinstance(value, (bool, np.bool_)):
                result[key] = bool(value)
            elif value in ("True", "False"):
                result[key] = value == "True"
            else:
                raise ValueError(f"Flag inválida: {key}")
    return result


class PriorHistory:
    """Sorted past-only sample. Read reference BEFORE admitting current value."""
    def __init__(self):
        self.values = []

    def reference(self, current):
        count = len(self.values)
        if count < MIN_HISTORY:
            return count, np.nan, np.nan
        middle = count//2
        median = self.values[middle] if count % 2 else (self.values[middle-1]+self.values[middle])/2
        percentile = 100.*bisect_right(self.values, current)/count if pd.notna(current) else np.nan
        return count, percentile, median

    def admit(self, value):
        if pd.notna(value):
            insort(self.values, value)


def risk_regime(score):
    if pd.isna(score):
        return "INDETERMINATE"
    return REGIMES[sum(score >= threshold for threshold in (40, 60, 80))]


def operational_risk(participation, cost):
    """Greatest severity of the two absolute measures; no warm-up needed here."""
    participation, cost = number(participation), number(cost)
    if pd.isna(participation) or pd.isna(cost):
        return "INDETERMINATE"
    if participation < 0 or cost < 0:
        raise ValueError("Participação/custo negativo.")
    severity = max(sum(participation >= threshold for threshold in (.5, 1., 2.)),
                   sum(cost >= threshold for threshold in (.5, 1., 2.)))
    return EXIT_CLASSES[severity]


def drivers(components):
    ranked = sorted(((name, value) for name, value in components.items() if pd.notna(value)),
                    key=lambda item: -item[1])
    return tuple([item[0] for item in ranked[:2]] + [None]*max(0, 2-len(ranked)))


class RiskV2Engine:
    def __init__(self):
        self.histories = {name: PriorHistory() for name in ("volatility", "volume", "flow")}
        self.exit_histories = {(size, scenario): PriorHistory() for size in POSITIONS for scenario in SCENARIOS}
        self.previous_timestamp = None

    def evaluate(self, feature_row, exit_rows):
        """All arguments describe the SAME closed minute; future rows inaccessible."""
        f = prepare(feature_row, FEATURE_NUMERIC)
        timestamp = f["timestamp"]
        if self.previous_timestamp is not None and timestamp != self.previous_timestamp + pd.Timedelta(minutes=1):
            raise ValueError("Minutos duplicados, fora de ordem ou descontínuos.")
        if pd.isna(f["swap_count"]) or f["swap_count"] < 0 or f["swap_count"] != int(f["swap_count"]):
            raise ValueError("Contagem de swaps inválida.")
        for metric in ("volume_usdc", "volatility"):
            if pd.notna(f[metric]) and f[metric] < 0:
                raise ValueError(f"{metric} negativo.")
        if pd.notna(f["flow_imbalance"]) and abs(f["flow_imbalance"]) > 1+1e-12:
            raise ValueError("Desequilíbrio de fluxo fora do domínio.")
        if pd.notna(f["volume_usdc"]):
            if f["volume_usdc"] > 0:
                same(f["flow_imbalance"], f["net_flow_usdc"]/f["volume_usdc"], "net flow / volume")
            else:
                same(f["net_flow_usdc"], 0., "empty net flow")
                same(f["flow_imbalance"], np.nan, "zero-volume flow")
        entries = [prepare(row, EXIT_NUMERIC, is_exit=True) for row in exit_rows]
        keys = [(row["position_size_usdc"], row["stress_scenario"]) for row in entries]
        if len(keys) != 24 or set(keys) != set(self.exit_histories):
            raise ValueError("Esperadas exatamente seis posições × quatro cenários por minuto.")
        references, raw_values = {}, {"volatility": f["volatility"], "volume": f["volume_usdc"],
                                     "flow": abs(f["flow_imbalance"])}
        prefixes = {"volatility": "volatility", "volume": "volume_usdc", "flow": "abs_flow_imbalance"}
        reasons = {}
        for metric, value in raw_values.items():
            prefix = prefixes[metric]
            count, percentile, median = self.histories[metric].reference(value)
            same(f[f"{prefix}_causal_history_count"], count, f"{prefix} history_count")
            same(f[f"{prefix}_causal_percentile"], percentile, f"{prefix} percentile")
            if metric == "volume":
                same(f["volume_usdc_causal_median"], median, "volume median")
            references[metric] = (count, percentile, median)
            reasons[metric] = []
            if count < MIN_HISTORY:
                reasons[metric].append(f"{metric.upper()}_HISTORY_LT_120")
            if pd.isna(value):
                reasons[metric].append(f"{metric.upper()}_OBSERVATION_MISSING")
        reference_entry = None
        for row in entries:
            if row["timestamp"] != timestamp or row["available_at"] != f["available_at"]:
                raise ValueError("Features e stress descrevem minutos/disponibilidades diferentes.")
            scenario, size = row["stress_scenario"], row["position_size_usdc"]
            same(row["liquidity_multiplier"], SCENARIOS[scenario], "scenario multiplier")
            same(row["available_volume_usdc"], f["volume_usdc"]*SCENARIOS[scenario], "available volume")
            same(row["volatility"], f["volatility"], "volatility")
            same(row["flow_imbalance"], f["flow_imbalance"], "flow imbalance")
            volume = row["available_volume_usdc"]
            expected_part = size/volume if pd.notna(volume) and volume > 0 else np.nan
            same(row["participation_rate"], expected_part, "participation")
            expected_cost = 100*f["volatility"]*np.sqrt(expected_part)
            same(row["estimated_exit_cost_pct"], expected_cost, "unchanged exit formula")
            count, percentile, _ = self.exit_histories[(size, scenario)].reference(row["estimated_exit_cost_pct"])
            same(row["exit_cost_causal_history_count"], count, "exit history_count")
            same(row["exit_cost_causal_percentile"], percentile, "exit percentile")
            same(row["volume_history_count"], references["volume"][0], "volume history_count")
            median = references["volume"][2]
            same(row["median_volume_usdc_causal"], median, "causal median volume")
            same(row["liquidity_multiple_causal"], size/median if pd.notna(median) and median > 0 else np.nan,
                 "causal liquidity multiple")
            # Validate flags without changing or suppressing their audited values.
            data_missing = pd.isna(volume) or pd.isna(f["volatility"])
            low_turnover = (pd.notna(volume) and volume <= 0) or (pd.notna(expected_part) and expected_part > 1)
            impact_extreme = pd.notna(expected_cost) and expected_cost >= 100
            extrapolated = impact_extreme or (pd.notna(expected_part) and expected_part > 1)
            if (row["insufficient_data_flag"], row["insufficient_liquidity_flag"], row["extrapolation_warning"]) != (data_missing, low_turnover, extrapolated):
                raise ValueError("Flags não correspondem aos dados auditados.")
            if size == REFERENCE_POSITION and scenario == "NORMAL":
                reference_entry = row
        components = dict(zip(MARKET_COMPONENTS, [references["volatility"][1],
            100-references["volume"][1], references["flow"][1], reference_entry["exit_cost_causal_percentile"]]))
        market_score = sum(components.values())/4
        forward_score = (components["liquidity_risk"]+components["flow_risk"])/2
        ref_reasons = []
        if reference_entry["exit_cost_causal_history_count"] < MIN_HISTORY:
            ref_reasons.append("REFERENCE_EXIT_HISTORY_LT_120")
        if pd.isna(reference_entry["estimated_exit_cost_pct"]):
            ref_reasons.append("REFERENCE_EXIT_OBSERVATION_MISSING")
        market_reasons = reasons["volatility"]+reasons["volume"]+reasons["flow"]+ref_reasons
        forward_reasons = reasons["volume"]+reasons["flow"]
        market_drivers = drivers(components)
        forward_drivers = drivers({name: components[name] for name in ("liquidity_risk", "flow_risk")})
        output = []
        for row in sorted(entries, key=lambda row: (row["position_size_usdc"], -row["liquidity_multiplier"])):
            position_reasons = []
            if pd.isna(row["available_volume_usdc"]):
                position_reasons.append("AVAILABLE_VOLUME_MISSING")
            elif row["available_volume_usdc"] <= 0:
                position_reasons.append("NO_OBSERVED_TURNOVER")
            if pd.isna(row["volatility"]):
                position_reasons.append("VOLATILITY_UNAVAILABLE")
            if pd.isna(row["participation_rate"]):
                position_reasons.append("PARTICIPATION_UNAVAILABLE")
            if pd.isna(row["estimated_exit_cost_pct"]):
                position_reasons.append("EXIT_COST_UNAVAILABLE")
            multiple_reason = ("VOLUME_HISTORY_LT_120" if row["volume_history_count"] < MIN_HISTORY else
                               "HISTORICAL_MEDIAN_NONPOSITIVE" if pd.notna(row["median_volume_usdc_causal"]) and row["median_volume_usdc_causal"] <= 0 else
                               "HISTORICAL_MEDIAN_MISSING" if pd.isna(row["median_volume_usdc_causal"]) else "AVAILABLE")
            output.append({**row, "position_size_usdc": int(row["position_size_usdc"]),
                "swap_count": int(f["swap_count"]), "volume_usdc": f["volume_usdc"], "net_flow_usdc": f["net_flow_usdc"],
                **components, "current_market_risk_score": market_score,
                "current_market_risk_regime": risk_regime(market_score),
                "current_market_risk_unavailable_reason": ";".join(market_reasons) or "AVAILABLE",
                "market_reference_position_usdc": REFERENCE_POSITION, "market_reference_scenario": "NORMAL",
                "reference_estimated_exit_cost_pct": reference_entry["estimated_exit_cost_pct"],
                "reference_exit_extrapolation_warning": reference_entry["extrapolation_warning"],
                "reference_exit_model_reliability_status": reference_entry["model_reliability_status"],
                "market_primary_risk_driver": market_drivers[0], "market_secondary_risk_driver": market_drivers[1],
                "forward_liquidity_risk_score": forward_score, "forward_liquidity_risk_regime": risk_regime(forward_score),
                "forward_liquidity_risk_unavailable_reason": ";".join(forward_reasons) or "AVAILABLE",
                "forward_primary_risk_driver": forward_drivers[0], "forward_secondary_risk_driver": forward_drivers[1],
                "position_exit_risk": operational_risk(row["participation_rate"], row["estimated_exit_cost_pct"]),
                "position_exit_risk_unavailable_reason": ";".join(position_reasons) or "AVAILABLE",
                "liquidity_multiple_unavailable_reason": multiple_reason,
                "volatility_history_count": references["volatility"][0], "flow_history_count": references["flow"][0],
                "reference_exit_cost_history_count": reference_entry["exit_cost_causal_history_count"],
                "exit_cost_interpretation": "UNVALIDATED_EXTRAPOLATED_PROXY" if row["extrapolation_warning"] else "UNVALIDATED_VOLATILITY_TURNOVER_PROXY",
                "availability_basis": "THEORETICAL_WINDOW_CLOSE_NOT_MEASURED_INGESTION"})
        # Admit only AFTER all computations and checks for this minute complete.
        for metric, value in raw_values.items():
            self.histories[metric].admit(value)
        for row in entries:
            self.exit_histories[(row["position_size_usdc"], row["stress_scenario"])].admit(row["estimated_exit_cost_pct"])
        self.previous_timestamp = timestamp
        return output


def build_risk(features, exits):
    """DataFrame adapter for tests/research; scoring still calls a past-only engine."""
    engine, rows = RiskV2Engine(), []
    grouped = {timestamp: part.to_dict("records") for timestamp, part in exits.groupby("timestamp", sort=False)}
    if set(features.timestamp) != set(grouped):
        raise ValueError("Cobertura temporal divergente.")
    for feature in features.to_dict("records"):
        rows.extend(engine.evaluate(feature, grouped[feature["timestamp"]]))
    detail = pd.DataFrame(rows)
    validate_output(detail, len(features))
    return detail


def validate_output(detail, window_count):
    keys = ["timestamp", "position_size_usdc", "stress_scenario"]
    if len(detail) != window_count*24 or detail.duplicated(keys).any():
        raise ValueError("Observações perdidas ou duplicadas.")
    if not detail.timestamp.is_monotonic_increasing or np.isinf(detail.select_dtypes("number").to_numpy()).any():
        raise ValueError("Ordem cronológica/infinito inválido.")
    for column in (*MARKET_COMPONENTS, "current_market_risk_score", "forward_liquidity_risk_score"):
        if not detail[column].dropna().between(0, 100).all():
            raise ValueError(f"Score fora de [0,100]: {column}")
        if detail.groupby("timestamp")[column].nunique(dropna=False).gt(1).any():
            raise ValueError("Indicador de mercado/forward depende da posição/cenário.")
    for score, regime, reason in (("current_market_risk_score", "current_market_risk_regime", "current_market_risk_unavailable_reason"),
                                  ("forward_liquidity_risk_score", "forward_liquidity_risk_regime", "forward_liquidity_risk_unavailable_reason")):
        if not detail[score].isna().equals(detail[regime].eq("INDETERMINATE")) or not detail[score].isna().equals(detail[reason].ne("AVAILABLE")):
            raise ValueError("Score/regime/motivo incompatíveis.")
    missing = detail.participation_rate.isna() | detail.estimated_exit_cost_pct.isna()
    if not missing.equals(detail.position_exit_risk.eq("INDETERMINATE")):
        raise ValueError("Classificação artificial em posição com dados insuficientes.")
    ranks = detail.position_exit_risk.map(dict(zip(EXIT_CLASSES, range(4))))
    ranked = detail.assign(_rank=ranks).sort_values(["timestamp", "stress_scenario", "position_size_usdc"])
    if ranked.groupby(["timestamp", "stress_scenario"])._rank.diff().lt(0).any():
        raise ValueError("Risco operacional diminuiu com a posição.")


def summarize_risk(detail):
    unique = detail.drop_duplicates("timestamp")
    rows = []
    for indicator, score, regime in (("CURRENT_MARKET_RISK", "current_market_risk_score", "current_market_risk_regime"),
                                      ("FORWARD_LIQUIDITY_RISK", "forward_liquidity_risk_score", "forward_liquidity_risk_regime")):
        rows.append({"indicator": indicator, "position_size_usdc": pd.NA, "stress_scenario": "POSITION_INDEPENDENT",
                     "observation_count": len(unique), "valid_count": unique[score].count(),
                     "indeterminate_count": unique[score].isna().sum(), "median_score": unique[score].median(),
                     "p95_score": unique[score].quantile(.95), "max_score": unique[score].max(),
                     **{f"count_{name}": int(unique[regime].eq(name).sum()) for name in REGIMES},
                     "unavailable_reasons": json.dumps(unique.loc[unique[score].isna(), indicator.lower()+"_unavailable_reason"].value_counts().to_dict(), sort_keys=True),
                     "market_reference_position_usdc": REFERENCE_POSITION})
    for (size, scenario), part in detail.groupby(["position_size_usdc", "stress_scenario"], sort=True):
        rows.append({"indicator": "POSITION_EXIT_RISK", "position_size_usdc": size, "stress_scenario": scenario,
                     "observation_count": len(part), "valid_count": part.position_exit_risk.ne("INDETERMINATE").sum(),
                     "indeterminate_count": part.position_exit_risk.eq("INDETERMINATE").sum(),
                     **{f"count_{name}": int(part.position_exit_risk.eq(name).sum()) for name in EXIT_CLASSES},
                     "median_exit_cost_pct": part.estimated_exit_cost_pct.median(),
                     "p95_exit_cost_pct": part.estimated_exit_cost_pct.quantile(.95),
                     "max_exit_cost_pct": part.estimated_exit_cost_pct.max(),
                     "median_participation_rate": part.participation_rate.median(),
                     **{f"count_{flag}": int(part[flag].sum()) for flag in FLAGS},
                     "cost_above_100_count": int(part.estimated_exit_cost_pct.gt(100).sum()),
                     "unavailable_reasons": json.dumps(part.loc[part.position_exit_risk.eq("INDETERMINATE"), "position_exit_risk_unavailable_reason"].value_counts().to_dict(), sort_keys=True),
                     "reliability_distribution": json.dumps(part.model_reliability_status.value_counts().to_dict(), sort_keys=True)})
    return pd.DataFrame(rows)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(audit_dir, output_dir=OUTPUT_DIR):
    """Stream inputs sequentially; stage all validated outputs before publication."""
    audit_dir, output_dir = Path(audit_dir).resolve(), Path(output_dir).resolve()
    feature_path = audit_dir/"liquidity_features_causal_1m.csv"
    exit_path = audit_dir/"exit_cost_stress_causal_1m.csv"
    manifest = {str(Path(name).resolve()): entry for name, entry in
                json.loads((audit_dir/"manifest_outputs.json").read_text()).items()}
    inputs = {str(path): sha256(path) for path in (feature_path, exit_path)}
    for name, digest in inputs.items():
        if manifest.get(name, {}).get("sha256") != digest:
            raise ValueError("Arquivo causal não corresponde ao manifesto da auditoria.")
    engine, rows, window_count = RiskV2Engine(), [], 0
    with feature_path.open(newline="", encoding="utf-8") as f, exit_path.open(newline="", encoding="utf-8") as e:
        features_reader, exit_reader = csv.DictReader(f), csv.DictReader(e)
        for feature in features_reader:
            current_exits = [next(exit_reader, None) for _ in range(24)]
            if any(row is None for row in current_exits):
                raise ValueError("Stress incompleto.")
            rows.extend(engine.evaluate(feature, current_exits))
            window_count += 1
        if next(exit_reader, None) is not None or window_count == 0:
            raise ValueError("Stress excedente ou dataset vazio.")
    detail = pd.DataFrame(rows)
    validate_output(detail, window_count)
    summary = summarize_risk(detail)
    if any(sha256(Path(name)) != digest for name, digest in inputs.items()):
        raise ValueError("Input alterado durante o processamento.")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir.mkdir(parents=True, exist_ok=True)
    version_dir = output_dir/"risk_v2_runs"/run_id
    version_dir.mkdir(parents=True, exist_ok=False)
    for name, frame in (("liquidity_risk_v2_1m.csv", detail), ("risk_v2_summary.csv", summary)):
        frame.to_csv(version_dir/name, index=False)
    metadata = {"run_id": run_id, "source_audit": str(audit_dir), "inputs_sha256": inputs,
                "output_sha256": {name: sha256(version_dir/name) for name in ("liquidity_risk_v2_1m.csv", "risk_v2_summary.csv")},
                "engine_sha256": sha256(Path(__file__)), "minimum_valid_prior_observations": MIN_HISTORY,
                "market_reference_position_usdc": REFERENCE_POSITION, "market_reference_scenario": "NORMAL",
                "policy": "HEURISTIC_UNCALIBRATED_40_60_80_AND_OPERATIONAL_0.5_1_2",
                "window_count": window_count, "position_scenario_row_count": len(detail),
                "swap_count_deduplicated_minutes": int(detail.drop_duplicates("timestamp").swap_count.sum()),
                "volume_usdc_deduplicated_minutes": detail.drop_duplicates("timestamp").volume_usdc.sum(),
                "net_flow_usdc_deduplicated_minutes": detail.drop_duplicates("timestamp").net_flow_usdc.sum(),
                "availability": "theoretical window close; actual ingestion/finality unmeasured"}
    (version_dir/"metadata.json").write_text(json.dumps(metadata, indent=2))
    for name in ("liquidity_risk_v2_1m.csv", "risk_v2_summary.csv"):
        with tempfile.NamedTemporaryFile(dir=output_dir, prefix=".risk_v2_", delete=False) as staged:
            temporary = Path(staged.name)
        try:
            shutil.copyfile(version_dir/name, temporary)
            temporary.replace(output_dir/name)
        finally:
            temporary.unlink(missing_ok=True)
    print("Current Market Risk e Forward Liquidity Risk: contagem por minuto único.")
    print(summary.iloc[:2][["indicator", "observation_count", "valid_count", "indeterminate_count",
                           "median_score", "p95_score", "count_NORMAL", "count_WATCH", "count_STRESSED", "count_CRITICAL"]].to_string(index=False))
    print("Position Exit Risk — NORMAL; custos em %, participação em razão:")
    selected = summary.loc[summary.position_size_usdc.isin([100000, 500000, 1000000]) & summary.stress_scenario.eq("NORMAL")]
    print(selected[["position_size_usdc", "count_LOW", "count_MODERATE", "count_HIGH", "count_EXTREME", "indeterminate_count",
                    "median_exit_cost_pct", "p95_exit_cost_pct", "median_participation_rate", "count_extrapolation_warning"]].to_string(index=False))
    print(f"Todos os cenários: extrapoladas {detail.extrapolation_warning.sum()}; posições indeterminadas {detail.position_exit_risk.eq('INDETERMINATE').sum()}; custos >100% {detail.estimated_exit_cost_pct.gt(100).sum()}.")
    print("Forward é sinal exploratório, não probabilidade. Exit cost não é slippage executável validado; Q/V não prova execução inviável.")
    print("Pronto tecnicamente para avaliação temporal fora da amostra, ainda sem validação preditiva/econômica fora da amostra.")
    print(f"Outputs: {output_dir/'liquidity_risk_v2_1m.csv'} | {output_dir/'risk_v2_summary.csv'} | versão: {version_dir}")
    return detail, summary, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR,
                        help="Versão causal auditada, com manifesto verificável; default fixo/reproduzível.")
    run(parser.parse_args().audit_dir)


if __name__ == "__main__":
    main()
