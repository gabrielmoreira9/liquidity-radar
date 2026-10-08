"""Local synthetic tests: no API calls and no writes to real datasets."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from src import features, liquidity, stress


class StatisticalPipelineTests(unittest.TestCase):
    def swaps(self):
        return pd.DataFrame({
            "timestamp": pd.to_datetime(["2026-06-30T00:01:00Z", "2026-06-30T00:01:00Z",
                                         "2026-06-30T00:01:30Z", "2026-07-03T23:58:00Z"], utc=True),
            "signature": ["a", "b", "c", "d"],
            "price": [100., 110., 99., 200.], "volume_usdc": [10., 20., 30., 40.],
            "usdc_delta": [10., -20., 30., -40.], "sol_delta": [-1., 1., -1., 1.],
            "direction": ["SOL_IN", "SOL_OUT", "SOL_IN", "SOL_OUT"],
        })

    def test_full_days_continuity_and_conservation(self):
        for freq, count in (("1min", 5760), ("5min", 1152), ("15min", 384)):
            with self.subTest(freq=freq):
                result = liquidity.build_liquidity_aggregations(self.swaps(), freq)
                self.assertEqual(len(result), count)
                self.assertEqual(result.swap_count.sum(), 4)
                self.assertAlmostEqual(result.volume_usdc.sum(), 100.)
                self.assertAlmostEqual(result.net_flow_usdc.sum(), -20.)
                self.assertFalse(result.timestamp.duplicated().any())
                self.assertTrue(result.timestamp.diff().dropna().eq(pd.Timedelta(freq)).all())

    def test_empty_windows_and_sample_volatility(self):
        result = liquidity.build_liquidity_aggregations(self.swaps(), "1min")
        empty = result.loc[result.swap_count.eq(0)]
        self.assertTrue(empty[["volume_usdc", "net_flow_usdc"]].eq(0).all().all())
        self.assertTrue(empty[["price_open", "price_close", "volatility", "flow_imbalance",
                               "avg_trade_size_usdc"]].isna().all().all())
        minute = result.iloc[1]
        self.assertEqual(minute.price_open, 100.)
        self.assertEqual(minute.price_close, 99.)
        self.assertAlmostEqual(minute.volatility, np.std([np.log(1.1), np.log(.9)], ddof=1))
        self.assertTrue(pd.isna(result.loc[result.swap_count.eq(1), "volatility"]).all())

    def test_duplicate_signature_rejected_without_filter(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "swaps.csv"
            pd.concat([self.swaps(), self.swaps().iloc[:1]]).to_csv(path, index=False)
            with self.assertRaises(ValueError):
                liquidity.load_swaps([path])

    def test_descriptive_formulas_missing_and_outliers_preserved(self):
        windows = liquidity.build_liquidity_aggregations(self.swaps(), "1min")
        output, summary = features.build_features(windows)
        self.assertEqual(len(output), len(windows))
        self.assertEqual(output.volume_usdc.max(), 60.)
        self.assertTrue(output.volume_usdc_robust_z.isna().all())  # zero MAD
        self.assertEqual(output.volatility_percentile.notna().sum(), 1)
        self.assertEqual(output.volatility_percentile.dropna().iloc[0], 100.)
        self.assertEqual(summary.set_index("metric").loc["volume_usdc", "median"], 0.)
        self.assertAlmostEqual(features.empirical_percentile(pd.Series([1., 1., 3., np.nan])).iloc[0], 200/3)

    def test_stress_formula_all_combinations_and_insufficient_data(self):
        windows = pd.DataFrame({"timestamp": pd.date_range("2026-07-01", periods=3, freq="min", tz="UTC"),
                                "volume_usdc": [100000., 0., 200000.],
                                "volatility": [.01, np.nan, np.nan],
                                "flow_imbalance": [.2, np.nan, .1]})
        result = stress.build_stress(windows)
        self.assertEqual(len(result), 72)
        self.assertEqual(set(result.position_size_usdc), set(stress.POSITION_SIZES))
        self.assertEqual(set(result.stress_scenario), set(stress.SCENARIOS))
        self.assertFalse(result.duplicated(["timestamp", "position_size_usdc", "stress_scenario"]).any())
        valid = result[result.timestamp.eq(windows.timestamp.iloc[0])]
        normal = valid[(valid.position_size_usdc == 100000) & (valid.stress_scenario == "NORMAL")].iloc[0]
        severe = valid[(valid.position_size_usdc == 100000) & (valid.stress_scenario == "STRESS_75")].iloc[0]
        self.assertAlmostEqual(normal.estimated_exit_cost_pct, 1.)
        self.assertAlmostEqual(severe.estimated_exit_cost_pct, 2.)
        self.assertTrue(result[result.timestamp.ne(windows.timestamp.iloc[0])].estimated_exit_cost_pct.isna().all())

    def test_discontinuous_feature_and_stress_inputs_rejected(self):
        windows = liquidity.build_liquidity_aggregations(self.swaps(), "1min").drop(index=1)
        for builder in (features.build_features, stress.build_stress):
            with self.assertRaises(ValueError):
                builder(windows)


if __name__ == "__main__":
    unittest.main()
