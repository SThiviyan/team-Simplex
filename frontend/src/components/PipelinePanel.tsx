import { Winner } from '../api';
import { confidenceSignals, recordKey, SignalList } from './confidence';

export type Phase = 'idle' | 'running' | 'done' | 'error';

// Aggregate counts at each stage of the pipeline, derived from the response.
export type PipelineData = {
  fetched: number;
  jurisdictions: string[];
  fuzzy: number;
  matches: number;
  winners: Winner[];
};

type StageDef = { id: string; label: string; desc: string };

// The nodes the data flows through, top to bottom. `winner` is rendered apart.
const STAGES: StageDef[] = [
  { id: 'fetch', label: 'Fetching', desc: 'Querying company registers' },
  { id: 'nation', label: 'Nation filter', desc: 'Scoping to jurisdiction' },
  { id: 'fuzzy', label: 'Fuzzy filter', desc: 'RapidFuzz shortlist' },
  { id: 'semantic', label: 'Semantic filter', desc: 'Claude evaluation' },
];

type Props = {
  phase: Phase;
  activeStage: number; // index into STAGES while running
  data: PipelineData | null;
  flowOpen: boolean;
  onToggleFlow: () => void;
};

function stageCount(id: string, data: PipelineData | null): number | null {
  if (!data) return null;
  switch (id) {
    case 'fetch':
      return data.fetched;
    case 'nation':
      return data.fetched; // server gathers jurisdiction-scoped, so all kept
    case 'fuzzy':
      return data.fuzzy;
    case 'semantic':
      return data.matches;
    default:
      return null;
  }
}

function statusOf(i: number, phase: Phase, activeStage: number): 'pending' | 'active' | 'done' {
  if (phase === 'done') return 'done';
  if (phase === 'running') {
    if (i < activeStage) return 'done';
    if (i === activeStage) return 'active';
    return 'pending';
  }
  return 'pending';
}

function Connector({ flowing }: { flowing: boolean }) {
  return (
    <div className="ml-[10px] h-5 w-px bg-line relative" aria-hidden>
      {flowing
        ? [0, 1, 2].map((d) => (
            <span
              key={d}
              className="absolute -left-[2px] top-0 h-[5px] w-[5px] rounded-full bg-accent animate-flow-dot"
              style={{ animationDelay: `${d * 0.37}s` }}
            />
          ))
        : null}
    </div>
  );
}

function LiveBars() {
  return (
    <div className="flex h-4 items-end gap-[3px]" aria-hidden>
      {[0, 1, 2, 3].map((b) => (
        <span
          key={b}
          className="w-[3px] origin-bottom rounded-sm bg-accent/60 animate-bar-live"
          style={{ height: '100%', animationDelay: `${b * 0.12}s` }}
        />
      ))}
    </div>
  );
}

function CountReadout({
  status,
  count,
  phase,
}: {
  status: 'pending' | 'active' | 'done';
  count: number | null;
  phase: Phase;
}) {
  if (phase === 'done' && count !== null) {
    return <span className="font-mono text-xs tabular-nums text-ink">{count}</span>;
  }
  if (status === 'active') return <LiveBars />;
  if (status === 'done') {
    return <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />;
  }
  // pending
  return (
    <span
      className="block h-2 w-8 rounded bg-[linear-gradient(90deg,#E8E2D7_25%,#FAF7F2_50%,#E8E2D7_75%)] bg-[length:200%_100%] animate-shimmer"
      aria-hidden
    />
  );
}

