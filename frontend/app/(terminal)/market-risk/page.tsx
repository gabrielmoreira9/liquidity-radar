import Link from "next/link";
import { getHistory, getMarketRisk } from "@/lib/api/server";
import { Heading, Failure, Provenance, AsOf } from "@/components/ui";
import { LiquidityChart } from "@/components/charts";
import {
  formatScore,
  formatUsdc,
  formatUtcDateTime,
} from "@/lib/format";
import { driverLabel, humanize } from "@/lib/labels";

function average(values: Array<number | null | undefined>): number | null {
  const usable = values.filter(
    (value): value is number => typeof value === "number" && Number.isFinite(value),
  );
  return usable.length
    ? usable.reduce((total, value) => total + value, 0) / usable.length
    : null;
}

function median(values: Array<number | null | undefined>): number | null {
  const usable = values
    .filter(
      (value): value is number =>
        typeof value === "number" && Number.isFinite(value),
    )
    .sort((a, b) => a - b);
  if (!usable.length) return null;
  const middle = Math.floor(usable.length / 2);
  return usable.length % 2
    ? usable[middle]
    : (usable[middle - 1] + usable[middle]) / 2;
}

function deltaLabel(value: number | null, unit = "") {
  if (value === null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}${unit}`;
}

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ as_of?: string }>;
}) {
  const { as_of } = await searchParams;
  const [riskResult, historyResult] = await Promise.all([
    getMarketRisk(as_of),
    getHistory("1m", undefined, undefined, as_of),
  ]);

  if (!riskResult.ok) {
    return (
      <>
        <Heading
          eyebrow="MARKET RISK"
          title="Read the conditions"
          description="A focused view of historical market risk and its drivers."
        />
        <form className="filters">
          <AsOf value={as_of} />
          <button className="button">Apply snapshot</button>
        </form>
        <Failure result={riskResult} />
      </>
    );
  }

  const risk = riskResult.value.data;
  const items = historyResult.ok ? historyResult.value.data.items : [];
  const recentItems = items.slice(-12);
  const windowMedianVolume = median(items.map((item) => item.volume_usdc));
  const recentAverageVolume = average(
    recentItems.map((item) => item.volume_usdc),
  );
  const windowAverageFlow = average(items.map((item) => item.net_flow_usdc));
  const recentAverageFlow = average(
    recentItems.map((item) => item.net_flow_usdc),
  );
  const recentRisk = average(
    recentItems.map(
      (item) => item.risk_at_close?.current_market_risk_score_0_100,
    ),
  );

  return (
    <>
      <Heading
        eyebrow="MARKET RISK"
        title="Read the conditions"
        description="A focused view of historical market risk and the liquidity signals behind it."
      />
      <form className="filters">
        <AsOf value={as_of} />
        <button className="button">Apply snapshot</button>
      </form>
      <Provenance meta={riskResult.value.meta} />

      <section className="risk-hero-panel">
        <div>
          <div className="eyebrow">CURRENT HISTORICAL SNAPSHOT</div>
          <div className="risk-hero-score">
            <strong>{formatScore(risk.score_0_100)}</strong>
            <span>/ 100</span>
          </div>
          <div className={`risk-state regime-${risk.regime.toLowerCase()}`}>
            {humanize(risk.regime)}
          </div>
          <p className="risk-hero-copy">
            {risk.interpretation
              ? humanize(risk.interpretation)
              : "Market conditions summarized across liquidity, flow, volatility, and estimated impact."}
          </p>
          <p className="fine">
            Available {formatUtcDateTime(risk.available_at)} · Historical
            observation
          </p>
        </div>
        <div className="risk-hero-drivers">
          <span className="eyebrow">PRIMARY DRIVERS</span>
          <div>
            <strong>
              {driverLabel(risk.primary_risk_driver) ?? "Unavailable"}
            </strong>
            <strong>
              {driverLabel(risk.secondary_risk_driver) ?? "Unavailable"}
            </strong>
          </div>
          <Link className="text-link" href="/methodology">
            Understand the methodology ↗
          </Link>
        </div>
      </section>

      <section className="panel risk-history-panel">
        <div className="section-top">
          <div>
            <div className="eyebrow">HISTORICAL TREND</div>
            <h2>Market risk through time</h2>
            <p className="muted">
              One-minute historical snapshots across the available observation
              window.
            </p>
          </div>
          <Link href="/history" className="text-link">
            Explore all history ↗
          </Link>
        </div>
        {historyResult.ok ? (
          <div className="risk-history-chart">
            <LiquidityChart items={items} kind="risk" />
          </div>
        ) : (
          <Failure result={historyResult} />
        )}
      </section>

      <div className="two-col market-analytics-grid">
        <section className="panel">
          <div className="eyebrow">RISK COMPONENTS</div>
          <h2>What is shaping the score</h2>
          <div className="risk-component-list">
            {Object.entries(risk.components).map(([key, value]) => (
              <div className="risk-component" key={key}>
                <div>
                  <span>{humanize(key.replace(/_0_100$/, ""))}</span>
                  <strong>{formatScore(value)}</strong>
                </div>
                <div className="component-track">
                  <i style={{ width: `${value ?? 0}%` }} />
                </div>
              </div>
            ))}
          </div>
          {risk.unavailable_reason && (
            <p className="fine">Some inputs were unavailable for this reading.</p>
          )}
        </section>

        <section className="panel">
          <div className="eyebrow">LIQUIDITY STRESS</div>
          <h2>Recent activity versus the window</h2>
          <div className="stress-metrics">
            <div>
              <span>Recent turnover</span>
              <strong>{formatUsdc(recentAverageVolume)}</strong>
              <small>
                vs median {formatUsdc(windowMedianVolume)}
              </small>
            </div>
            <div>
              <span>Recent net flow</span>
              <strong>{formatUsdc(recentAverageFlow, { signed: true })}</strong>
              <small>
                Window average {formatUsdc(windowAverageFlow, { signed: true })}
              </small>
            </div>
            <div>
              <span>Recent risk</span>
              <strong>{formatScore(recentRisk)}</strong>
              <small>
                {recentRisk === null || risk.score_0_100 === null
                  ? "Historical reading"
                  : `${deltaLabel(recentRisk - risk.score_0_100)} vs current`}
              </small>
            </div>
          </div>
          {historyResult.ok && (
            <div className="stress-chart">
              <LiquidityChart items={items} kind="volume" />
            </div>
          )}
          <p className="fine">
            Recent activity uses the latest 12 closed windows available in the
            selected snapshot.
          </p>
        </section>
      </div>

      <section className="panel risk-interpretation">
        <div>
          <div className="eyebrow">ANALYTICAL READ</div>
          <h2>Context before conviction</h2>
        </div>
        <p>
          {risk.primary_risk_driver || risk.secondary_risk_driver
            ? `${driverLabel(risk.primary_risk_driver) ?? "The leading signal"} and ${driverLabel(risk.secondary_risk_driver) ?? "secondary conditions"} are the clearest contributors to the current reading.`
            : "The current reading is based on the available historical inputs."}{" "}
          Compare the score with its historical path before drawing conclusions
          about changing liquidity conditions.
        </p>
      </section>
    </>
  );
}
