// Generated from OpenAPI; do not edit. All timestamps UTC, all nullable numbers remain null.
export type ISODateTime = string;

export type Backtest = { "analysis_scope"?: "RETROSPECTIVE_HOLDOUT"; "train_start": ISODateTime; "test_start": ISODateTime; "test_end_exclusive": ISODateTime; "result_available_at": ISODateTime; "frozen_volume_p05_usdc": number; "frozen_volatility_p95_std_log_return": number; "frozen_exit_cost_p95_pct": number; "items": Array<BacktestRow>; "limitations": Array<string>; };
export type BacktestRow = { "predictor": "CURRENT_MARKET_RISK" | "FORWARD_LIQUIDITY_RISK" | "LOW_CURRENT_VOLUME" | "RECENT_VOLUME_DROP" | "PERSISTENCE_PREVIOUS_CONDITION" | "PREVALENCE_TRAINING"; "horizon_minutes": number; "event": "LOW_VOLUME_EVENT" | "HIGH_VOLATILITY_EVENT" | "HIGH_EXIT_COST_EVENT" | "ANY_DETERIORATION"; "sample": "ALL_AVAILABLE" | "MATCHED" | "NON_OVERLAPPING_MATCHED" | "STRICT_COMPLETE_MATCHED"; "training_event_rate": number | null; "training_event_valid_count": number; "n_candidates": number; "n_valid": number; "n_excluded": number; "n_excluded_incomplete_horizon": number; "n_excluded_unknown_label": number; "n_excluded_strict_missing_future_data": number; "n_excluded_predictor": number; "event_count": number; "event_rate": number | null; "tp": number; "fp": number; "tn": number; "fn": number; "precision": number | null; "recall": number | null; "false_positive_rate": number | null; "specificity": number | null; "lift": number | null; "roc_auc": number | null; "pr_auc_average_precision": number | null; "auc_unavailable_reason": string; "alert_rate": number | null; "lead_time_median_minutes": number | null; "lead_time_p25_minutes": number | null; "lead_time_p75_minutes": number | null; "alerted_first_breach_minutes": number; "observed_first_breach_minutes": number; };
export type Envelope_Backtest_ = { "meta": Metadata; "data": Backtest; };
export type Envelope_ForwardRisk_ = { "meta": Metadata; "data": ForwardRisk; };
export type Envelope_Health_ = { "meta": Metadata; "data": Health; };
export type Envelope_History_ = { "meta": Metadata; "data": History; };
export type Envelope_MarketRisk_ = { "meta": Metadata; "data": MarketRisk; };
export type Envelope_Overview_ = { "meta": Metadata; "data": Overview; };
export type Envelope_PositionExit_ = { "meta": Metadata; "data": PositionExit; };
export type Envelope_Positions_ = { "meta": Metadata; "data": Positions; };
export type ErrorDetail = { "field": string; "message": string; };
export type ErrorInfo = { "code": string; "message": string; "details"?: Array<ErrorDetail>; };
export type ErrorResponse = { "error": ErrorInfo; };
export type ForwardComponents = { "liquidity_risk_0_100": number | null; "flow_risk_0_100": number | null; };
export type ForwardRisk = { "timestamp": ISODateTime; "available_at": ISODateTime; "score_0_100": number | null; "regime": "NORMAL" | "WATCH" | "STRESSED" | "CRITICAL" | "INDETERMINATE"; "status": "AVAILABLE" | "INDETERMINATE"; "unavailable_reason": string | null; "primary_risk_driver": string | null; "secondary_risk_driver": string | null; "regime_policy"?: string; "indicator"?: "FORWARD_LIQUIDITY_RISK"; "components": ForwardComponents; "is_calibrated_probability"?: false; "interpretation"?: string; };
export type Health = { "status"?: "OK"; "api_version": string; "datasets_ready": boolean; "cache_loaded_at": ISODateTime; "helius_required"?: false; };
export type History = { "interval": "1m" | "5m" | "15m"; "order"?: "ASC"; "pagination": Pagination; "items": Array<LiquidityWindow>; };
export type LiquidityWindow = { "timestamp": ISODateTime; "available_at": ISODateTime; "swap_count": number; "volume_usdc": number | null; "avg_trade_size_usdc": number | null; "median_trade_size_usdc": number | null; "price_open_usdc_per_sol": number | null; "price_close_usdc_per_sol": number | null; "price_min_usdc_per_sol": number | null; "price_max_usdc_per_sol": number | null; "price_return_pct": number | null; "volatility_std_log_return": number | null; "volatility_status": "AVAILABLE" | "INSUFFICIENT_PRICES" | "NO_SWAPS" | "NON_FINITE_SOURCE"; "net_flow_usdc": number | null; "flow_imbalance_ratio": number | null; "flow_status": "AVAILABLE" | "NO_VOLUME" | "MISSING_SOURCE"; "risk_at_close"?: RiskAtClose | null; };
export type MarketComponents = { "volatility_risk_0_100": number | null; "liquidity_risk_0_100": number | null; "flow_risk_0_100": number | null; "reference_exit_cost_risk_0_100": number | null; };
export type MarketRisk = { "timestamp": ISODateTime; "available_at": ISODateTime; "score_0_100": number | null; "regime": "NORMAL" | "WATCH" | "STRESSED" | "CRITICAL" | "INDETERMINATE"; "status": "AVAILABLE" | "INDETERMINATE"; "unavailable_reason": string | null; "primary_risk_driver": string | null; "secondary_risk_driver": string | null; "regime_policy"?: string; "indicator"?: "CURRENT_MARKET_RISK"; "components": MarketComponents; "reference_position_size_usdc"?: 100000; "reference_scenario"?: "NORMAL"; "reference_estimated_exit_cost_pct": number | null; "reference_extrapolation_warning": boolean; "reference_model_reliability_status": string; "interpretation"?: string; };
export type Metadata = { "data_kind"?: "HISTORICAL"; "dataset_mode": "FULL" | "DEMO"; "reference_at": ISODateTime; "requested_as_of"?: ISODateTime | null; "available_period": Period; "risk_version": string; "availability_basis"?: string; "coverage_notice": string; "units": Record<string, string>; };
export type Overview = { "closed_window_count_1m": number; "swap_count": number; "volume_usdc": number | null; "net_flow_usdc": number | null; "empty_window_count_1m": number; "missing_volatility_count_1m": number; "current_market_risk": MarketRisk; "forward_liquidity_risk": ForwardRisk; "positions_normal": Array<PositionExit>; };
export type Pagination = { "total": number; "offset": number; "limit": number; "next_offset": number | null; };
export type Period = { "window_start": ISODateTime; "window_end_exclusive": ISODateTime; "available_from": ISODateTime; "available_until": ISODateTime; };
export type PositionExit = { "timestamp": ISODateTime; "available_at": ISODateTime; "indicator"?: "POSITION_EXIT_RISK"; "position_size_usdc": number; "scenario": "NORMAL" | "STRESS_25" | "STRESS_50" | "STRESS_75"; "available_volume_usdc": number | null; "estimated_exit_cost_pct": number | null; "participation_rate_ratio": number | null; "liquidity_multiple_causal_ratio": number | null; "liquidity_multiple_unavailable_reason": string | null; "operational_risk": "LOW" | "MODERATE" | "HIGH" | "EXTREME" | "INDETERMINATE"; "status": "AVAILABLE" | "INDETERMINATE"; "unavailable_reason": string | null; "model_reliability_status": string; "extrapolation_warning": boolean; "extrapolation_reason": string | null; "insufficient_liquidity_flag": boolean; "insufficient_data_flag": boolean; "volatility_std_log_return": number | null; "interpretation": string; "is_observed_slippage"?: false; };
export type Positions = { "position_sizes_usdc": Array<number>; "scenarios": Array<"NORMAL" | "STRESS_25" | "STRESS_50" | "STRESS_75">; "selected_scenario": "NORMAL" | "STRESS_25" | "STRESS_50" | "STRESS_75"; "items": Array<PositionExit>; };
export type RiskAtClose = { "available_at": ISODateTime; "current_market_risk_score_0_100": number | null; "current_market_risk_regime": "NORMAL" | "WATCH" | "STRESSED" | "CRITICAL" | "INDETERMINATE"; "current_market_unavailable_reason": string | null; "forward_liquidity_risk_score_0_100": number | null; "forward_liquidity_risk_regime": "NORMAL" | "WATCH" | "STRESSED" | "CRITICAL" | "INDETERMINATE"; "forward_unavailable_reason": string | null; "components": MarketComponents; "reference_position_size_usdc"?: 100000; "reference_scenario"?: "NORMAL"; "reference_estimated_exit_cost_pct": number | null; "reference_extrapolation_warning": boolean; "reference_model_reliability_status": string; };

export type HealthResponse = Envelope_Health_;
export type OverviewResponse = Envelope_Overview_;
export type MarketRiskResponse = Envelope_MarketRisk_;
export type ForwardRiskResponse = Envelope_ForwardRisk_;
export type PositionsResponse = Envelope_Positions_;
export type ExitCostResponse = Envelope_PositionExit_;
export type HistoryResponse = Envelope_History_;
export type BacktestResponse = Envelope_Backtest_;

export type RadarEndpoint = "/api/health" | "/api/overview" | "/api/market-risk" | "/api/forward-risk" | "/api/positions" | "/api/exit-cost" | "/api/liquidity/history" | "/api/backtest/summary";
export async function fetchRadar<T>(baseUrl: string, path: RadarEndpoint, params: Record<string, string | number> = {}): Promise<T> {
  const url = new URL(path, baseUrl);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value));
  const response = await fetch(url.toString(), { cache: 'no-store' });
  if (!response.ok) {
    const body: ErrorResponse = await response.json();
    throw new Error(`${body.error.code}: ${body.error.message}`);
  }
  return await response.json() as T;
}
