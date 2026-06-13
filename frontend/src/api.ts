export type CompanyRecord = {
  query_id: string;
  registry_id: string | null;
  registry_court: string | null;
  name_normalized_register_name: string | null;
  jurisdiction_confirmed: string | null;
  confidence: number;
  source: string | null;
  no_match_reason: string | null;
  // Extra entity context surfaced by the gather + matching layers.
  last_update: string | null;
  address: string | null;
  organization_type: string | null;
  provider: string | null;
  snippet: string | null;
};

export type QueryRow = {
  query_id: string;
  name: string;
  jurisdiction: string | null;
  count: number;
};

// One winning result produced by the matching layer (RapidFuzz + LLM filter).
export type Winner = {
  query_id: string;
  name: string;
  jurisdiction: string | null;
  decision: 'match' | 'no_match' | 'recursive_search';
  winning_candidate: CompanyRecord | null;
  confidence: number;
  reasoning: string;
  recursive_search: { suggested_query: string } | null;
  candidates: CompanyRecord[];
};

export type CsvSearchResponse = {
  queries: QueryRow[];
  // How many records were gathered and written to the JSON file.
  count: number;
  // The final per-query winners chosen by the matching layer.
  winners: Winner[];
  // The raw result rows are NOT returned — they are written server-side to
  // `output_file` for provenance / the downstream pipeline.
  output_file?: string;
  output_file_error?: string;
};

/**
 * Convert the raw search box text into CSV with the fields `name,jurisdiction`.
 * Each line is one company; an optional comma separates name and jurisdiction
 * (e.g. "Tesla, US"). Returns a CSV string with a header row.
 */
export function queryToCsv(raw: string): string {
  const rows = raw
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const comma = line.indexOf(',');
      const name = (comma === -1 ? line : line.slice(0, comma)).trim();
      const jurisdiction = comma === -1 ? '' : line.slice(comma + 1).trim();
      return `${name},${jurisdiction}`;
    });
  return ['name,jurisdiction', ...rows].join('\n');
}

