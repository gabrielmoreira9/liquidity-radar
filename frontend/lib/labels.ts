import type { BacktestEvent, BacktestSample, Interval, OperationalRisk, Predictor, Regime, Scenario } from "@/lib/contracts";

/**
 * Human labels for backend codes. Several fields are plain strings in the contract
 * (reasons, drivers, reliability), so every lookup falls back to the raw code instead of
 * guessing a meaning.
 */

const DRIVERS: Record<string, string> = {
  volatility_risk: "Volatility",
  liquidity_risk: "Low volume",
  flow_risk: "Flow imbalance",
  reference_exit_cost_risk: "Reference exit cost",
};

export function driverLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return DRIVERS[code] ?? humanize(code);
}

const COMPONENT_SUBJECT: Record<string, string> = {
  VOLATILITY: "volatility",
  VOLUME: "volume",
  FLOW: "flow",
  REFERENCE_EXIT: "reference exit cost",
  LIQUIDITY: "liquidity",
};

const REASONS: Record<string, string> = {
  NO_OBSERVED_TURNOVER: "No observed turnover in the window",
  VOLATILITY_UNAVAILABLE: "Volatility unavailable",
  PARTICIPATION_UNAVAILABLE: "Participation unavailable",
  EXIT_COST_UNAVAILABLE: "Exit cost unavailable",
  AVAILABLE_VOLUME_MISSING: "Available volume missing",
  HISTORICAL_MEDIAN_MISSING: "No prior median volume yet",
  HISTORICAL_MEDIAN_NONPOSITIVE: "Prior median volume is not positive",
  Q_GT_TURNOVER: "Position exceeds the window's observed turnover",
  IMPACT_GE_ONE: "Modeled impact ≥ 100%",
  IMPACT_GE_ONE_AND_Q_GT_TURNOVER: "Impact ≥ 100% and position exceeds observed turnover",
};

/** One reason code → readable text. Handles generated `<COMPONENT>_OBSERVATION_MISSING` / `_HISTORY_LT_120` codes. */
export function reasonLabel(code: string): string {
  if (REASONS[code]) return REASONS[code];
  const missing = code.match(/^(.+)_OBSERVATION_MISSING$/);
  if (missing) return `No ${COMPONENT_SUBJECT[missing[1]] ?? humanize(missing[1])} observation in this window`;
  const short = code.match(/^(.+)_HISTORY_LT_120$/);
  if (short) return `Fewer than 120 prior valid ${COMPONENT_SUBJECT[short[1]] ?? humanize(short[1])} observations`;
  return humanize(code);
}

/** Reason fields may carry several `;`-separated codes. */
export function splitReasons(value: string | null | undefined): string[] {
  return value ? value.split(";").map((part) => part.trim()).filter(Boolean) : [];
}

const RELIABILITY: Record<string, { label: string; tone: "ok" | "warn" | "bad" }> = {
  BASELINE_PROXY_ONLY: { label: "Baseline proxy", tone: "ok" },
  EXTRAPOLATION: { label: "Extrapolation", tone: "warn" },
  EXTREME_EXTRAPOLATION: { label: "Extreme extrapolation", tone: "bad" },
  INSUFFICIENT_DATA: { label: "Insufficient data", tone: "bad" },
  INSUFFICIENT_LIQUIDITY: { label: "Insufficient liquidity", tone: "bad" },
  INSUFFICIENT_DATA_AND_LIQUIDITY: { label: "Insufficient data & liquidity", tone: "bad" },
};

export function reliabilityLabel(code: string): { label: string; tone: "ok" | "warn" | "bad" } {
  return RELIABILITY[code] ?? { label: humanize(code), tone: "warn" };
}

export const REGIME_LABEL: Record<Regime, string> = {
  NORMAL: "Normal",
  WATCH: "Watch",
  STRESSED: "Stressed",
  CRITICAL: "Critical",
  INDETERMINATE: "Indeterminate",
};

export const OPERATIONAL_LABEL: Record<OperationalRisk, string> = {
  LOW: "Low",
  MODERATE: "Moderate",
  HIGH: "High",
  EXTREME: "Extreme",
  INDETERMINATE: "Indeterminate",
};

export const SCENARIO_INFO: Record<Scenario, { label: string; short: string; retained: string }> = {
  NORMAL: { label: "Normal", short: "Normal", retained: "100% of observed volume" },
  STRESS_25: { label: "Stress −25%", short: "−25%", retained: "75% of observed volume" },
  STRESS_50: { label: "Stress −50%", short: "−50%", retained: "50% of observed volume" },
  STRESS_75: { label: "Stress −75%", short: "−75%", retained: "25% of observed volume" },
};

export const INTERVAL_LABEL: Record<Interval, string> = { "1m": "1 min", "5m": "5 min", "15m": "15 min" };

export const PREDICTOR_INFO: Record<Predictor, { label: string; kind: "model" | "baseline"; rule: string }> = {
  CURRENT_MARKET_RISK: { label: "Current Market Risk", kind: "model", rule: "A combined view of current market conditions." },
  FORWARD_LIQUIDITY_RISK: { label: "Forward Liquidity Risk", kind: "model", rule: "A directional view of changing liquidity conditions." },
  LOW_CURRENT_VOLUME: { label: "Low current volume", kind: "baseline", rule: "A simple low-activity comparison." },
  RECENT_VOLUME_DROP: { label: "Recent volume drop", kind: "baseline", rule: "A simple comparison based on recent activity." },
  PERSISTENCE_PREVIOUS_CONDITION: { label: "Persistence", kind: "baseline", rule: "A simple comparison based on the previous condition." },
  PREVALENCE_TRAINING: { label: "Training prevalence", kind: "baseline", rule: "A simple comparison based on historical event frequency." },
};

export const EVENT_INFO: Record<BacktestEvent, { label: string; description: string }> = {
  ANY_DETERIORATION: { label: "Any deterioration", description: "A combined view of weaker activity, higher volatility, or higher estimated exit cost." },
  LOW_VOLUME_EVENT: { label: "Low volume", description: "Periods where activity weakened within the selected horizon." },
  HIGH_VOLATILITY_EVENT: { label: "High volatility", description: "Periods where price movement intensified within the selected horizon." },
  HIGH_EXIT_COST_EVENT: { label: "High exit cost", description: "Periods where estimated exit costs rose within the selected horizon." },
};

export const SAMPLE_INFO: Record<BacktestSample, { label: string; description: string }> = {
  MATCHED: { label: "Matched", description: "Uses a consistent set of historical observations for comparison." },
  NON_OVERLAPPING_MATCHED: { label: "Non-overlapping", description: "Uses separated historical windows for a cleaner comparison." },
  STRICT_COMPLETE_MATCHED: { label: "Strict complete", description: "Uses only windows with complete future measurements." },
  ALL_AVAILABLE: { label: "All available", description: "Uses each predictor's available historical observations." },
};

export function humanize(code: string): string {
  const text = code.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}
