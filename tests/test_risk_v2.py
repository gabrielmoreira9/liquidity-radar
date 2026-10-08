"""Local causal V2 tests. No external calls, no real dataset writes."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from src import features, risk_v2, stress


class RiskV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        count = 145
        i = np.arange(count)
        volume = (1+i % 10)*500000.
        cls.windows = pd.DataFrame({"timestamp": pd.date_range("2026-06-30", periods=count, freq="min", tz="UTC"),
                                   "swap_count": 5, "volume_usdc": volume, "avg_trade_size_usdc": volume/5,
                                   "volatility": .001*(1+i % 5), "net_flow_usdc": volume*np.sin(i)*.9,
                                   "flow_imbalance": np.sin(i)*.9})
        cls.windows.loc[5, ["volume_usdc", "swap_count", "net_flow_usdc"]] = 0
        cls.windows.loc[5, ["volatility", "flow_imbalance", "avg_trade_size_usdc"]] = np.nan
        cls.windows.loc[7, "volatility"] = np.nan
        cls.windows.loc[7, "swap_count"] = 2
        cls.causal_features = features.build_causal_features(cls.windows)
        cls.causal_exits = stress.build_causal_stress(cls.windows)
        cls.detail = risk_v2.build_risk(cls.causal_features, cls.causal_exits)

    def prefix(self, end):
        features_prefix = self.causal_features.iloc[:end].copy()
        exits_prefix = self.causal_exits[self.causal_exits.timestamp.isin(features_prefix.timestamp)].copy()
        return features_prefix, exits_prefix

    def test_minimum_120_valid_each_component_and_no_partial_mean(self):
        detail = self.detail.drop_duplicates("timestamp").reset_index(drop=True)
        self.assertTrue(detail.loc[:121, "current_market_risk_score"].isna().all())
        self.assertTrue(pd.notna(detail.loc[122, "current_market_risk_score"]))
        self.assertEqual(detail.loc[122, "volatility_history_count"], 120)
        self.assertTrue(pd.notna(detail.loc[121, "forward_liquidity_risk_score"]))
        self.assertTrue(detail.loc[:120, "forward_liquidity_risk_score"].isna().all())
        self.assertIn("VOLATILITY_HISTORY_LT_120", detail.loc[121, "current_market_risk_unavailable_reason"])

    def test_market_and_forward_independent_of_position_and_scenario(self):
        for col in ["current_market_risk_score", "current_market_risk_regime", "forward_liquidity_risk_score", "forward_liquidity_risk_regime"]:
            self.assertTrue(self.detail.groupby("timestamp")[col].nunique(dropna=False).eq(1).all())
        self.assertTrue(self.detail.market_reference_position_usdc.eq(100000).all())
        self.assertTrue(self.detail.market_reference_scenario.eq("NORMAL").all())

    def test_equal_means_and_volume_inversion(self):
        row = self.detail[self.detail.current_market_risk_score.notna()].iloc[0]
        self.assertAlmostEqual(row.current_market_risk_score, (row.volatility_risk+row.liquidity_risk+row.flow_risk+row.reference_exit_cost_risk)/4)
        self.assertAlmostEqual(row.forward_liquidity_risk_score, (row.liquidity_risk+row.flow_risk)/2)
        f = self.causal_features[self.causal_features.timestamp.eq(row.timestamp)].iloc[0]
        self.assertAlmostEqual(row.liquidity_risk, 100-f.volume_usdc_causal_percentile)
        self.assertAlmostEqual(row.flow_risk, f.abs_flow_imbalance_causal_percentile)

    def test_operational_thresholds_inclusive_and_greatest_severity(self):
        for participation, cost, expected in [(.49, .49, "LOW"), (.5, 0, "MODERATE"), (0, .5, "MODERATE"),
                                               (1, 0, "HIGH"), (0, 1, "HIGH"), (2, 0, "EXTREME"),
                                               (0, 2, "EXTREME"), (.1, 150, "EXTREME"), (np.nan, 0, "INDETERMINATE"),
                                               (0, np.nan, "INDETERMINATE")]:
            with self.subTest(participation=participation, cost=cost):
                self.assertEqual(risk_v2.operational_risk(participation, cost), expected)

    def test_regime_boundaries(self):
        for score, expected in [(39.999, "NORMAL"), (40, "WATCH"), (60, "STRESSED"), (80, "CRITICAL"), (np.nan, "INDETERMINATE")]:
            self.assertEqual(risk_v2.risk_regime(score), expected)

    def test_empty_and_insufficient_data_never_low(self):
        for index in (5, 7):
            rows = self.detail[self.detail.timestamp.eq(self.windows.timestamp.iloc[index])]
            self.assertTrue(rows.position_exit_risk.eq("INDETERMINATE").all())
            self.assertTrue(rows.estimated_exit_cost_pct.isna().all())
            self.assertTrue(rows.position_exit_risk_unavailable_reason.str.contains("VOLATILITY_UNAVAILABLE").all())
        # Operational measures do not require a historical percentile or median.
        first = self.detail[self.detail.timestamp.eq(self.windows.timestamp.iloc[0])]
        self.assertTrue(first.current_market_risk_score.isna().all())
        self.assertTrue(first.position_exit_risk.ne("INDETERMINATE").all())
        self.assertTrue(first.liquidity_multiple_causal.isna().all())

    def test_monotonic_position_risk(self):
        order = {label: i for i, label in enumerate(risk_v2.EXIT_CLASSES)}
        valid = self.detail[self.detail.position_exit_risk.ne("INDETERMINATE")].copy()
        valid["rank"] = valid.position_exit_risk.map(order)
        diffs = valid.sort_values("position_size_usdc").groupby(["timestamp", "stress_scenario"])["rank"].diff()
        self.assertTrue(diffs.dropna().ge(0).all())

    def test_future_appended_does_not_change_prefix(self):
        f, e = self.prefix(130)
        before = risk_v2.build_risk(f, e)
        pd.testing.assert_frame_equal(before, self.detail[self.detail.timestamp.isin(f.timestamp)].reset_index(drop=True))

    def test_current_extreme_and_future_change_cannot_affect_past_or_forward(self):
        changed = self.windows.copy()
        changed.loc[130:, "volatility"] = 10.
        f = features.build_causal_features(changed)
        e = stress.build_causal_stress(changed)
        result = risk_v2.build_risk(f, e)
        pd.testing.assert_frame_equal(self.detail[self.detail.timestamp.lt(changed.timestamp.iloc[130])].reset_index(drop=True),
                                      result[result.timestamp.lt(changed.timestamp.iloc[130])].reset_index(drop=True))
        self.assertTrue(result.forward_liquidity_risk_score.equals(self.detail.forward_liquidity_risk_score))
        # No clipping, including large costs and extrapolation notes.
        self.assertGreater(result.estimated_exit_cost_pct.max(), 100)
        self.assertTrue(result[result.estimated_exit_cost_pct.gt(100)].extrapolation_warning.all())

    def test_audited_flags_and_numeric_costs_propagated_exactly(self):
        cols = ["timestamp", "position_size_usdc", "stress_scenario", "estimated_exit_cost_pct", "participation_rate", "liquidity_multiple_causal",
                *risk_v2.FLAGS, "model_reliability_status", "extrapolation_reason"]
        pd.testing.assert_frame_equal(self.detail[cols], self.causal_exits[cols], check_dtype=False)

    def test_forged_global_percentile_and_counts_rejected(self):
        f, e = self.prefix(124)
        for column, value in [("volume_usdc_causal_percentile", 99.), ("volume_usdc_causal_history_count", 9999.)]:
            forged = f.copy()
            forged.loc[122, column] = value
            with self.assertRaises(ValueError):
                risk_v2.build_risk(forged, e)

    def test_no_future_availability_or_invalid_history_policy(self):
        f, e = self.prefix(1)
        for column, value in [("available_at", f.timestamp.iloc[0]), ("historical_reference_before", f.timestamp.iloc[0]+pd.Timedelta(minutes=1)),
                               ("minimum_history_observations", 119)]:
            bad = f.copy()
            bad.loc[0, column] = value
            with self.assertRaises(ValueError):
                risk_v2.build_risk(bad, e)

    def test_duplicate_and_nonchronological_rows_rejected(self):
        f, e = self.prefix(3)
        for bad in (pd.concat([f, f.iloc[:1]], ignore_index=True), f.iloc[::-1]):
            with self.assertRaises(ValueError):
                risk_v2.build_risk(bad, e)
        with self.assertRaises(ValueError):
            risk_v2.build_risk(f, e.iloc[:-1])

    def test_summary_market_counts_are_not_multiplied_by_positions(self):
        summary = risk_v2.summarize_risk(self.detail)
        self.assertEqual(len(summary), 26)
        self.assertTrue(summary.iloc[:2].observation_count.eq(len(self.windows)).all())
        self.assertTrue(summary.observation_count.eq(summary.valid_count+summary.indeterminate_count).all())
        self.assertTrue(self.detail.groupby("timestamp").size().eq(24).all())
        self.assertEqual(self.detail.drop_duplicates("timestamp").swap_count.sum(), self.windows.swap_count.sum())
        self.assertAlmostEqual(self.detail.drop_duplicates("timestamp").volume_usdc.sum(), self.windows.volume_usdc.sum())
        self.assertAlmostEqual(self.detail.drop_duplicates("timestamp").net_flow_usdc.sum(), self.windows.net_flow_usdc.sum())

    def test_inconsistent_flow_rejected(self):
        f, e = self.prefix(1)
        f.loc[0, "net_flow_usdc"] = 10000.
        with self.assertRaises(ValueError):
            risk_v2.build_risk(f, e)

    def test_streamed_publication_preserves_inputs_and_records_provenance(self):
        f, e = self.prefix(3)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root/"audit"
            audit.mkdir()
            f.to_csv(audit/"liquidity_features_causal_1m.csv", index=False)
            e.to_csv(audit/"exit_cost_stress_causal_1m.csv", index=False)
            before = {str(path): {"sha256": risk_v2.sha256(path)} for path in audit.glob("*.csv")}
            (audit/"manifest_outputs.json").write_text(json.dumps(before))
            detail, summary, metadata = risk_v2.run(audit, root/"output")
            self.assertEqual(len(detail), 72)
            self.assertEqual(len(summary), 26)
            for name, expected in before.items():
                self.assertEqual(risk_v2.sha256(Path(name)), expected["sha256"])
            self.assertEqual(metadata["market_reference_position_usdc"], 100000)
            self.assertTrue((root/"output/liquidity_risk_v2_1m.csv").is_file())
            self.assertEqual(len(list((root/"output/risk_v2_runs").glob("*/metadata.json"))), 1)


if __name__ == "__main__":
    unittest.main()
