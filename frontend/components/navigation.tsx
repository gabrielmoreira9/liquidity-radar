"use client";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Activity,
  ChartNoAxesCombined,
  Layers,
  FlaskConical,
  BookOpen,
  ArrowUpRight,
} from "lucide-react";
const links = [
  ["/dashboard", "Overview", LayoutDashboard],
  ["/market-risk", "Market risk", Activity],
  ["/history", "Liquidity history", ChartNoAxesCombined],
  ["/positions", "Position analysis", Layers],
  ["/backtest", "Backtesting", FlaskConical],
  ["/methodology", "Methodology", BookOpen],
] as const;
export function Brand({ footer = false }: { footer?: boolean }) {
  return (
    <Link
      href="/"
      className={`brand${footer ? " brand-footer" : ""}`}
      aria-label="Liquidity Radar home"
    >
      <Image
        src="/liquidity-radar-logo.webp"
        alt="Liquidity Radar — ON-CHAIN"
        width={1637}
        height={723}
        unoptimized
        loading={footer ? "lazy" : "eager"}
        className="brand-image"
      />
    </Link>
  );
}
export function Navigation() {
  const path = usePathname();
  return (
    <aside className="sidebar">
      <Brand />
      <div className="nav-label">RESEARCH TERMINAL</div>
      <nav aria-label="Main navigation">
        {links.map(([href, label, Icon]) => (
          <Link
            key={href}
            href={href}
            aria-current={path === href ? "page" : undefined}
            className={path === href ? "active" : ""}
          >
            <Icon size={18} />
            {label}
          </Link>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <span className="badge">HISTORICAL DATA</span>
        <p>Context before conviction.</p>
        <Link href="/methodology">
          Understand the model <ArrowUpRight size={14} />
        </Link>
      </div>
    </aside>
  );
}
