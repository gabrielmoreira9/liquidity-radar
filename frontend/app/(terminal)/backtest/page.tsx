import Link from "next/link";
import { getBacktest } from "@/lib/api/server";
import { Heading, Failure, Provenance, Notice, Metric } from "@/components/ui";
import {
  BACKTEST_EVENTS,
  BACKTEST_SAMPLES,
  BACKTEST_HORIZONS,
  PREDICTORS,
  isOneOf,
} from "@/lib/api/params";
import { EVENT_INFO, SAMPLE_INFO, PREDICTOR_INFO } from "@/lib/labels";
import {
  formatFraction,
  formatDecimal,
  formatMultiple,
  formatMinutes,
  formatUtcDateTime,
} from "@/lib/format";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{
    event?: string;
    sample?: string;
    horizon?: string;
    predictor?: string;
  }>;
}) {
  const q = await searchParams;
  const event = isOneOf(BACKTEST_EVENTS, q.event)
    ? q.event
    : "ANY_DETERIORATION";
  const sample = isOneOf(BACKTEST_SAMPLES, q.sample) ? q.sample : "MATCHED";
  const horizon = isOneOf(BACKTEST_HORIZONS, Number(q.horizon))
    ? Number(q.horizon)
    : 5;
  const r = await getBacktest(event, sample);
  const rows = r.ok
    ? r.value.data.items.filter(
        (x) =>
          x.horizon_minutes === horizon &&
          (!q.predictor || x.predictor === q.predictor),
      )
    : [];
  return (
    <>
      <Heading
        eyebrow="SIGNAL REVIEW"
        title="Challenge the signal"
        description="Review how the indicators performed across historical periods."
      />
      <form className="filters">
        <label>
          Target event
          <select name="event" defaultValue={event}>
            {BACKTEST_EVENTS.map((v) => (
              <option key={v} value={v}>
                {EVENT_INFO[v].label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Sample
          <select name="sample" defaultValue={sample}>
            {BACKTEST_SAMPLES.map((v) => (
              <option key={v} value={v}>
                {SAMPLE_INFO[v].label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Horizon
          <select name="horizon" defaultValue={horizon}>
            {BACKTEST_HORIZONS.map((v) => (
              <option key={v} value={v}>
                {v} minutes
              </option>
            ))}
          </select>
        </label>
        <label>
          Predictor
          <select name="predictor" defaultValue={q.predictor ?? ""}>
            <option value="">All predictors</option>
            {PREDICTORS.map((v) => (
              <option key={v} value={v}>
                {PREDICTOR_INFO[v].label}
              </option>
            ))}
          </select>
        </label>
        <button className="button">Explore results</button>
      </form>
      <p className="muted">{EVENT_INFO[event].description}</p>
      {!r.ok ? (
        <Failure result={r} />
      ) : (
        <>
          <Provenance meta={r.value.meta} />
          <Notice>
            Historical comparison of signal behavior.
          </Notice>
          <div className="three-col">
            <Metric
              label="Reference period"
              value={formatUtcDateTime(r.value.data.train_start)}
              note="Historical comparison begins"
            />
            <Metric
              label="Test begins"
              value={formatUtcDateTime(r.value.data.test_start)}
              note={`Through ${formatUtcDateTime(r.value.data.test_end_exclusive)}`}
            />
            <Metric
              label="Result available"
              value={formatUtcDateTime(r.value.data.result_available_at)}
              note="Results from historical data"
            />
          </div>
          <section className="panel">
            <h2>Predictive performance</h2>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    {[
                      "Predictor",
                      "Precision",
                      "Recall",
                      "False alarms",
                      "Event rate",
                      "Lift",
                      "Ranking",
                      "Average precision",
                      "Samples",
                    ].map((v) => (
                      <th key={v}>{v}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((x) => (
                    <tr key={x.predictor}>
                      <td>
                        {PREDICTOR_INFO[x.predictor].label}
                        <small>{PREDICTOR_INFO[x.predictor].kind}</small>
                      </td>
                      <td>{formatFraction(x.precision)}</td>
                      <td>{formatFraction(x.recall)}</td>
                      <td>{formatFraction(x.false_positive_rate)}</td>
                      <td>{formatFraction(x.event_rate)}</td>
                      <td>{formatMultiple(x.lift)}</td>
                      <td>{formatDecimal(x.roc_auc)}</td>
                      <td>{formatDecimal(x.pr_auc_average_precision)}</td>
                      <td>
                        {x.n_valid} / {x.n_excluded}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!rows.length && <p>No results match these filters.</p>}
          </section>
          <div className="two-col">
            {rows.map((x) => (
              <details className="panel" key={x.predictor}>
                <summary>
                  {PREDICTOR_INFO[x.predictor].label} · More context
                </summary>
                <p>{PREDICTOR_INFO[x.predictor].rule}</p>
                <div className="detail-grid">
                  <div>
                    Positive matches<strong>{x.tp}</strong>
                  </div>
                  <div>
                    False alarms<strong>{x.fp}</strong>
                  </div>
                  <div>
                    Correct non-events<strong>{x.tn}</strong>
                  </div>
                  <div>
                    Missed events<strong>{x.fn}</strong>
                  </div>
                </div>
                <p>
                  Lead time median {formatMinutes(x.lead_time_median_minutes)} ·
                  P25 {formatMinutes(x.lead_time_p25_minutes)} · P75{" "}
                  {formatMinutes(x.lead_time_p75_minutes)}
                </p>
                <p>
                  Observations {x.n_candidates} · Events {x.event_count} ·
                  Alert rate {formatFraction(x.alert_rate)}
                </p>
                <p>
                  {x.n_excluded_incomplete_horizon} observations had incomplete
                  windows and were left out.
                </p>
                <p>{x.auc_unavailable_reason}</p>
              </details>
            ))}
          </div>
          <section className="panel">
            <h2>Put the results in context</h2>
            <p>
              These comparisons describe historical behavior, not a promise
              about future market conditions.
            </p>
            <Link className="story-link" href="/methodology">
              Read the methodology ↗
            </Link>
          </section>
        </>
      )}
    </>
  );
}
