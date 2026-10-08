"""Read and validate existing outputs ONCE; no model execution or network imports."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import gzip
import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

from . import api_contract as c

ROOT = Path(__file__).resolve().parent.parent
POSITIONS = (10000, 50000, 100000, 250000, 500000, 1000000)
SCENARIOS = ("NORMAL", "STRESS_25", "STRESS_50", "STRESS_75")
INTERVALS = {"1m": 1, "5m": 5, "15m": 15}
RISK_VERSION = "20261008T153108969585Z"
BACKTEST_VERSION = "20261008T160739629858Z"
RISK_FILE = "liquidity_risk_v2_1m.csv"
REQUIRED_FILES = (RISK_FILE, "liquidity_1m.csv", "liquidity_5m.csv", "liquidity_15m.csv",
                  "backtest_v2_summary.csv", "backtest_v2_baselines.csv")
COMPONENTS = ("volatility_risk", "liquidity_risk", "flow_risk", "reference_exit_cost_risk")
RISK_COLUMNS = (
    "timestamp", "available_at", "historical_reference_before", "position_size_usdc", "stress_scenario",
    "minimum_history_observations", "market_reference_position_usdc", "market_reference_scenario",
    "volume_usdc", "net_flow_usdc", "swap_count", "volatility", "flow_imbalance",
    "available_volume_usdc", "estimated_exit_cost_pct", "participation_rate", "liquidity_multiple_causal",
    "liquidity_multiple_unavailable_reason", "model_reliability_status", "extrapolation_warning",
    "extrapolation_reason", "insufficient_liquidity_flag", "insufficient_data_flag", "exit_cost_interpretation",
    "position_exit_risk", "position_exit_risk_unavailable_reason", "reference_estimated_exit_cost_pct",
    "reference_exit_extrapolation_warning", "reference_exit_model_reliability_status",
    "current_market_risk_score", "current_market_risk_regime", "current_market_risk_unavailable_reason",
    "market_primary_risk_driver", "market_secondary_risk_driver",
    "forward_liquidity_risk_score", "forward_liquidity_risk_regime", "forward_liquidity_risk_unavailable_reason",
    "forward_primary_risk_driver", "forward_secondary_risk_driver",
    "volatility_history_count", "volume_history_count", "flow_history_count", "reference_exit_cost_history_count",
    *COMPONENTS,
)
LIQUIDITY_COLUMNS = ("timestamp", "swap_count", "volume_usdc", "avg_trade_size_usdc", "median_trade_size_usdc",
    "price_open", "price_close", "price_min", "price_max", "price_return_pct", "volatility", "net_flow_usdc", "flow_imbalance")
UNITS = {"*_usdc": "USDC", "*_usdc_per_sol": "USDC/SOL", "*_pct": "percent (0.5 means 0.5%)",
         "*_0_100": "empirical percentile score, 0..100", "*_ratio": "dimensionless ratio",
         "volatility_std_log_return": "within-window sample standard deviation of log returns; not annualized",
         "timestamps": "ISO 8601 UTC; timestamp=window open, available_at=window close",
         "backtest_rates_and_auc": "fraction 0..1", "lead_time_*_minutes": "minutes", "lift": "ratio to prevalence"}


class DatasetError(ValueError):
    """No underlying path/provider text is ever returned over HTTP."""


@dataclass(frozen=True)
class Settings:
    mode: str = "auto"
    processed_dir: Path = ROOT / "data/processed"
    demo_file: Path = ROOT / "data/demo/snapshot.json.gz"
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    history_max_limit: int = 2000

    @classmethod
    def from_env(cls):
        origins = tuple(x.strip() for x in os.getenv("RADAR_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if x.strip())
        for origin in origins:
            url = urlsplit(origin)
            if url.scheme not in ("http", "https") or not url.netloc or url.path or url.query or url.fragment or url.username:
                raise ValueError("RADAR_CORS_ORIGINS must contain explicit HTTP origins without paths.")
        mode = os.getenv("RADAR_DATA_MODE", "auto")
        if mode not in ("auto", "full", "demo"):
            raise ValueError("RADAR_DATA_MODE must be auto, full or demo.")
        maximum = int(os.getenv("RADAR_HISTORY_MAX_LIMIT", "2000"))
        if not 1 <= maximum <= 10000:
            raise ValueError("RADAR_HISTORY_MAX_LIMIT must be 1..10000.")
        return cls(mode, Path(os.getenv("RADAR_PROCESSED_DIR", str(ROOT / "data/processed"))),
                   ROOT / "data/demo/snapshot.json.gz", origins, maximum)


def finite(value):
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def reason(value):
    return None if value is None or pd.isna(value) or value in ("AVAILABLE", "NONE", "") else str(value)


def utc(value):
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is None:
        raise DatasetError("Timestamp must have timezone.")
    return result.tz_convert("UTC")


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def expect(condition):
    if not condition:
        raise DatasetError("Processed dataset integrity validation failed.")


def regime(values):
    return np.select([values.isna(), values.lt(40), values.lt(60), values.lt(80)],
                     ["INDETERMINATE", "NORMAL", "WATCH", "STRESSED"], default="CRITICAL")


class DatasetStore:
    def __init__(self, risk, liquidity, backtest, provenance, mode):
        self.mode, self.provenance = mode, provenance
        self.risk = risk.copy()
        for key in ("timestamp", "available_at", "historical_reference_before"):
            self.risk[key] = self.risk[key].map(utc)
        self.liquidity = {}
        for interval, raw in liquidity.items():
            frame = raw.copy()
            frame["timestamp"] = frame.timestamp.map(utc)
            frame["available_at"] = frame.timestamp + pd.Timedelta(minutes=INTERVALS[interval])
            self.liquidity[interval] = frame
        self.backtest = backtest.copy()
        self.validate()
        self.market = self.risk.loc[self.risk.position_size_usdc.eq(100000) & self.risk.stress_scenario.eq("NORMAL")].reset_index(drop=True)
        self.market_by_close = self.market.set_index("available_at", drop=False)
        self.latest = self.market.available_at.iloc[-1]
        self.loaded_at = datetime.now(timezone.utc)

    @classmethod
    def load(cls, settings):
        mode = settings.mode
        present = [(settings.processed_dir/name).exists() for name in REQUIRED_FILES]
        if mode == "auto":
            mode = "full" if any(present) else "demo"
        if mode == "demo":
            if settings.demo_file.suffix == ".gz":
                with gzip.open(settings.demo_file, "rt", encoding="utf-8") as source:
                    bundle = json.load(source)
            else:
                bundle = json.loads(settings.demo_file.read_text())
            expect(bundle["format_version"] == 1)
            return cls(pd.DataFrame(bundle["risk_rows"]), {k: pd.DataFrame(v) for k, v in bundle["liquidity"].items()},
                       pd.DataFrame(bundle["backtest_rows"]), bundle["provenance"], "DEMO")
        expect(all(present))
        root = settings.processed_dir
        risk_manifest = json.loads((root/"risk_v2_runs"/RISK_VERSION/"metadata.json").read_text())
        back_manifest = json.loads((root/"backtest_v2_runs"/BACKTEST_VERSION/"metadata.json").read_text())
        expect(digest(root/RISK_FILE) == risk_manifest["output_sha256"][RISK_FILE])
        for name in ("backtest_v2_summary.csv", "backtest_v2_baselines.csv"):
            expect(digest(root/name) == back_manifest["output_sha256"][name])
        risk = pd.read_csv(root/RISK_FILE, usecols=RISK_COLUMNS, float_precision="round_trip")
        liquid = {interval: pd.read_csv(root/f"liquidity_{interval}.csv", usecols=LIQUIDITY_COLUMNS, float_precision="round_trip") for interval in INTERVALS}
        backtest = pd.concat([pd.read_csv(root/name) for name in ("backtest_v2_summary.csv", "backtest_v2_baselines.csv")], ignore_index=True)
        provenance = {"risk_version": RISK_VERSION, "backtest_version": BACKTEST_VERSION,
            "backtest_result_available_at": datetime.strptime(BACKTEST_VERSION, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).isoformat(),
            "backtest_reference": back_manifest["frozen_reference"],
            "train_start": back_manifest["train_start"], "test_start": back_manifest["test_start"],
            "test_end_exclusive": back_manifest["test_end_exclusive"]}
        return cls(risk, liquid, backtest, provenance, "FULL")

    def validate(self):
        r = self.risk
        expect(set(RISK_COLUMNS) <= set(r) and not r.empty)
        expect(r.timestamp.is_monotonic_increasing and not r.duplicated(["timestamp", "position_size_usdc", "stress_scenario"]).any())
        expect(r.available_at.eq(r.timestamp + pd.Timedelta(minutes=1)).all())
        expect(r.historical_reference_before.eq(r.timestamp).all() and r.minimum_history_observations.eq(120).all())
        expect(set(r.position_size_usdc) == set(POSITIONS) and set(r.stress_scenario) == set(SCENARIOS))
        expect(r.groupby("timestamp").size().eq(24).all())
        expect(r.market_reference_position_usdc.eq(100000).all() and r.market_reference_scenario.eq("NORMAL").all())
        for flag in ("extrapolation_warning", "insufficient_liquidity_flag", "insufficient_data_flag", "reference_exit_extrapolation_warning"):
            expect(r[flag].map(lambda x: isinstance(x, (bool, np.bool_))).all())
        for column in (*COMPONENTS, "current_market_risk_score", "forward_liquidity_risk_score"):
            expect(r[column].dropna().between(0, 100).all())
            expect(r.groupby("timestamp")[column].nunique(dropna=False).eq(1).all())
        for prefix, expected in (("current_market_risk", r[list(COMPONENTS)].sum(axis=1, min_count=4)/4),
                                 ("forward_liquidity_risk", (r.liquidity_risk+r.flow_risk)/2)):
            expect(np.allclose(r[f"{prefix}_score"], expected, equal_nan=True, rtol=1e-10, atol=1e-10))
            expect(r[f"{prefix}_regime"].eq(regime(r[f"{prefix}_score"])).all())
            expect(r[f"{prefix}_score"].isna().eq(r[f"{prefix}_unavailable_reason"].ne("AVAILABLE")).all())
        for component, count in (("volatility_risk", "volatility_history_count"), ("liquidity_risk", "volume_history_count"),
                                 ("flow_risk", "flow_history_count"), ("reference_exit_cost_risk", "reference_exit_cost_history_count")):
            expect(r.loc[r[component].notna(), count].ge(120).all())
        missing = r.estimated_exit_cost_pct.isna() | r.participation_rate.isna()
        expect(missing.eq(r.position_exit_risk.eq("INDETERMINATE")).all())
        expect(r.position_exit_risk.isin(("LOW", "MODERATE", "HIGH", "EXTREME", "INDETERMINATE")).all())
        expect(set(self.liquidity) == set(INTERVALS))
        for interval, frame in self.liquidity.items():
            expect(set(LIQUIDITY_COLUMNS) <= set(frame) and not frame.empty)
            expect(frame.timestamp.is_monotonic_increasing and not frame.timestamp.duplicated().any())
            expect(frame.swap_count.ge(0).all() and frame.swap_count.mod(1).eq(0).all() and frame.volume_usdc.ge(0).all())
            expect(frame.timestamp.eq(frame.timestamp.dt.floor(f"{INTERVALS[interval]}min")).all())
            expect(frame.timestamp.diff().dropna().eq(pd.Timedelta(minutes=INTERVALS[interval])).all())
            for price in ("price_open", "price_close", "price_min", "price_max"):
                expect(frame[price].dropna().gt(0).all())
            expect(frame.flow_imbalance.dropna().abs().le(1+1e-12).all())
        one = self.liquidity["1m"]
        unique = r.loc[r.position_size_usdc.eq(100000) & r.stress_scenario.eq("NORMAL")]
        expect(one.timestamp.reset_index(drop=True).equals(unique.timestamp.reset_index(drop=True)))
        expect(one.timestamp.diff().dropna().eq(pd.Timedelta(minutes=1)).all())
        for column in ("volume_usdc", "net_flow_usdc", "swap_count", "volatility", "flow_imbalance"):
            expect(np.allclose(one[column], unique[column], equal_nan=True, rtol=1e-10, atol=1e-9))
        for frame in self.liquidity.values():
            expect(frame.timestamp.iloc[0] == one.timestamp.iloc[0] and frame.available_at.iloc[-1] == one.available_at.iloc[-1])
            for column in ("volume_usdc", "net_flow_usdc", "swap_count"):
                expect(np.isclose(frame[column].sum(), one[column].sum(), rtol=1e-10, atol=1e-8))
        expect(set(c.BacktestRow.model_fields) <= set(self.backtest))
        expect(not self.backtest.duplicated(["predictor", "horizon_minutes", "event", "sample"]).any())
        expect(self.backtest.n_valid.eq(self.backtest.tp+self.backtest.fp+self.backtest.tn+self.backtest.fn).all())
        # Validate public enums and finite/null metric contracts once, never at request time from CSV.
        for record in self.backtest.to_dict("records"):
            self.backtest_row(record)

    def decision(self, as_of=None):
        selected = self.market if as_of is None else self.market.loc[self.market.available_at.le(utc(as_of))]
        if selected.empty:
            raise LookupError("No closed window available at the requested instant.")
        return selected.iloc[-1]

    def metadata(self, reference_at, requested_as_of=None):
        one = self.liquidity["1m"]
        return c.Metadata(dataset_mode=self.mode, reference_at=reference_at, requested_as_of=requested_as_of,
            available_period=c.Period(window_start=one.timestamp.iloc[0], window_end_exclusive=one.available_at.iloc[-1],
                                      available_from=one.available_at.iloc[0], available_until=one.available_at.iloc[-1]),
            risk_version=self.provenance["risk_version"], units=UNITS,
            coverage_notice="Only a real two-hour subset; backtest retains the full four-day evaluation." if self.mode == "DEMO" else "Full processed four-day historical dataset.")

    @staticmethod
    def indicator_fields(row, prefix, driver_prefix):
        missing = pd.isna(row[f"{prefix}_score"])
        return {"timestamp": row.timestamp, "available_at": row.available_at, "score_0_100": finite(row[f"{prefix}_score"]),
            "regime": row[f"{prefix}_regime"], "status": "INDETERMINATE" if missing else "AVAILABLE",
            "unavailable_reason": reason(row[f"{prefix}_unavailable_reason"]),
            "primary_risk_driver": reason(row[f"{driver_prefix}_primary_risk_driver"]),
            "secondary_risk_driver": reason(row[f"{driver_prefix}_secondary_risk_driver"])}

    def market_risk(self, row):
        return c.MarketRisk(**self.indicator_fields(row, "current_market_risk", "market"),
            components=c.MarketComponents(**{f"{key}_0_100": finite(row[key]) for key in COMPONENTS}),
            reference_estimated_exit_cost_pct=finite(row.reference_estimated_exit_cost_pct),
            reference_extrapolation_warning=bool(row.reference_exit_extrapolation_warning),
            reference_model_reliability_status=row.reference_exit_model_reliability_status)

    def forward_risk(self, row):
        return c.ForwardRisk(**self.indicator_fields(row, "forward_liquidity_risk", "forward"),
            components=c.ForwardComponents(liquidity_risk_0_100=finite(row.liquidity_risk), flow_risk_0_100=finite(row.flow_risk)))

    @staticmethod
    def position(row):
        return c.PositionExit(timestamp=row.timestamp, available_at=row.available_at, position_size_usdc=int(row.position_size_usdc),
            scenario=row.stress_scenario, available_volume_usdc=finite(row.available_volume_usdc),
            estimated_exit_cost_pct=finite(row.estimated_exit_cost_pct), participation_rate_ratio=finite(row.participation_rate),
            liquidity_multiple_causal_ratio=finite(row.liquidity_multiple_causal),
            liquidity_multiple_unavailable_reason=reason(row.liquidity_multiple_unavailable_reason),
            operational_risk=row.position_exit_risk, status="INDETERMINATE" if row.position_exit_risk == "INDETERMINATE" else "AVAILABLE",
            unavailable_reason=reason(row.position_exit_risk_unavailable_reason), model_reliability_status=row.model_reliability_status,
            extrapolation_warning=bool(row.extrapolation_warning), extrapolation_reason=reason(row.extrapolation_reason),
            insufficient_liquidity_flag=bool(row.insufficient_liquidity_flag), insufficient_data_flag=bool(row.insufficient_data_flag),
            volatility_std_log_return=finite(row.volatility), interpretation=row.exit_cost_interpretation)

    def positions_at(self, row, scenario="NORMAL"):
        selected = self.risk.loc[self.risk.timestamp.eq(row.timestamp) & self.risk.stress_scenario.eq(scenario)]
        return [self.position(item) for _, item in selected.sort_values("position_size_usdc").iterrows()]

    def risk_at_close(self, instant):
        row = self.market_by_close.loc[instant]
        return c.RiskAtClose(available_at=row.available_at,
            current_market_risk_score_0_100=finite(row.current_market_risk_score), current_market_risk_regime=row.current_market_risk_regime,
            current_market_unavailable_reason=reason(row.current_market_risk_unavailable_reason),
            forward_liquidity_risk_score_0_100=finite(row.forward_liquidity_risk_score), forward_liquidity_risk_regime=row.forward_liquidity_risk_regime,
            forward_unavailable_reason=reason(row.forward_liquidity_risk_unavailable_reason),
            components=c.MarketComponents(**{f"{key}_0_100": finite(row[key]) for key in COMPONENTS}),
            reference_estimated_exit_cost_pct=finite(row.reference_estimated_exit_cost_pct),
            reference_extrapolation_warning=bool(row.reference_exit_extrapolation_warning),
            reference_model_reliability_status=row.reference_exit_model_reliability_status)

    @staticmethod
    def liquidity_window(row):
        vol = finite(row.volatility)
        volatility_status = "AVAILABLE" if vol is not None else "NO_SWAPS" if row.swap_count == 0 else "INSUFFICIENT_PRICES" if row.swap_count < 3 else "NON_FINITE_SOURCE"
        flow = finite(row.flow_imbalance)
        return c.LiquidityWindow(timestamp=row.timestamp, available_at=row.available_at, swap_count=int(row.swap_count),
            **{key: finite(row[key]) for key in ("volume_usdc", "avg_trade_size_usdc", "median_trade_size_usdc", "price_return_pct", "net_flow_usdc")},
            **{f"{key}_usdc_per_sol": finite(row[key]) for key in ("price_open", "price_close", "price_min", "price_max")},
            volatility_std_log_return=vol, volatility_status=volatility_status, flow_imbalance_ratio=flow,
            flow_status="AVAILABLE" if flow is not None else "NO_VOLUME" if row.volume_usdc == 0 else "MISSING_SOURCE")

    @staticmethod
    def backtest_row(record):
        numeric_nullable = ("training_event_rate", "event_rate", "precision", "recall", "false_positive_rate", "specificity", "lift",
            "roc_auc", "pr_auc_average_precision", "alert_rate", "lead_time_median_minutes", "lead_time_p25_minutes", "lead_time_p75_minutes")
        values = {key: record[key] for key in c.BacktestRow.model_fields}
        values.update({key: finite(record[key]) for key in numeric_nullable})
        return c.BacktestRow(**values)
