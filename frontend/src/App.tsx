import { useState } from 'react';
import { SearchBar } from './components/SearchBar';
import { csvSearch, CompanyRecord, QueryRow } from './api';

type State =
  | { kind: 'idle' }
  | { kind: 'loading'; query: string }
  | { kind: 'ok'; query: string; results: CompanyRecord[]; queries: QueryRow[]; file?: string }
  | { kind: 'error'; query: string; message: string };

export default function App() {
  const [state, setState] = useState<State>({ kind: 'idle' });

  async function runSearch(q: string) {
    setState({ kind: 'loading', query: q });
    try {
      const r = await csvSearch(q);
      setState({ kind: 'ok', query: q, results: r.results, queries: r.queries, file: r.output_file });
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
  if (state.results.length === 0) return <EmptyResultsState query={state.query} />;

  const queryLabel = state.queries
    .map((q) => (q.jurisdiction ? `${q.name} [${q.jurisdiction}]` : q.name))
    .join(' · ');

  return (
    <section aria-label="Search results" className="space-y-0">
      <p className="font-mono text-xs uppercase tracking-wider text-muted pb-1">
        {state.results.length} result{state.results.length === 1 ? '' : 's'} · {queryLabel}
      </p>
      {state.file && (
        <p className="font-mono text-[11px] text-muted pb-3">
          written to <span className="text-ink/70">{state.file}</span>
        </p>
      )}
      <ul className="divide-y divide-line">
        {state.results.map((r, i) => {
          const isUrl = !!r.source && /^https?:\/\//.test(r.source);
          const title =
            r.name_normalized_register_name ?? `No match — ${r.no_match_reason ?? 'unknown'}`;
          return (
            <li
              key={`${r.query_id}-${i}`}
              style={{ animationDelay: `${i * 40}ms` }}
              className="animate-fade-in-up py-5 first:pt-3"
            >
              <div className="flex items-baseline gap-4">
                <h3 className="font-medium text-[17px] tracking-[-0.005em]">
                  {isUrl ? (
                    <a
                      href={r.source!}
                      target="_blank"
                      rel="noreferrer"
                      className="hover:text-accent transition-colors duration-150"
                    >
                      {title}
                    </a>
                  ) : (
                    <span className={r.name_normalized_register_name ? '' : 'text-muted'}>
                      {title}
                    </span>
                  )}
                </h3>
                <span className="ml-auto shrink-0 flex items-baseline gap-2 font-mono text-[11px] tabular-nums text-muted">
                  {r.jurisdiction_confirmed && (
                    <span className="rounded bg-accent-soft/60 px-1.5 py-0.5 text-accent">
                      {r.jurisdiction_confirmed}
                    </span>
                  )}
                  {r.provider && (
                    <span className="rounded bg-line/60 px-1.5 py-0.5 uppercase tracking-wide">
                      {r.provider}
                    </span>
                  )}
                  <span title="confidence">{Math.round(r.confidence * 100)}%</span>
                </span>
              </div>
              <p className="mt-1.5 text-[15px] text-ink/75 text-balance max-w-prose">
                {r.registry_id && (
                  <span className="font-mono text-[13px] text-ink/60">
                    {r.registry_id}
                    {r.registry_court ? ` · ${r.registry_court}` : ''} ·{' '}
                  </span>
                )}
                {r.snippet}
              </p>
            </li>
          );
        })}
      </ul>
    </section>
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

function EmptyResultsState({ query }: { query: string }) {
  return (
    <section className="pt-6 text-sm text-muted animate-fade-in-up">
      No companies match <span className="text-ink">"{query}"</span>. Try a different name.
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
