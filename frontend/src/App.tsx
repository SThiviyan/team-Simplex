import { useState } from 'react';
import { PipelinePanel } from './components/PipelinePanel';
import { SearchBar } from './components/SearchBar';
import { ExtractionResult, resolveCompany } from './api';

type State =
  | { kind: 'idle' }
  | { kind: 'loading'; query: string }
  | { kind: 'ok'; query: string; result: ExtractionResult }
  | { kind: 'error'; query: string; message: string };

export default function App() {
  const [state, setState] = useState<State>({ kind: 'idle' });

  async function runSearch(q: string) {
    setState({ kind: 'loading', query: q });
    try {
      const result = await resolveCompany(q);
      setState({ kind: 'ok', query: q, result });
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
      <PipelinePanel />
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

const FLAG_STYLE: Record<string, string> = {
  verified: 'bg-emerald-500/10 text-emerald-700',
  probable: 'bg-accent/10 text-accent',
  ambiguous: 'bg-amber-500/15 text-amber-700',
  not_found: 'bg-line/70 text-muted',
  error: 'bg-red-500/10 text-red-700',
};

function Results({ state, onRetry }: { state: State; onRetry: () => void }) {
  if (state.kind === 'idle') return <IdleState />;
  if (state.kind === 'loading') return <LoadingState />;
  if (state.kind === 'error') return <ErrorState message={state.message} onRetry={onRetry} />;

  // The full pipeline (registry foundation hierarchy + Tier A/B enrichment)
  // resolves one company and returns every CSV column — shown below.
  return (
    <section aria-label="Search complete" className="space-y-5 animate-fade-in-up">
      <ResultCard query={state.query} r={state.result} />
    </section>
  );
}

function ResultCard({ query, r }: { query: string; r: ExtractionResult }) {
  const pct = Math.round((r.confidence ?? 0) * 100);
  const flag = r.confidence_flag ?? (r.registry_id ? 'probable' : 'not_found');
  const matched = !!r.registry_id || !!r.name_normalized_register_name;
  const isUrl = (r.source ?? '').startsWith('http');

  return (
    <div className="rounded-xl border border-accent/30 bg-accent-soft/30 px-4 py-4 space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <div className="space-y-0.5 min-w-0">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted truncate">
            {query}
          </p>
          <h3 className="text-lg font-semibold tracking-[-0.01em] text-ink">
            {r.name_normalized_register_name ?? <span className="text-muted">No match</span>}
          </h3>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span
            className={`rounded-full px-2 py-1 font-mono text-[10px] uppercase tracking-wide ${
              FLAG_STYLE[flag] ?? 'bg-line/70 text-muted'
            }`}
          >
            {flag}
          </span>
          <span
            className="rounded-full bg-accent/10 px-2.5 py-1 text-xs font-semibold tabular-nums text-accent"
            title="confidence (calibrated)"
          >
            {pct}%
          </span>
        </div>
      </div>

      {/* Every CSV column, in order. Empty fields are shown as a dash so the
          full schema is always visible (matches the downloadable CSV). */}
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-2">
        <Field label="Registry ID" value={r.registry_id} mono />
        <Field label="Registry court" value={r.registry_court} />
        <Field label="Registered name" value={r.name_normalized_register_name} />
        <Field label="Jurisdiction" value={r.jurisdiction_confirmed} />
        <Field label="Organization type" value={r.organization_type} />
        <Field label="Status" value={r.status} />
        <Field label="Incorporation date" value={r.incorporation_date} />
        <Field label="No-match reason" value={r.no_match_reason} />
        <Field label="Registered address" value={r.registered_address} span />
        <Field label="Officers" value={r.officers} span />
      </dl>

      <div className="flex items-center justify-between gap-3 border-t border-line pt-2">
        <span className="text-[10px] uppercase tracking-wide text-muted">Source</span>
        {r.source ? (
          isUrl ? (
            <a
              href={r.source}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-[11px] text-accent hover:text-ink transition-colors break-all text-right"
            >
              {r.source}
            </a>
          ) : (
            <span className="font-mono text-[11px] text-ink/70 break-all text-right">{r.source}</span>
          )
        ) : (
          <span className="text-[11px] text-muted">—</span>
        )}
      </div>

      {!matched ? (
        <p className="text-[12px] text-muted text-balance">
          No registry entry was confidently found. The fields above are left blank rather than
          guessed.
        </p>
      ) : null}
    </div>
  );
}

function Field({
  label,
  value,
  span = false,
  mono = false,
}: {
  label: string;
  value: string | null | undefined;
  span?: boolean;
  mono?: boolean;
}) {
  // Always render the row — every CSV column stays visible; empty -> dash.
  return (
    <div className={span ? 'sm:col-span-2' : undefined}>
      <dt className="text-[10px] uppercase tracking-wide text-muted">{label}</dt>
      <dd className={`${mono ? 'font-mono ' : ''}${value ? 'text-ink' : 'text-muted'}`}>
        {value || '—'}
      </dd>
    </div>
  );
}

function IdleState() {
  return (
    <section className="pt-10 pb-4 flex flex-col items-center text-center gap-3 text-muted animate-fade-in-up">
      <BigLens className="opacity-20" />
      <p className="text-sm max-w-xs text-balance">
        Type a company name, optionally with a jurisdiction (e.g. "Tesla, US"). We query every
        relevant register, then RapidFuzz + Claude pick the single best-matching registered entity.
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
