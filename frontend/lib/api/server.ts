import "server-only";
import { cache } from "react";
import type {
  BacktestEvent,
  BacktestResponse,
  BacktestSample,
  ExitCostResponse,
  ForwardRiskResponse,
  HealthResponse,
  HistoryResponse,
  Interval,
  LiquidityWindow,
  MarketRiskResponse,
  OverviewResponse,
  PositionsResponse,
  Scenario,
} from "@/lib/contracts";
import { radarGet, type ApiResult, type ClientOptions } from "./client";
import { HISTORY_PAGE_LIMIT } from "./params";

function intFromEnv(name: string, fallback: number, min: number, max: number): number {
  const parsed = Number.parseInt(process.env[name] ?? "", 10);
  return Number.isFinite(parsed) ? Math.min(Math.max(parsed, min), max) : fallback;
}

/**
 * Server-only configuration. The browser never talks to FastAPI directly, so the URL is not
 * exposed and CORS is not involved. NEXT_PUBLIC_RADAR_API_URL is accepted as a fallback because
 * the repository's .env.example documents it, but RADAR_API_URL is preferred for deployments.
 */
function options(): ClientOptions {
  return {
    baseUrl: process.env.RADAR_API_URL ?? process.env.NEXT_PUBLIC_RADAR_API_URL ?? (process.env.NODE_ENV === "production" ? undefined : "http://127.0.0.1:8000"),
    // Generous default for hosted cold starts; bounded so a dead backend fails visibly.
    timeoutMs: intFromEnv("RADAR_API_TIMEOUT_MS", 12000, 1000, 60000),
    retries: intFromEnv("RADAR_API_RETRIES", 2, 0, 4),
  };
}

// React `cache` de-duplicates identical calls within one server render.
export const getHealth = cache(() => radarGet<HealthResponse, "/api/health">("/api/health", {}, options()));
export const getOverview = cache((asOf?: string) => radarGet<OverviewResponse, "/api/overview">("/api/overview", { as_of: asOf }, options()));
export const getMarketRisk = cache((asOf?: string) => radarGet<MarketRiskResponse, "/api/market-risk">("/api/market-risk", { as_of: asOf }, options()));
export const getForwardRisk = cache((asOf?: string) => radarGet<ForwardRiskResponse, "/api/forward-risk">("/api/forward-risk", { as_of: asOf }, options()));
export const getPositions = cache((scenario: Scenario, asOf?: string) =>
  radarGet<PositionsResponse, "/api/positions">("/api/positions", { scenario, as_of: asOf }, options()),
);
export const getExitCost = cache((positionSize: number, scenario: Scenario, asOf?: string) =>
  radarGet<ExitCostResponse, "/api/exit-cost">("/api/exit-cost", { position_size: positionSize, scenario, as_of: asOf }, options()),
);
export const getBacktest = cache((event: BacktestEvent, sample: BacktestSample) =>
  radarGet<BacktestResponse, "/api/backtest/summary">("/api/backtest/summary", { event, sample }, options()),
);

/**
 * Follows `pagination.next_offset` until the filtered series is complete (or `maxBars` is reached,
 * bounding payload size). Returns the first page's envelope with all collected items and a `truncated` flag.
 * Arguments are primitives so React `cache` can de-duplicate calls.
 */
export const getHistory = cache(
  async (
    interval: Interval,
    start?: string,
    end?: string,
    asOf?: string,
    maxBars = 6000,
  ): Promise<ApiResult<HistoryResponse & { truncated: boolean }>> => {
    const items: LiquidityWindow[] = [];
    let offset = 0;
    let first: HistoryResponse | undefined;
    for (;;) {
      const page = await radarGet<HistoryResponse, "/api/liquidity/history">(
        "/api/liquidity/history",
        { interval, start, end, as_of: asOf, limit: Math.min(HISTORY_PAGE_LIMIT, maxBars - items.length), offset },
        options(),
      );
      if (!page.ok) return page;
      first ??= page.value;
      items.push(...page.value.data.items);
      const next = page.value.data.pagination.next_offset;
      if (next === null || items.length >= maxBars) {
        return {
          ok: true,
          value: { ...first, data: { ...first.data, items }, truncated: next !== null },
        };
      }
      offset = next;
    }
  },
);
