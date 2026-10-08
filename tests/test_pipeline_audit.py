"""Synthetic audits; no RPC, source mutation or network dependency."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src import audit_pipeline, features, liquidity, stress


class PipelineAuditTests(unittest.TestCase):
    def windows(self, n=125):
        volume = np.arange(1., n+1)*100000
        return pd.DataFrame({"timestamp": pd.date_range("2026-06-30", periods=n, freq="min", tz="UTC"),
                             "swap_count": 5, "volume_usdc": volume, "avg_trade_size_usdc": volume/5,
                             "volatility": np.arange(1., n+1)*.0001, "net_flow_usdc": volume/2,
                             "flow_imbalance": .5})

    def test_atomic_dust_and_extreme_prices_are_preserved(self):
        swaps = pd.DataFrame({"timestamp": pd.to_datetime(["2026-07-01T00:00:01Z", "2026-07-01T00:00:02Z"], utc=True),
                              "signature": ["min", "max"], "usdc_delta": [-.000002, .000002],
                              "sol_delta": [31e-9, -12e-9], "price": [2/31*1000, 2/12*1000],
                              "volume_usdc": [.000002, .000002], "direction": ["SOL_IN", "SOL_OUT"]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"source.csv"
            swaps.to_csv(path, index=False)
            before = path.read_bytes()
            loaded = liquidity.load_swaps([path])
            self.assertEqual(len(loaded), 2)
            self.assertAlmostEqual(loaded.price.max(), 166.6666666666667)
            self.assertEqual(path.read_bytes(), before)
        atomic = audit_pipeline.atomic_diagnostics(swaps.iloc[0])
        self.assertEqual(atomic["usdc_atomic_units"], 2)
        self.assertEqual(atomic["sol_atomic_units"], 31)
        self.assertEqual(atomic["usdc_one_unit_relative_resolution"], .5)

    def test_sign_and_price_identity_errors_rejected(self):
        swaps = pd.DataFrame({"usdc_delta": [1.], "sol_delta": [-.01], "volume_usdc": [1.],
                              "price": [100.], "direction": ["SOL_IN"]})
        with self.assertRaises(ValueError):
            liquidity.validate_swap_economics(swaps)
        swaps.direction = "SOL_OUT"
        swaps.price = 99.
        with self.assertRaises(ValueError):
            liquidity.validate_swap_economics(swaps)

    def test_extreme_cost_unchanged_and_flagged(self):
        windows = self.windows(1)
        windows.volume_usdc = 2.487823
        windows.volatility = .3662959281653745
        result = stress.build_stress(windows)
        row = result[(result.position_size_usdc == 1000000) & (result.stress_scenario == "NORMAL")].iloc[0]
        self.assertAlmostEqual(row.estimated_exit_cost_pct, 100*.3662959281653745*np.sqrt(1000000/2.487823))
        self.assertGreater(row.estimated_exit_cost_pct, 100)
        self.assertEqual(row.model_reliability_status, "EXTREME_EXTRAPOLATION")
        self.assertTrue(row.extrapolation_warning)
        self.assertTrue(row.insufficient_liquidity_flag)
        self.assertFalse(row.insufficient_data_flag)

    def test_zero_volume_and_missing_sigma_not_zero_cost(self):
        windows = self.windows(2)
        windows.loc[0, "volume_usdc"] = 0
        windows.loc[0, "volatility"] = np.nan
        windows.loc[1, "volatility"] = np.nan
        result = stress.build_stress(windows)
        self.assertTrue(result.estimated_exit_cost_pct.isna().all())
        self.assertTrue(result.insufficient_data_flag.all())
        self.assertTrue(result[result.timestamp.eq(windows.timestamp.iloc[0])].participation_rate.isna().all())

    def test_zero_sigma_does_not_hide_participation_extrapolation(self):
        windows = self.windows(1)
        windows.volume_usdc = 1e-12
        windows.volatility = 0.
        result = stress.build_stress(windows)
        self.assertTrue(result.estimated_exit_cost_pct.eq(0).all())
        self.assertTrue(result.extrapolation_warning.all())
        self.assertTrue(result.model_reliability_status.eq("EXTRAPOLATION").all())

    def test_non_extrapolated_is_still_an_uncalibrated_proxy(self):
        windows = self.windows(1)
        windows.volume_usdc = 1e9
        result = stress.build_stress(windows)
        self.assertTrue(result.model_reliability_status.eq("BASELINE_PROXY_ONLY").all())
        self.assertFalse(result.insufficient_liquidity_flag.any())

    def test_prior_reference_excludes_current_and_ties(self):
        result = features.expanding_statistics(pd.Series([1., 1., 3., 100.]), min_history=3)
        self.assertEqual(result.loc[3, "median"], 1.)
        self.assertEqual(result.loc[3, "p95"], 2.8)
        self.assertEqual(result.loc[3, "percentile"], 100.)
        self.assertTrue(pd.isna(result.loc[3, "robust_z"]))
        tied = features.expanding_statistics(pd.Series([1., 1., 3., 1.]), min_history=3)
        self.assertAlmostEqual(tied.loc[3, "percentile"], 200/3)

    def test_minimum_120_valid_preceding_minutes(self):
        windows = self.windows(125)
        windows.loc[0, "volatility"] = np.nan
        result = features.build_causal_features(windows)
        self.assertTrue(result.loc[:120, "volatility_causal_percentile"].isna().all())
        self.assertEqual(result.loc[121, "volatility_causal_history_count"], 120)
        self.assertTrue(pd.notna(result.loc[121, "volatility_causal_percentile"]))
        self.assertEqual(result.loc[120, "volume_usdc_causal_history_count"], 120)

    def test_appending_future_cannot_change_causal_features(self):
        windows = self.windows()
        short = features.build_causal_features(windows.iloc[:123])
        windows.loc[123:, "volume_usdc"] = 1e15
        windows.loc[123:, "volatility"] = 10.
        full = features.build_causal_features(windows)
        pd.testing.assert_frame_equal(short, full.iloc[:123])
        self.assertFalse(any("robust_z" in col and "causal" not in col for col in full.columns))

    def test_close_time_and_current_value_does_not_enter_threshold(self):
        windows = self.windows()
        before = features.build_causal_features(windows)
        windows.loc[120, "volume_usdc"] = 1e12
        after = features.build_causal_features(windows)
        self.assertEqual(before.loc[120, "volume_usdc_causal_p95"], after.loc[120, "volume_usdc_causal_p95"])
        self.assertTrue(after.available_at.eq(after.timestamp + pd.Timedelta(minutes=1)).all())
        self.assertTrue(after.historical_reference_before.eq(after.timestamp).all())

    def test_causal_exit_references_separate_position_and_scenario(self):
        windows = self.windows()
        result = stress.build_causal_stress(windows)
        normal = result[(result.position_size_usdc == 100000) & result.stress_scenario.eq("NORMAL")]
        self.assertEqual(normal.iloc[120].exit_cost_causal_history_count, 120)
        previous = stress.build_stress(windows.iloc[:120])
        expected = previous[(previous.position_size_usdc == 100000) & previous.stress_scenario.eq("NORMAL")].estimated_exit_cost_pct.quantile(.95)
        self.assertAlmostEqual(normal.iloc[120].exit_cost_causal_p95, expected)
        self.assertAlmostEqual(normal.iloc[120].median_volume_usdc_causal, 6050000.)
        self.assertNotIn("median_volume_usdc", result)
        self.assertNotIn("liquidity_multiple", result)

    def test_future_cannot_change_causal_stress(self):
        windows = self.windows()
        before = stress.build_causal_stress(windows.iloc[:122])
        windows.loc[122:, "volume_usdc"] = 1e15
        after = stress.build_causal_stress(windows)
        prefix = after[after.timestamp.lt(windows.timestamp.iloc[122])].reset_index(drop=True)
        pd.testing.assert_frame_equal(before, prefix)

    def test_manifest_detects_mutation_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"historical.csv"
            path.write_text("unchanged")
            manifest = audit_pipeline.file_manifest([path])
            audit_pipeline.verify_manifest(manifest)
            self.assertEqual(path.read_text(), "unchanged")
            path.write_text("changed")
            with self.assertRaises(ValueError):
                audit_pipeline.verify_manifest(manifest)

    def test_dust_diagnostic_does_not_modify_swaps(self):
        swaps = pd.DataFrame({"timestamp": pd.to_datetime(["2026-07-02T08:50:40Z", "2026-07-02T08:50:47Z",
                                                           "2026-07-02T08:50:48Z", "2026-07-02T08:50:53Z"], utc=True),
                              "signature": ["a", "b", "c", "d"], "price": [75., 120., 78.3, 78.38],
                              "volume_usdc": [.000003, .000003, .1, 9.4],
                              "usdc_delta": [-.000003, .000003, -.1, 9.4],
                              "direction": ["SOL_IN", "SOL_OUT", "SOL_IN", "SOL_OUT"]})
        before = swaps.copy(deep=True)
        windows = pd.DataFrame({"timestamp": [pd.Timestamp("2026-07-02T08:50:00Z")], "volatility": [.3]})
        result = audit_pipeline.window_sensitivity(swaps, windows)
        self.assertEqual(result.iloc[0].tiny_trade_count_DIAGNOSTIC_ONLY, 2)
        self.assertGreater(result.iloc[0].tiny_adjacent_return_energy_share_DIAGNOSTIC_ONLY, .99)
        pd.testing.assert_frame_equal(swaps, before)

    def test_source_csv_correspondence_no_reclassification(self):
        swaps = pd.DataFrame({"timestamp": [pd.Timestamp("2026-07-01T00:00:00Z")], "signature": ["a"],
                              "price": [100.], "usdc_delta": [1.], "sol_delta": [-.01],
                              "volume_usdc": [1.], "direction": ["SOL_OUT"]})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"data/historical/swaps").mkdir(parents=True)
            (root/"data/historical/classified").mkdir(parents=True)
            swaps.to_csv(root/"data/historical/swaps/sol_usdc_swaps_2026-07-01.csv", index=False)
            classification_path = root/"data/historical/classified/sol_usdc_classified_2026-07-01.csv"
            pd.DataFrame({"signature": ["a"], "transaction_type": ["SIMPLE_SWAP"]}).to_csv(classification_path, index=False)
            manifest = audit_pipeline.file_manifest(root.rglob("*"))
            with patch.object(audit_pipeline, "ROOT", root):
                self.assertTrue(audit_pipeline.audit_source_correspondence(swaps).match.all())
            audit_pipeline.verify_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
