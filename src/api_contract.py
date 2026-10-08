"""Public read-only contracts. Percentages are percentage points, not fractions."""
from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

Regime = Literal["NORMAL", "WATCH", "STRESSED", "CRITICAL", "INDETERMINATE"]
Scenario = Literal["NORMAL", "STRESS_25", "STRESS_50", "STRESS_75"]
Interval = Literal["1m", "5m", "15m"]
Event = Literal["LOW_VOLUME_EVENT", "HIGH_VOLATILITY_EVENT", "HIGH_EXIT_COST_EVENT", "ANY_DETERIORATION"]
Sample = Literal["ALL_AVAILABLE", "MATCHED", "NON_OVERLAPPING_MATCHED", "STRICT_COMPLETE_MATCHED"]
Predictor = Literal["CURRENT_MARKET_RISK", "FORWARD_LIQUIDITY_RISK", "LOW_CURRENT_VOLUME", "RECENT_VOLUME_DROP", "PERSISTENCE_PREVIOUS_CONDITION", "PREVALENCE_TRAINING"]
Score = float | None


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Period(Contract):
    window_start: datetime
    window_end_exclusive: datetime
    available_from: datetime
    available_until: datetime


class Metadata(Contract):
    data_kind: Literal["HISTORICAL"] = "HISTORICAL"
    dataset_mode: Literal["FULL", "DEMO"]
    reference_at: datetime
    requested_as_of: datetime | None = None
    available_period: Period
    risk_version: str
    availability_basis: str = "THEORETICAL_WINDOW_CLOSE_NOT_MEASURED_INGESTION"
    coverage_notice: str
    units: dict[str, str]


T = TypeVar("T")


class Envelope(Contract, Generic[T]):
    meta: Metadata
    data: T


class ErrorDetail(Contract):
    field: str
    message: str


class ErrorInfo(Contract):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(Contract):
    error: ErrorInfo


class Health(Contract):
    status: Literal["OK"] = "OK"
    api_version: str
    datasets_ready: bool
    cache_loaded_at: datetime
    helius_required: Literal[False] = False


class MarketComponents(Contract):
    volatility_risk_0_100: Score
    liquidity_risk_0_100: Score
    flow_risk_0_100: Score
    reference_exit_cost_risk_0_100: Score


class ForwardComponents(Contract):
    liquidity_risk_0_100: Score
    flow_risk_0_100: Score


class Indicator(Contract):
    timestamp: datetime
    available_at: datetime
    score_0_100: float | None = Field(ge=0, le=100)
    regime: Regime
    status: Literal["AVAILABLE", "INDETERMINATE"]
    unavailable_reason: str | None
    primary_risk_driver: str | None
    secondary_risk_driver: str | None
    regime_policy: str = "HEURISTIC_UNCALIBRATED_40_60_80"


class MarketRisk(Indicator):
    indicator: Literal["CURRENT_MARKET_RISK"] = "CURRENT_MARKET_RISK"
    components: MarketComponents
    reference_position_size_usdc: Literal[100000] = 100000
    reference_scenario: Literal["NORMAL"] = "NORMAL"
    reference_estimated_exit_cost_pct: float | None
    reference_extrapolation_warning: bool
    reference_model_reliability_status: str
    interpretation: str = "CURRENT_MARKET_SEVERITY"


class ForwardRisk(Indicator):
    indicator: Literal["FORWARD_LIQUIDITY_RISK"] = "FORWARD_LIQUIDITY_RISK"
    components: ForwardComponents
    is_calibrated_probability: Literal[False] = False
    interpretation: str = "EXPLORATORY_FORWARD_SIGNAL_NOT_VALIDATED_FORECAST"


class PositionExit(Contract):
    timestamp: datetime
    available_at: datetime
    indicator: Literal["POSITION_EXIT_RISK"] = "POSITION_EXIT_RISK"
    position_size_usdc: int
    scenario: Scenario
    available_volume_usdc: float | None
    estimated_exit_cost_pct: float | None
    participation_rate_ratio: float | None
    liquidity_multiple_causal_ratio: float | None
    liquidity_multiple_unavailable_reason: str | None
    operational_risk: Literal["LOW", "MODERATE", "HIGH", "EXTREME", "INDETERMINATE"]
    status: Literal["AVAILABLE", "INDETERMINATE"]
    unavailable_reason: str | None
    model_reliability_status: str
    extrapolation_warning: bool
    extrapolation_reason: str | None
    insufficient_liquidity_flag: bool
    insufficient_data_flag: bool
    volatility_std_log_return: float | None
    interpretation: str
    is_observed_slippage: Literal[False] = False


