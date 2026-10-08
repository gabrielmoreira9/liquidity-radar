"use client";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <section className="panel" role="alert">
      <h1>Unable to render this view.</h1>
      <p>Try again or check the local API configuration.</p>
      <button onClick={reset} className="button">
        Retry
      </button>
    </section>
  );
}
