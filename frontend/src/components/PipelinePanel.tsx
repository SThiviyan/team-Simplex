import { useCallback, useEffect, useRef, useState } from 'react';
import {
  listRuns,
  PipelineEvent,
  PipelineRun,
  runEvents,
  uploadPipelineCsv,
} from '../api';

/**
 * Batch pipeline panel: upload the query CSV, watch the agent work live
 * (every tool call, grounding check, eval decision and enrichment step from
 * the event log), download the finished result CSV when the run completes.
 */
export function PipelinePanel() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const lastSeq = useRef(0);
  const watching = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);

  // Poll the run list + incremental events while a batch is in flight (and
  // keep watching the latest run afterwards so the page doubles as a live
  // monitor for runs started elsewhere, e.g. the CLI).
  const poll = useCallback(async () => {
    try {
      const runs = await listRuns();
      const latest = runs[0] ?? null;
      setRun(latest);
      if (!latest) return;
      if (lastSeq.current === 0 || latest.run_id !== watchingRunId.current) {
        watchingRunId.current = latest.run_id;
        lastSeq.current = 0;
        setEvents([]);
      }
      const feed = await runEvents(latest.run_id, lastSeq.current);
      if (feed.events.length) {
        lastSeq.current = feed.last_seq;
        setEvents((prev) => [...prev, ...feed.events].slice(-400));
      }
    } catch {
      // Polling is best-effort; the upload promise carries the real error.
    }
  }, []);
  const watchingRunId = useRef<string | null>(null);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (watching.current || busy) void poll();
    }, 1500);
    return () => window.clearInterval(id);
  }, [busy, poll]);

  async function onUpload(file: File) {
    setBusy(true);
    setError(null);
    setDone(null);
    watching.current = true;
    void poll();
    try {
      const { blob, filename } = await uploadPipelineCsv(file);
      // Hand the finished CSV to the browser as a download.
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      setDone(filename);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'pipeline failed');
    } finally {
      setBusy(false);
      void poll(); // pick up the run_completed event
    }
  }

  return (
    <section className="space-y-4 rounded-xl border border-line bg-paper px-4 py-4">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted">
            Batch pipeline
          </p>
          <h2 className="text-base font-semibold text-ink">
            Upload a query CSV, get the result CSV
          </h2>
          <p className="text-[12px] text-muted">
            Columns: <span className="font-mono">query_id, name, jurisdiction</span>. The run
            streams below while the agents work; the finished file downloads automatically.
          </p>
        </div>
        <RunBadge run={run} busy={busy} />
      </div>

      <div className="flex items-center gap-3">
        <input
          ref={fileInput}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void onUpload(f);
            e.target.value = '';
          }}
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => fileInput.current?.click()}
          className="rounded-lg bg-ink px-3.5 py-2 text-sm font-medium text-paper transition-opacity hover:opacity-85 disabled:opacity-40"
        >
          {busy ? 'Pipeline running…' : 'Upload CSV & run'}
        </button>
        <button
          type="button"
          onClick={() => {
            watching.current = !watching.current;
            if (watching.current) void poll();
          }}
          className="text-[12px] font-medium text-accent hover:text-ink transition-colors"
        >
          {watching.current ? 'live view on' : 'watch latest run'}
        </button>
      </div>

      {error ? (
        <p role="alert" className="text-[13px] text-ink rounded-lg border border-accent/30 bg-accent-soft/40 px-3 py-2">
          {error}
        </p>
      ) : null}
      {done ? (
        <p className="text-[13px] text-ink">
          Done — <span className="font-mono">{done}</span> downloaded.
        </p>
      ) : null}

      {events.length > 0 ? <EventFeed events={events} /> : null}
    </section>
  );
}

function RunBadge({ run, busy }: { run: PipelineRun | null; busy: boolean }) {
  if (!run && !busy) return null;
  const running = busy || run?.status === 'running';
  return (
    <span
      className={`shrink-0 rounded-full px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide ${
        running ? 'bg-accent/10 text-accent' : 'bg-line/60 text-muted'
      }`}
    >
      {running ? '● live' : `run ${run?.run_id.slice(0, 8)} · done`}
    </span>
  );
}

// What each event type means for the reader, in one short clause.
function describe(e: PipelineEvent): string {
  const p = e.payload as Record<string, any>;
  switch (e.event_type) {
    case 'run_started':
      return `batch started · ${p.rows} rows`;
    case 'query_started':
      return `searching "${p.name}" [${p.jurisdiction}]`;
    case 'mcp_selected':
      return `connecting to ${p.endpoint}`;
    case 'mcp_connected':
      return `tools: ${(p.tools ?? []).join(', ')}`;
    case 'tool_call':
      return `→ ${p.tool}(${JSON.stringify(p.arguments ?? {}).slice(0, 80)})`;
    case 'tool_result':
      return `← ${p.tool}: ${p.record_count} records${
        p.top_hits?.length ? ` · ${p.top_hits.slice(0, 2).join('; ')}` : ''
      }`;
    case 'grounding_check':
      return p.grounded
        ? `grounded: ${p.registry_id ?? '—'}`
        : `NOT grounded — blanked ${p.registry_id ?? ''}`;
    case 'agent_answer':
      return `answer: ${p.registry_id ?? 'no id'} @ ${Math.round((p.confidence ?? 0) * 100)}%`;
    case 'eval_started':
      return `matching layer over ${p.record_count} records`;
    case 'eval_result':
      return `matcher: ${p.decision} @ ${Math.round((p.confidence ?? 0) * 100)}%`;
    case 'eval_skipped':
      return `eval skipped — ${p.reason}`;
    case 'recursion_triggered':
      return `recursive search: "${p.suggested_query}"`;
    case 'records_salvaged':
      return `agent died, salvaging ${p.record_count} gathered records`;
    case 'impressum_checked':
      return `impressum ${p.corroborates ? 'corroborates the registry id' : 'checked'} (${p.url})`;
    case 'enrichment_web_fill':
      return `web enrichment filled: ${(p.fields ?? []).join(', ')}`;
    case 'enrichment_done':
      return `done · ${p.flag} @ ${Math.round((p.confidence ?? 0) * 100)}% · filled ${(p.filled ?? []).length} fields`;
    case 'web_search_fallback':
      return 'falling back to web search';
    case 'agent_override':
      return `kept agent answer (${p.reason ?? 'stronger'})`;
    case 'query_completed':
      return `completed: ${p.registry_id ?? 'blank'}${p.no_match_reason ? ` (${p.no_match_reason})` : ''}`;
    case 'run_completed':
      return `batch finished · ${p.rows} rows → ${p.output_csv}`;
    case 'error':
      return `error: ${p.kind ?? 'unknown'}`;
    default:
      return JSON.stringify(p).slice(0, 100);
  }
}

function EventFeed({ events }: { events: PipelineEvent[] }) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'nearest' });
  }, [events.length]);

  return (
    <ol
      aria-label="Live agent activity"
      className="max-h-72 space-y-0.5 overflow-y-auto rounded-lg border border-line bg-white/40 px-3 py-2 font-mono text-[11px] leading-relaxed"
    >
      {events.map((e) => (
        <li key={e.seq} className="flex gap-2">
          <span className="shrink-0 text-muted/70 tabular-nums w-12">
            {e.query_id ?? '—'}
          </span>
          <span className={e.event_type === 'error' ? 'text-accent' : 'text-ink/80'}>
            {describe(e)}
          </span>
        </li>
      ))}
      <div ref={bottom} />
    </ol>
  );
}
