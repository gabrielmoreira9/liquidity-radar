import type { ErrorDetail, RadarEndpoint } from "@/lib/contracts";
import { buildQuery, type ParamsFor } from "./params";

export type ApiFailure =
  | {
      kind: "http";
      status: number;
      code: string;
      message: string;
      details: ErrorDetail[];
    }
  | { kind: "network"; message: string }
  | { kind: "timeout"; timeoutMs: number }
  | { kind: "invalid"; message: string }
  | { kind: "config"; message: string };

export type ApiResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: ApiFailure; endpoint: RadarEndpoint };

export interface ClientOptions {
  baseUrl: string | undefined;
  timeoutMs: number;
  /** Extra attempts after the first one, for transient failures only. */
  retries: number;
  retryDelayMs?: number;
  fetchImpl?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
}

const defaultSleep = (ms: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, ms));

function isErrorEnvelope(
  body: unknown,
): body is {
  error: { code: string; message: string; details?: ErrorDetail[] };
} {
  if (typeof body !== "object" || body === null || !("error" in body))
    return false;
  const error = (body as { error: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { code?: unknown }).code === "string"
  );
}

function isSuccessEnvelope(body: unknown): boolean {
  return (
    typeof body === "object" &&
    body !== null &&
    "meta" in body &&
    "data" in body
  );
}

/**
 * Transient = worth retrying: no connection, timeout, or an upstream/proxy failure that did not come
 * from the API itself (e.g. a hosting cold start answering 502/503/504 without the documented envelope).
 * A documented API error (404 NO_DATA, 422, 503 DATASETS_UNAVAILABLE, …) is persistent and returned at once.
 */
function isTransient(failure: ApiFailure, fromApi: boolean): boolean {
  if (failure.kind === "network" || failure.kind === "timeout") return true;
  if (failure.kind === "http")
    return !fromApi && [502, 503, 504].includes(failure.status);
  return false;
}

async function attempt<T>(
  url: string,
  options: ClientOptions,
): Promise<{ result: ApiResult<T>; fromApi: boolean }> {
  const endpoint = new URL(url).pathname as RadarEndpoint;
  const fail = (error: ApiFailure, fromApi = false) => ({
    result: { ok: false as const, error, endpoint },
    fromApi,
  });
  const fetchImpl = options.fetchImpl ?? fetch;
  let response: Response;
  try {
    response = await fetchImpl(url, {
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(options.timeoutMs),
    });
  } catch (error) {
    const name = error instanceof Error ? error.name : "";
    if (name === "TimeoutError" || name === "AbortError")
      return fail({ kind: "timeout", timeoutMs: options.timeoutMs });
    return fail({
      kind: "network",
      message: "The analytics API could not be reached.",
    });
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    if (!response.ok)
      return fail({
        kind: "http",
        status: response.status,
        code: "HTTP_ERROR",
        message: `Upstream responded with HTTP ${response.status}.`,
        details: [],
      });
    return fail({
      kind: "invalid",
      message: "The API returned a response that is not valid JSON.",
    });
  }

  if (!response.ok) {
    if (isErrorEnvelope(body)) {
      return fail(
        {
          kind: "http",
          status: response.status,
          code: body.error.code,
          message: body.error.message,
          details: body.error.details ?? [],
        },
        true,
      );
    }
    return fail({
      kind: "http",
      status: response.status,
      code: "HTTP_ERROR",
      message: `Upstream responded with HTTP ${response.status}.`,
      details: [],
    });
  }
  if (!isSuccessEnvelope(body))
    return fail({
      kind: "invalid",
      message: "The API response is missing the {meta, data} envelope.",
    });
  return { result: { ok: true, value: body as T }, fromApi: true };
}

export async function radarGet<T, E extends RadarEndpoint = RadarEndpoint>(
  endpoint: E,
  params: ParamsFor<E>,
  options: ClientOptions,
): Promise<ApiResult<T>> {
  if (!options.baseUrl) {
    return {
      ok: false,
      endpoint,
      error: {
        kind: "config",
        message: "RADAR_API_URL is not configured for the frontend server.",
      },
    };
  }
  const url = new URL(endpoint, options.baseUrl);
  url.search = buildQuery(endpoint, params).toString();
  const sleep = options.sleep ?? defaultSleep;
  const baseDelay = options.retryDelayMs ?? 500;

  let last = await attempt<T>(url.toString(), options);
  for (let retry = 1; retry <= options.retries; retry++) {
    if (last.result.ok || !isTransient(last.result.error, last.fromApi)) break;
    await sleep(baseDelay * 2 ** (retry - 1));
    last = await attempt<T>(url.toString(), options);
  }
  return last.result;
}
