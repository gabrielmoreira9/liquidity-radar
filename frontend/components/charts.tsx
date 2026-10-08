"use client";
import {
  ResponsiveContainer,
  ComposedChart,
  Area,
  Line,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import type { LiquidityWindow } from "@/lib/contracts";
import { formatUtcTime } from "@/lib/format";
export function LiquidityChart({
  items,
  kind = "volume",
}: {
  items: LiquidityWindow[];
  kind?: "volume" | "risk" | "flow" | "price";
}) {
  if (!items.length)
    return <p className="empty">No closed windows match these filters.</p>;
  const data = items.map((r) => ({
    ...r,
    market: r.risk_at_close?.current_market_risk_score_0_100 ?? null,
    forward: r.risk_at_close?.forward_liquidity_risk_score_0_100 ?? null,
  }));
  return (
    <div
      className="chart"
      role="img"
      aria-label={`${kind} chart of ${items.length} historical windows. Exact values available in the data table.`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={data}
          margin={{ top: 12, right: 14, bottom: 4, left: 0 }}
        >
          <defs>
            <linearGradient id={`fill-${kind}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#53e7e1" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#53e7e1" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid
            stroke="#223239"
            strokeDasharray="3 5"
            vertical={false}
          />
          <XAxis
            dataKey="timestamp"
            tickFormatter={formatUtcTime}
            minTickGap={45}
            stroke="#72858e"
            fontSize={11}
          />
          <YAxis
            width={62}
            domain={kind === "risk" ? [0, 100] : ["auto", "auto"]}
            tickFormatter={(v) =>
              Intl.NumberFormat("en", {
                notation: "compact",
                maximumSignificantDigits: 3,
              }).format(Number(v))
            }
            stroke="#72858e"
            fontSize={11}
          />
          <Tooltip
            labelFormatter={(v) => `${String(v)} UTC window open`}
            contentStyle={{
              background: "#102127",
              border: "1px solid #365057",
              borderRadius: 8,
              color: "#eef9fa",
            }}
          />
          <Legend />
          {kind === "risk" ? (
            <>
              <Line
                isAnimationActive={false}
                dataKey="market"
                name="Market risk / 100"
                stroke="#53e7e1"
                dot={false}
                strokeWidth={2}
              />
              <Line
                isAnimationActive={false}
                dataKey="forward"
                name="Forward risk / 100 · experimental"
                stroke="#bd9fff"
                dot={false}
                strokeWidth={2}
              />
            </>
          ) : kind === "flow" ? (
            <Bar
              isAnimationActive={false}
              dataKey="net_flow_usdc"
              name="Net flow · USDC"
              fill="#53e7e1"
              radius={[2, 2, 0, 0]}
            />
          ) : (
            <Area
              isAnimationActive={false}
              type="linear"
              dataKey={
                kind === "price" ? "price_close_usdc_per_sol" : "volume_usdc"
              }
              name={
                kind === "price" ? "Close · USDC/SOL" : "Observed volume · USDC"
              }
              stroke="#53e7e1"
              fill={`url(#fill-${kind})`}
              strokeWidth={2}
              connectNulls={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
