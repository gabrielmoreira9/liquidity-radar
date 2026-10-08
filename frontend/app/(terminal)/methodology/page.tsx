import { Heading, Notice } from "@/components/ui";
export default function Page() {
  return (
    <>
      <Heading
        eyebrow="TRANSPARENCY / MODEL NOTES"
        title="Know what you are measuring"
        description="A useful model starts with an honest account of its boundaries."
      />
      <Notice>
        Historical analysis for one SOL/USDC pool. Forward risk is experimental;
        estimated exit cost is a model output.
      </Notice>
      <div className="methodology">
        <section className="panel">
          <div className="eyebrow">01 / OBSERVATIONS</div>
          <h2>Activity is not executable depth.</h2>
          <p>
            Only structurally classified SIMPLE_SWAP records enter the
            historical aggregations. Structural validation checks
            classification, unit/price identities, and dataset integrity; it
            does not validate executable liquidity. The demo preserves original
            causal scores from a real two-hour excerpt. The full backtest covers
            four UTC days.
          </p>
          <p>
            Volume, swap counts, price, and directional flow describe observed
            activity. Empty windows remain explicit. Missing volatility is never
            replaced by zero. Within-window volatility is the sample standard
            deviation of log returns, without annualization.
          </p>
        </section>
        <section className="panel">
          <div className="eyebrow">02 / CAUSAL INDICATORS</div>
          <h2>Only information available at the decision.</h2>
          <p>
            Percentile components use strictly earlier valid observations in an
            expanding history, with at least 120 prior values per component. The
            Current Market Risk score equally averages volatility, inverted
            volume, absolute flow imbalance, and reference exit-cost risk for
            100K USDC / NORMAL. No partial means or arbitrary weights are used.
          </p>
          <p>
            Forward Liquidity Risk equally averages liquidity and flow
            components. It is an exploratory indicator, not a calibrated
            probability. NORMAL &lt; 40; WATCH 40–60; STRESSED 60–80; CRITICAL ≥
            80. These boundaries are heuristic. Missing essential information
            yields INDETERMINATE.
          </p>
          <p>
            A timestamp marks window open; available_at marks theoretical close,
            not measured ingestion latency or Solana finality. Scores on 5m/15m
            charts remain original 1m snapshots at bar close.
          </p>
        </section>
        <section className="panel">
          <div className="eyebrow">03 / MODELED ESTIMATES</div>
          <h2>The square-root baseline.</h2>
          <pre>
            available volume = observed volume × scenario multiplier
            <br />
            participation = position size / available volume
            <br />
            estimated exit cost (%) = 100 × volatility × √participation
          </pre>
          <p>
            NORMAL retains 100% of volume; STRESS_25, STRESS_50, and STRESS_75
            retain 75%, 50%, and 25%. A percent field of 0.5 means 0.5%. Costs
            above 100% remain visible for audit.
          </p>
          <p>
            The model excludes spread, fees, execution duration, and actual
            routes. Participation above observed turnover is extrapolation.
            Operational risk uses participation/cost boundaries of 0.5/1/2 with
            the highest triggered severity. Model reliability is separate: low
            estimated cost or LOW operational risk never establishes execution
            confidence.
          </p>
        </section>
        <section className="panel">
          <div className="eyebrow">04 / VALIDATION</div>
          <h2>Evidence over conviction.</h2>
          <p>
            Training thresholds and quartile boundaries are frozen from June
            30–July 2, 2026. The chronological test day is July 3, at
            5/15/30/60-minute horizons. Future targets begin after the decision
            close. Unknown measurements are not “no event.”
          </p>
          <p>
            Both indicators are compared against current low volume, recent
            volume drop, previous-condition persistence, and training
            prevalence. Discrimination is modest. Long horizons saturate
            prevalence; precision alone can mislead. The test day was previously
            inspected, so this is not untouched prospective validation.
            Overlapping targets are dependent; the non-overlapping sample is
            small. Lead times deduplicate first-breach minutes, not independent
            physical episodes.
          </p>
        </section>
      </div>
    </>
  );
}
