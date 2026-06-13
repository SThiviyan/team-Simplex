import { ExtractionResult, PipelineEvent } from '../api';

// ---------------------------------------------------------------------------
// Pipeline flow graph in the MCP-branch style: circular "father" stage nodes on
// one horizontal axis, each ringed green/amber/red by the confidence at that
// stage and sized by how many items reached it, with fan connectors between and
// the confidence trajectory along the way. Driven by the run's event log.

// Status ring colors (from the MCP branch).
const GREEN = '#4E9B61';
const AMBER = '#D9A03F';
const RED = '#C7553F';
const GRAY = '#C2BAAB';

const SIZE_MIN = 40;
const SIZE_MAX = 78;
const BAND = SIZE_MAX;

function sizeFor(count: number, maxCount: number): number {
  // Area tracks the count (sqrt), like the MCP father circles.
  const t = Math.sqrt(Math.max(count, 0) / Math.max(maxCount, 1));
  return Math.round(SIZE_MIN + (SIZE_MAX - SIZE_MIN) * Math.min(t, 1));
}
function ringForConf(conf: number | undefined): string {
  if (conf === undefined) return GRAY;
  if (conf >= 0.7) return GREEN;
  if (conf >= 0.4) return AMBER;
  return RED;
}

type StageId = 'gather' | 'identify' | 'ground' | 'match' | 'enrich' | 'winner';

type Stage = {
  id: StageId;
  label: string;
  state: 'pending' | 'active' | 'done' | 'skipped';
  count: number; // items reaching this stage (drives circle size)
  confidence?: number; // confidence observed here (drives ring color)
  grounded?: boolean;
  detail?: string;
  emphasis?: boolean; // the outcome node (Winner, or Match when no winner id)
  displayConfidence?: number; // % to show — the final calibrated score (matches the card)
};

const STAGE_ORDER: StageId[] = ['gather', 'identify', 'ground', 'match', 'enrich', 'winner'];
const STAGE_LABEL: Record<StageId, string> = {
  gather: 'Gather',
  identify: 'Identify',
  ground: 'Ground',
  match: 'Match',
  enrich: 'Enrich',
  winner: 'Winner',
};
const EVENT_STAGE: Record<string, StageId> = {
  mcp_selected: 'gather',
  mcp_connected: 'gather',
  tool_call: 'gather',
  tool_result: 'gather',
  web_search_fallback: 'gather',
  fastpath_match: 'identify',
  agent_answer: 'identify',
  grounding_check: 'ground',
  eval_started: 'match',
  eval_result: 'match',
  eval_skipped: 'match',
  recursion_triggered: 'match',
  agent_override: 'match',
  records_salvaged: 'match',
  impressum_checked: 'enrich',
  enrichment_web_fill: 'enrich',
  enrichment_done: 'enrich',
  query_completed: 'winner',
};

export type Contradiction = { field: string; values: { value: string; source: string }[] };

export function buildStages(events: PipelineEvent[]): {
  stages: Stage[];
  trajectory: { label: string; confidence: number }[];
  contradictions: Contradiction[];
} {
  const map = new Map<StageId, Stage>();
  for (const id of STAGE_ORDER) {
    map.set(id, { id, label: STAGE_LABEL[id], state: 'pending', count: 0 });
  }
  const lastByStage = new Map<string, number>();
  const contraByField = new Map<string, Contradiction>();

  for (const e of events) {
    if (e.event_type === 'contradiction') {
      const p = e.payload as Record<string, any>;
      contraByField.set(p.field, { field: p.field, values: p.values ?? [] });
    }
    const id = EVENT_STAGE[e.event_type];
    if (!id) continue;
    const st = map.get(id)!;
    st.state = 'done';
    const p = e.payload as Record<string, any>;

    if (e.event_type === 'tool_result' && typeof p.record_count === 'number') {
      st.count += p.record_count;
    }
    if (e.event_type === 'eval_started' && typeof p.record_count === 'number') {
      st.count = Math.max(st.count, p.record_count);
    }
    if (e.event_type === 'eval_result' && typeof p.kept_candidates === 'number') {
      st.count = p.kept_candidates;
    }
    if (e.event_type === 'fastpath_match') st.detail = 'single match';
    if (e.event_type === 'eval_skipped') st.detail = 'skipped';
    if (e.event_type === 'grounding_check') {
      st.grounded = !!p.grounded;
      st.detail = p.grounded ? 'grounded' : 'blanked';
    }
    if (e.event_type === 'query_completed') {
      st.count = p.registry_id ? 1 : 0;
      st.detail = p.registry_id ?? p.no_match_reason ?? '';
    }
    if (typeof p.confidence === 'number') {
      st.confidence = p.confidence;
      const label = TRAJECTORY_LABEL[e.event_type];
      // Keep only the LATEST confidence per stage (the agent / matcher can fire
      // many times over a multi-round resolution — show one point each, not one
      // per event).
      if (label) lastByStage.set(label, p.confidence);
    }
  }

  const lastDone = STAGE_ORDER.reduce((acc, id, i) => (map.get(id)!.state === 'done' ? i : acc), -1);
  STAGE_ORDER.forEach((id, i) => {
    const st = map.get(id)!;
    if (st.state === 'pending' && i < lastDone) st.state = 'skipped';
    if (st.state === 'pending' && i === lastDone + 1) st.state = 'active';
  });
  const trajectory = TRAJECTORY_ORDER.filter((l) => lastByStage.has(l)).map((l) => ({
    label: l,
    confidence: lastByStage.get(l)!,
  }));
  return {
    stages: STAGE_ORDER.map((id) => map.get(id)!),
    trajectory,
    contradictions: [...contraByField.values()],
  };
}