class Positions(Contract):
    position_sizes_usdc: list[int]
    scenarios: list[Scenario]
    selected_scenario: Scenario
    items: list[PositionExit]


class Overview(Contract):
    closed_window_count_1m: int
    swap_count: int
    volume_usdc: float | None
    net_flow_usdc: float | None
    empty_window_count_1m: int
    missing_volatility_count_1m: int
    current_market_risk: MarketRisk
    forward_liquidity_risk: ForwardRisk
    positions_normal: list[PositionExit]


class RiskAtClose(Contract):
    """Existing 1m indicators at this bar close; never aggregated or recalculated."""
    available_at: datetime
    current_market_risk_score_0_100: Score
    current_market_risk_regime: Regime
    current_market_unavailable_reason: str | None
    forward_liquidity_risk_score_0_100: Score
    forward_liquidity_risk_regime: Regime
    forward_unavailable_reason: str | None
    components: MarketComponents
    reference_position_size_usdc: Literal[100000] = 100000
    reference_scenario: Literal["NORMAL"] = "NORMAL"
    reference_estimated_exit_cost_pct: float | None
    reference_extrapolation_warning: bool
    reference_model_reliability_status: str


class LiquidityWindow(Contract):
    timestamp: datetime
    available_at: datetime
    swap_count: int
    volume_usdc: float | None
    avg_trade_size_usdc: float | None
    median_trade_size_usdc: float | None
    price_open_usdc_per_sol: float | None
    price_close_usdc_per_sol: float | None
    price_min_usdc_per_sol: float | None
    price_max_usdc_per_sol: float | None
    price_return_pct: float | None
    volatility_std_log_return: float | None
    volatility_status: Literal["AVAILABLE", "INSUFFICIENT_PRICES", "NO_SWAPS", "NON_FINITE_SOURCE"]
    net_flow_usdc: float | None
    flow_imbalance_ratio: float | None
    flow_status: Literal["AVAILABLE", "NO_VOLUME", "MISSING_SOURCE"]
    risk_at_close: RiskAtClose | None = None


class Pagination(Contract):
    total: int
    offset: int
    limit: int
    next_offset: int | None


class History(Contract):
    interval: Interval
    order: Literal["ASC"] = "ASC"
    pagination: Pagination
    items: list[LiquidityWindow]


class BacktestRow(Contract):
    predictor: Predictor
    horizon_minutes: int
    event: Event
    sample: Sample
    training_event_rate: float | None
    training_event_valid_count: int
    n_candidates: int
    n_valid: int
    n_excluded: int
    n_excluded_incomplete_horizon: int
    n_excluded_unknown_label: int
    n_excluded_strict_missing_future_data: int
    n_excluded_predictor: int
    event_count: int
    event_rate: float | None
    tp: int
    fp: int
    tn: int
    fn: int
    precision: float | None
    recall: float | None
    false_positive_rate: float | None
    specificity: float | None
    lift: float | None
    roc_auc: float | None
    pr_auc_average_precision: float | None
    auc_unavailable_reason: str
    alert_rate: float | None
    lead_time_median_minutes: float | None
    lead_time_p25_minutes: float | None
    lead_time_p75_minutes: float | None
    alerted_first_breach_minutes: int
    observed_first_breach_minutes: int


class Backtest(Contract):
    analysis_scope: Literal["RETROSPECTIVE_HOLDOUT"] = "RETROSPECTIVE_HOLDOUT"
    train_start: datetime
    test_start: datetime
    test_end_exclusive: datetime
    result_available_at: datetime
    frozen_volume_p05_usdc: float
    frozen_volatility_p95_std_log_return: float
    frozen_exit_cost_p95_pct: float
    items: list[BacktestRow]
    limitations: list[str]
