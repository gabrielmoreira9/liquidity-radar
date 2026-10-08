"""Local structural-cleaning tests. All files use isolated temporary folders."""
import contextlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src import clean_history as engine


def sample(day="2026-07-01"):
    swaps = pd.DataFrame({
        "timestamp": [f"{day}T00:00:0{second}+00:00" for second in (3, 2, 1, 0)],
        "signature": ["simple", "composite", "liquidity", "unknown"],
        "usdc_delta": [-10., -20., -30., -40.], "sol_delta": [1., 2., 3., 4.],
        "price": [10.] * 4, "volume_usdc": [10., 20., 30., 40.], "direction": ["SOL_IN"] * 4,
    })
    classified = pd.DataFrame({"signature": swaps.signature,
                               "transaction_type": ["SIMPLE_SWAP", "COMPOSITE_SWAP", "LIQUIDITY_OPERATION", "UNKNOWN"]})
    return swaps, classified


class CleanHistoryTests(unittest.TestCase):
    def test_excludes_every_non_simple_category_regardless_join_order(self):
        swaps, classified = sample()
        original = swaps.copy(deep=True)
        result = engine.clean_history(swaps, classified.iloc[::-1], "2026-07-01")
        self.assertEqual(result.signature.tolist(), ["simple"])
        self.assertEqual(result.columns.tolist(), engine.COLUMNS)
        pd.testing.assert_frame_equal(swaps, original)
        for category in ("COMPOSITE_SWAP", "LIQUIDITY_OPERATION", "UNKNOWN"):
            filtered = classified.copy()
            filtered["transaction_type"] = category
            self.assertTrue(engine.clean_history(swaps, filtered).empty)

    def test_no_arbitrary_price_or_volume_filters(self):
        swaps, classified = sample()
        classified["transaction_type"] = "SIMPLE_SWAP"
        swaps.loc[0, ["usdc_delta", "sol_delta", "price", "volume_usdc"]] = [-1e12, 1., 1e12, 1e12]
        swaps.loc[1, ["usdc_delta", "sol_delta", "price", "volume_usdc"]] = [-1e-12, 1., 1e-12, 1e-12]
        result = engine.clean_history(swaps, classified)
        self.assertEqual(len(result), 4)
        self.assertEqual(set(result.signature), set(swaps.signature))

    def test_duplicate_signatures_in_either_input_rejected(self):
        swaps, classified = sample()
        for left, right in ((pd.concat([swaps, swaps.iloc[:1]]), classified),
                            (swaps, pd.concat([classified, classified.iloc[:1]]))):
            with self.assertRaisesRegex(ValueError, "duplicadas"):
                engine.clean_history(left, right)

    def test_missing_and_extra_classifications_rejected(self):
        swaps, classified = sample()
        for labels in (classified.iloc[:-1], pd.concat([classified, pd.DataFrame({"signature": ["extra"], "transaction_type": ["SIMPLE_SWAP"]})])):
            with self.assertRaisesRegex(ValueError, "Correspondência"):
                engine.clean_history(swaps, labels)

    def test_invalid_categories_missing_columns_and_blank_signatures_rejected(self):
        swaps, classified = sample()
        bad_labels = classified.copy(); bad_labels.loc[0, "transaction_type"] = "OTHER"
        with self.assertRaises(ValueError): engine.clean_history(swaps, bad_labels)
        with self.assertRaises(ValueError): engine.clean_history(swaps.drop(columns="price"), classified)
        with self.assertRaises(ValueError): engine.clean_history(swaps, classified.drop(columns="transaction_type"))
        for value in (None, "", " ", " simple "):
            broken = swaps.copy(); broken.loc[0, "signature"] = value
            with self.assertRaises(ValueError): engine.clean_history(broken, classified)

    def test_timestamps_checked_even_on_excluded_rows(self):
        swaps, classified = sample()
        for value in (None, "invalid", "2026-07-01T00:00:00", "2026-07-02T00:00:00+00:00"):
            broken = swaps.copy(); broken.loc[3, "timestamp"] = value
            with self.assertRaises(ValueError): engine.clean_history(broken, classified, "2026-07-01")

    def test_numeric_integrity_and_price_identity_rejected_without_dropping_rows(self):
        swaps, classified = sample()
        for column, value in (("price", np.inf), ("volume_usdc", np.nan), ("volume_usdc", 123.),
                              ("price", -1.), ("price", 123.), ("direction", "OTHER")):
            broken = swaps.copy(); broken.loc[0, column] = value
            with self.assertRaises(ValueError): engine.clean_history(broken, classified)

    def test_timestamp_ties_allowed_with_stable_sort(self):
        swaps, classified = sample()
        swaps.loc[0, "timestamp"] = swaps.loc[1, "timestamp"]
        classified.loc[1, "transaction_type"] = "SIMPLE_SWAP"
        result = engine.clean_history(swaps, classified)
        self.assertEqual(result.signature.tolist(), ["simple", "composite"])

    def test_four_day_outputs_conservation_originals_and_idempotence(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            for name, directory in (("SWAPS_DIR", root/"swaps"), ("CLASSIFIED_DIR", root/"classified"), ("OUTPUT_DIR", root/"processed")):
                directory.mkdir()
                stack.enter_context(patch.object(engine, name, directory))
            days = ["2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"]
            for day in reversed(days):
                swaps, classified = sample(day)
                swaps["signature"] = day + "-" + swaps["signature"]
                classified["signature"] = swaps["signature"]
                swaps.to_csv(engine.SWAPS_DIR/f"sol_usdc_swaps_{day}.csv", index=False)
                classified.to_csv(engine.CLASSIFIED_DIR/f"sol_usdc_classified_{day}.csv", index=False)
            originals = {p: p.read_bytes() for directory in (engine.SWAPS_DIR, engine.CLASSIFIED_DIR) for p in directory.iterdir()}
            outputs, summary = engine.build_outputs()
            self.assertEqual(len(outputs), 5)
            self.assertEqual(summary.original.sum(), 16)
            self.assertEqual(summary.retained.sum(), 4)
            self.assertEqual(summary.excluded.sum(), 12)
            consolidated = outputs[-1][1]
            self.assertTrue(pd.to_datetime(consolidated.timestamp, utc=True).is_monotonic_increasing)
            self.assertEqual(set(consolidated.signature), {day+"-simple" for day in days})
            self.assertFalse(consolidated.signature.duplicated().any())
            engine.publish_outputs(outputs)
            existing = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in engine.OUTPUT_DIR.iterdir()}
            engine.publish_outputs(outputs)
            self.assertEqual(existing, {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in engine.OUTPUT_DIR.iterdir()})
            self.assertTrue(all(p.read_bytes() == content for p, content in originals.items()))

    def test_missing_daily_pair_blocks_before_publishing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); swaps_dir = root/"swaps"; swaps_dir.mkdir()
            swaps, _ = sample(); swaps.to_csv(swaps_dir/"sol_usdc_swaps_2026-07-01.csv", index=False)
            with patch.object(engine, "SWAPS_DIR", swaps_dir), patch.object(engine, "CLASSIFIED_DIR", root/"classified"):
                with self.assertRaisesRegex(ValueError, "sem classificação"):
                    engine.build_outputs()


if __name__ == "__main__":
    unittest.main()
