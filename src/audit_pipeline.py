"""Reproducible, entirely local audit. Writes only a NEW versioned directory.

Run as python -m src.audit_pipeline from the repository root. Never invokes
collectors, RPC, risk or backtest. Existing CSVs, checkpoints and raw data are
read-only, hashed before/after, and baseline statistical outputs are copied.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from . import features, liquidity, stress

ROOT = Path(__file__).resolve().parent.parent
POOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
MINTS = {"USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
         "SOL": "So11111111111111111111111111111111111111112"}
BASELINE_NAMES = ("liquidity_1m.csv", "liquidity_5m.csv", "liquidity_15m.csv",
                  "liquidity_features_1m.csv", "liquidity_baseline_summary.csv",
                  "exit_cost_stress_1m.csv", "exit_cost_summary.csv")


def file_manifest(paths):
    return {str(path): {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
            for path in sorted(set(paths)) if path.is_file()}


def verify_manifest(manifest):
    for name, expected in manifest.items():
        path = Path(name)
        actual = file_manifest([path]).get(name)
        if actual != expected:
            raise ValueError(f"Arquivo protegido alterado: {name}")


def atomic_diagnostics(row):
    """Nominal mint precision, not an RPC-verified amount for this signature."""
    usdc_units = abs(Decimal(str(row["usdc_delta"]))) * Decimal(10**6)
    sol_units = abs(Decimal(str(row["sol_delta"]))) * Decimal(10**9)
    return {"usdc_atomic_units": float(usdc_units), "sol_atomic_units": float(sol_units),
            "usdc_one_unit_relative_resolution": float(1 / usdc_units),
            "sol_one_unit_relative_resolution": float(1 / sol_units),
            "atomic_grid_consistent": abs(usdc_units-usdc_units.to_integral_value()) < Decimal("0.0001")
                                      and abs(sol_units-sol_units.to_integral_value()) < Decimal("0.0001")}


def local_raw_evidence(signatures):
    evidence, coverage = {}, []
    for path in (ROOT / "data/raw/orca_sol_usdc_transactions.json",
                 ROOT / "src/data/raw/orca_sol_usdc_transactions.json"):
        if not path.exists():
            continue
        records = json.loads(path.read_text())
        times = [tx.get("blockTime") for tx in records if tx.get("blockTime") is not None]
        coverage.append({"path": str(path.relative_to(ROOT)), "transactions": len(records),
                         "min_block_time": min(times) if times else None,
                         "max_block_time": max(times) if times else None,
                         "observed_mint_decimals": {token: sorted({balance["uiTokenAmount"]["decimals"]
                              for tx in records for stage in ("preTokenBalances", "postTokenBalances")
                              for balance in (tx.get("meta") or {}).get(stage, []) if balance.get("mint") == mint})
                              for token, mint in MINTS.items()}})
        for tx in records:
            keys = tx.get("transaction", {}).get("signatures", [])
            if not keys or keys[0] not in signatures:
                continue
            meta = tx.get("meta") or {}
            legs = {}
            for token, mint in MINTS.items():
                totals, decimals = [], set()
                for stage in ("preTokenBalances", "postTokenBalances"):
                    total = Decimal(0)
                    for balance in meta.get(stage, []):
                        if balance.get("owner") == POOL and balance.get("mint") == mint:
                            amount = balance["uiTokenAmount"]
                            decimals.add(amount["decimals"])
                            total += Decimal(amount["amount"]) / Decimal(10)**amount["decimals"]
                    totals.append(total)
                legs[token] = {"decimals": sorted(decimals), "raw_amount_delta_ui": str(totals[1]-totals[0])}
            evidence[keys[0]] = {"local_path": str(path.relative_to(ROOT)), "slot": tx.get("slot"),
                                 "transaction_index": tx.get("transactionIndex"), "legs": legs,
                                 "meta_error": meta.get("err")}
    return evidence, coverage


def extreme_swaps(swaps, windows):
    top_windows = windows.nlargest(10, "volatility")["timestamp"]
    extrema = pd.concat([swaps.nsmallest(5, "price"), swaps.nlargest(5, "price")]).signature
    # Include all ties at the absolute extrema, not just the first five.
    select = swaps.timestamp.dt.floor("min").isin(top_windows) | swaps.signature.isin(extrema) | \
             swaps.price.eq(swaps.price.min()) | swaps.price.eq(swaps.price.max())
    chosen = swaps.loc[select].copy()
    raw, coverage = local_raw_evidence(set(chosen.signature))
    rows = []
    for minute, group in swaps.groupby(swaps.timestamp.dt.floor("min"), sort=False):
        selected = group.loc[group.signature.isin(chosen.signature)]
        if selected.empty:
            continue
        original = liquidity.aggregate_window(group)["volatility"]
        returns = np.log(group.price / group.price.shift()).to_numpy()
        valid = returns[1:]
        mean = valid.mean() if len(valid) else np.nan
        energy = np.sum((valid-mean)**2)
        for signature, row in selected.set_index("signature", drop=False).iterrows():
            position = list(group.signature).index(signature)
            adjacent = [returns[index] for index in (position, position+1) if 0 < index < len(group)]
            without = liquidity.aggregate_window(group.loc[group.signature.ne(signature)])["volatility"]
            diagnostic = atomic_diagnostics(row)
            rows.append({**row.to_dict(), "minute": minute, **diagnostic,
                         "minute_volatility": original,
                         "minute_volume_usdc": group.volume_usdc.sum(),
                         "trade_volume_share": row.volume_usdc/group.volume_usdc.sum(),
                         "adjacent_return_centered_energy_share": sum((value-mean)**2 for value in adjacent)/energy if energy > 0 else np.nan,
                         "volatility_without_this_swap_DIAGNOSTIC_ONLY": without,
                         "volatility_reduction_DIAGNOSTIC_ONLY": original-without,
                         "local_rpc_evidence": signature in raw,
                         "cause_assessment": "Atomic granularity sensitivity; auxiliary flows NOT ruled out" if diagnostic["usdc_one_unit_relative_resolution"] >= .1 else "Inspect instructions; no structural cause confirmed",
                         "classification": "SIMPLE_SWAP"})
    result = pd.DataFrame(rows).sort_values(["timestamp", "signature"], kind="stable").reset_index(drop=True)
    return result, raw, coverage


def window_sensitivity(swaps, windows):
    """Diagnostic selection <=10 USDC atoms is NOT a cleaning rule.

    Measures overlap-free variance energy from returns adjoining selected tiny
    trades, and order sensitivity. Every production observation is retained.
    The one-unit USDC resolution here is at least 10% of the trade amount.
    """
    rows = []
    for minute in windows.nlargest(10, "volatility").timestamp:
        group = swaps.loc[swaps.timestamp.dt.floor("min").eq(minute)].copy()
        tiny = group.volume_usdc.le(1e-5)
        returns = np.log(group.price/group.price.shift())
        energy = (returns-returns.mean())**2
        adjacent = tiny | tiny.shift(fill_value=False)
        reversed_ties = pd.concat([part.iloc[::-1] for _, part in group.groupby("timestamp", sort=False)])
        signature_order = group.sort_values(["timestamp", "signature"], kind="stable")
        rows.append({"timestamp": minute, "swap_count": len(group), "volume_usdc": group.volume_usdc.sum(),
                     "original_volatility": liquidity.aggregate_window(group)["volatility"],
                     "tiny_trade_count_DIAGNOSTIC_ONLY": int(tiny.sum()),
                     "tiny_trade_volume_usdc_DIAGNOSTIC_ONLY": group.loc[tiny, "volume_usdc"].sum(),
                     "tiny_trade_volume_share_DIAGNOSTIC_ONLY": group.loc[tiny, "volume_usdc"].sum()/group.volume_usdc.sum(),
                     "tiny_adjacent_return_energy_share_DIAGNOSTIC_ONLY": energy.loc[adjacent].sum()/energy.sum(),
                     "reversed_tie_volatility_DIAGNOSTIC_ONLY": liquidity.aggregate_window(reversed_ties)["volatility"],
                     "signature_order_volatility_DIAGNOSTIC_ONLY": liquidity.aggregate_window(signature_order)["volatility"]})
    return pd.DataFrame(rows)


def audit_source_correspondence(swaps):
    """Verify CSV evidence, without pretending labels prove transfer exclusivity."""
    rows = []
    for day, source in swaps.groupby(swaps.timestamp.dt.strftime("%Y-%m-%d")):
        historical = pd.read_csv(ROOT/f"data/historical/swaps/sol_usdc_swaps_{day}.csv")
        classified = pd.read_csv(ROOT/f"data/historical/classified/sol_usdc_classified_{day}.csv")
        if historical.signature.duplicated().any() or classified.signature.duplicated().any():
            raise ValueError("Duplicata em histórico/classificação.")
        historical.timestamp = pd.to_datetime(historical.timestamp, utc=True)
        simple = classified.loc[classified.transaction_type.eq("SIMPLE_SWAP"), "signature"]
        if set(source.signature) != set(simple):
            raise ValueError("Consolidado não corresponde exatamente às classificações SIMPLE_SWAP.")
        original = historical.loc[historical.signature.isin(simple)].sort_values("signature").reset_index(drop=True)
        pd.testing.assert_frame_equal(source.sort_values("signature").reset_index(drop=True),
                                      original[source.columns], check_dtype=False, rtol=1e-12, atol=1e-12)
        rows.append({"day": day, "source_swaps": len(historical), "simple_swaps": len(source), "match": True})
    return pd.DataFrame(rows)


def audit_conservation(swaps, windows_by_label):
    rows = []
    for label, windows in windows_by_label.items():
        for day, source in swaps.groupby(swaps.timestamp.dt.strftime("%Y-%m-%d")):
            target = windows.loc[windows.timestamp.dt.strftime("%Y-%m-%d").eq(day)]
            counts_ok = len(source) == target.swap_count.sum()
            volume_error = target.volume_usdc.sum()-source.volume_usdc.sum()
            flow_error = target.net_flow_usdc.sum()-source.usdc_delta.sum()
            if not counts_ok or not np.isclose(volume_error, 0, atol=1e-6) or not np.isclose(flow_error, 0, atol=1e-6):
                raise ValueError(f"Conservação violada: {day} {label}")
            rows.append({"day": day, "frequency": label, "swap_count": len(source),
                         "volume_usdc": source.volume_usdc.sum(), "net_flow_usdc": source.usdc_delta.sum(),
                         "volume_error_usdc": volume_error, "flow_error_usdc": flow_error, "counts_ok": counts_ok})
    return pd.DataFrame(rows)


def audit_invariants(swaps, windows):
    liquidity.validate_swap_economics(swaps)
    counts = windows.swap_count
    if not windows.volatility.isna().equals(counts.lt(3)):
        raise ValueError("Volatilidade ausente incompatível com número de preços.")
    empty = counts.eq(0)
    if not windows.loc[empty, ["volume_usdc", "net_flow_usdc"]].eq(0).all().all():
        raise ValueError("Janela vazia deve ter fluxo e volume zero.")
    if not windows.loc[empty, ["price_open", "price_close", "volatility", "flow_imbalance"]].isna().all().all():
        raise ValueError("Janela vazia não pode ter preços/métricas artificiais.")
    positive = windows.volume_usdc.gt(0)
    if not np.allclose(windows.loc[positive, "flow_imbalance"],
                       windows.loc[positive, "net_flow_usdc"]/windows.loc[positive, "volume_usdc"], rtol=1e-12):
        raise ValueError("Flow imbalance inconsistente.")
    if windows.flow_imbalance.abs().gt(1 + 1e-12).any() or np.isinf(windows.select_dtypes("number")).any().any():
        raise ValueError("Invariante numérico violado.")


def main():
    protected = list((ROOT/"data").rglob("*")) + list((ROOT/"src/data/raw").rglob("*"))
    protected += [ROOT/"src"/name for name in ("data.py", "classify_history.py", "diagnostics.py", "clean_history.py", "risk.py", "backtest.py")]
    manifest = file_manifest(protected)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = ROOT/"data/processed/audit"/run_id
    out.mkdir(parents=True, exist_ok=False)
    (out/"baseline").mkdir()
    for name in BASELINE_NAMES:
        shutil.copy2(ROOT/"data/processed"/name, out/"baseline"/name)
    (out/"manifest_before.json").write_text(json.dumps(manifest, indent=2))
    swaps = liquidity.load_swaps([liquidity.INPUT_PATH])
    windows_by_label = {}
    for label, freq in liquidity.WINDOWS.items():
        original = pd.read_csv(out/"baseline"/f"liquidity_{label}.csv", parse_dates=["timestamp"])
        rebuilt = liquidity.build_liquidity_aggregations(swaps, freq)
        pd.testing.assert_frame_equal(original, rebuilt, check_dtype=False, rtol=1e-12, atol=1e-12)
        audit_invariants(swaps, rebuilt)
        windows_by_label[label] = rebuilt
    windows = windows_by_label["1m"]
    conservation = audit_conservation(swaps, windows_by_label)
    conservation.to_csv(out/"conservation_by_day.csv", index=False)
    audit_source_correspondence(swaps).to_csv(out/"source_correspondence.csv", index=False)
    window_sensitivity(swaps, windows).to_csv(out/"window_sensitivity_DIAGNOSTIC_ONLY.csv", index=False)
    extremes, raw, coverage = extreme_swaps(swaps, windows)
    extremes.to_csv(out/"extreme_swaps.csv", index=False)
    (out/"local_raw_evidence.json").write_text(json.dumps({"coverage": coverage, "matched_transactions": raw}, indent=2))
    requests = [{"signature": signature, "reason": "Reconcile pool vault balance deltas with swap CPI transfers, integer amounts, decimals, fees, auxiliary transfers and transaction order",
                 "request": {"jsonrpc": "2.0", "id": index+1, "method": "getTransaction",
                             "params": [signature, {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0}]}}
                for index, signature in enumerate(extremes.loc[extremes.usdc_one_unit_relative_resolution.ge(.1) & ~extremes.local_rpc_evidence, "signature"])]
    (out/"rpc_requests_NOT_EXECUTED.json").write_text(json.dumps(requests, indent=2))
    print(f"Auditoria local: {len(swaps)} swaps; {len(extremes)} swaps investigados; {len(requests)} consultas propostas, não executadas.", flush=True)
    causal_features = features.build_causal_features(windows)
    causal_features.to_csv(out/"liquidity_features_causal_1m.csv", index=False)
    detail = stress.build_stress(windows)
    original_stress = pd.read_csv(out/"baseline/exit_cost_stress_1m.csv", parse_dates=["timestamp"])
    pd.testing.assert_frame_equal(original_stress, detail[original_stress.columns], check_dtype=False, rtol=1e-12, atol=1e-12)
    detail.to_csv(out/"exit_cost_stress_audited_1m.csv", index=False)
    causal_stress = stress.build_causal_stress(windows, detail)
    causal_stress.to_csv(out/"exit_cost_stress_causal_1m.csv", index=False)
    stress.summarize_stress(detail).to_csv(out/"exit_cost_summary_audited.csv", index=False)
    statuses = detail.groupby(["position_size_usdc", "stress_scenario", "model_reliability_status"]).size().rename("count").reset_index()
    statuses.to_csv(out/"model_reliability_summary.csv", index=False)
    summary = {"run_id": run_id, "output_directory": str(out), "swap_count": len(swaps),
               "total_volume_usdc": swaps.volume_usdc.sum(), "net_flow_usdc": swaps.usdc_delta.sum(),
               "windows": {label: len(frame) for label, frame in windows_by_label.items()},
               "empty_minutes": int(windows.swap_count.eq(0).sum()),
               "one_price_minutes": int(windows.swap_count.eq(1).sum()),
               "two_price_minutes": int(windows.swap_count.eq(2).sum()),
               "missing_volatility_minutes": int(windows.volatility.isna().sum()),
               "costs_above_100_pct": int(detail.estimated_exit_cost_pct.gt(100).sum()),
               "participation_above_one": int(detail.participation_rate.gt(1).sum()),
               "max_exit_cost_pct": detail.estimated_exit_cost_pct.max(),
               "causal_valid_volatility_percentiles": int(causal_features.volatility_causal_percentile.notna().sum()),
               "first_causal_volatility_available_at": str(causal_features.loc[causal_features.volatility_causal_percentile.notna(), "available_at"].min()),
               "causal_valid_normal_exit_percentiles_per_position": causal_stress.loc[causal_stress.stress_scenario.eq("NORMAL")].groupby("position_size_usdc").exit_cost_causal_percentile.count().to_dict(),
               "model_reliability_counts": detail.model_reliability_status.value_counts().to_dict(),
               "protected_file_count": len(manifest), "local_raw_matches": len(raw), "proposed_rpc_requests": len(requests),
               "baseline_numeric_results_unchanged": True}
    # Append-only output version; no global paths are overwritten.
    verify_manifest(manifest)
    (out/"summary.json").write_text(json.dumps(summary, indent=2))
    (out/"manifest_code.json").write_text(json.dumps(file_manifest(ROOT/"src"/name for name in (
        "audit_pipeline.py", "liquidity.py", "features.py", "stress.py")), indent=2))
    (out/"manifest_outputs.json").write_text(json.dumps(file_manifest(out.rglob("*")), indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
