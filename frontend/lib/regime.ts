import type { OperationalRisk, Regime } from "@/lib/contracts";
import { OPERATIONAL_LABEL, REGIME_LABEL } from "@/lib/labels";

/** Severity 1..4, or 0 for indeterminate. Drives the 4-segment glyph, so colour is never the only signal. */
export type Level = 0 | 1 | 2 | 3 | 4;

export const REGIME_LEVEL: Record<Regime, Level> = { INDETERMINATE: 0, NORMAL: 1, WATCH: 2, STRESSED: 3, CRITICAL: 4 };
export const OPERATIONAL_LEVEL: Record<OperationalRisk, Level> = { INDETERMINATE: 0, LOW: 1, MODERATE: 2, HIGH: 3, EXTREME: 4 };

/** CSS custom properties defined in globals.css. */
export const LEVEL_COLOR: Record<Level, string> = {
  0: "var(--risk-unknown)",
  1: "var(--risk-1)",
  2: "var(--risk-2)",
  3: "var(--risk-3)",
  4: "var(--risk-4)",
};

/** Hex values for SVG/Recharts where CSS variables are inconvenient. Kept in sync with globals.css. */
export const LEVEL_HEX: Record<Level, string> = {
  0: "#7D8799",
  1: "#4CC38A",
  2: "#E8C547",
  3: "#FF8A3D",
  4: "#FF4D5E",
};

export function regimeView(regime: Regime) {
  const level = REGIME_LEVEL[regime];
  return { level, label: REGIME_LABEL[regime], color: LEVEL_COLOR[level], hex: LEVEL_HEX[level] };
}

export function operationalView(risk: OperationalRisk) {
  const level = OPERATIONAL_LEVEL[risk];
  return { level, label: OPERATIONAL_LABEL[risk], color: LEVEL_COLOR[level], hex: LEVEL_HEX[level] };
}

/** Heuristic, uncalibrated regime boundaries documented by the backend (regime_policy). */
export const REGIME_THRESHOLDS = [40, 60, 80] as const;
