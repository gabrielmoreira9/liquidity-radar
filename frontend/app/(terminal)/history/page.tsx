import { getHistory } from "@/lib/api/server";
import { Heading, Failure, Provenance, AsOf, Notice } from "@/components/ui";
import { LiquidityChart } from "@/components/charts";
import { INTERVALS, isOneOf } from "@/lib/api/params";
import {
  formatUsdc,
  formatUtcDateTime,
  formatScore,
  formatScientific,
} from "@/lib/format";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{
    interval?: string;
    start?: string;
    end?: string;
    as_of?: string;
  }>;
}) {
  const q = await searchParams;
  const interval = isOneOf(INTERVALS, q.interval) ? q.interval : "1m";
  const r = await getHistory(interval, q.start, q.end, q.as_of);
  return (
    <>
      <Heading
        eyebrow="HISTORICAL ACTIVITY"
        title="Historical liquidity"
        description="Explore turnover, price, flow, and risk across the selected period."
      />
      <form className="filters">
        <label>
          Interval
          <select name="interval" defaultValue={interval}>
            {INTERVALS.map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        <label>
          Start date (UTC)
          <input
            name="start"
            defaultValue={q.start}
            placeholder="ISO 8601 with Z"
          />
        </label>
        <label>
          End date (UTC)
          <input
            name="end"
            defaultValue={q.end}
            placeholder="ISO 8601 with Z"
          />
        </label>
        <AsOf value={q.as_of} />
        <button className="button">Apply range</button>
      </form>
      {!r.ok ? (
        <Failure result={r} />
      ) : (
        <>
          <Provenance meta={r.value.meta} />
          {r.value.truncated && (
            <Notice>
              Showing {r.value.data.items.length} of{" "}
              {r.value.data.pagination.total} windows. Narrow the date range to
              see more.
            </Notice>
          )}
          <div className="two-col">
            {(["volume", "price", "flow", "risk"] as const).map((kind) => (
              <section className="panel" key={kind}>
                <h2>
                  {
                    {
                      volume: "Observed turnover",
                      price: "Closing price",
                      flow: "Net flow",
                      risk: "Risk at close",
                    }[kind]
                  }
                </h2>
                <LiquidityChart items={r.value.data.items} kind={kind} />
              </section>
            ))}
          </div>
          <Notice>
            Risk values are historical snapshots recorded at each window close.
            Gaps indicate unavailable readings.
          </Notice>
          <details className="panel">
            <summary>
              Inspect exact observations · {r.value.data.items.length} windows
            </summary>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    {[
                      "Open (UTC)",
                      "Volume (USDC)",
                      "Close (USDC/SOL)",
                      "Volatility",
                      "Data quality",
                      "Market / 100",
                      "Forward / 100",
                    ].map((x) => (
                      <th key={x}>{x}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {r.value.data.items.map((w) => (
                    <tr key={w.timestamp}>
                      <td>{formatUtcDateTime(w.timestamp)}</td>
                      <td>
                        {formatUsdc(w.volume_usdc, {
                          compact: false,
                          unit: false,
                        })}
                      </td>
                      <td>{w.price_close_usdc_per_sol ?? "—"}</td>
                      <td>{formatScientific(w.volatility_std_log_return)}</td>
                      <td>
                        {w.volatility_status === "AVAILABLE" &&
                        w.flow_status === "AVAILABLE"
                          ? "Complete"
                          : "Partial"}
                      </td>
                      <td>
                        {formatScore(
                          w.risk_at_close?.current_market_risk_score_0_100,
                        )}
                      </td>
                      <td>
                        {formatScore(
                          w.risk_at_close?.forward_liquidity_risk_score_0_100,
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </>
  );
}
