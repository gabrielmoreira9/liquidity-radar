import Link from "next/link";
import { AlertTriangle, ArrowUpRight, Database } from "lucide-react";
import type { ApiResult } from "@/lib/api/client";
import type {
  Metadata,
  MarketRisk,
  ForwardRisk,
  PositionExit,
} from "@/lib/contracts";
import {
  formatScore,
  formatUtcDateTime,
  formatPct,
  formatMultiple,
  formatUsdc,
} from "@/lib/format";
import { humanize, driverLabel } from "@/lib/labels";
export function Heading({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <div className="page-heading">
      <div className="eyebrow">{eyebrow}</div>
      <h1>
        {title}
        <span className="cyan">.</span>
      </h1>
      <p>{description}</p>
    </div>
  );
}
export function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="notice">
      <AlertTriangle size={17} />
      <div>{children}</div>
    </div>
  );
}
export function Failure({
  result,
}: {
  result: Extract<ApiResult<unknown>, { ok: false }>;
}) {
  const e = result.error;
  return (
    <section className="panel" role="alert">
      <h2>Historical data unavailable</h2>
      <p>{e.kind === "timeout" ? "The analytics API timed out." : e.message}</p>
      <p className="muted">
        {e.kind === "http" ? `${e.status} · ${e.code}` : humanize(e.kind)} · No
        substitute market data is shown. Check the backend or adjust your
        filters.
      </p>
      <Link className="button" href="?">
        Reset & retry <ArrowUpRight size={16} />
      </Link>
    </section>
  );
}
export function Provenance({ meta }: { meta: Metadata }) {
  return (
    <div className="provenance">
      <Database size={16} />
      <div>
        <strong>
          {meta.data_kind} / {meta.dataset_mode}
        </strong>{" "}
        · Reference {formatUtcDateTime(meta.reference_at)}
        <p>{meta.coverage_notice}</p>
        <details>
          <summary>Data details</summary>
          <p>
            Coverage: {formatUtcDateTime(meta.available_period.window_start)} →{" "}
            {formatUtcDateTime(meta.available_period.window_end_exclusive)}{" "}
            (exclusive)
          </p>
          <p>
            Risk version: {meta.risk_version} · {meta.availability_basis}
          </p>
          {Object.entries(meta.units).map(([k, v]) => (
            <p key={k}>
              {k}: {v}
            </p>
          ))}
        </details>
      </div>
    </div>
  );
}
export function Metric({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note: string;
}) {
  return (
    <div className="panel metric">
      <div className="eyebrow">{label}</div>
      <strong>{value}</strong>
      <p>{note}</p>
    </div>
  );
}
export function RiskCard({
  risk,
  forward = false,
}: {
  risk: MarketRisk | ForwardRisk;
  forward?: boolean;
}) {
  return (
    <section className="panel">
      <div className="section-top">
        <h2>{forward ? "Forward Liquidity Risk" : "Current Market Risk"}</h2>
        <span className="badge">
          {forward ? "EXPERIMENTAL" : "HISTORICAL"}
        </span>
      </div>
      <div className="risk-number">
        {formatScore(risk.score_0_100)}
        <small>/ 100</small>
        <span className={`badge regime-${risk.regime.toLowerCase()}`}>
          {humanize(risk.regime)}
        </span>
      </div>
      <div className="score-track">
        <span style={{ width: `${risk.score_0_100 ?? 0}%` }} />
      </div>
      <p className="muted">
        {forward
          ? "Directional signal for changing liquidity conditions."
          : "Severity of conditions in the selected historical window."}
      </p>
      {risk.unavailable_reason && (
        <Notice>Some inputs were unavailable for this reading.</Notice>
      )}
      <div className="components">
        {Object.entries(risk.components).map(([k, v]) => (
          <div key={k}>
            <span>{humanize(k.replace(/_0_100$/, ""))}</span>
            <strong>{formatScore(v)}</strong>
            <div className="component-track">
              <i style={{ width: `${v ?? 0}%` }} />
            </div>
          </div>
        ))}
      </div>
      <p className="muted">
        Main drivers: {driverLabel(risk.primary_risk_driver) ?? "Unavailable"}{" "}
        and {driverLabel(risk.secondary_risk_driver) ?? "Unavailable"}
      </p>
      <p className="fine">Historical reading · Available {formatUtcDateTime(risk.available_at)}.</p>
      {risk.interpretation && (
        <p className="fine">{humanize(risk.interpretation)}</p>
      )}
    </section>
  );
}
export function PositionDetail({ p }: { p: PositionExit }) {
  return (
    <section className="panel">
      <div className="section-top">
        <h2>{formatUsdc(p.position_size_usdc)} exit analysis</h2>
        <span className="badge">ESTIMATED</span>
      </div>
      <div className="risk-number">{formatPct(p.estimated_exit_cost_pct)}</div>
      <p>
        Estimated exit cost · {humanize(p.operational_risk)} operational risk
      </p>
      <div className="detail-grid">
        <div>
          Participation
          <strong>{formatMultiple(p.participation_rate_ratio)}</strong>
        </div>
        <div>
          Causal liquidity multiple
          <strong>{formatMultiple(p.liquidity_multiple_causal_ratio)}</strong>
        </div>
        <div>
          Scenario volume<strong>{formatUsdc(p.available_volume_usdc)}</strong>
        </div>
      </div>
      {(p.unavailable_reason ||
        p.extrapolation_reason ||
        p.liquidity_multiple_unavailable_reason) && (
        <p className="fine">Some inputs were unavailable.</p>
      )}
      <p className="fine">
        Window opens {formatUtcDateTime(p.timestamp)} · Available{" "}
        {formatUtcDateTime(p.available_at)}
      </p>
    </section>
  );
}
export function AsOf({ value }: { value?: string }) {
  return (
    <label>
      Historical date (UTC)
      <input
        name="as_of"
        defaultValue={value}
        placeholder="Latest historical close"
        aria-label="As of (UTC ISO 8601)"
      />
    </label>
  );
}
