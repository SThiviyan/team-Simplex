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

export async function listRuns(): Promise<PipelineRun[]> {
  const r = await fetch('/api/pipeline/runs');
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
  const r = await fetch(url.toString());
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
  const r = await fetch('/api/pipeline/run-csv', { method: 'POST', body });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`pipeline failed: ${r.status} ${detail.slice(0, 200)}`);
  }
  const disposition = r.headers.get('content-disposition') ?? '';
  const m = /filename="?([^";]+)"?/.exec(disposition);
  return { blob: await r.blob(), filename: m?.[1] ?? 'results.csv' };
}
