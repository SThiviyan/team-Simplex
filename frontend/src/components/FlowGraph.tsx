import { useState } from 'react';
import { CompanyRecord, QueryRow, Winner } from '../api';
import { confidenceSignals, recordKey, SignalList } from './confidence';
import { PipelineData } from './PipelinePanel';

type Props = {
  data: PipelineData;
  queries: QueryRow[];
  onClose: () => void;
};

// The stages of the matching chain, as graph nodes.
type StageId =
  | 'input'
  | 'routing'
  | 'fetch'
  | 'fuzzy'
  | 'jurisdiction'
  | 'semantic'
  | 'winner';

type StageDef = {
  id: StageId;
  label: string;
  count: number | null;
  tone?: 'winner' | 'muted';
};

// One entry in a node's "top ranked" detail list.
type RankedItem = {
  name: string;
  sub?: string;
  score?: number; // 0..1 — rendered as a percentage bar
  record?: CompanyRecord; // expandable confidence signals when present
  highlight?: boolean;
  muted?: boolean;
};

type StageDetail = {
  title: string;
  note?: string;
  items: RankedItem[];
};

function candName(c: CompanyRecord, fallback: string): string {
  return c.name_normalized_register_name ?? fallback;
}

/** Build the ranked list shown when a stage node is clicked. */
function stageDetail(
  stage: StageId,
  winner: Winner,
  query: QueryRow | undefined,
  label: string,
): StageDetail {
  const cands = winner.candidates ?? [];
  const winnerKey = winner.winning_candidate ? recordKey(winner.winning_candidate) : null;
  const asItem = (c: CompanyRecord, score?: number, sub?: string): RankedItem => ({
    name: candName(c, winner.name),
    sub: sub ?? c.provider ?? undefined,
    score,
    record: c,
    highlight: recordKey(c) === winnerKey,
  });

  switch (stage) {
    case 'input':
      return {
        title: 'Input query',
        items: [
          {
            name: winner.name,
            sub: winner.jurisdiction
              ? `target jurisdiction ${winner.jurisdiction}`
              : 'no jurisdiction constraint',
          },
        ],
      };
    case 'routing': {
      const called = (query?.sources_called ?? []).map((s) => ({ name: s, sub: 'queried' }));
      const skipped = (query?.sources_skipped ?? []).map((s) => ({
        name: s,
        sub: 'skipped — cannot match jurisdiction',
        muted: true,
      }));
      return {
        title: 'Registers routed',
        note: 'National registers are only called when they can match the target jurisdiction.',
        items: [...called, ...skipped],
      };
    }
    case 'fetch': {
      const items = [...cands]
        .sort((a, b) => (b._match?.prior_confidence ?? 0) - (a._match?.prior_confidence ?? 0))
        .map((c) => asItem(c, c._match?.prior_confidence));
      return {
        title: 'Top fetched records',
        note:
          query && query.count > cands.length
            ? `Showing the top ${cands.length} of ${query.count} gathered records (ranked by source confidence) — the rest were dropped before reaching the browser.`
            : 'Ranked by the confidence the source reported at gather time.',
        items,
      };
    }
    case 'fuzzy': {
      const items = [...cands]
        .sort((a, b) => (b._match?.name_score ?? 0) - (a._match?.name_score ?? 0))
        .map((c) =>
          asItem(c, c._match?.name_score, `${c.provider ?? 'register'} · token-sort similarity`),
        );
      return {
        title: 'Fuzzy shortlist',
        note: 'RapidFuzz token-sort similarity against the queried name; below-cutoff rows were dropped.',
        items,
      };
    }
    case 'jurisdiction': {
      const items = [...cands]
        .sort((a, b) => b.confidence - a.confidence)
        .map((c) => ({
          ...asItem(
            c,
            c.confidence,
            c._match?.jurisdiction_match
              ? `${c.jurisdiction_confirmed ?? '—'} · aligned`
              : `${c.jurisdiction_confirmed ?? '—'} · mismatch (demoted)`,
          ),
          muted: winner.jurisdiction ? !c._match?.jurisdiction_match : false,
        }));
      return {
        title: 'Jurisdiction alignment',
        note: winner.jurisdiction
          ? `Candidates outside ${winner.jurisdiction} are demoted rather than discarded.`
          : 'No jurisdiction given — nothing was demoted at this stage.',
        items,
      };
    }
    case 'semantic': {
      const items = [...cands]
        .sort((a, b) => b.confidence - a.confidence)
        .map((c) => asItem(c, c.confidence));
      return {
        title: 'Semantic ranking',
        note: winner.reasoning || undefined,
        items,
      };
    }
    case 'winner': {
      const w = winner.winning_candidate;
      return {
        title: 'Winner',
        note:
          winner.decision === 'recursive_search' && winner.recursive_search
            ? `No direct match — suggested re-search: "${winner.recursive_search.suggested_query}"`
            : winner.decision === 'no_match'
              ? winner.reasoning || 'No confident match.'
              : undefined,
        items: w ? [asItem(w, winner.confidence, `final calibrated confidence — ${label}`)] : [],
      };
    }
  }
}

