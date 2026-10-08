import { getPositions, getExitCost } from "@/lib/api/server";
import {
  Heading,
  Failure,
  Provenance,
  AsOf,
  PositionDetail,
} from "@/components/ui";
import { SCENARIOS, POSITION_SIZES_USDC, isOneOf } from "@/lib/api/params";
import { SCENARIO_INFO, humanize } from "@/lib/labels";
import { formatUsdc, formatPct, formatMultiple } from "@/lib/format";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{
    scenario?: string;
    position_size?: string;
    as_of?: string;
  }>;
}) {
  const q = await searchParams;
  const scenario = isOneOf(SCENARIOS, q.scenario) ? q.scenario : "NORMAL";
  const n = Number(q.position_size ?? 100000);
  const size = isOneOf(POSITION_SIZES_USDC, n) ? n : 100000;
  const [r, e] = await Promise.all([
    getPositions(scenario, q.as_of),
    getExitCost(size, scenario, q.as_of),
  ]);
  return (
    <>
      <Heading
        eyebrow="POSITION EXPOSURE"
        title="Size changes everything"
        description="Compare exposure across position sizes and market scenarios."
      />
      <form className="filters">
        <label>
          Position size
          <select name="position_size" defaultValue={size}>
            {POSITION_SIZES_USDC.map((s) => (
              <option value={s} key={s}>
                {formatUsdc(s)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Scenario
          <select name="scenario" defaultValue={scenario}>
            {SCENARIOS.map((s) => (
              <option value={s} key={s}>
                {SCENARIO_INFO[s].label}
              </option>
            ))}
          </select>
        </label>
        <AsOf value={q.as_of} />
        <button className="button">Analyze position</button>
      </form>
      {r.ok ? <Provenance meta={r.value.meta} /> : <Failure result={r} />}
      <p className="muted">
        Selected scenario retains {SCENARIO_INFO[scenario].retained}.
      </p>
      {e.ok ? <PositionDetail p={e.value.data} /> : <Failure result={e} />}{" "}
      {r.ok && (
        <section className="panel">
          <h2>Position comparison / {SCENARIO_INFO[scenario].label}</h2>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {[
                    "Position",
                    "Estimated cost",
                    "Participation",
                    "Operational risk",
                    "Context",
                  ].map((s) => (
                    <th key={s}>{s}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {r.value.data.items.map((p) => (
                  <tr
                    key={p.position_size_usdc}
                    className={
                      p.position_size_usdc === size ? "selected-row" : ""
                    }
                  >
                    <td>{formatUsdc(p.position_size_usdc)}</td>
                    <td>{formatPct(p.estimated_exit_cost_pct)}</td>
                    <td>{formatMultiple(p.participation_rate_ratio)}</td>
                    <td>{humanize(p.operational_risk)}</td>
                    <td>
                      {p.extrapolation_warning ||
                      p.insufficient_liquidity_flag ||
                      p.insufficient_data_flag
                        ? "Limited history"
                        : "Historical"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