export async function csvSearch(rawQuery: string): Promise<CsvSearchResponse> {
  const csv = queryToCsv(rawQuery);
  const url = new URL('/api/csv-search', window.location.origin);
  url.searchParams.set('q', csv);
  const r = await fetch(url.toString());
  if (!r.ok) throw new Error(`search failed: ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------------------
// Full pipeline (single company) — runs the foundation hierarchy + Tier A/B
// enrichment, so the result carries EVERY CSV column and never anchors on
// Wikidata. This is what the search box uses.

// Mirrors backend ExtractionResult (app/pipeline/models.py) — the CSV row.
export type ExtractionResult = {
  query_id: string;
  registry_id: string | null;
  registry_court: string | null;
  name_normalized_register_name: string | null;
  jurisdiction_confirmed: string | null;
  no_match_reason: string | null;
  registered_address: string | null;
  incorporation_date: string | null;
  organization_type: string | null;
  status: string | null;
  vat_number: string | null;
  trade_names: string | null;
  industry_code: string | null;
  industry: string | null;
  capitalization: string | null;
  business_purpose: string | null;
  confidence_flag: string | null;
  officers: string | null;
  confidence: number;
  source: string | null;
};

type PipelineRunSummary = {
  run_id: string;
  rows_processed: number;
  output_csv: string;
  results: ExtractionResult[];
};

/** Split a free-text search box into (name, jurisdiction): "Audi, DE" or
 *  "Audi DE" -> {name:"Audi", jurisdiction:"DE"}. */
export function parseQuery(raw: string): { name: string; jurisdiction: string } {
  const text = raw.trim();
  const comma = text.indexOf(',');
  if (comma !== -1) {
    return { name: text.slice(0, comma).trim(), jurisdiction: text.slice(comma + 1).trim() };
  }
  const m = /^(.*?)\s+([A-Za-z]{2}(?:-[A-Za-z0-9]{1,3})?)$/.exec(text);
  if (m) return { name: m[1].trim(), jurisdiction: m[2].toUpperCase() };
  return { name: text, jurisdiction: '' };
}

// --- Per-tab session identity ---------------------------------------------
// A fresh browser tab gets its own sessionStorage, so each tab is its own
// pipeline session: runs started in one tab never surface in another. The id is
// sent on every pipeline request as the X-Session-Id header; the backend stamps
// it on each event row and scopes GET /runs to it (so `runs[0]` is always THIS
// tab's latest run).
const SESSION_KEY = 'kyb_session_id';

export function sessionId(): string {
  let id = sessionStorage.getItem(SESSION_KEY);
  if (!id) {
    id =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    sessionStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

function sessionHeaders(extra?: Record<string, string>): Record<string, string> {
  return { 'X-Session-Id': sessionId(), ...extra };
}

/** Resolve one company through the full pipeline and return its CSV row. */
export async function resolveCompany(raw: string): Promise<ExtractionResult> {
  const { name, jurisdiction } = parseQuery(raw);
  const r = await fetch('/api/pipeline/run', {
    method: 'POST',
    headers: sessionHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ query: { name, jurisdiction } }),
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`resolve failed: ${r.status} ${detail.slice(0, 200)}`);
  }
  const summary: PipelineRunSummary = await r.json();
  const row = summary.results[0];
  if (!row) throw new Error('pipeline returned no result row');
  return row;
}

// ---------------------------------------------------------------------------
// Pipeline (registry-lookup chain): CSV upload, live run feed.

export type PipelineRun = {
  run_id: string;
  started_at: number;
  last_event_at: number;
  event_count: number;
  status: 'running' | 'completed';
};

export type PipelineEvent = {
  seq: number;
  query_id: string | null;
  ts: number;
  event_type: string;
  payload: Record<string, unknown>;
};

// One named contribution to the deterministic confidence score (backend
// app/pipeline/confidence.py — emitted in the enrichment_done event). The final
// confidence is the sum of these, so the score is fully traceable.
export type ConfidenceComponent = { label: string; points: number; detail: string };

/** The confidence breakdown from the latest enrichment_done event, if any. */
export function confidenceBreakdown(
  events: PipelineEvent[],
): ConfidenceComponent[] | undefined {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].event_type === 'enrichment_done') {
      const sig = (events[i].payload as { signals?: unknown }).signals;
      return Array.isArray(sig) ? (sig as ConfidenceComponent[]) : undefined;
    }
  }
  return undefined;
}

export async function listRuns(): Promise<PipelineRun[]> {
  const r = await fetch('/api/pipeline/runs', { headers: sessionHeaders() });
  if (!r.ok) throw new Error(`runs failed: ${r.status}`);
  const data = await r.json();
  return data.runs as PipelineRun[];
}

export async function runEvents(
  runId: string,
  after: number,
): Promise<{ events: PipelineEvent[]; last_seq: number }> {
  const url = new URL(`/api/pipeline/runs/${runId}/events`, window.location.origin);
  url.searchParams.set('after', String(after));
  const r = await fetch(url.toString(), { headers: sessionHeaders() });
  if (!r.ok) throw new Error(`events failed: ${r.status}`);
  return r.json();
}

/**
 * Upload a query CSV (query_id,name,jurisdiction), run the whole pipeline,
 * and resolve with the finished result CSV as a Blob plus its filename.
 * The promise resolves only when the batch is done — poll listRuns/runEvents
 * in parallel to show live progress.
 */
export async function uploadPipelineCsv(
  file: File,
): Promise<{ blob: Blob; filename: string }> {
  const body = new FormData();
  body.append('file', file);
  const r = await fetch('/api/pipeline/run-csv', {
    method: 'POST',
    body,
    headers: sessionHeaders(),
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`pipeline failed: ${r.status} ${detail.slice(0, 200)}`);
  }
  const disposition = r.headers.get('content-disposition') ?? '';
  const m = /filename="?([^";]+)"?/.exec(disposition);
  return { blob: await r.blob(), filename: m?.[1] ?? 'results.csv' };
}
