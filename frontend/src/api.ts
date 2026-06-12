export type CompanyRecord = {
  query_id: string;
  registry_id: string | null;
  registry_court: string | null;
  name_normalized_register_name: string | null;
  jurisdiction_confirmed: string | null;
  confidence: number;
  source: string | null;
  no_match_reason: string | null;
  provider: string | null;
  snippet: string | null;
};

export type QueryRow = {
  query_id: string;
  name: string;
  jurisdiction: string | null;
  count: number;
};

export type CsvSearchResponse = {
  queries: QueryRow[];
  count: number;
  results: CompanyRecord[];
  output_file?: string;
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
