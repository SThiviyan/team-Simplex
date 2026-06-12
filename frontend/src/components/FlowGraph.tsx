import { useState } from 'react';
import { CompanyRecord, QueryRow, Winner } from '../api';
import { confidenceSignals, recordKey, SignalList } from './confidence';
import { PipelineData } from './PipelinePanel';

type Props = {
  data: PipelineData;
  queries: QueryRow[];
  onClose: () => void;
};

// The parent ("father") stages of the matching chain.
type StageId =
  | 'input'
  | 'routing'
  | 'fetch'
  | 'fuzzy'
  | 'jurisdiction'
  | 'semantic'
  | 'winner';

// One child of a stage node — a top-ranked item at that stage.
type RankedItem = {
  name: string;
  sub?: string;
  score?: number; // 0..1
  record?: CompanyRecord; // expandable confidence signals when present
  highlight?: boolean;
  muted?: boolean;
};

type StageDetail = {
  title: string;
  note?: string;
  items: RankedItem[];
};

// Status ring colors.
const GREEN = '#4E9B61';
const AMBER = '#D9A03F';
const RED = '#C7553F';
const GRAY = '#C2BAAB';

const CHILD_MAX = 8;

// Father circles scale with how many items reach the stage (sqrt so the AREA
// tracks the count); all circles are vertically centered on one horizontal
// axis inside a band of height BAND.
const SIZE_MIN = 38;
const SIZE_MAX = 78;
const BAND = SIZE_MAX;

function sizeFor(count: number, maxCount: number): number {
  const t = Math.sqrt(Math.max(count, 0) / Math.max(maxCount, 1));
  return Math.round(SIZE_MIN + (SIZE_MAX - SIZE_MIN) * Math.min(t, 1));
}

function ringForConf(conf: number): string {
  if (conf >= 0.7) return GREEN;
  if (conf >= 0.4) return AMBER;
  return RED;
}

function candName(c: CompanyRecord, fallback: string): string {
  return c.name_normalized_register_name ?? fallback;
}

/* --------------------------- children content --------------------------- */

/** Build the children (top-ranked items) revealed when a father is selected. */
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
        sub: 'skipped',
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
            ? `Top ${cands.length} of ${query.count} gathered records, ranked by source confidence.`
            : 'Ranked by the confidence the source reported at gather time.',
        items,
      };
    }
    case 'fuzzy': {
      const items = [...cands]
        .sort((a, b) => (b._match?.name_score ?? 0) - (a._match?.name_score ?? 0))
        .map((c) => asItem(c, c._match?.name_score, c.provider ?? 'register'));
      return {
        title: 'Fuzzy shortlist',
        note: 'RapidFuzz name similarity against the query; below-cutoff rows were dropped.',
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
              ? `${c.jurisdiction_confirmed ?? '—'} aligned`
              : `${c.jurisdiction_confirmed ?? '—'} mismatch`,
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
        items: w ? [asItem(w, winner.confidence, `final confidence — ${label}`)] : [],
      };
    }
  }
}

/* ------------------------------- visuals -------------------------------- */

function NodeCircle({
  size,
  ring,
  big,
  small,
  active = false,
}: {
  size: number;
  ring: string;
  big: string;
  small: string;
  active?: boolean;
}) {
  return (
    <span
      className="flex shrink-0 flex-col items-center justify-center rounded-full bg-paper shadow-sm"
      style={{
        width: size,
        height: size,
        border: `2.5px solid ${ring}`,
        boxShadow: active ? `0 0 0 5px ${ring}26` : undefined,
      }}
    >
      <span
        className="font-mono tabular-nums leading-none text-ink"
        style={{ fontSize: size >= 52 ? 15 : 12 }}
      >
        {big}
      </span>
      <span className="mt-1 leading-none text-muted" style={{ fontSize: 8.5 }}>
        {small}
      </span>
    </span>
  );
}

