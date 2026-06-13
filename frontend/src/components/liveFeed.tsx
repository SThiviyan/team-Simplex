import { useCallback, useEffect, useRef, useState } from 'react';
import { listRuns, PipelineEvent, runEvents } from '../api';

// What each event type means for the reader, in one short clause. Shared by the
// single-search progress view and the batch PipelinePanel.
export function describe(e: PipelineEvent): string {
  const p = e.payload as Record<string, any>;
  switch (e.event_type) {
    case 'run_started':
      return `batch started · ${p.rows} rows`;
    case 'query_started':
      return `searching "${p.name}" [${p.jurisdiction}]`;
    case 'mcp_selected':
      return `connecting to ${p.endpoint}`;
    case 'mcp_connected':
      return `tools ready: ${(p.tools ?? []).join(', ')}`;
    case 'tool_call':
      return `→ ${p.tool}(${JSON.stringify(p.arguments ?? {}).slice(0, 70)})`;
    case 'tool_result':
      return `← ${p.tool}: ${p.record_count} records${
        p.top_hits?.length ? ` · ${p.top_hits.slice(0, 2).join('; ')}` : ''
      }`;
    case 'fastpath_match':
      return `fast path: single registry match (${p.registry_id}) — no LLM needed`;
    case 'grounding_check':
      return p.grounded
        ? `grounded: ${p.registry_id ?? '—'}`
        : `NOT grounded — blanked ${p.registry_id ?? ''}`;
    case 'agent_answer':
      return `agent answer: ${p.registry_id ?? 'no id'} @ ${Math.round((p.confidence ?? 0) * 100)}%`;
    case 'eval_started':
      return `matching layer over ${p.record_count} records`;
    case 'eval_result':
      return `matcher: ${p.decision} @ ${Math.round((p.confidence ?? 0) * 100)}%`;
    case 'eval_skipped':
      return `eval skipped — ${p.reason}`;
    case 'records_salvaged':
      return `agent died, salvaging ${p.record_count} gathered records`;
    case 'impressum_checked':
      return `impressum ${p.corroborates ? 'corroborates the registry id' : 'checked'}`;
    case 'contradiction': {
      const vals = (p.values ?? [])
        .map((v: any) => `${v.value} (${v.source})`)
        .join(' vs ');
      return `⚠ contradiction on ${p.field}: ${vals} — field left blank, confidence lowered`;
    }
    case 'enrichment_web_fill':
      return `web enrichment filled: ${(p.fields ?? []).join(', ')}`;
    case 'enrichment_done':
      return `enriched · ${p.flag} @ ${Math.round((p.confidence ?? 0) * 100)}% · ${
        (p.filled ?? []).length
      } fields`;
    case 'web_search_fallback':
      return 'falling back to web search';
    case 'agent_override':
      return `kept agent answer (${p.reason ?? 'stronger'})`;
    case 'query_completed':
      return `done: ${p.registry_id ?? 'blank'}${
        p.no_match_reason ? ` (${p.no_match_reason})` : ''
      }`;
    case 'run_completed':
      return `batch finished · ${p.rows} rows`;
    case 'error':
      return `error: ${p.kind ?? 'unknown'}`;
    default:
      return e.event_type;
  }
}

/**
 * Poll the latest pipeline run's events while `active`. Used to show live
 * progress during a search/run so the wait doesn't look like dead loading.
 * Returns the accumulated events of the most recent run.
 */
export function useLiveRun(active: boolean): PipelineEvent[] {
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const lastSeq = useRef(0);
  const runId = useRef<string | null>(null);

  const poll = useCallback(async () => {
    try {
      const runs = await listRuns();
      const latest = runs[0];
      if (!latest) return;
      if (latest.run_id !== runId.current) {
        runId.current = latest.run_id;
        lastSeq.current = 0;
        setEvents([]);
      }
      const feed = await runEvents(latest.run_id, lastSeq.current);
      if (feed.events.length) {
        lastSeq.current = feed.last_seq;
        setEvents((prev) => [...prev, ...feed.events].slice(-300));
      }
    } catch {
      /* best-effort */
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    void poll();
    const id = window.setInterval(() => void poll(), 1000);
    return () => window.clearInterval(id);
  }, [active, poll]);

  return events;
}

export function EventFeed({
  events,
  label = 'Live activity',
  max = 'max-h-72',
}: {
  events: PipelineEvent[];
  label?: string;
  max?: string;
}) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'nearest' });
  }, [events.length]);

  if (events.length === 0) return null;
  return (
    <ol
      aria-label={label}
      className={`${max} space-y-0.5 overflow-y-auto rounded-lg border border-line bg-white/40 px-3 py-2 font-mono text-[11px] leading-relaxed`}
    >
      {events.map((e) => (
        <li key={e.seq} className="flex gap-2">
          <span className="w-12 shrink-0 tabular-nums text-muted/70">{e.query_id ?? '—'}</span>
          <span className={e.event_type === 'error' ? 'text-accent' : 'text-ink/80'}>
            {describe(e)}
          </span>
        </li>
      ))}
      <div ref={bottom} />
    </ol>
  );
}