function StageNode({
  stage,
  status,
  count,
  phase,
  data,
  max,
}: {
  stage: StageDef;
  status: 'pending' | 'active' | 'done';
  count: number | null;
  phase: Phase;
  data: PipelineData | null;
  max: number;
}) {
  const dot =
    status === 'done'
      ? 'bg-accent border-accent'
      : status === 'active'
        ? 'bg-paper border-accent ring-4 ring-accent/15'
        : 'bg-paper border-line';
  const widthPct = phase === 'done' && count !== null && max > 0 ? Math.max(4, (count / max) * 100) : 0;
  const sub =
    stage.id === 'nation' && data && data.jurisdictions.length
      ? `${stage.desc} · ${data.jurisdictions.join(', ')}`
      : stage.desc;

  return (
    <div className="flex gap-3">
      <span className={`mt-1 h-[21px] w-[21px] shrink-0 rounded-full border-2 transition-colors ${dot}`} />
      <div className="min-w-0 flex-1 pb-0.5">
        <div className="flex items-baseline justify-between gap-2">
          <span
            className={`text-[13px] font-medium ${
              status === 'pending' ? 'text-muted' : 'text-ink'
            }`}
          >
            {stage.label}
          </span>
          <CountReadout status={status} count={count} phase={phase} />
        </div>
        <p className="truncate text-[11px] text-muted">{sub}</p>
        {phase === 'done' ? (
          <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-line/60">
            <div
              className="h-full origin-left rounded-full bg-accent/70 animate-grow-x"
              style={{ width: `${widthPct}%` }}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}

// A hoverable result/candidate chip: hovering reveals the confidence marks.
function CandidateChip({
  winner,
  candidate,
  primary,
}: {
  winner: Winner;
  candidate: Winner['candidates'][number];
  primary?: boolean;
}) {
  const name = candidate.name_normalized_register_name ?? winner.name;
  const conf =
    primary && typeof winner.confidence === 'number'
      ? winner.confidence
      : candidate.confidence;
  return (
    <div className="group relative">
      <div
        className={`flex cursor-default items-center justify-between gap-2 rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors ${
          primary
            ? 'border-accent/40 bg-accent-soft/40 hover:bg-accent-soft/70'
            : 'border-line bg-paper hover:border-accent/40'
        }`}
      >
        <span className="truncate text-ink">{name}</span>
        {typeof conf === 'number' ? (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-accent">
            {Math.round(conf * 100)}%
          </span>
        ) : null}
      </div>
      {/* Confidence popover — opens to the left of the side panel. */}
      <div className="pointer-events-none absolute right-full top-0 z-20 mr-2 w-60 origin-top-right rounded-lg border border-line bg-paper p-3 opacity-0 shadow-lg shadow-ink/5 transition-opacity duration-150 group-hover:opacity-100">
        <p className="mb-2 font-mono text-[10px] uppercase tracking-wide text-muted">
          why this is confident
        </p>
        <SignalList signals={confidenceSignals(candidate)} />
      </div>
    </div>
  );
}

export function PipelinePanel({ phase, activeStage, data, flowOpen, onToggleFlow }: Props) {
  const max = data ? Math.max(data.fetched, 1) : 1;
  const primaryWinner = data?.winners.find((w) => w.decision === 'match' && w.winning_candidate);

  return (
    <div className="rounded-2xl border border-line bg-paper/70 p-4 backdrop-blur-sm">
      <div className="mb-3 flex items-center justify-between">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted">Data flow</p>
        <span
          className={`flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide ${
            phase === 'running' ? 'text-accent' : 'text-muted'
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              phase === 'running'
                ? 'animate-pulse bg-accent'
                : phase === 'done'
                  ? 'bg-accent'
                  : 'bg-line'
            }`}
          />
          {phase === 'running' ? 'live' : phase === 'done' ? 'done' : phase === 'error' ? 'error' : 'idle'}
        </span>
      </div>

      {/* Stage nodes + connectors */}
      <div>
        {STAGES.map((stage, i) => {
          const status = statusOf(i, phase, activeStage);
          const nextActive = phase === 'running' && i + 1 === activeStage;
          return (
            <div key={stage.id}>
              <StageNode
                stage={stage}
                status={status}
                count={stageCount(stage.id, data)}
                phase={phase}
                data={data}
                max={max}
              />
              {i < STAGES.length - 1 ? <Connector flowing={nextActive} /> : null}
            </div>
          );
        })}
      </div>

      {/* Winner / shortlist — only once the run is done. */}
      {phase === 'done' && data ? (
        <div className="mt-4 space-y-3 border-t border-line pt-4 animate-fade-in">
          {primaryWinner && primaryWinner.winning_candidate ? (
            <div className="space-y-1.5">
              <p className="font-mono text-[10px] uppercase tracking-wide text-muted">Winner</p>
              <CandidateChip
                winner={primaryWinner}
                candidate={primaryWinner.winning_candidate}
                primary
              />
            </div>
          ) : (
            <p className="text-[12px] text-muted">No confident winner.</p>
          )}

          {data.winners.some((w) => (w.candidates?.length ?? 0) > 1) ? (
            <div className="space-y-1.5">
              <p className="font-mono text-[10px] uppercase tracking-wide text-muted">
                Shortlist · hover for signals
              </p>
              <div className="space-y-1.5">
                {data.winners.flatMap((w) => {
                  const winnerKey = w.winning_candidate ? recordKey(w.winning_candidate) : null;
                  return (w.candidates ?? [])
                    .filter((c) => recordKey(c) !== winnerKey)
                    .slice(0, 4)
                    .map((c, idx) => (
                      <CandidateChip key={`${w.query_id}-${idx}`} winner={w} candidate={c} />
                    ));
                })}
              </div>
            </div>
          ) : null}

          <button
            type="button"
            onClick={onToggleFlow}
            className="group flex w-full items-center justify-center gap-1.5 rounded-lg border border-accent/30 bg-accent-soft/30 px-3 py-2 text-[12px] font-medium text-accent transition-colors hover:bg-accent-soft/60"
          >
            {flowOpen ? 'Hide data flow' : 'Show data flow'}
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`transition-transform ${flowOpen ? 'rotate-180' : 'group-hover:translate-y-0.5'}`}
              aria-hidden
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
        </div>
      ) : null}

      {phase === 'idle' ? (
        <p className="mt-4 border-t border-line pt-3 text-[11px] text-muted text-balance">
          Run a search to watch records flow through fetching, jurisdiction scoping, fuzzy
          matching and Claude's semantic verification.
        </p>
      ) : null}
    </div>
  );
}
