import { Brand, Navigation } from "@/components/navigation";
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="terminal">
      <Navigation />
      <div className="workspace">
        <header className="topbar">
          <span>
            <span className="cyan">◈</span> SOL / USDC{" "}
            <span className="muted"> / Solana</span>
          </span>
          <span className="badge">HISTORICAL · NOT LIVE</span>
        </header>
        <main id="main" className="content">
          {children}
        </main>
        <footer>
          <Brand footer />
          <span>Historical intelligence. Estimates are not execution quotes.</span>
        </footer>
      </div>
    </div>
  );
}
