"""Offline contracts against the versionable, real processed-data demo."""
import copy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd

from src.api import create_app
from src import api_contract as c
from src.api_data import DatasetError, DatasetStore, ROOT, Settings, finite

ENDPOINTS = {
    "/api/health": c.Health, "/api/overview": c.Overview, "/api/market-risk": c.MarketRisk,
    "/api/forward-risk": c.ForwardRisk, "/api/positions": c.Positions,
    "/api/exit-cost?position_size=100000&scenario=NORMAL": c.PositionExit,
    "/api/liquidity/history?interval=1m": c.History, "/api/backtest/summary": c.Backtest,
}


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = Settings(mode="demo")
        cls.store = DatasetStore.load(cls.settings)
        cls.client = TestClient(create_app(cls.settings, cls.store))
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["status"], "OK")
        self.assertTrue(data["datasets_ready"])
        self.assertFalse(data["helius_required"])

    def test_all_endpoint_contracts(self):
        for url, contract in ENDPOINTS.items():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                c.Envelope[contract].model_validate(response.json())
                self.assertEqual(response.json()["meta"]["data_kind"], "HISTORICAL")

    def test_dates_are_iso_utc_and_all_numbers_finite(self):
        date_keys = {"timestamp", "available_at", "reference_at", "cache_loaded_at", "window_start", "window_end_exclusive",
                     "available_from", "available_until", "train_start", "test_start", "test_end_exclusive", "result_available_at"}
        def inspect(value, key=None):
            if isinstance(value, dict):
                for k, v in value.items(): inspect(v, k)
            elif isinstance(value, list):
                for v in value: inspect(v)
            elif isinstance(value, float):
                self.assertTrue(math.isfinite(value))
            elif key in date_keys and value is not None:
                self.assertTrue(value.endswith("Z"), (key, value))
                datetime.fromisoformat(value.replace("Z", "+00:00"))
        for url in ENDPOINTS: inspect(self.client.get(url).json())

    def test_invalid_parameters_are_422_and_do_not_reflect_inputs(self):
        cases = ["/api/exit-cost?position_size=1", "/api/exit-cost?position_size=NaN", "/api/exit-cost?scenario=LIVE",
            "/api/liquidity/history?interval=30s", "/api/liquidity/history?limit=0", "/api/liquidity/history?limit=2001",
            "/api/liquidity/history?offset=-1", "/api/liquidity/history?start=2026-07-04T00:00:00Z&end=2026-07-03T00:00:00Z",
            "/api/market-risk?as_of=2026-07-03T22:00:00", "/api/market-risk?as_of=PRIVATE_SECRET_EXAMPLE",
            "/api/backtest/summary?horizon=7", "/api/backtest/summary?event=BAD", "/api/backtest/summary?sample=BAD",
            "/api/backtest/summary?as_of=2026-07-03T23:00:00Z", "/api/health?token=PRIVATE_SECRET_EXAMPLE",
            "/api/exit-cost?scenario=NORMAL&scenario=STRESS_75"]
        for url in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 422)
                c.ErrorResponse.model_validate(response.json())
                self.assertNotIn("PRIVATE_SECRET_EXAMPLE", response.text)

    def test_before_first_close_has_no_data(self):
        for path in ("/api/market-risk", "/api/forward-risk", "/api/overview", "/api/positions", "/api/exit-cost", "/api/liquidity/history"):
            response = self.client.get(path, params={"as_of": "2026-07-03T22:00:00Z"})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "NO_DATA")

    def test_as_of_does_not_expose_later_scores_or_positions(self):
        instant = pd.Timestamp("2026-07-03T22:30:30Z")
        source = self.store.decision(instant)
        for path in ("/api/market-risk", "/api/forward-risk", "/api/exit-cost", "/api/positions"):
            payload = self.client.get(path, params={"as_of": str(instant)}).json()
            self.assertEqual(pd.Timestamp(payload["meta"]["reference_at"]), source.available_at)
            self.assertLessEqual(pd.Timestamp(payload["meta"]["reference_at"]), instant)
            if path == "/api/market-risk":
                self.assertEqual(payload["data"]["score_0_100"], source.current_market_risk_score)
            elif path == "/api/forward-risk":
                self.assertEqual(payload["data"]["score_0_100"], source.forward_liquidity_risk_score)
            else:
                items = payload["data"]["items"] if path == "/api/positions" else [payload["data"]]
                for item in items: self.assertLessEqual(pd.Timestamp(item["available_at"]), instant)

    def test_overview_totals_only_closed_prefix(self):
        response = self.client.get("/api/overview", params={"as_of": "2026-07-03T22:30:00Z"}).json()["data"]
        prefix = self.store.liquidity["1m"].iloc[:30]
        self.assertEqual(response["closed_window_count_1m"], 30)
        self.assertEqual(response["swap_count"], prefix.swap_count.sum())
        self.assertEqual(response["volume_usdc"], prefix.volume_usdc.sum())
        self.assertEqual(response["net_flow_usdc"], prefix.net_flow_usdc.sum())

    def test_history_intervals_are_chronological_with_closed_bars_only(self):
        instant = pd.Timestamp("2026-07-03T22:31:00Z")
        for interval in ("1m", "5m", "15m"):
            payload = self.client.get("/api/liquidity/history", params={"interval": interval, "as_of": str(instant)}).json()["data"]
            times = [pd.Timestamp(item["timestamp"]) for item in payload["items"]]
            self.assertEqual(times, sorted(set(times)))
            for item in payload["items"]: self.assertLessEqual(pd.Timestamp(item["available_at"]), instant)
            self.assertEqual(payload["pagination"]["total"], {"1m": 31, "5m": 6, "15m": 2}[interval])

    def test_history_filters_and_pagination(self):
        args = {"start": "2026-07-03T22:10:00Z", "end": "2026-07-03T22:20:00Z", "limit": 4}
        first = self.client.get("/api/liquidity/history", params=args).json()["data"]
        second = self.client.get("/api/liquidity/history", params={**args, "offset": 4}).json()["data"]
        self.assertEqual(first["pagination"]["total"], 10)
        self.assertEqual(first["pagination"]["next_offset"], 4)
        self.assertLess(first["items"][-1]["timestamp"], second["items"][0]["timestamp"])
        empty = self.client.get("/api/liquidity/history?offset=1000000").json()["data"]
        self.assertEqual(empty["items"], [])
        self.assertIsNone(empty["pagination"]["next_offset"])

    def test_history_risk_at_close_is_original_1m_snapshot_not_aggregation(self):
        for interval in ("1m", "5m", "15m"):
            rows = self.client.get("/api/liquidity/history", params={"interval": interval, "limit": 2}).json()["data"]["items"]
            for item in rows:
                score = item["risk_at_close"]
                original = self.store.decision(pd.Timestamp(item["available_at"]))
                self.assertEqual(score["available_at"], item["available_at"])
                self.assertEqual(score["current_market_risk_score_0_100"], original.current_market_risk_score)
                self.assertEqual(score["forward_liquidity_risk_score_0_100"], original.forward_liquidity_risk_score)

    def test_nan_cost_is_null_and_indeterminate_never_low(self):
        row = self.store.market.loc[self.store.market.estimated_exit_cost_pct.isna()].iloc[0]
        payload = self.client.get("/api/exit-cost", params={"as_of": str(row.available_at)}).json()["data"]
        self.assertIsNone(payload["estimated_exit_cost_pct"])
        self.assertEqual(payload["operational_risk"], "INDETERMINATE")
        self.assertTrue(payload["insufficient_data_flag"])
        self.assertIsNotNone(payload["unavailable_reason"])

    def test_non_finite_serialization_is_null(self):
        for value in (np.nan, np.inf, -np.inf, None): self.assertIsNone(finite(value))
        source = self.store.liquidity["1m"].iloc[0].copy()
        source["volatility"] = np.inf
        source["price_return_pct"] = -np.inf
        result = self.store.liquidity_window(source).model_dump(mode="json")
        self.assertIsNone(result["volatility_std_log_return"])
        self.assertIsNone(result["price_return_pct"])
        json.dumps(result, allow_nan=False)

    def test_reliability_flags_and_costs_propagate_all_positions_scenarios(self):
        for scenario in ("NORMAL", "STRESS_25", "STRESS_50", "STRESS_75"):
            payload = self.client.get("/api/positions", params={"scenario": scenario}).json()["data"]
            self.assertEqual(payload["position_sizes_usdc"], [10000, 50000, 100000, 250000, 500000, 1000000])
            previous = -1
            ranks = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "EXTREME": 3}
            for item in payload["items"]:
                source = self.store.risk.loc[self.store.risk.timestamp.eq(self.store.market.timestamp.iloc[-1]) &
                    self.store.risk.stress_scenario.eq(scenario) & self.store.risk.position_size_usdc.eq(item["position_size_usdc"])].iloc[0]
                self.assertEqual(item["estimated_exit_cost_pct"], source.estimated_exit_cost_pct)
                for flag in ("extrapolation_warning", "insufficient_data_flag", "insufficient_liquidity_flag"):
                    self.assertEqual(item[flag], bool(source[flag]))
                self.assertEqual(item["model_reliability_status"], source.model_reliability_status)
                self.assertFalse(item["is_observed_slippage"])
                rank = ranks[item["operational_risk"]]
                self.assertGreaterEqual(rank, previous)
                previous = rank

    def test_scores_and_regimes_preserve_original_components(self):
        current = self.client.get("/api/market-risk").json()["data"]
        forward = self.client.get("/api/forward-risk").json()["data"]
        self.assertAlmostEqual(current["score_0_100"], sum(current["components"].values())/4)
        self.assertAlmostEqual(forward["score_0_100"], sum(forward["components"].values())/2)
        for item in (current, forward):
            expected = ("NORMAL", "WATCH", "STRESSED", "CRITICAL")[sum(item["score_0_100"] >= x for x in (40,60,80))]
            self.assertEqual(item["regime"], expected)
        self.assertFalse(forward["is_calibrated_probability"])
        self.assertEqual(current["reference_position_size_usdc"], 100000)
        self.assertNotIn("estimated_exit_cost_pct", forward)
        self.assertNotIn("volatility_risk_0_100", forward["components"])

    def test_backtest_filtering_null_auc_and_retrospective_availability(self):
        payload = self.client.get("/api/backtest/summary", params={"sample": "NON_OVERLAPPING_MATCHED", "horizon": 60,
            "predictor": "CURRENT_MARKET_RISK"}).json()["data"]
        self.assertEqual(len(payload["items"]), 1)
        self.assertIsNone(payload["items"][0]["roc_auc"])
        self.assertEqual(payload["items"][0]["auc_unavailable_reason"], "SINGLE_CLASS_OR_EMPTY")
        self.assertGreater(pd.Timestamp(payload["result_available_at"]), pd.Timestamp(payload["test_end_exclusive"]))
        self.assertEqual(payload["analysis_scope"], "RETROSPECTIVE_HOLDOUT")
        default = self.client.get("/api/backtest/summary").json()["data"]["items"]
        self.assertEqual(len(default), 24)
        for row in default: self.assertEqual(row["n_valid"], row["tp"]+row["fp"]+row["tn"]+row["fn"])

    def test_api_never_calls_helius_or_exposes_keys_paths_or_raw_data(self):
        with mock.patch("requests.Session.request", side_effect=AssertionError("Network forbidden")) as network, \
             mock.patch.dict(os.environ, {"HELIUS_API_KEY": "PRIVATE_KEY_FOR_OFFLINE_TEST"}):
            for url in ENDPOINTS:
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                for forbidden in ("PRIVATE_KEY_FOR_OFFLINE_TEST", str(ROOT), "signature", "api-key=", "traceback"):
                    self.assertNotIn(forbidden, response.text.lower() if forbidden == "traceback" else response.text)
            network.assert_not_called()
        self.assertEqual(self.client.get("/data/historical/swaps/example.csv").status_code, 404)

    def test_read_only_no_script_execution_endpoints(self):
        for path in ("/api/health", "/api/overview", "/api/positions"):
            response = self.client.post(path)
            self.assertEqual(response.status_code, 405)
            c.ErrorResponse.model_validate(response.json())
        self.assertEqual(self.client.get("/api/collect").status_code, 404)

    def test_cors_allowlist_and_errors(self):
        headers = {"Origin": "http://localhost:3000"}
        response = self.client.get("/api/health", headers=headers)
        self.assertEqual(response.headers["access-control-allow-origin"], headers["Origin"])
        rejected = self.client.get("/api/health", headers={"Origin": "https://untrusted.example"})
        self.assertNotIn("access-control-allow-origin", rejected.headers)
        invalid = self.client.get("/api/health?unknown=1", headers=headers)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.headers["access-control-allow-origin"], headers["Origin"])
        preflight = self.client.options("/api/health", headers={**headers, "Access-Control-Request-Method": "GET"})
        self.assertEqual(preflight.status_code, 200)

    def test_openapi_declares_all_endpoints_and_read_only_methods(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        self.assertEqual(set(paths), {url.split("?")[0] for url in ENDPOINTS})
        for value in paths.values(): self.assertEqual(set(value), {"get"})
        self.assertEqual(self.client.get("/docs").status_code, 200)

    def test_committed_openapi_typescript_and_examples_match_contracts(self):
        from scripts.export_api_contract import typescript
        schema = create_app(Settings(mode="demo")).openapi()
        self.assertEqual(json.loads((ROOT/"contracts/openapi.json").read_text()), schema)
        self.assertEqual((ROOT/"contracts/liquidity-radar.ts").read_text(), typescript(schema))
        examples = json.loads((ROOT/"docs/api_examples.json").read_text())
        for url, example in examples.items():
            path = url.split("?")[0]
            contract = next(value for key, value in ENDPOINTS.items() if key.split("?")[0] == path)
            c.Envelope[contract].model_validate(example)

    def test_extrapolated_cost_is_never_clipped(self):
        row = self.store.market.iloc[-1].copy()
        row["estimated_exit_cost_pct"] = 23223.215471
        result = self.store.position(row)
        self.assertEqual(result.estimated_exit_cost_pct, 23223.215471)
        self.assertTrue(result.extrapolation_warning)

    def test_no_csv_reads_during_requests(self):
        with mock.patch("pandas.read_csv", side_effect=AssertionError("Request attempted CSV read")):
            for url in ENDPOINTS: self.assertEqual(self.client.get(url).status_code, 200)

    def test_demo_and_cached_frames_are_unchanged_after_requests(self):
        digest = hashlib.sha256(self.settings.demo_file.read_bytes()).hexdigest()
        before = self.store.risk.copy(deep=True)
        for url in ENDPOINTS: self.client.get(url)
        pd.testing.assert_frame_equal(before, self.store.risk)
        self.assertEqual(digest, hashlib.sha256(self.settings.demo_file.read_bytes()).hexdigest())


class StartupTests(unittest.TestCase):
    def test_cache_loaded_once_on_startup(self):
        store = DatasetStore.load(Settings(mode="demo"))
        with mock.patch.object(DatasetStore, "load", return_value=store) as load:
            with TestClient(create_app(Settings(mode="demo"))) as client:
                for _ in range(3): self.assertEqual(client.get("/api/health").status_code, 200)
            load.assert_called_once()

    def test_auto_uses_demo_on_clean_clone(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(Settings(processed_dir=Path(directory)))) as client:
                self.assertEqual(client.get("/api/health").json()["meta"]["dataset_mode"], "DEMO")

    def test_partial_full_datasets_fail_closed_without_path_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/"liquidity_1m.csv").write_text("invalid")
            with TestClient(create_app(Settings(processed_dir=Path(directory)))) as client:
                for url in ENDPOINTS:
                    result = client.get(url)
                    self.assertEqual(result.status_code, 503)
                    self.assertNotIn(directory, result.text)
                    self.assertEqual(result.json()["error"]["code"], "DATASETS_UNAVAILABLE")

    def test_invalid_processed_scores_flags_and_timestamps_are_rejected(self):
        base = DatasetStore.load(Settings(mode="demo"))
        for field, value in (("available_at", pd.Timestamp("2026-07-03T22:00:00Z")),
            ("current_market_risk_score", 101), ("current_market_risk_regime", "INVALID"),
            ("extrapolation_warning", "False"), ("minimum_history_observations", 1), ("volume_history_count", 0)):
            risk = base.risk.copy()
            risk[field] = risk[field].astype(object)
            risk.loc[0, field] = value
            with self.subTest(field=field), self.assertRaises((DatasetError, ValueError)):
                DatasetStore(risk, base.liquidity, base.backtest, base.provenance, "DEMO")

    def test_small_configured_limit_has_valid_default_and_enforces_bound(self):
        settings = Settings(mode="demo", history_max_limit=2)
        with TestClient(create_app(settings, DatasetStore.load(settings))) as client:
            response = client.get("/api/liquidity/history")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()["data"]["items"]), 2)
            self.assertEqual(client.get("/api/liquidity/history?limit=3").status_code, 422)

    def test_cors_and_modes_configurable_without_credentials(self):
        with mock.patch.dict(os.environ, {"RADAR_CORS_ORIGINS": "http://localhost:3001", "RADAR_DATA_MODE": "demo"}):
            settings = Settings.from_env()
            self.assertEqual(settings.cors_origins, ("http://localhost:3001",))
            self.assertEqual(settings.mode, "demo")
        with mock.patch.dict(os.environ, {"RADAR_CORS_ORIGINS": "*"}):
            with self.assertRaises(ValueError): Settings.from_env()

    def test_unexpected_error_has_no_stack_trace_secret_or_internal_path(self):
        store = DatasetStore.load(Settings(mode="demo"))
        with TestClient(create_app(Settings(mode="demo"), store), raise_server_exceptions=False) as client:
            with mock.patch.object(store, "market_risk", side_effect=RuntimeError("PRIVATE_KEY /private/internal/path")):
                result = client.get("/api/market-risk")
                self.assertEqual(result.status_code, 500)
                self.assertNotIn("PRIVATE_KEY", result.text)
                self.assertNotIn("/private", result.text)
                c.ErrorResponse.model_validate(result.json())


if __name__ == "__main__":
    unittest.main()
