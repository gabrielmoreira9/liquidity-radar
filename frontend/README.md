# Liquidity Radar frontend

Next.js App Router, strict TypeScript, Tailwind CSS, Recharts, Lucide, and Motion. All analytical values are read from the FastAPI service through server-only typed clients. No bundled response fixtures or fabricated market values are used at runtime. The generated contracts in `../contracts` remain the source of truth.

## Local startup

From the repository root, prepare Python (3.12 is the backend's documented version):

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
RADAR_DATA_MODE=demo .venv/bin/python -m uvicorn src.api:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```sh
cd frontend
npm ci
RADAR_API_URL=http://127.0.0.1:8000 npm run dev
```

Open http://localhost:3000. For a production-mode local server:

```sh
npm run build
RADAR_API_URL=http://127.0.0.1:8000 npm run start
```

`RADAR_API_URL` is server-only and required in production. `NEXT_PUBLIC_RADAR_API_URL` is accepted as a compatibility fallback. Development defaults to localhost:8000. Optional timeout/retry settings are listed in `.env.example`. Requests have bounded timeouts and transient retries; documented API failures render without substitute data. The backend must be launched with `RADAR_DATA_MODE=demo` to select the bundled snapshot.

## Pages

- `/`: cinematic landing page, original CSS radar illustration, reduced-motion-aware entrance.
- `/dashboard`: observed totals, causal risk cards, turnover chart, historical `as_of` selection.
- `/market-risk`: separate current and experimental forward indicators, components, drivers, missing reasons, reference reliability.
- `/history`: interval and UTC start/end/as-of filters, complete paginated history (bounded at 6,000 windows with explicit truncation), turnover/price/flow/risk charts and accessible observation table.
- `/positions`: six positions and four scenarios; exit-cost endpoint details, comparison table and explicit flags.
- `/backtest`: event, sample, horizon and predictor filters; baseline comparison, confusion matrices, lead times, exclusions and frozen thresholds. No historical as-of filter is applied to this retrospective endpoint.
- `/methodology`: observations, causal construction, model formula, units, and validation limitations.

Every data page exposes historical/demo provenance, coverage, units and risk version. Nulls remain missing; percentages and fractional backtest rates have separate formatting. Risk is never presented as a probability. Model estimates are never labeled executable slippage. All charts use real endpoint values; the landing illustration is decorative, not a data visualization.

## Validation

```sh
npm run typecheck
npm run lint
npm test
npm run build
# With the demo API already running:
npx playwright install chromium
npm run test:e2e
```

Browser tests launch the production frontend and exercise desktop/mobile navigation, real demo provenance, stress controls, historical 404 states, backtest filters and horizontal overflow. Unit tests protect null semantics, units, UTC formatting, query contracts and API failure/retry handling. Webpack with the official Tailwind PostCSS integration avoids a Turbopack worker-port restriction in the development tool environment. No external font fetches are required.

## Verified results and known limitations

TypeScript, ESLint (zero warnings), seven unit tests, six Chromium desktop/mobile end-to-end tests, and the production webpack build pass. Browser tests use the real DEMO FastAPI service, not mocked market responses.

`npm audit` reports five high-severity development dependency findings in the ESLint → fast-glob → micromatch → braces chain. Its suggested automated remediation downgrades `eslint-config-next` to 14.x; this was not applied to the Next 16 project. These findings remain unresolved. The frontend retains all historical dataset and statistical model limitations exposed by the backend.

## Product-story redesign

The landing page streams request-time FastAPI previews behind Suspense, so the hero remains available while analytics load and build-time API failures cannot become permanent static previews. It uses original procedural SVG line art labeled as conceptual (not market data), the original logo, and distinct editorial chapters for market conditions, experimental forward risk, position exposure, historical observations, methodology, and the terminal CTA.

`components/scroll-scene.tsx` uses Motion `useScroll` and `useTransform` MotionValues for reversible scroll-linked scale, translation, opacity and layered light opacity. No scroll listener updates React state, no WebGL, and no animated blur. Sticky compositions expand and recede on desktop. At 900px and below, or with `prefers-reduced-motion`, sections use a static, unpinned layout with transforms removed. Quantitative controls and tables are not scroll-animated in the terminal. The revised system uses a native system typography stack and has no external font dependency.

Additional browser tests verify changing transforms at distinct scroll positions, mobile simplification, reduced-motion visibility, real demo provenance, and absence of browser exceptions.
