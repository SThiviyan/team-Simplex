import { CompanyRecord } from '../api';

// One human-readable reason the matcher is (or isn't) confident in a record.
export type Signal = {
  label: string;
  value: string;
  ok: boolean;
  hint: string;
};

/**
 * A stable identity for a record. The backend returns the winner as a *copy* of
 * a shortlist record, so object identity can't be used to find it again — match
 * on the registry id (falling back to provider + name).
 */
export function recordKey(rec: CompanyRecord): string {
  return rec.registry_id ?? `${rec.provider ?? ''}:${rec.name_normalized_register_name ?? ''}`;
}

/**
 * Derive the confidence signals behind a candidate from the data the backend
 * already returns — the RapidFuzz `_match` diagnostics plus the enriched
 * registry fields. These are the marks shown when hovering a candidate.
 */
export function confidenceSignals(rec: CompanyRecord): Signal[] {
  const s: Signal[] = [];
  const m = rec._match;

  if (m && typeof m.name_score === 'number') {
    s.push({
      label: 'Fuzzy name match',
      value: `${Math.round(m.name_score * 100)}%`,
      ok: m.name_score >= 0.6,
      hint: 'RapidFuzz token similarity between your query and the registered name.',
    });
  }
  if (m && typeof m.jurisdiction_match === 'boolean') {
    s.push({
      label: 'Jurisdiction alignment',
      value: rec.jurisdiction_confirmed ?? '—',
      ok: m.jurisdiction_match,
      hint: m.jurisdiction_match
        ? 'Registry jurisdiction matches the requested country.'
        : 'Registry jurisdiction differs from the requested country.',
    });
  }
  if (rec.registry_id) {
    s.push({
      label: 'Active registry record',
      value: rec.registry_id,
      ok: true,
      hint: 'Entity carries an official registration number / LEI in the register.',
    });
  }
  if (rec.organization_type) {
    s.push({
      label: 'Legal form',
      value: rec.organization_type,
      ok: true,
      hint: 'Registered legal / organization form (e.g. GmbH, AG).',
    });
  }
  if (rec.address) {
    s.push({
      label: 'Registered address',
      value: rec.address,
      ok: true,
      hint: 'A registered address is on file for this entity.',
    });
  }
  if (rec.last_update) {
    s.push({
      label: 'Data freshness',
      value: rec.last_update,
      ok: true,
      hint: 'When the source last updated this record.',
    });
  }
  // --- Corroboration / "fame" from the graph-consolidation layer ----------
  const providerCount = m?.provider_count ?? rec._provider_count;
  if (typeof providerCount === 'number' && providerCount > 1) {
    const provs = rec._providers?.length ? ` · ${rec._providers.join(', ')}` : '';
    s.push({
      label: 'Corroborating sources',
      value: `${providerCount}×${provs}`,
      ok: true,
      hint: 'Distinct registers/sources that independently returned this entity; '
        + 'duplicates were merged into one record by graph consolidation.',
    });
  }
  if (m && typeof m.confidence_boost === 'number' && m.confidence_boost > 0) {
    s.push({
      label: 'Fame boost',
      value: `+${Math.round(m.confidence_boost * 100)}%`,
      ok: true,
      hint: 'Confidence lift from multi-source corroboration (well-attested entities '
        + 'are nudged toward 1.0).',
    });
  }
  const completeness = m?.completeness ?? rec._completeness;
  if (typeof completeness === 'number') {
    s.push({
      label: 'Record completeness',
      value: `${Math.round(completeness * 100)}%`,
      ok: completeness >= 0.5,
      hint: 'Share of key registry attributes populated after merging duplicate records.',
    });
  }
  // Conflicts resolved by cross-referencing duplicate records (e.g. two sources
  // gave a different address); shows which value won the output and why.
  for (const cf of rec._conflicts ?? []) {
    s.push({
      label: `Cross-referenced ${cf.field.replace(/_/g, ' ')}`,
      value: cf.reason,
      ok: true,
      hint: `Sources disagreed on ${cf.field.replace(/_/g, ' ')}; "${cf.chosen}" was written `
        + 'to the output after cross-referencing the gathered evidence.',
    });
  }
  return s;
}

function Mark({ ok }: { ok: boolean }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden className="shrink-0">
      {ok ? (
        <path
          d="M20 6 9 17l-5-5"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : (
        <path
          d="M18 6 6 18M6 6l12 12"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </svg>
  );
}

/** A vertical list of confidence signals, each with a check / cross mark. */
export function SignalList({ signals }: { signals: Signal[] }) {
  if (signals.length === 0) {
    return <p className="text-[11px] text-muted">No signals available.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {signals.map((sig) => (
        <li key={sig.label} className="flex items-start gap-2 text-[11px] leading-snug" title={sig.hint}>
          <span className={sig.ok ? 'mt-0.5 text-accent' : 'mt-0.5 text-muted'}>
            <Mark ok={sig.ok} />
          </span>
          <span className="min-w-0">
            <span className="font-medium text-ink">{sig.label}</span>
            <span className="text-muted"> · </span>
            <span className="break-all text-ink/80">{sig.value}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
