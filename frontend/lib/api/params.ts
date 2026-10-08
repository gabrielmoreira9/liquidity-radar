import type {
  BacktestEvent,
  BacktestSample,
  Interval,
  Predictor,
  RadarEndpoint,
  Scenario,
} from "@/lib/contracts";

/**
 * Query parameters accepted by each endpoint, mirroring the backend middleware
 * (src/api.py). The backend rejects unknown or repeated parameters with 422, so
 * every request is built through `buildQuery`, which drops anything not listed.
 */
export const ALLOWED_PARAMS = {
  "/api/health": [],
  "/api/overview": ["as_of"],
  "/api/market-risk": ["as_of"],
  "/api/forward-risk": ["as_of"],
  "/api/positions": ["scenario", "as_of"],
  "/api/exit-cost": ["position_size", "scenario", "as_of"],
  "/api/liquidity/history": ["interval", "start", "end", "as_of", "limit", "offset"],
  "/api/backtest/summary": ["event", "sample", "horizon", "predictor"],
} as const satisfies Record<RadarEndpoint, readonly string[]>;

export type ParamsFor<E extends RadarEndpoint> = Partial<
  Record<(typeof ALLOWED_PARAMS)[E][number], string | number | null | undefined>
>;

/** Keep only allowed, non-empty parameters, in a deterministic order. */
export function buildQuery<E extends RadarEndpoint>(endpoint: E, params: ParamsFor<E> = {}): URLSearchParams {
  const query = new URLSearchParams();
  const values = params as Record<string, string | number | null | undefined>;
  for (const key of ALLOWED_PARAMS[endpoint] as readonly string[]) {
    const value = values[key];
    if (value === undefined || value === null || value === "") continue;
    if (typeof value === "number" && !Number.isFinite(value)) continue;
    query.set(key, String(value));
  }
  return query;
}

// Runtime enumerations. `exhaustive` fails type-checking if the contract gains or loses a value.
type Exhaustive<T, A extends readonly T[]> = [T] extends [A[number]] ? A : never;
const exhaustive =
  <T>() =>
  <const A extends readonly T[]>(values: Exhaustive<T, A>) =>
    values;

export const SCENARIOS = exhaustive<Scenario>()(["NORMAL", "STRESS_25", "STRESS_50", "STRESS_75"]);
export const INTERVALS = exhaustive<Interval>()(["1m", "5m", "15m"]);
export const BACKTEST_EVENTS = exhaustive<BacktestEvent>()([
  "ANY_DETERIORATION",
  "LOW_VOLUME_EVENT",
  "HIGH_VOLATILITY_EVENT",
  "HIGH_EXIT_COST_EVENT",
]);
export const BACKTEST_SAMPLES = exhaustive<BacktestSample>()([
  "MATCHED",
  "NON_OVERLAPPING_MATCHED",
  "STRICT_COMPLETE_MATCHED",
  "ALL_AVAILABLE",
]);
export const PREDICTORS = exhaustive<Predictor>()([
  "CURRENT_MARKET_RISK",
  "FORWARD_LIQUIDITY_RISK",
  "LOW_CURRENT_VOLUME",
  "RECENT_VOLUME_DROP",
  "PERSISTENCE_PREVIOUS_CONDITION",
  "PREVALENCE_TRAINING",
]);

/** Documented in docs/api.md; the backend validates and returns 422 for anything else. */
export const POSITION_SIZES_USDC = [10000, 50000, 100000, 250000, 500000, 1000000] as const;
export const BACKTEST_HORIZONS = [5, 15, 30, 60] as const;
/** Server default (RADAR_HISTORY_MAX_LIMIT); not exposed by the API, so pagination is always followed. */
export const HISTORY_PAGE_LIMIT = 2000;

export function isOneOf<T extends string | number>(values: readonly T[], value: unknown): value is T {
  return values.includes(value as T);
}
