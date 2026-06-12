import { useEffect, useState } from 'react';
import { SearchBar } from './components/SearchBar';
import { FlowGraph } from './components/FlowGraph';
import { Phase, PipelineData, PipelinePanel } from './components/PipelinePanel';
import { csvSearch, QueryRow, Winner } from './api';

type State =
  | { kind: 'idle' }
  | { kind: 'loading'; query: string }
  | {
      kind: 'ok';
      query: string;
      count: number;
      queries: QueryRow[];
      winners: Winner[];
      file?: string;
    }
  | { kind: 'error'; query: string; message: string };

// The matching layer is a single HTTP call, so there is no server-streamed
// progress. While the request is in flight we advance an "active stage" on a
// timer to animate the live flow; on response we snap to the real counts.
function useStageProgress(running: boolean): number {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    if (!running) return;
    setStage(0);
    const id = setInterval(() => {
      // Walk fetch -> nation -> fuzzy -> semantic, then hold on the last.
      setStage((s) => (s < 3 ? s + 1 : s));
    }, 650);
    return () => clearInterval(id);
  }, [running]);
  return stage;
}

function pipelineData(state: State): PipelineData | null {
  if (state.kind !== 'ok') return null;
  const fetched = state.queries.reduce((acc, q) => acc + (q.count || 0), 0);
  const jurisdictions = [
    ...new Set(state.queries.map((q) => q.jurisdiction).filter((j): j is string => !!j)),
  ];
  const fuzzy = state.winners.reduce((acc, w) => acc + (w.candidates?.length ?? 0), 0);
  const matches = state.winners.filter((w) => w.decision === 'match').length;
  return { fetched, jurisdictions, fuzzy, matches, winners: state.winners };
}

export default function App() {
  const [state, setState] = useState<State>({ kind: 'idle' });
  const [showFlow, setShowFlow] = useState(false);

  async function runSearch(q: string) {
    setShowFlow(false);
    setState({ kind: 'loading', query: q });
    try {
      const r = await csvSearch(q);
      setState({
        kind: 'ok',
        query: q,
        count: r.count,
        queries: r.queries,
        winners: r.winners ?? [],
        file: r.output_file,
      });
    } catch (e) {
      setState({
        kind: 'error',
        query: q,
        message: e instanceof Error ? e.message : 'Search failed. Try again.',
      });
    }
  }

  const busy = state.kind === 'loading';
  const activeStage = useStageProgress(busy);
  const phase: Phase =
    state.kind === 'loading'
      ? 'running'
      : state.kind === 'ok'
        ? 'done'
        : state.kind === 'error'
          ? 'error'
          : 'idle';
  const data = pipelineData(state);
  const queries = state.kind === 'ok' ? state.queries : [];

  return (
    <Shell>
      <div className="space-y-6 min-w-0">
        <Header />
        <SearchBar onSubmit={runSearch} busy={busy} />
        <Results
          state={state}
          onRetry={() => state.kind === 'error' && runSearch(state.query)}
        />
        {showFlow && data ? (
          <FlowGraph data={data} queries={queries} onClose={() => setShowFlow(false)} />
        ) : null}
      </div>
      <aside className="lg:sticky lg:top-12 h-fit">
        <PipelinePanel
          phase={phase}
          activeStage={activeStage}
          data={data}
          flowOpen={showFlow}
          onToggleFlow={() => setShowFlow((v) => !v)}
        />
      </aside>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-5xl px-5 pt-12 pb-24 sm:pt-16">
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">{children}</div>
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

  // The matching layer (RapidFuzz + LLM semantic filter) picks one winning
  // company per query; show those final results.
  return (
    <section aria-label="Search complete" className="space-y-5 animate-fade-in-up">
      <div className="flex items-center gap-2 text-sm text-ink">
        <CheckIcon className="text-accent" />
        <span>
          Gathered <span className="font-semibold tabular-nums">{state.count}</span> record
          {state.count === 1 ? '' : 's'} ·{' '}
          <span className="font-semibold tabular-nums">{state.winners.length}</span> query
          {state.winners.length === 1 ? '' : 'ies'} resolved.
        </span>
      </div>

      <ul className="space-y-4">
        {state.winners.map((w) => (
          <WinnerCard key={w.query_id} winner={w} />
        ))}
      </ul>

      {state.file ? (
        <p className="font-mono text-[11px] text-muted">
          gathered records written to <span className="text-ink/70">{state.file}</span>
        </p>
      ) : null}
    </section>
  );
}

function WinnerCard({ winner }: { winner: Winner }) {
  const queryLabel = winner.jurisdiction
    ? `${winner.name} [${winner.jurisdiction}]`
    : winner.name;
  const c = winner.winning_candidate;
  const pct = Math.round((winner.confidence ?? 0) * 100);

  if (winner.decision !== 'match' || !c) {
    return (
      <li className="rounded-xl border border-line bg-paper px-4 py-4 space-y-2">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm font-medium text-ink">{queryLabel}</span>
          <span className="font-mono text-[11px] uppercase tracking-wide text-muted">
            {winner.decision === 'recursive_search' ? 'needs re-search' : 'no match'}
          </span>
        </div>
        {winner.decision === 'recursive_search' && winner.recursive_search ? (
          <p className="text-[13px] text-ink">
            Try searching for{' '}
            <span className="font-semibold">
              {winner.recursive_search.suggested_query}
            </span>
            .
          </p>
        ) : null}
        {winner.reasoning ? (
          <p className="text-[13px] text-muted text-balance">{winner.reasoning}</p>
        ) : null}
      </li>
    );
  }

  return (
    <li className="rounded-xl border border-accent/30 bg-accent-soft/30 px-4 py-4 space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <div className="space-y-0.5">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted">
            {queryLabel}
          </p>
          <h3 className="text-lg font-semibold tracking-[-0.01em] text-ink">
            {c.name_normalized_register_name}
          </h3>
        </div>
        <span
          className="shrink-0 rounded-full bg-accent/10 px-2.5 py-1 text-xs font-semibold tabular-nums text-accent"
          title="match confidence"
        >
          {pct}%
        </span>
      </div>

      <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-2">
        <Field label="Jurisdiction" value={c.jurisdiction_confirmed} />
        <Field label="Organization type" value={c.organization_type} />
        <Field label="Registry ID" value={c.registry_id} />
        <Field label="Registry court" value={c.registry_court} />
        <Field label="Address" value={c.address} span />
        <Field label="Last update" value={c.last_update} />
        <Field label="Source" value={c.provider} />
      </dl>

      {c.source ? (
        <a
          href={c.source}
          target="_blank"
          rel="noreferrer"
          className="inline-block font-mono text-[11px] text-accent hover:text-ink transition-colors break-all"
        >
          {c.source}
        </a>
      ) : null}

      {winner.reasoning ? (
        <p className="text-[12px] text-muted text-balance border-t border-line pt-2">
          {winner.reasoning}
        </p>
      ) : null}
    </li>
  );
}

function Field({
  label,
  value,
  span = false,
}: {
  label: string;
  value: string | null | undefined;
  span?: boolean;
}) {
  if (!value) return null;
  return (
    <div className={span ? 'sm:col-span-2' : undefined}>
      <dt className="text-[10px] uppercase tracking-wide text-muted">{label}</dt>
      <dd className="text-ink">{value}</dd>
    </div>
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