// A straight arrow whose shaft TAPERS: thin at the origin, growing thicker
// toward the arrowhead, ending in a solid tip pointing at the target.
function TaperedArrow({
  x1,
  y1,
  x2,
  y2,
  w0 = 1.2,
  w1 = 5,
  headLen = 8,
  color = '#BFB7A8',
}: {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  w0?: number; // shaft width at the start
  w1?: number; // shaft width at the arrowhead base
  headLen?: number;
  color?: string;
}) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  // Unit perpendicular, for offsetting the shaft edges.
  const px = -uy;
  const py = ux;
  // Base of the arrowhead.
  const bx = x2 - ux * headLen;
  const by = y2 - uy * headLen;
  const shaft = [
    `${x1 + (px * w0) / 2},${y1 + (py * w0) / 2}`,
    `${bx + (px * w1) / 2},${by + (py * w1) / 2}`,
    `${bx - (px * w1) / 2},${by - (py * w1) / 2}`,
    `${x1 - (px * w0) / 2},${y1 - (py * w0) / 2}`,
  ].join(' ');
  const hw = Math.max(w1 * 2, 8); // head width
  const head = [
    `${bx + (px * hw) / 2},${by + (py * hw) / 2}`,
    `${x2},${y2}`,
    `${bx - (px * hw) / 2},${by - (py * hw) / 2}`,
  ].join(' ');
  return (
    <g fill={color}>
      <polygon points={shaft} />
      <polygon points={head} />
    </g>
  );
}

// Straight tapered arrow between two consecutive fathers, centered on the
// shared horizontal node axis; the filtered-out count sits beneath it.
function Connector({ drop }: { drop?: number }) {
  const c = BAND / 2;
  return (
    <div className="flex shrink-0 flex-col items-center">
      <svg width="38" height={BAND} aria-hidden className="shrink-0">
        <TaperedArrow x1={3} y1={c} x2={35} y2={c} />
      </svg>
      {drop != null && drop > 0 ? (
        <span className="mt-1 whitespace-nowrap font-mono text-[10px] text-muted">−{drop}</span>
      ) : null}
    </div>
  );
}

// Straight tapered arrows fanning from the selected father down to its
// children. Drawn in a fixed 576-unit coordinate space matching the row's
// max width (children are evenly flex-distributed).
const FAN_W = 576;
const FAN_H = 44;

function ChildFan({ n }: { n: number }) {
  if (n === 0) return null;
  return (
    <svg
      className="block w-full"
      height={FAN_H}
      viewBox={`0 0 ${FAN_W} ${FAN_H}`}
      preserveAspectRatio="none"
      aria-hidden
    >
      {Array.from({ length: n }, (_, i) => {
        const x = ((i + 0.5) / n) * FAN_W;
        return (
          <TaperedArrow
            key={i}
            x1={FAN_W / 2}
            y1={2}
            x2={x}
            y2={FAN_H - 2}
            w0={1}
            w1={4}
            headLen={7}
          />
        );
      })}
    </svg>
  );
}

/* ------------------------------ per query ------------------------------- */

