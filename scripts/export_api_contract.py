"""Export OpenAPI, matching TypeScript types and real demo response examples offline."""
import json

from fastapi.testclient import TestClient

from src.api import create_app
from src.api_data import ROOT, Settings


def ts_type(schema):
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    for key, separator in (("anyOf", " | "), ("oneOf", " | "), ("allOf", " & ")):
        if key in schema:
            return separator.join(ts_type(part) for part in schema[key])
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    kind = schema.get("type")
    if kind == "array":
        return f"Array<{ts_type(schema['items'])}>"
    if kind == "object":
        if "properties" in schema:
            required = set(schema.get("required", []))
            fields = [f'{json.dumps(key)}{"" if key in required else "?"}: {ts_type(value)};'
                      for key, value in schema["properties"].items()]
            return "{ " + " ".join(fields) + " }"
        extra = schema.get("additionalProperties", True)
        return f"Record<string, {ts_type(extra) if isinstance(extra, dict) else 'unknown'}>"
    if kind in ("integer", "number"):
        return "number"
    if kind == "string":
        return "ISODateTime" if schema.get("format") == "date-time" else "string"
    return {"boolean": "boolean", "null": "null"}.get(kind, "unknown")


def typescript(schema):
    lines = ["// Generated from OpenAPI; do not edit. All timestamps UTC, all nullable numbers remain null.",
             "export type ISODateTime = string;", ""]
    for name, model in sorted(schema["components"]["schemas"].items()):
        lines.append(f"export type {name} = {ts_type(model)};")
    aliases = {"/api/health": "HealthResponse", "/api/overview": "OverviewResponse", "/api/market-risk": "MarketRiskResponse",
        "/api/forward-risk": "ForwardRiskResponse", "/api/positions": "PositionsResponse", "/api/exit-cost": "ExitCostResponse",
        "/api/liquidity/history": "HistoryResponse", "/api/backtest/summary": "BacktestResponse"}
    lines.append("")
    for path, name in aliases.items():
        response = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        lines.append(f"export type {name} = {ts_type(response)};")
    lines += ["", "export type RadarEndpoint = " + " | ".join(json.dumps(path) for path in aliases) + ";",
        "export async function fetchRadar<T>(baseUrl: string, path: RadarEndpoint, params: Record<string, string | number> = {}): Promise<T> {",
        "  const url = new URL(path, baseUrl);",
        "  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value));",
        "  const response = await fetch(url.toString(), { cache: 'no-store' });",
        "  if (!response.ok) {",
        "    const body: ErrorResponse = await response.json();",
        "    throw new Error(`${body.error.code}: ${body.error.message}`);",
        "  }", "  return await response.json() as T;", "}", ""]
    return "\n".join(lines)


def export():
    settings = Settings(mode="demo")
    schema = create_app(settings).openapi()
    directory = ROOT/"contracts"
    directory.mkdir(exist_ok=True)
    (directory/"openapi.json").write_text(json.dumps(schema, indent=2)+"\n")
    (directory/"liquidity-radar.ts").write_text(typescript(schema))
    endpoints = ("/api/health", "/api/overview", "/api/market-risk", "/api/forward-risk", "/api/positions",
                 "/api/exit-cost?position_size=100000&scenario=NORMAL", "/api/liquidity/history?interval=1m&limit=2", "/api/backtest/summary?horizon=5")
    with TestClient(create_app(settings)) as client:
        examples = {}
        for url in endpoints:
            response = client.get(url)
            response.raise_for_status()
            examples[url] = response.json()
    (ROOT/"docs/api_examples.json").write_text(json.dumps(examples, indent=2, allow_nan=False)+"\n")
    print("Exported contracts/openapi.json, contracts/liquidity-radar.ts and docs/api_examples.json")


if __name__ == "__main__":
    export()
