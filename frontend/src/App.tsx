import { useState } from 'react';
import { SearchBar } from './components/SearchBar';
import { csvSearch, QueryRow } from './api';

type State =
  | { kind: 'idle' }
  | { kind: 'loading'; query: string }
  | { kind: 'ok'; query: string; count: number; queries: QueryRow[]; file?: string }
  | { kind: 'error'; query: string; message: string };

export default function App() {
  const [state, setState] = useState<State>({ kind: 'idle' });

  async function runSearch(q: string) {
    setState({ kind: 'loading', query: q });
    try {
      const r = await csvSearch(q);
      setState({ kind: 'ok', query: q, count: r.count, queries: r.queries, file: r.output_file });
    } catch (e) {
      setState({
        kind: 'error',
        query: q,
        message: e instanceof Error ? e.message : 'Search failed. Try again.',
      });
    }
  }

  const busy = state.kind === 'loading';

  return (
    <Shell>
      <Header />
      <SearchBar onSubmit={runSearch} busy={busy} />
      <Results state={state} onRetry={() => state.kind === 'error' && runSearch(state.query)} />
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-2xl px-5 pt-16 pb-24 sm:pt-24">
      <div className="space-y-6">{children}</div>
    </main>
  );
}

function Header() {
  return (
    <header className="space-y-2">
      <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted">
        <span className="text-accent font-semibold">Sinpex</span> · Company Search
      </p>
      <h1 className="font-sans text-4xl sm:text-5xl font-semibold tracking-[-0.02em] text-balance leading-[1.05]">
        Find any company,
        <br />
        <span className="text-muted">anywhere.</span>
      </h1>
    </header>
  );
}

function Results({ state, onRetry }: { state: State; onRetry: () => void }) {
  if (state.kind === 'idle') return <IdleState />;
  if (state.kind === 'loading') return <LoadingState />;
  if (state.kind === 'error') return <ErrorState message={state.message} onRetry={onRetry} />;

  // Results are no longer returned to the view — the backend gathers them and
  // writes them to a JSON file for the downstream pipeline. Show what was
  // gathered (count + destination) instead of a result list.
  const queryLabel = state.queries
    .map((q) => (q.jurisdiction ? `${q.name} [${q.jurisdiction}]` : q.name))
    .join(' · ');

  return <WrittenState count={state.count} queryLabel={queryLabel} file={state.file} />;
}

function WrittenState({
  count,
  queryLabel,
  file,
}: {
  count: number;
  queryLabel: string;
  file?: string;
}) {
  return (
    <section aria-label="Search complete" className="space-y-3 animate-fade-in-up">
      <div className="flex items-center gap-2 text-sm text-ink">
        <CheckIcon className="text-accent" />
        <span>
          Gathered <span className="font-semibold tabular-nums">{count}</span> record
          {count === 1 ? '' : 's'} for <span className="text-muted">{queryLabel}</span>.
        </span>
      </div>
      {file ? (
        <p className="font-mono text-[11px] text-muted">
          written to <span className="text-ink/70">{file}</span>
        </p>
      ) : null}
      <p className="text-[13px] text-muted text-balance max-w-prose">
        Results are no longer shown here — they are written server-side for the
        downstream pipeline to consume.
      </p>
    </section>
  );
}

function CheckIcon({ className = '' }: { className?: string }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function IdleState() {
  return (
    <section className="pt-10 pb-4 flex flex-col items-center text-center gap-3 text-muted animate-fade-in-up">
      <BigLens className="opacity-20" />
      <p className="text-sm max-w-xs text-balance">
        Type a company name, optionally with a jurisdiction (e.g. "Tesla, US"). We query every
        relevant register and return all matches — filtered to that jurisdiction if you give one.
      </p>
    </section>
  );
}

function LoadingState() {
  return (
    <section aria-label="Loading" className="space-y-0 animate-pulse">
      <div className="h-3 w-32 rounded bg-line mb-4" />
      <ul className="divide-y divide-line">
        {[0, 1, 2].map((i) => (
          <li key={i} className="py-5 first:pt-3">
            <div className="flex items-baseline gap-4">
              <div className="h-4 w-2/3 rounded bg-line" />
              <div className="ml-auto h-3 w-20 rounded bg-line" />
            </div>
            <div className="mt-2.5 h-3 w-full rounded bg-line/70" />
            <div className="mt-1.5 h-3 w-5/6 rounded bg-line/70" />
          </li>
        ))}
      </ul>
    </section>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section
      role="alert"
      className="rounded-lg border border-accent/30 bg-accent-soft/40 px-4 py-3 text-sm text-ink animate-fade-in-up flex items-center gap-3"
    >
      <span className="flex-1">Search failed. {message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="font-medium text-accent hover:text-ink transition-colors duration-150"
      >
        Retry
      </button>
    </section>
  );
}

function BigLens({ className = '' }: { className?: string }) {
  return (
    <svg
      width="56"
      height="56"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m20 20-5.2-5.2" />
    </svg>
  );
}
