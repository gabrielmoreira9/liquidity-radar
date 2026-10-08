/**
 * Every number and timestamp shown in the UI goes through this module.
 * Units follow `meta.units` from the API:
 *  - `_pct` values are already percentages (0.5 means 0.5%), never multiplied by 100;
 *  - backtest rates/AUC are fractions 0..1;
 *  - `_0_100` scores are empirical percentiles, not probabilities;
 *  - timestamps are UTC.
 * Null, undefined and non-finite values render as an em dash, never as zero.
 */

export const NULL_DISPLAY = "—";
const MINUS = "−";

export function isNum(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function sign(value: number, text: string, signed: boolean): string {
  if (value < 0) return `${MINUS}${text}`;
  return signed && value > 0 ? `+${text}` : text;
}

function compactNumber(abs: number): string {
  const units: [number, string][] = [
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  for (const [size, suffix] of units) {
    if (abs >= size) {
      const scaled = abs / size;
      const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2;
      return `${trimZeros(scaled.toFixed(digits))}${suffix}`;
    }
  }
  return abs >= 100 ? abs.toFixed(0) : trimZeros(abs.toFixed(2));
}

function trimZeros(text: string): string {
  return text.includes(".") ? text.replace(/\.?0+$/, "") : text;
}

const grouped = (digits: number) =>
  new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** USDC amount. Compact for headlines (`4.14M`), full precision for tooltips. */
export function formatUsdc(
  value: number | null | undefined,
  { compact = true, signed = false, unit = true }: { compact?: boolean; signed?: boolean; unit?: boolean } = {},
): string {
  if (!isNum(value)) return NULL_DISPLAY;
  const abs = Math.abs(value);
  const body = compact ? compactNumber(abs) : grouped(abs >= 1000 ? 0 : 2).format(abs);
  return `${sign(value, body, signed)}${unit ? " USDC" : ""}`;
}

/** Position size label, e.g. `100K`. */
export function formatSize(value: number): string {
  return compactNumber(value);
}

/** Integer counts with grouping. */
export function formatInt(value: number | null | undefined): string {
  return isNum(value) ? grouped(0).format(value) : NULL_DISPLAY;
}

/**
 * A field already expressed in percent (`*_pct`). Small values keep three significant digits so a
 * 0.00394% estimate is not rounded to 0.00%; large values (including >100% modeled costs) are kept whole.
 */
export function formatPct(value: number | null | undefined, { signed = false }: { signed?: boolean } = {}): string {
  if (!isNum(value)) return NULL_DISPLAY;
  const abs = Math.abs(value);
  let body: string;
  if (abs === 0) body = "0";
  else if (abs < 1) body = trimZeros(abs.toPrecision(3));
  else if (abs < 100) body = trimZeros(abs.toFixed(2));
  else body = grouped(1).format(abs);
  return `${sign(value, body, signed)}%`;
}

/** A fraction 0..1 (backtest rates) displayed as a percentage. */
export function formatFraction(value: number | null | undefined, digits = 1): string {
  if (!isNum(value)) return NULL_DISPLAY;
  return `${(value * 100).toFixed(digits)}%`;
}

/** AUC-style statistic: decimal, three places, never a percentage. */
export function formatDecimal(value: number | null | undefined, digits = 3): string {
  return isNum(value) ? value.toFixed(digits) : NULL_DISPLAY;
}

/** Empirical percentile score on 0..100. */
export function formatScore(value: number | null | undefined): string {
  return isNum(value) ? value.toFixed(1) : NULL_DISPLAY;
}

/** Dimensionless multiple (participation, lift, liquidity multiple), e.g. `9.77×`. */
export function formatMultiple(value: number | null | undefined): string {
  if (!isNum(value)) return NULL_DISPLAY;
  const abs = Math.abs(value);
  const body = abs >= 100 ? grouped(1).format(abs) : abs >= 10 ? abs.toFixed(1) : abs.toFixed(2);
  return `${sign(value, body, false)}×`;
}

/** Signed dimensionless ratio in [-1, 1] (flow imbalance). */
export function formatSignedRatio(value: number | null | undefined): string {
  return isNum(value) ? sign(value, Math.abs(value).toFixed(2), true) : NULL_DISPLAY;
}

const SUPERSCRIPT: Record<string, string> = {
  "-": "⁻", "0": "⁰", "1": "¹", "2": "²", "3": "³",
  "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
};

/** Scientific notation for very small quantities such as per-minute log-return volatility. */
export function formatScientific(value: number | null | undefined, digits = 3): string {
  if (!isNum(value)) return NULL_DISPLAY;
  if (value === 0) return "0";
  const [mantissa, exponent] = value.toExponential(digits - 1).split("e");
  const exp = String(Number(exponent)).split("").map((c) => SUPERSCRIPT[c] ?? c).join("");
  return `${mantissa.replace("-", MINUS)} × 10${exp}`;
}

export function formatMinutes(value: number | null | undefined): string {
  return isNum(value) ? `${trimZeros(value.toFixed(1))} min` : NULL_DISPLAY;
}

// ---------- UTC time ----------

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n: number) => String(n).padStart(2, "0");

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** `03 Jul 2026` */
export function formatUtcDate(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? `${pad(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}` : NULL_DISPLAY;
}

/** `23:59` (UTC) */
export function formatUtcTime(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` : NULL_DISPLAY;
}

/** `03 Jul 2026 · 23:59 UTC` */
export function formatUtcDateTime(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? `${formatUtcDate(iso)} · ${formatUtcTime(iso)} UTC` : NULL_DISPLAY;
}

/** Window open → close, e.g. `03 Jul 2026 · 23:59–00:00 UTC`. */
export function formatWindow(openIso: string | null | undefined, closeIso: string | null | undefined): string {
  const open = parse(openIso);
  const close = parse(closeIso);
  if (!open || !close) return NULL_DISPLAY;
  return `${formatUtcDate(openIso)} · ${formatUtcTime(openIso)}–${formatUtcTime(closeIso)} UTC`;
}

/** Span between two instants, e.g. `2 h` or `4 d`. */
export function formatSpan(startIso: string, endIso: string): string {
  const a = parse(startIso);
  const b = parse(endIso);
  if (!a || !b) return NULL_DISPLAY;
  const minutes = Math.round((b.getTime() - a.getTime()) / 60000);
  if (minutes % 1440 === 0) return `${minutes / 1440} d`;
  if (minutes % 60 === 0) return `${minutes / 60} h`;
  return `${minutes} min`;
}

/** Canonical `YYYY-MM-DDTHH:MM:SSZ`, the form sent as `as_of`/`start`/`end`. */
export function toApiInstant(date: Date): string {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}
