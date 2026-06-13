import { ExtractionResult, PipelineEvent } from '../api';

// ---------------------------------------------------------------------------
// Pipeline flow graph + confidence-along-the-way, driven by the run's event
// log. Mirrors the MCP branch's idea: show the stages the company passes
// through and how confident the system is at each, ending in a calibrated
// final score with the signals that justify it.

type StageState = 'pending' | 'active' | 'done' | 'skipped';

type Stage = {
  key: string;
  label: string;
  state: StageState;
  detail?: string;
  confidence?: number; // confidence observed at this stage, if any
};

const STAGE_ORDER = ['gather', 'identify', 'ground', 'match', 'enrich', 'done'] as const;
const STAGE_LABEL: Record<string, string> = {
  gather: 'Gather',
  identify: 'Identify',
  ground: 'Ground',
  match: 'Match',
  enrich: 'Enrich',
  done: 'Result',
};

// Which stage each event type belongs to.
const EVENT_STAGE: Record<string, string> = {
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
  query_completed: 'done',
};

/** Build the staged view + the confidence trajectory from a run's events. */
export function buildStages(events: PipelineEvent[]): {
  stages: Stage[];
  trajectory: { label: string; confidence: number }[];
} {
  const seen = new Map<string, Stage>();
  for (const key of STAGE_ORDER) {
    seen.set(key, { key, label: STAGE_LABEL[key], state: 'pending' });
  }
  const trajectory: { label: string; confidence: number }[] = [];

  for (const e of events) {
    const stageKey = EVENT_STAGE[e.event_type];
    if (!stageKey) continue;
    const st = seen.get(stageKey)!;
    st.state = 'done';
    const p = e.payload as Record<string, any>;

    if (e.event_type === 'eval_skipped') st.detail = 'eval skipped (clear answer)';
    if (e.event_type === 'fastpath_match') st.detail = 'single registry match';
    if (e.event_type === 'grounding_check') {
      st.detail = p.grounded ? 'id grounded in evidence' : 'ungrounded — blanked';
    }
    if (e.event_type === 'tool_result' && typeof p.record_count === 'number') {
      st.detail = `${p.record_count} records`;
    }

    if (typeof p.confidence === 'number') {
      st.confidence = p.confidence;
      const labels: Record<string, string> = {
        agent_answer: 'agent',
        eval_result: 'matcher',
        enrichment_done: 'final',
      };
      if (labels[e.event_type]) {
        trajectory.push({ label: labels[e.event_type], confidence: p.confidence });
      }
    }
  }

  // Mark stages before the last-touched one that never fired as "skipped" (a
  // clean fast-path skips match; eval_skipped skips the matcher LLM, etc.).
  const lastDone = STAGE_ORDER.reduce((acc, k, i) => (seen.get(k)!.state === 'done' ? i : acc), -1);
  STAGE_ORDER.forEach((k, i) => {
    const st = seen.get(k)!;
    if (st.state === 'pending' && i < lastDone) st.state = 'skipped';
    if (st.state === 'pending' && i === lastDone + 1) st.state = 'active';
  });

  return { stages: STAGE_ORDER.map((k) => seen.get(k)!), trajectory };
}

export function FlowGraph({ events }: { events: PipelineEvent[] }) {
  const { stages, trajectory } = buildStages(events);
  if (events.length === 0) return null;

  return (
    <div className="space-y-3 rounded-lg border border-line bg-white/40 px-3 py-3">
      <ol className="flex items-stretch gap-1 overflow-x-auto">
        {stages.map((s, i) => (
          <li key={s.key} className="flex items-center gap-1">
            <StageNode stage={s} />
            {i < stages.length - 1 ? (
              <span className={`h-px w-4 ${s.state === 'done' ? 'bg-accent/50' : 'bg-line'}`} />
            ) : null}
          </li>
        ))}
      </ol>
      {trajectory.length > 0 ? (
        <div className="flex items-center gap-3 border-t border-line pt-2 text-[11px]">
          <span className="font-mono uppercase tracking-wide text-muted">confidence</span>
          {trajectory.map((t, i) => (
            <span key={i} className="flex items-center gap-1">
              {i > 0 ? <span className="text-muted/50">→</span> : null}
              <span className="text-muted">{t.label}</span>
              <span className="font-semibold tabular-nums text-ink">
                {Math.round(t.confidence * 100)}%
              </span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function StageNode({ stage }: { stage: Stage }) {
  const tone =
    stage.state === 'done'
      ? 'border-accent/40 bg-accent-soft/40 text-ink'
      : stage.state === 'active'
        ? 'border-accent/40 bg-white text-ink animate-pulse'
        : stage.state === 'skipped'
          ? 'border-line bg-paper text-muted/60 line-through'
          : 'border-line bg-paper text-muted';
  return (
    <div className={`rounded-md border px-2 py-1 ${tone}`} title={stage.detail ?? stage.label}>
      <div className="font-mono text-[10px] uppercase tracking-wide">{stage.label}</div>
      {stage.confidence !== undefined ? (
        <div className="text-[10px] tabular-nums text-accent">
          {Math.round(stage.confidence * 100)}%
        </div>
      ) : stage.detail ? (
        <div className="text-[9px] text-muted truncate max-w-[8rem]">{stage.detail}</div>
      ) : null}
    </div>
  );
}

// --- Confidence signals: the SOUND breakdown of the final score -------------

type Signal = { label: string; value: string; ok: boolean };

/** The evidence behind the calibrated confidence — derived from the result the
 *  pipeline returned (flag, registry id, jurisdiction, enrichment coverage). */
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
    s.push({
      label: 'Jurisdiction alignment',
      value: r.jurisdiction_confirmed ?? '—',
      ok: aligned,
    });
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