/* ------------------------------- visuals -------------------------------- */

function NodeCircle({
  stage,
  active,
  onClick,
}: {
  stage: StageDef;
  active: boolean;
  onClick: () => void;
}) {
  const tone =
    stage.tone === 'winner'
      ? active
        ? 'border-accent bg-accent text-white'
        : 'border-accent/60 bg-accent-soft/60 text-accent hover:bg-accent-soft'
      : active
        ? 'border-accent bg-accent text-white'
        : stage.tone === 'muted'
          ? 'border-line bg-paper/60 text-muted hover:border-accent/40'
          : 'border-line bg-paper text-ink hover:border-accent/50';
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="group flex w-16 shrink-0 flex-col items-center gap-1.5 focus:outline-none"
    >
      <span
        className={`flex h-12 w-12 items-center justify-center rounded-full border-2 font-mono text-sm tabular-nums shadow-sm transition-all ${tone} ${
          active ? 'ring-4 ring-accent/15 scale-105' : ''
        }`}
      >
        {stage.count === null ? '—' : stage.count}
      </span>
      <span
        className={`text-center text-[10px] leading-tight ${
          active ? 'font-semibold text-ink' : 'text-muted group-hover:text-ink'
        }`}
      >
        {stage.label}
      </span>
    </button>
  );
}

function Edge({ drop }: { drop?: number }) {
  return (
    <div className="flex min-w-5 flex-1 flex-col items-center pt-[23px]" aria-hidden>
      <span className="relative h-px w-full bg-line">
        <span className="absolute -right-px -top-[3px] h-0 w-0 border-y-[3.5px] border-l-[5px] border-y-transparent border-l-line" />
      </span>
      {drop != null && drop > 0 ? (
        <span className="mt-1 whitespace-nowrap font-mono text-[9px] text-muted">−{drop}</span>
      ) : null}
    </div>
  );
}

function RankedRow({ rank, item }: { rank: number; item: RankedItem }) {
  const [open, setOpen] = useState(false);
  const pct = typeof item.score === 'number' ? Math.round(item.score * 100) : null;
  const expandable = !!item.record;
  return (
    <li
      className={`rounded-lg border px-3 py-2 transition-colors ${
        item.highlight
          ? 'border-accent/40 bg-accent-soft/30'
          : item.muted
            ? 'border-line bg-paper/50 opacity-70'
            : 'border-line bg-paper'
      } ${expandable ? 'cursor-pointer hover:border-accent/40' : ''}`}
      onClick={expandable ? () => setOpen((v) => !v) : undefined}
    >
      <div className="flex items-center gap-3">
        <span className="w-5 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted">
          {rank}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium text-ink">
            {item.name}
            {item.highlight ? (
              <span className="ml-1.5 text-[9px] font-semibold uppercase tracking-wide text-accent">
                winner
              </span>
            ) : null}
          </p>
          {item.sub ? <p className="truncate text-[11px] text-muted">{item.sub}</p> : null}
        </div>
        {pct !== null ? (
          <div className="flex w-28 shrink-0 items-center gap-2">
            <div className="h-1 flex-1 overflow-hidden rounded-full bg-line/60">
              <div
                className="h-full origin-left rounded-full bg-accent/70 animate-grow-x"
                style={{ width: `${Math.max(3, pct)}%` }}
              />
            </div>
            <span className="w-9 text-right font-mono text-[11px] tabular-nums text-accent">
              {pct}%
            </span>
          </div>
        ) : null}
        {expandable ? (
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={`shrink-0 text-muted transition-transform ${open ? 'rotate-180' : ''}`}
            aria-hidden
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        ) : null}
      </div>
      {open && item.record ? (
        <div className="mt-2 border-t border-line pt-2 animate-fade-in">
          <SignalList signals={confidenceSignals(item.record)} />
        </div>
      ) : null}
    </li>
  );
}

