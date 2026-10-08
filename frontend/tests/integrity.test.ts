import { describe, it, expect, vi } from "vitest";
import {
  formatPct,
  formatFraction,
  formatScore,
  formatUsdc,
  formatUtcDateTime,
} from "../lib/format";
import { buildQuery } from "../lib/api/params";
import { radarGet } from "../lib/api/client";
describe("Data integrity", () => {
  it("preserves missing values rather than inventing zeros", () => {
    expect(formatPct(null)).toBe("—");
    expect(formatScore(undefined)).toBe("—");
    expect(formatUsdc(NaN)).toBe("—");
    expect(formatPct(0)).toBe("0%");
  });
  it("distinguishes percentage units from fractions and keeps tiny costs", () => {
    expect(formatPct(0.5)).toBe("0.5%");
    expect(formatFraction(0.5)).toBe("50.0%");
    expect(formatPct(0.003943881)).toBe("0.00394%");
    expect(formatPct(150)).toBe("150.0%");
  });
  it("renders historical instants in UTC", () => {
    expect(formatUtcDateTime("2026-07-03T21:00:00-03:00")).toBe(
      "04 Jul 2026 · 00:00 UTC",
    );
  });
  it("never sends as_of to retrospective backtests", () => {
    expect(
      buildQuery("/api/backtest/summary", {
        horizon: 5,
        ...{ as_of: "2026-07-03T23:00:00Z" },
      }).toString(),
    ).toBe("horizon=5");
  });
  it("does not retry documented dataset failures or substitute data", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            error: { code: "DATASETS_UNAVAILABLE", message: "Unavailable" },
          }),
          { status: 503 },
        ),
    );
    const r = await radarGet(
      "/api/overview",
      {},
      {
        baseUrl: "http://localhost:8000",
        timeoutMs: 1000,
        retries: 2,
        fetchImpl,
      },
    );
    expect(r.ok).toBe(false);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
  it("retries transient connection errors", async () => {
    const fetchImpl = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ meta: {}, data: {} })),
      );
    const r = await radarGet(
      "/api/overview",
      {},
      {
        baseUrl: "http://localhost:8000",
        timeoutMs: 1000,
        retries: 1,
        fetchImpl,
        sleep: async () => {},
      },
    );
    expect(r.ok).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });
  it("rejects malformed JSON envelopes", async () => {
    const r = await radarGet(
      "/api/overview",
      {},
      {
        baseUrl: "http://localhost:8000",
        timeoutMs: 1000,
        retries: 0,
        fetchImpl: async () => new Response("{}"),
      },
    );
    expect(r.ok).toBe(false);
  });
});