function QueryFlow({ winner, query }: { winner: Winner; query?: QueryRow }) {
  const [selected, setSelected] = useState<StageId | null>(null);
  const [openChild, setOpenChild] = useState<number | null>(null);

  const cands = winner.candidates ?? [];
  const hasJuris = !!winner.jurisdiction;
  const sources = query?.sources_called?.length ?? null;
  const fetched = query?.count ?? cands.length;
  const fuzzy = cands.length;
  const aligned = hasJuris ? cands.filter((c) => c._match?.jurisdiction_match).length : fuzzy;
  const matched = winner.decision === 'match';
  const verified = matched ? 1 : 0;
  const pct = Math.round((winner.confidence ?? 0) * 100);
  const label = winner.jurisdiction ? `${winner.name} [${winner.jurisdiction}]` : winner.name;

  const stages: {
    id: StageId;
    count: number; // items reaching this stage — drives the circle size
    big: string;
    small: string;
    label: string;
    sub: string;
    ring: string;
  }[] = [
    {
      id: 'input',
      count: 1,
      big: '1',
      small: 'query',
      label: 'Input',
      sub: winner.name,
      ring: GRAY,
    },
    {
      id: 'routing',
      count: sources ?? 0,
      big: sources === null ? '—' : String(sources),
      small: 'regs',
      label: 'Routing',
      sub: 'register selection',
      ring: sources ? GREEN : GRAY,
    },
    {
      id: 'fetch',
      count: fetched,
      big: String(fetched),
      small: 'items',
      label: 'Fetch',
      sub: 'gathered records',
      ring: fetched > 0 ? GREEN : RED,
    },
    {
      id: 'fuzzy',
      count: fuzzy,
      big: String(fuzzy),
      small: 'items',
      label: 'Fuzzy',
      sub: 'RapidFuzz filter',
      ring: fuzzy > 0 ? GREEN : RED,
    },
    {
      id: 'jurisdiction',
      count: aligned,
      big: String(aligned),
      small: 'items',
      label: 'Nation',
      sub: hasJuris ? `${winner.jurisdiction} alignment` : 'not constrained',
      ring: hasJuris ? (aligned > 0 ? GREEN : RED) : GRAY,
    },
    {
      id: 'semantic',
      count: verified,
      big: String(verified),
      small: 'match',
      label: 'Semantic',
      sub: 'Claude verification',
      ring: matched ? ringForConf(winner.confidence ?? 0) : RED,
    },
    {
      id: 'winner',
      count: verified,
      big: matched ? `${pct}%` : '0',
      small: matched ? 'conf' : 'none',
      label: 'Winner',
      sub: winner.winning_candidate?.name_normalized_register_name ?? 'no match',
      ring: matched ? ringForConf(winner.confidence ?? 0) : RED,
    },
  ];
  const maxCount = Math.max(...stages.map((s) => s.count), 1);

  // Items filtered out on the hop AFTER each stage (drawn next to connectors).
  const drops: (number | undefined)[] = [
    undefined,
    undefined,
    Math.max(0, fetched - fuzzy),
    hasJuris ? Math.max(0, fuzzy - aligned) : undefined,
    Math.max(0, aligned - verified),
    undefined,
  ];

  const select = (id: StageId | null) => {
    setSelected(id);
    setOpenChild(null);
  };

  const father = selected ? stages.find((s) => s.id === selected) : null;
  const detail = selected ? stageDetail(selected, winner, query, label) : null;
  const children = detail ? detail.items.slice(0, CHILD_MAX) : [];
  const overflow = detail ? detail.items.length - children.length : 0;
  const expanded = openChild !== null ? children[openChild] : null;

  return (
    <div className="animate-fade-in-up">
      <p className="mb-3 text-[13px]">
        <span className="font-medium text-ink">{label}</span>
        <span className="text-muted"> · {fuzzy} shortlisted · </span>
        <span className="capitalize text-muted">{winner.decision.replace('_', ' ')}</span>
      </p>

      {!father ? (
        /* ---- Chain view: fathers disposed on one horizontal axis, circle
               area proportional to the items reaching each stage. ---- */
        <div className="animate-fade-in">
          <div className="-mx-1 overflow-x-auto px-1 pb-1">
            <div className="flex items-start">
              {stages.map((s, i) => (
                <div key={s.id} className="contents">
                  <button
                    type="button"
                    onClick={() => select(s.id)}
                    className="group flex w-[88px] shrink-0 flex-col items-center gap-1.5 rounded-xl px-1 py-0.5 transition-colors hover:bg-paper"
                  >
                    <span
                      className="flex items-center justify-center"
                      style={{ height: BAND }}
                    >
                      <NodeCircle
                        size={sizeFor(s.count, maxCount)}
                        ring={s.ring}
                        big={s.big}
                        small={s.small}
                      />
                    </span>
                    <span className="w-full truncate text-center text-[11px] font-medium leading-tight text-ink group-hover:text-accent">
                      {s.label}
                    </span>
                    <span className="-mt-1 w-full truncate text-center text-[9.5px] leading-tight text-muted">
                      {s.sub}
                    </span>
                  </button>
                  {i < stages.length - 1 ? <Connector drop={drops[i]} /> : null}
                </div>
              ))}
            </div>
          </div>
          <p className="mt-2 text-[11px] text-muted">
            Click a stage to reveal its top-ranked items — circle size tracks how many items
            reached it.
          </p>
        </div>
      ) : (
        /* ---- Focus view: one father, its children fanned in below. ---- */
        <div className="animate-fade-in" key={father.id}>
          <button
            type="button"
            onClick={() => select(null)}
            className="mb-3 flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-[12px] text-muted transition-colors hover:border-accent/40 hover:text-ink"
          >
            <svg
              width="11"
              height="11"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
            All stages
          </button>

          <div className="flex flex-col items-center">
            <button
              type="button"
              onClick={() => select(null)}
              className="flex flex-col items-center gap-1.5"
              title="Back to all stages"
            >
              <NodeCircle
                size={Math.max(sizeFor(father.count, maxCount), 56)}
                ring={father.ring}
                big={father.big}
                small={father.small}
                active
              />
              <span className="text-[13px] font-semibold text-ink">{father.label}</span>
            </button>

            <div className="w-full max-w-xl">
              <ChildFan n={children.length} />
              {children.length ? (
                <div className="flex">
                  {children.map((item, i) => {
                    const p = typeof item.score === 'number' ? Math.round(item.score * 100) : null;
                    const ring = item.record
                      ? item.muted
                        ? RED
                        : ringForConf(item.score ?? item.record.confidence)
                      : item.muted
                        ? GRAY
                        : GREEN;
                    const big = p !== null ? String(p) : item.muted ? '✗' : '✓';
                    const small = p !== null ? '%' : 'reg';
                    const open = openChild === i;
                    return (
                      <button
                        key={`${item.name}-${i}`}
                        type="button"
                        onClick={
                          item.record ? () => setOpenChild(open ? null : i) : undefined
                        }
                        className={`flex min-w-0 flex-1 flex-col items-center gap-1.5 px-1 ${
                          item.record ? 'cursor-pointer' : 'cursor-default'
                        } ${item.muted ? 'opacity-60' : ''}`}
                      >
                        <NodeCircle size={48} ring={ring} big={big} small={small} active={open} />
                        <span
                          className={`w-full truncate text-center text-[10px] leading-tight ${
                            open ? 'font-semibold text-ink' : 'text-ink/80'
                          }`}
                        >
                          {item.name}
                          {item.highlight ? ' ★' : ''}
                        </span>
                        {item.sub ? (
                          <span className="-mt-1 w-full truncate text-center text-[9px] text-muted">
                            {item.sub}
                          </span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              ) : (
                <p className="text-center text-[12px] text-muted">Nothing reached this stage.</p>
              )}
              {overflow > 0 ? (
                <p className="mt-1 text-center font-mono text-[10px] text-muted">
                  +{overflow} more
                </p>
              ) : null}
            </div>
          </div>

          {detail?.note ? (
            <p className="mx-auto mt-3 max-w-prose text-center text-[12px] text-muted text-balance">
              {detail.note}
            </p>
          ) : null}

          {/* A clicked child reveals its confidence signals. */}
          {expanded?.record ? (
            <div className="mx-auto mt-3 max-w-md rounded-xl border border-line bg-paper px-4 py-3 animate-fade-in">
              <div className="flex items-baseline justify-between gap-3">
                <p className="truncate text-[13px] font-medium text-ink">
                  {expanded.name}
                  {expanded.highlight ? (
                    <span className="ml-1.5 text-[9px] font-semibold uppercase tracking-wide text-accent">
                      winner
                    </span>
                  ) : null}
                </p>
                {typeof expanded.score === 'number' ? (
                  <span className="shrink-0 font-mono text-[11px] tabular-nums text-accent">
                    {Math.round(expanded.score * 100)}%
                  </span>
                ) : null}
              </div>
              <div className="mt-2">
                <SignalList signals={confidenceSignals(expanded.record)} />
              </div>
            </div>
          ) : null}
        </div>
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
          <h2 className="text-lg font-semibold tracking-[-0.01em] text-ink">Data flow</h2>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-line px-3 py-1.5 text-sm text-muted transition-colors hover:border-accent/40 hover:text-ink"
        >
          Hide
        </button>
      </div>
      <div className="space-y-10">
        {data.winners.map((w) => (
          <QueryFlow key={w.query_id} winner={w} query={byId.get(w.query_id)} />
        ))}
      </div>
    </section>
  );
}