const TRAJECTORY_LABEL: Record<string, string> = {
  agent_answer: 'agent',
  eval_result: 'matcher',
  enrichment_done: 'final',
};
const TRAJECTORY_ORDER = ['agent', 'matcher', 'final'];

export function FlowGraph({
  events,
  finalConfidence,
  hasId,
}: {
  events: PipelineEvent[];
  finalConfidence?: number; // the result's calibrated confidence (== the card's %)
  hasId?: boolean; // did the run end with a registry_id?
}) {
  const { stages, trajectory, contradictions } = buildStages(events);
  if (events.length === 0) return null;
  const maxCount = Math.max(...stages.map((s) => s.count), 1);

  // The OUTCOME node: the Winner when a registry_id was found, otherwise Match —
  // so when Enrich/Winner produced nothing, Match is the highlighted endpoint.
  // It shows the FINAL calibrated confidence (the same number as the result card
  // below), keeping the diagram and the card in lock-step.
  const winnerStage = stages.find((s) => s.id === 'winner');
  const ended = hasId ?? (winnerStage ? winnerStage.count > 0 : false);
  const finalConf =
    finalConfidence ??
    trajectory.find((t) => t.label === 'final')?.confidence ??
    trajectory[trajectory.length - 1]?.confidence;
  const outcomeId: StageId = ended ? 'winner' : 'match';
  const outcome = stages.find((s) => s.id === outcomeId && s.state !== 'pending');
  if (outcome) {
    outcome.emphasis = true;
    if (finalConf !== undefined) outcome.displayConfidence = finalConf;
  }

  return (
    <div className="space-y-3 rounded-xl border border-line bg-paper px-3 py-3">
      <div className="-mx-1 overflow-x-auto px-1">
        <div className="flex items-start">
          {stages.map((s, i) => (
            <div key={s.id} className="contents">
              <StageNode stage={s} maxCount={maxCount} />
              {i < stages.length - 1 ? <Connector active={s.state === 'done'} /> : null}
            </div>
          ))}
        </div>
      </div>
      {trajectory.length > 0 ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-2 text-[11px]">
          <span className="font-mono uppercase tracking-wide text-muted">confidence</span>
          {trajectory.map((t, i) => (
            <span key={i} className="flex items-center gap-1">
              {i > 0 ? <span className="text-muted/40">→</span> : null}
              <span className="text-muted">{t.label}</span>
              <span
                className="font-semibold tabular-nums"
                style={{ color: ringForConf(t.confidence) }}
              >
                {Math.round(t.confidence * 100)}%
              </span>
            </span>
          ))}
        </div>
      ) : null}
      {contradictions.length > 0 ? (
        <div className="space-y-1 border-t border-line pt-2">
          <p className="text-[11px] font-medium" style={{ color: AMBER }}>
            ⚠ {contradictions.length} source contradiction{contradictions.length === 1 ? '' : 's'} —
            field{contradictions.length === 1 ? '' : 's'} left blank, confidence lowered
          </p>
          {contradictions.map((c, i) => (
            <p key={i} className="text-[10px] text-muted">
              <span className="font-mono">{c.field}</span>:{' '}
              {c.values.map((v, j) => (
                <span key={j}>
                  {j > 0 ? ' ≠ ' : ''}
                  <span className="text-ink/70">{v.value}</span>
                  <span className="text-muted/60"> ({v.source})</span>
                </span>
              ))}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function StageNode({ stage, maxCount }: { stage: Stage; maxCount: number }) {
  const active = stage.state === 'done' || stage.state === 'active';
  // The displayed confidence: the final calibrated score on the emphasised
  // outcome node (so it equals the result card), else the stage's own value.
  const shownConf = stage.emphasis && stage.displayConfidence !== undefined
    ? stage.displayConfidence
    : stage.confidence;
  // The outcome is "finalised" once we know the calibrated score; until then the
  // run is still processing.
  const emphasisFinal = stage.emphasis && stage.displayConfidence !== undefined;
  // Emphasised outcome node: a bit bigger + thicker ring so it reads as the
  // endpoint, but not oversized.
  const EMPHASIS_SIZE = 58;
  const size = stage.emphasis
    ? EMPHASIS_SIZE
    : active
      ? sizeFor(Math.max(stage.count, 1), maxCount)
      : SIZE_MIN;
  const ring =
    stage.state === 'pending'
      ? GRAY
      : stage.id === 'ground'
        ? stage.grounded === false
          ? RED
          : GREEN
        : ringForConf(shownConf);
  const dim = stage.state === 'pending' || stage.state === 'skipped';

  return (
    <div className="flex w-[84px] shrink-0 flex-col items-center gap-1.5">
      <div className="flex items-center justify-center" style={{ height: BAND }}>
        <div
          className={`flex flex-col items-center justify-center rounded-full transition-all ${
            stage.state === 'active' ? 'animate-pulse' : ''
          } ${stage.emphasis ? 'shadow-md ring-2 ring-offset-2' : 'shadow-sm bg-paper'}`}
          style={{
            width: size,
            height: size,
            border: `${stage.emphasis ? 3.5 : 2.5}px solid ${ring}`,
            background: stage.emphasis ? `${ring}1a` : undefined, // faint tint of the ring color
            opacity: dim ? 0.45 : 1,
            ...(stage.emphasis ? { ['--tw-ring-color' as any]: `${ring}55` } : {}),
          }}
          title={stage.detail || stage.label}
        >
          {shownConf !== undefined ? (
            <span className="font-mono text-[12px] font-semibold tabular-nums leading-none text-ink">
              {Math.round(shownConf * 100)}%
            </span>
          ) : stage.count > 0 ? (
            <span className="font-mono text-[12px] tabular-nums leading-none text-ink">
              {stage.count}
            </span>
          ) : (
            <span className="leading-none text-muted" style={{ fontSize: 9 }}>
              {stage.state === 'skipped' ? '–' : ''}
            </span>
          )}
        </div>
      </div>
      <span
        className={`w-full truncate text-center text-[11px] leading-tight font-medium ${
          stage.emphasis
            ? 'font-semibold ' + (emphasisFinal ? '' : 'text-ink') // black while processing
            : dim
              ? 'text-muted'
              : 'text-ink'
        }`}
        // Once finalised, the label color matches the circle's ring (the
        // confidence color), so green at 95% reads green, not red.
        style={emphasisFinal ? { color: ring } : undefined}
      >
        {stage.label}
      </span>
      <span className="-mt-1 w-full truncate text-center text-[9px] leading-tight text-muted">
        {emphasisFinal ? 'winner' : stage.emphasis ? '' : stage.detail || ''}
      </span>
    </div>
  );
}

function Connector({ active }: { active: boolean }) {
  return (
    <div className="flex shrink-0 items-center" style={{ height: BAND, width: 18 }}>
      <svg width="18" height="10" aria-hidden>
        <line
          x1="0"
          y1="5"
          x2="18"
          y2="5"
          stroke={active ? GREEN : GRAY}
          strokeWidth="1.5"
          strokeDasharray={active ? '' : '2 2'}
        />
      </svg>
    </div>
  );
}

// --- Confidence signals: the SOUND breakdown of the final score -------------

type Signal = { label: string; value: string; ok: boolean };

export function confidenceSignals(r: ExtractionResult, queryJurisdiction?: string): Signal[] {
  const s: Signal[] = [];
  const flag = r.confidence_flag ?? '';
  s.push({
    label: 'Registry-backed identity',
    value: r.registry_id ? `id ${r.registry_id}` : 'no official number',
    ok: !!r.registry_id,
  });
  s.push({
    label: 'Source authority',
    value:
      flag === 'verified'
        ? 'national register / corroborated'
        : flag === 'probable'
          ? 'single source'
          : flag || '—',
    ok: flag === 'verified',
  });
  if (queryJurisdiction) {
    const country = (x: string) => x.split('-')[0].toUpperCase();
    const aligned =
      !!r.jurisdiction_confirmed &&
      country(r.jurisdiction_confirmed) === country(queryJurisdiction);
    s.push({ label: 'Jurisdiction alignment', value: r.jurisdiction_confirmed ?? '—', ok: aligned });
  }
  const tierA = ['registered_address', 'incorporation_date', 'organization_type', 'status'] as const;
  const filled = tierA.filter((k) => r[k]).length;
  s.push({
    label: 'Datapoint coverage',
    value: `${filled}/${tierA.length} Tier A fields`,
    ok: filled >= 3,
  });
  return s;
}

export function ConfidenceSignals({
  result,
  queryJurisdiction,
}: {
  result: ExtractionResult;
  queryJurisdiction?: string;
}) {
  const signals = confidenceSignals(result, queryJurisdiction);
  return (
    <ul className="space-y-1 border-t border-line pt-2 text-[12px]">
      {signals.map((sig) => (
        <li key={sig.label} className="flex items-baseline justify-between gap-3">
          <span className="flex items-center gap-1.5 text-muted">
            <span className={sig.ok ? 'text-emerald-600' : 'text-muted/50'}>
              {sig.ok ? '✓' : '○'}
            </span>
            {sig.label}
          </span>
          <span className={`text-right ${sig.ok ? 'text-ink' : 'text-muted'}`}>{sig.value}</span>
        </li>
      ))}
    </ul>
  );
}
