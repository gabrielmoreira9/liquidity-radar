import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";
import { ArrowUpRight, ArrowDown, ArrowRight, Plus } from "lucide-react";
import { Brand } from "@/components/navigation";
import { ScrollScene, HeroStage } from "@/components/scroll-scene";
import { LiquiditySculpture } from "@/components/liquidity-sculpture";
import { LiquidityChart } from "@/components/charts";
import { getOverview, getHistory } from "@/lib/api/server";
import { Failure, Provenance } from "@/components/ui";
import {
  formatScore,
  formatPct,
  formatMultiple,
  formatUsdc,
  formatUtcDateTime,
} from "@/lib/format";
import { humanize } from "@/lib/labels";

function Chapter({
  number,
  children,
}: {
  number: string;
  children: React.ReactNode;
}) {
  return (
    <div className="chapter">
      <span>{number}</span>
      {children}
    </div>
  );
}
async function RiskReveals() {
  await connection();
  const r = await getOverview();
  if (!r.ok)
    return (
      <div className="story-unavailable">
        <Failure result={r} />
        <p>
          Market Risk, experimental Forward Risk, and position previews require
          the historical API.
        </p>
      </div>
    );
  const {
    current_market_risk: market,
    forward_liquidity_risk: forward,
    positions_normal: positions,
  } = r.value.data;
  return (
    <>
      <ScrollScene id="market" tone="blue">
        <div className="story-split">
          <div className="story-copy">
            <Chapter number="02">MARKET RISK</Chapter>
            <h2>
              Context.
              <br />
              <span>Before conviction.</span>
            </h2>
            <p>
              A price tells you where the market went. A causal risk score puts
              the conditions around it into focus.
            </p>
            <Link className="story-link" href="/market-risk">
              Explore Market Risk <ArrowUpRight size={17} />
            </Link>
          </div>
          <div className="risk-instrument">
            <div className="instrument-top">
              <span>MARKET CONDITIONS</span>
              <span className="badge">HISTORICAL</span>
            </div>
            <div className="instrument-score">
              <strong>{formatScore(market.score_0_100)}</strong>
              <span>
                / 100
                <br />
                {humanize(market.regime)}
              </span>
            </div>
            <div className="instrument-components">
              {Object.entries(market.components).map(([key, value]) => (
                <div key={key}>
                  <span>{humanize(key.replace(/_0_100$/, ""))}</span>
                  <b>{formatScore(value)}</b>
                  <div>
                    <i style={{ width: `${value ?? 0}%` }} />
                  </div>
                </div>
              ))}
            </div>
            <p className="instrument-note">
              Historical severity reading.
            </p>
            {market.unavailable_reason && (
              <p className="story-warning">Some inputs were unavailable.</p>
            )}
          </div>
        </div>
        <div className="scene-caption">
          <span>
            {r.value.meta.data_kind} / {r.value.meta.dataset_mode}
          </span>
          <span>Available {formatUtcDateTime(market.available_at)}</span>
        </div>
      </ScrollScene>
      <ScrollScene id="forward" tone="violet">
        <div className="forward-composition">
          <Chapter number="03">FORWARD LIQUIDITY RISK</Chapter>
          <h2>
            A question about
            <br />
            <span>what comes next.</span>
          </h2>
          <div className="forward-line">
            <div className="forward-value">
              {formatScore(forward.score_0_100)}
              <small>/ 100</small>
            </div>
            <div>
              <span className="experimental-label">EXPERIMENTAL INDICATOR</span>
              <p>
                Liquidity and flow, considered together.
              </p>
              <p className="story-warning">
                Experimental reading.
              </p>
              <Link className="story-link" href="/backtest">
                Examine the evidence <ArrowUpRight size={17} />
              </Link>
            </div>
          </div>
          <div className="forward-details">
            <span>{humanize(forward.regime)}</span>
            <span>
              Liquidity {formatScore(forward.components.liquidity_risk_0_100)}
            </span>
            <span>Flow {formatScore(forward.components.flow_risk_0_100)}</span>
          </div>
          {forward.unavailable_reason && (
            <p className="story-warning">Some inputs were unavailable.</p>
          )}
          <p className="fine">
            Original causal score · {formatUtcDateTime(forward.available_at)} ·
            Heuristic regime boundaries
          </p>
        </div>
      </ScrollScene>
      <ScrollScene id="position-story" expand>
        <div className="position-intro">
          <Chapter number="04">POSITION ANALYSIS</Chapter>
          <h2>
            The market is the same.
            <br />
            <span>Your exposure isn’t.</span>
          </h2>
          <p>
            Bring position size into the picture. Then challenge the
            assumptions.
          </p>
        </div>
        <div className="product-preview">
          <div className="preview-toolbar">
            <span>
              <i /> POSITION ANALYSIS
            </span>
            <span>
              SOL / USDC <b>NORMAL SCENARIO</b>
            </span>
          </div>
          <div className="preview-layout">
            <div className="preview-side">
              <span className="eyebrow">TURNOVER STRESS LAB</span>
              <h3>
                Size.
                <br />
                Stress.
                <br />
                Understand.
              </h3>
              <p>
                Explore six sizes across four turnover scenarios in the
                terminal.
              </p>
              <Link className="story-link" href="/positions">
                Analyze your position <ArrowUpRight size={17} />
              </Link>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Position / USDC</th>
                    <th>Estimated cost</th>
                    <th>Participation</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p) => (
                    <tr key={p.position_size_usdc}>
                      <td>
                        {formatUsdc(p.position_size_usdc, { unit: false })}
                      </td>
                      <td>{formatPct(p.estimated_exit_cost_pct)}</td>
                      <td>{formatMultiple(p.participation_rate_ratio)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
        <div className="scene-caption">
          <span>
            Historical snapshot · {formatUtcDateTime(r.value.meta.reference_at)}
          </span>
          <span>Historical estimate · Not live execution data.</span>
        </div>
      </ScrollScene>
    </>
  );
}
async function HistoryReveal() {
  await connection();
  const r = await getHistory("1m");
  return (
    <ScrollScene id="history-story" tone="blue" expand>
      <div className="history-heading">
        <div>
          <Chapter number="05">HISTORICAL LIQUIDITY</Chapter>
          <h2>
            Every window.
            <br />
            <span>A different story.</span>
          </h2>
        </div>
        <p>
          Trace the activity behind the signal.
          <br />
          Observed turnover and historical risk snapshots.
        </p>
      </div>
      <div className="history-reveal">
        {r.ok ? (
          <>
            <div className="instrument-top">
              <span>OBSERVED TURNOVER / USDC</span>
              <Link href="/history" className="story-link">
                Explore the timeline <ArrowUpRight size={16} />
              </Link>
            </div>
            <LiquidityChart items={r.value.data.items} />
            {r.value.truncated && (
              <p className="story-warning">
                Preview truncated. Open history to narrow the range.
              </p>
            )}
            <Provenance meta={r.value.meta} />
          </>
        ) : (
          <Failure result={r} />
        )}
      </div>
    </ScrollScene>
  );
}
function PreviewLoading() {
  return (
    <div className="story-loading" role="status">
      <span className="eyebrow">READING THE HISTORICAL SNAPSHOT</span>
      <div className="skeleton" />
      <p>Real observations. No substitute data.</p>
    </div>
  );
}
export default function Home() {
  return (
    <div className="launch">
      <header className="launch-nav">
        <Brand />
        <nav aria-label="Product navigation">
          <Link href="#market">The intelligence</Link>
          <Link href="/methodology">Methodology</Link>
          <Link className="button small" href="/dashboard">
            Open terminal <ArrowUpRight size={15} />
          </Link>
        </nav>
      </header>
      <main id="main">
        <HeroStage>
          <div className="hero-editorial">
            <div className="eyebrow">SOLANA LIQUIDITY INTELLIGENCE</div>
            <h1>
              Volume is
              <br />
              not <span>depth.</span>
            </h1>
            <p>
              A deeper perspective on on-chain liquidity.
              <br />
              Built for the questions a price chart can’t answer.
            </p>
            <Link href="/dashboard" className="button launch-cta">
              Explore the radar <ArrowUpRight size={18} />
            </Link>
          </div>
          <LiquiditySculpture />
          <div className="hero-baseline">
            <span>HISTORICAL RESEARCH / SOLANA</span>
            <a href="#beneath">
              Go beneath the surface <ArrowDown size={14} />
            </a>
            <span>CONCEPTUAL LIQUIDITY FORM</span>
          </div>
        </HeroStage>
        <ScrollScene id="beneath">
          <div className="problem-composition">
            <Chapter number="01">BENEATH THE SURFACE</Chapter>
            <h2>
              Activity is visible.
              <br />
              <span>
                Liquidity is more
                <br />
                complicated.
              </span>
            </h2>
            <div className="problem-bottom">
              <p>
                Volume is not depth. A busy market tells you what traded. It
                doesn’t tell you what your exit will cost.
              </p>
              <p>
                Liquidity Radar connects historical observations, causal risk
                indicators, and position-specific estimates — with their
                limitations in plain sight.
              </p>
            </div>
            <div className="concept-axis" aria-hidden="true">
              <span>OBSERVE</span>
              <i />
              <span>CONTEXTUALIZE</span>
              <i />
              <span>QUESTION</span>
            </div>
          </div>
        </ScrollScene>
        <Suspense fallback={<PreviewLoading />}>
          <RiskReveals />
        </Suspense>
        <Suspense fallback={<PreviewLoading />}>
          <HistoryReveal />
        </Suspense>
        <section id="transparency" className="transparency-story">
          <Chapter number="06">METHODOLOGY</Chapter>
          <div className="transparency-heading">
            <h2>
              Nothing hidden.
              <br />
              <span>Including the limits.</span>
            </h2>
            <Link className="story-link" href="/methodology">
              Read the methodology <ArrowUpRight size={18} />
            </Link>
          </div>
          <div className="principles">
            {[
              [
                "01",
                "Historical context.",
                "One SOL/USDC pool with a defined historical coverage window.",
              ],
              [
                "02",
                "Clear by design.",
                "Key indicators, timestamps, and data gaps remain visible.",
              ],
              [
                "03",
                "Evidence before certainty.",
                "Forward risk is experimental. Exit costs are estimates, not quotes.",
              ],
            ].map(([n, title, body]) => (
              <div key={n}>
                <span>{n}</span>
                <h3>{title}</h3>
                <p>{body}</p>
                <Plus size={17} aria-hidden="true" />
              </div>
            ))}
          </div>
        </section>
        <section className="final-reveal">
          <div className="eyebrow">CLARITY BEFORE CONVICTION</div>
          <h2>
            See beneath
            <br />
            <span>the surface.</span>
          </h2>
          <Link className="button launch-cta" href="/dashboard">
            Enter the dashboard <ArrowRight size={18} />
          </Link>
          <p>Historical intelligence. An informed perspective.</p>
        </section>
      </main>
      <footer className="launch-footer">
        <Brand footer />
        <span>Historical intelligence. Estimates are not execution quotes.</span>
        <Link href="/methodology">Methodology ↗</Link>
      </footer>
    </div>
  );
}
