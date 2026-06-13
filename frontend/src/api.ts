// RapidFuzz diagnostics the matching layer attaches to each shortlisted
// candidate — the raw signals behind the confidence score.
export type MatchDiagnostics = {
  name_score: number; // fuzzy name similarity, 0..1
  jurisdiction_match: boolean;
  prior_confidence: number; // confidence from the gather layer, pre re-scoring
};

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
  // Present on candidates that went through the RapidFuzz layer.
  _match?: MatchDiagnostics;
};

export type QueryRow = {
  query_id: string;
  name: string;
  jurisdiction: string | null;
  count: number;
  // Which registers were queried vs skipped for this row (jurisdiction routing).
  sources_called?: string[];
  sources_skipped?: string[];
};

// Who owns/controls a matched company — web-searched after the match is made.
export type Owner = {
  owner_name: string | null;
  owner_type: string | null;
  ownership_basis: string | null;
  confidence: number;
  source: string | null;
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
  // The owner, when found (decision === 'match' and a web result was returned).
  owner?: Owner | null;
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
