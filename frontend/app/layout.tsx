import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: {
    default: "Liquidity Radar | Volume is not depth.",
    template: "%s | Liquidity Radar",
  },
  description:
    "Historical Solana liquidity intelligence. Explainable risk, position stress estimates, and transparent temporal evaluation.",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <a className="skip" href="#main">
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
