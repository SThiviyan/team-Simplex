export type SearchResult = {
  title: string;
  url: string | null;
  snippet: string;
  score: number;
  source: string;
};

export type SearchResponse = {
  query: string;
  count: number;
  results: SearchResult[];
};

export async function search(q: string): Promise<SearchResponse> {
  const url = new URL('/api/search', window.location.origin);
  url.searchParams.set('q', q);
  const r = await fetch(url.toString());
  if (!r.ok) throw new Error(`search failed: ${r.status}`);
  return r.json();
}
