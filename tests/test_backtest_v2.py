"""Synthetic holdout/backtest integrity tests; no network and no real writes."""
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from src import backtest_v2 as bt, risk_v2


class BacktestV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        n = 380
        i = np.arange(n)
        cls.frame = pd.DataFrame({"timestamp": pd.date_range("2026-06-30", periods=n, freq="min", tz="UTC"),
                                 "volume_usdc": 1000.+(i % 5)*100,
                                 "volatility": .001+(i % 6)*.0004})
        cls.frame["estimated_exit_cost_pct"] = 100*cls.frame.volatility*np.sqrt(100000/cls.frame.volume_usdc)
        cls.frame["available_at"] = cls.frame.timestamp + pd.Timedelta(minutes=1)
        for offset, column in enumerate(risk_v2.MARKET_COMPONENTS):
            cls.frame[column] = (i*(7+2*offset)) % 100
            cls.frame.loc[:119, column] = np.nan
        cls.frame["current_market_risk_score"] = cls.frame[list(risk_v2.MARKET_COMPONENTS)].mean(axis=1, skipna=False)
        cls.frame["forward_liquidity_risk_score"] = cls.frame[["liquidity_risk", "flow_risk"]].mean(axis=1, skipna=False)
        for score in bt.MODEL_SCORES.values():
            cls.frame[score.replace("_score", "_regime")] = cls.frame[score].map(risk_v2.risk_regime)
        cls.start = cls.frame.timestamp.iloc[0]
        cls.cutoff = cls.frame.timestamp.iloc[300]
        cls.end = cls.frame.available_at.iloc[-1]
        cls.results = bt.build_backtest(cls.frame, cls.start, cls.cutoff, cls.end)

    def toy(self, n=8):
        frame = pd.DataFrame({"timestamp": pd.date_range("2026-07-03", periods=n, freq="min", tz="UTC"),
                              "volume_usdc": 2000., "volatility": .001, "estimated_exit_cost_pct": .1})
        frame["available_at"] = frame.timestamp+pd.Timedelta(minutes=1)
        return frame

    def reference(self):
        return {"thresholds": {"volume_p05": 1000., "volatility_p95": .01, "exit_cost_p95": .5}}

    def test_event_thresholds_and_quartiles_cannot_see_test(self):
        changed = self.frame.copy()
        changed.loc[300:, "volume_usdc"] = 1e15
        changed.loc[300:, "volatility"] = 5.
        changed.loc[300:, "estimated_exit_cost_pct"] = 10000.
        changed.loc[300:, list(bt.MODEL_SCORES.values())] = 100.
        other = bt.build_backtest(changed, self.start, self.cutoff, self.end)[-1]
        reference = self.results[-1]
        self.assertEqual(reference, other)
        self.assertEqual(reference["thresholds"]["volume_p05"], self.frame.iloc[:300].volume_usdc.quantile(.05))

    def test_fit_rejects_future_training_information(self):
        with self.assertRaises(ValueError):
            bt.fit_reference(self.frame.iloc[:301], self.cutoff)
        bad = self.frame.iloc[:300].copy()
        bad.loc[0, "available_at"] = self.cutoff+pd.Timedelta(minutes=1)
        with self.assertRaises(ValueError):
            bt.fit_reference(bad, self.cutoff)

    def test_current_decision_bar_does_not_enter_future_event(self):
        frame = self.toy()
        frame.loc[0, ["volume_usdc", "volatility", "estimated_exit_cost_pct"]] = [0, 100, 10000]
        events = bt.future_events(frame, self.reference(), (5,))
        self.assertTrue(events.loc[0, list(bt.EVENTS)].eq(0).all())
        frame.loc[1, "volume_usdc"] = 0
        events = bt.future_events(frame, self.reference(), (5,))
        self.assertEqual(events.loc[0, "LOW_VOLUME_EVENT"], 1.)
        self.assertEqual(events.loc[0, "LOW_VOLUME_EVENT_first_hit_at"], frame.available_at.iloc[1])
        self.assertGreater(events.loc[0, "LOW_VOLUME_EVENT_first_hit_at"], events.loc[0, "decision_at"])

    def test_missing_future_is_unknown_not_no_event(self):
        frame = self.toy()
        frame.loc[1, "volatility"] = np.nan
        events = bt.future_events(frame, self.reference(), (2,))
        self.assertTrue(pd.isna(events.loc[0, "HIGH_VOLATILITY_EVENT"]))
        self.assertTrue(pd.isna(events.loc[0, "ANY_DETERIORATION"]))
        self.assertEqual(events.loc[0, "HIGH_VOLATILITY_EVENT_valid_future_count"], 1)
        frame.loc[2, "volatility"] = .02
        events = bt.future_events(frame, self.reference(), (2,))
        self.assertEqual(events.loc[0, "HIGH_VOLATILITY_EVENT"], 1.)
        self.assertFalse(events.loc[0, "HIGH_VOLATILITY_EVENT_complete_data"])
        frame.loc[2, "volatility"] = .001
        frame.loc[2, "volume_usdc"] = 0
        events = bt.future_events(frame, self.reference(), (2,))
        self.assertEqual(events.loc[0, "ANY_DETERIORATION"], 1.)

    def test_incomplete_horizon_always_unknown_even_with_partial_hit(self):
        frame = self.toy(3)
        frame.loc[1, "volume_usdc"] = 0
        events = bt.future_events(frame, self.reference(), (5,))
        self.assertTrue(events[list(bt.EVENTS)].isna().all().all())
        self.assertTrue(events.horizon_unavailable_reason.eq("INCOMPLETE_FUTURE_HORIZON").all())

    def test_event_boundaries_are_strict(self):
        frame = self.toy()
        frame[["volume_usdc", "volatility", "estimated_exit_cost_pct"]] = [1000., .01, .5]
        events = bt.future_events(frame, self.reference(), (2,))
        self.assertTrue(events.loc[0, list(bt.EVENTS)].eq(0).all())

    def test_baselines_use_only_past_and_known_current(self):
        reference = self.results[-1]
        short = bt.add_baseline_inputs(self.frame.iloc[:310], reference)
        changed = self.frame.copy()
        changed.loc[310:, "volume_usdc"] = 1e12
        full = bt.add_baseline_inputs(changed, reference)
        columns = [column for column in full if column.startswith("baseline_")]
        pd.testing.assert_frame_equal(short[columns], full.iloc[:310][columns])
        self.assertAlmostEqual(full.loc[300, "baseline_prior_5m_mean_volume"], self.frame.volume_usdc.iloc[295:300].mean())
        conditions = bt.current_conditions(self.frame, reference["thresholds"])
        self.assertEqual(full.loc[300, "baseline_previous_ANY_DETERIORATION"], conditions.loc[299, "ANY_DETERIORATION"])

    def test_training_prevalence_does_not_cross_into_test(self):
        train = self.frame.iloc[:300]
        reference = self.results[-1]
        prevalences = bt.training_prevalences(train, reference)
        for horizon in bt.HORIZONS:
            self.assertEqual(prevalences[(horizon, "LOW_VOLUME_EVENT")]["valid_count"], 300-horizon)
            self.assertEqual(prevalences[(horizon, "LOW_VOLUME_EVENT")]["excluded_count"], horizon)

    def test_auc_handles_constant_scores_ties_and_one_class(self):
        roc, ap, _ = bt.auc_metrics([0, 1, 0, 1], [.1, .9, .2, .8])
        self.assertEqual(roc, 1.)
        self.assertEqual(ap, 1.)
        roc, ap, _ = bt.auc_metrics([0, 1, 0, 1], [1., 1., 1., 1.])
        self.assertEqual(roc, .5)
        self.assertEqual(ap, .5)
        roc, ap, reason = bt.auc_metrics([1, 1], [.1, .9])
        self.assertTrue(np.isnan(roc) and np.isnan(ap))
        self.assertEqual(reason, "SINGLE_CLASS_OR_EMPTY")

    def test_lead_time_deduplicates_first_breach_minute(self):
        decision = pd.date_range("2026-07-03", periods=2, freq="min", tz="UTC")
        part = pd.DataFrame({"decision_at": decision, "horizon_complete": True,
                             "non_overlapping_grid": [True, False], "LOW_VOLUME_EVENT": [1., 1.],
                             "LOW_VOLUME_EVENT_first_hit_at": pd.Timestamp("2026-07-03T00:04:00Z")})
        metrics = bt.evaluate_metrics(part, "LOW_VOLUME_EVENT", np.array([90., 90.]), np.ones(2), np.ones(2, dtype=bool), "MATCHED")
        self.assertEqual(metrics["lead_time_median_minutes"], 4.)
        self.assertEqual(metrics["alerted_first_breach_minutes"], 1)
        self.assertEqual(metrics["tp"], 2)

    def test_common_samples_and_confusions_conserve_evaluations(self):
        events, summary, baselines, _, _ = self.results
        metrics = pd.concat([summary, baselines])
        self.assertTrue(metrics.n_valid.eq(metrics.tp+metrics.fp+metrics.tn+metrics.fn).all())
        self.assertTrue(metrics.n_candidates.eq(metrics.n_valid+metrics.n_excluded).all())
        matched = metrics[metrics["sample"].eq("MATCHED")]
        self.assertTrue(matched.groupby(["horizon_minutes", "event"]).n_valid.nunique().eq(1).all())
        self.assertEqual(len(events), 80*4)
        self.assertFalse(events.duplicated(["timestamp", "horizon_minutes"]).any())

    def test_non_overlapping_grid_is_fixed_and_disjoint(self):
        events = self.results[0]
        for horizon, part in events.groupby("horizon_minutes"):
            grid = part[part.non_overlapping_grid & part.horizon_complete]
            if len(grid) > 1:
                self.assertTrue(grid.future_start_at.iloc[1:].reset_index(drop=True).ge(grid.future_end_at.iloc[:-1].reset_index(drop=True)).all())

    def test_no_negative_event_labels_from_nan_or_no_alert_precision(self):
        part = pd.DataFrame({"decision_at": pd.date_range("2026-07-03", periods=3, freq="min", tz="UTC"),
                             "horizon_complete": [True, True, False], "non_overlapping_grid": True,
                             "LOW_VOLUME_EVENT": [0., np.nan, np.nan], "LOW_VOLUME_EVENT_first_hit_at": pd.NaT})
        result = bt.evaluate_metrics(part, "LOW_VOLUME_EVENT", np.ones(3), np.zeros(3), np.ones(3, dtype=bool), "MATCHED")
        self.assertEqual(result["n_valid"], 1)
        self.assertEqual(result["tn"], 1)
        self.assertEqual(result["n_excluded_unknown_label"], 1)
        self.assertEqual(result["n_excluded_incomplete_horizon"], 1)
        self.assertTrue(pd.isna(result["precision"]))

    def test_original_frame_is_preserved_and_bad_dates_rejected(self):
        before = self.frame.copy(deep=True)
        bt.build_backtest(self.frame, self.start, self.cutoff, self.end)
        pd.testing.assert_frame_equal(self.frame, before)
        bad = self.frame.copy()
        bad.loc[302, "available_at"] -= pd.Timedelta(minutes=1)
        with self.assertRaises(ValueError):
            bt.build_backtest(bad, self.start, self.cutoff, self.end)

    def test_existing_outputs_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/bt.OUTPUTS[0]
            path.write_text("original")
            with self.assertRaises(FileExistsError):
                bt.run(output_dir=Path(directory))
            self.assertEqual(path.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
