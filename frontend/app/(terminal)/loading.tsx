export default function Loading() {
  return (
    <div role="status" aria-live="polite">
      <div className="eyebrow">LOADING HISTORICAL SNAPSHOT</div>
      <div className="skeleton" />
      <div className="two-col">
        <div className="skeleton" />
        <div className="skeleton" />
      </div>
      <p className="muted">Reading the analytics API…</p>
    </div>
  );
}
