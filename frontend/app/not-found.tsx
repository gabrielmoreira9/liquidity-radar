import Link from "next/link";
export default function NotFound() {
  return (
    <main id="main" className="content">
      <h1>Outside radar coverage.</h1>
      <p>This page does not exist.</p>
      <Link className="button" href="/dashboard">
        Return to overview
      </Link>
    </main>
  );
}
