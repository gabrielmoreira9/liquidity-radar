import Link from "next/link";
import { getOverview, getHistory } from "@/lib/api/server";
import {
  Heading,
  Failure,
  Provenance,
  Metric,
  RiskCard,
  AsOf,
} from "@/components/ui";
import { LiquidityChart } from "@/components/charts";
import { formatUsdc, formatInt } from "@/lib/format";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ as_of?: string }>;
}) {
  const { as_of } = await searchParams;
  const [r, h] = await Promise.all([
    getOverview(as_of),
    getHistory("1m", undefined, undefined, as_of),
  ]);
  return (
    <>
      <Heading
        eyebrow="THE RESEARCH TERMINAL"
        title="Liquidity overview"
        description="A historical perspective on the market beneath the price."
      />
      <form className="filters">
        <AsOf value={as_of} />
        <button className="button">Apply snapshot</button>
      </form>
      {!r.ok ? (
        <Failure result={r} />
      ) : (
        <>
          <Provenance meta={r.value.meta} />
          <div className="four-col overview-metrics">
            <Metric
              label="Observed turnover"
              value={formatUsdc(r.value.data.volume_usdc)}
              note="All closed windows in selected coverage"
            />
            <Metric
              label="Classified swaps"
              value={formatInt(r.value.data.swap_count)}
              note="Observed swap activity"
            />
            <Metric
              label="Net flow"
              value={formatUsdc(r.value.data.net_flow_usdc, { signed: true })}
              note="Observed directional flow"
            />
            <Metric
              label="Closed windows"
              value={formatInt(r.value.data.closed_window_count_1m)}
              note={`${r.value.data.empty_window_count_1m} quiet · ${r.value.data.missing_volatility_count_1m} incomplete`}
            />
          </div>
          <section className="panel overview-chart">
            <div className="section-top">
              <div>
                <div className="eyebrow">OBSERVED ACTIVITY</div>
                <h2>Liquidity through time</h2>
              </div>
              <Link href="/history" className="text-link">
                Explore history ↗
              </Link>
            </div>
            {h.ok ? (
              <>
                <LiquidityChart items={h.value.data.items} />
                {h.value.truncated && (
                  <p>Series truncated. Narrow the history range.</p>
                )}
              </>
            ) : (
              <Failure result={h} />
            )}
            <p className="fine">
              Historical windows · UTC · Turnover is an activity measure, not
              executable depth.
            </p>
          </section>
          <div className="two-col">
            <RiskCard risk={r.value.data.current_market_risk} />
            <RiskCard risk={r.value.data.forward_liquidity_risk} forward />
          </div>
        </>
      )}
    </>
  );
}
