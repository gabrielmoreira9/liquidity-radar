/** Abstract brand illustration, deliberately not a plot of market measurements. */
export function LiquiditySculpture() {
  const paths = Array.from({ length: 42 }, (_, row) => {
    const points = Array.from({ length: 81 }, (_, col) => {
      const u = col / 80,
        v = row / 41;
      const x = 60 + u * 1080;
      const ridge =
        Math.exp(-((u - 0.53) ** 2) / 0.018) *
        (105 + 70 * Math.sin(v * Math.PI));
      const y = 150 + v * 205 - ridge + Math.sin(u * Math.PI * 2 + v * 2) * 22;
      return `${col ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    return (
      <path
        key={row}
        d={points}
        opacity={0.18 + 0.65 * Math.sin((row / 42) * Math.PI)}
      />
    );
  });
  return (
    <div className="liquidity-sculpture" aria-hidden="true">
      <svg viewBox="0 0 1200 420" fill="none">
        <defs>
          <linearGradient id="sculpture-spectrum">
            <stop stopColor="#1552aa" />
            <stop offset=".45" stopColor="#64e7fa" />
            <stop offset=".65" stopColor="#c1ffff" />
            <stop offset="1" stopColor="#174491" />
          </linearGradient>
        </defs>
        <g stroke="url(#sculpture-spectrum)" strokeWidth="1.2">
          {paths}
        </g>
        <ellipse cx="620" cy="355" rx="490" ry="42" stroke="#336c8955" />
        <ellipse cx="620" cy="355" rx="350" ry="27" stroke="#336c8933" />
      </svg>
    </div>
  );
}
