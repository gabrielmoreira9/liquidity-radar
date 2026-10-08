// Single entry point for the generated backend contracts.
// The source file (../contracts/liquidity-radar.ts) is generated from OpenAPI and is never edited;
// only types are imported, so its runtime helper is not bundled.
export type {
  Backtest,
  BacktestResponse,
  BacktestRow,
  ErrorDetail,
  ErrorResponse,
  ExitCostResponse,
  ForwardRisk,
  ForwardRiskResponse,
  Health,
  HealthResponse,
  History,
  HistoryResponse,
  LiquidityWindow,
  MarketComponents,
  MarketRisk,
  MarketRiskResponse,
  Metadata,
  Overview,
  OverviewResponse,
  Pagination,
  Period,
  PositionExit,
  Positions,
  PositionsResponse,
  RadarEndpoint,
  RiskAtClose,
} from "@contracts/liquidity-radar";

import type {
  BacktestRow,
  ForwardRisk,
  History,
  PositionExit,
} from "@contracts/liquidity-radar";

// Enum unions derived from the contract (never re-declared by hand).
export type Regime = ForwardRisk["regime"];
export type OperationalRisk = PositionExit["operational_risk"];
export type Scenario = PositionExit["scenario"];
export type Interval = History["interval"];
export type Predictor = BacktestRow["predictor"];
export type BacktestEvent = BacktestRow["event"];
export type BacktestSample = BacktestRow["sample"];