/* ------------------------------ per query ------------------------------- */

function QueryFlow({ winner, query }: { winner: Winner; query?: QueryRow }) {
  const [selected, setSelected] = useState<StageId | null>(null);

  const cands = winner.candidates ?? [];
  const hasJuris = !!winner.jurisdiction;
  const sources = query?.sources_called?.length ?? null;
  const fetched = query?.count ?? cands.length;
  const fuzzy = cands.length;
  const aligned = hasJuris ? cands.filter((c) => c._match?.jurisdiction_match).length : fuzzy;
  const verified = winner.decision === 'match' ? 1 : 0;
  const label = winner.jurisdiction ? `${winner.name} [${winner.jurisdiction}]` : winner.name;

  const stages: StageDef[] = [
    { id: 'input', label: 'Input', count: 1 },
    { id: 'routing', label: 'Routing', count: sources },
    { id: 'fetch', label: 'Fetch', count: fetched },
    { id: 'fuzzy', label: 'Fuzzy', count: fuzzy },
    { id: 'jurisdiction', label: 'Nation', count: aligned, tone: hasJuris ? undefined : 'muted' },
    { id: 'semantic', label: 'Semantic', count: verified },
    { id: 'winner', label: 'Winner', count: verified, tone: 'winner' },
  ];
  // Items dropped on the way INTO the next node, shown on the edges.
  const drops: (number | undefined)[] = [
    undefined,
    undefined,
    Math.max(0, fetched - fuzzy),
    hasJuris ? Math.max(0, fuzzy - aligned) : undefined,
    Math.max(0, aligned - verified),
    undefined,
  ];

  const detail = selected ? stageDetail(selected, winner, query, label) : null;

  return (
    <div className="animate-fade-in-up">
      <p className="mb-3 text-[13px]">
        <span className="font-medium text-ink">{label}</span>
        <span className="text-muted"> · {fuzzy} shortlisted · </span>
        <span className="capitalize text-muted">{winner.decision.replace('_', ' ')}</span>
      </p>

      {/* The node graph: clickable nodes joined by edges with drop counts. */}
      <div className="-mx-1 overflow-x-auto px-1 pb-1">
        <div className="flex min-w-[560px] items-start">
          {stages.map((s, i) => (
            <div key={s.id} className="contents">
              <NodeCircle
                stage={s}
                active={selected === s.id}
                onClick={() => setSelected((cur) => (cur === s.id ? null : s.id))}
              />
              {i < stages.length - 1 ? <Edge drop={drops[i]} /> : null}
            </div>
          ))}
        </div>
      </div>

      {/* Top-ranked items for the clicked node. */}
      {detail ? (
        <div className="mt-3 rounded-xl border border-line bg-paper px-4 py-3 animate-fade-in">
          <p className="font-mono text-[10px] uppercase tracking-wide text-muted">
            {detail.title} · top ranked
          </p>
          {detail.note ? (
            <p className="mt-1 max-w-prose text-[12px] text-muted text-balance">{detail.note}</p>
          ) : null}
          {detail.items.length ? (
            <ul className="mt-2.5 space-y-1.5">
              {detail.items.map((item, i) => (
                <RankedRow key={`${item.name}-${i}`} rank={i + 1} item={item} />
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-[12px] text-muted">Nothing reached this stage.</p>
          )}
        </div>
      ) : (
        <p className="mt-2 text-[11px] text-muted">
          Click a node to see the top-ranked items at that stage.
        </p>
      )}
    </div>
  );
}

export function FlowGraph({ data, queries, onClose }: Props) {
  const byId = new Map(queries.map((q) => [q.query_id, q]));
  return (
    <section
      aria-label="Data flow graph"
      className="rounded-2xl border border-line bg-paper/60 p-5 animate-fade-in-up"
    >
      <div className="mb-4 flex items-center justify-between">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted">Process</p>
          <h2 className="text-lg font-semibold tracking-[-0.01em] text-ink">Data flow graph</h2>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-line px-3 py-1.5 text-sm text-muted transition-colors hover:border-accent/40 hover:text-ink"
        >
          Hide
        </button>
      </div>
      <div className="space-y-8">
        {data.winners.map((w) => (
          <QueryFlow key={w.query_id} winner={w} query={byId.get(w.query_id)} />
        ))}
      </div>
    </section>
  );
}
